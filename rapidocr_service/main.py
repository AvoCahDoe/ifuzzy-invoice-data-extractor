import os
import time
import tempfile
import html.parser
import psutil
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from PIL import Image

app = FastAPI(title="ONNX Hybrid OCR Service")


# ---------------------------------------------------------------------------
# HTML → Markdown pipe-table converter
# Uses Python's built-in html.parser — no extra dependencies required.
# ---------------------------------------------------------------------------

class HTMLTableToMarkdown(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.markdown = ""
        self.row_data = []
        self.in_cell = False
        self.is_header = False
        self._current_cell_data = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row_data = []
        elif tag == "th":
            self.in_cell = True
            self.is_header = True
            self._current_cell_data = []
        elif tag == "td":
            self.in_cell = True
            self._current_cell_data = []

    def handle_data(self, data):
        if self.in_cell:
            cleaned = data.strip().replace("\n", " ")
            if cleaned:
                self._current_cell_data.append(cleaned)

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self.row_data.append(" ".join(self._current_cell_data))
            self.in_cell = False
        elif tag == "tr":
            if not self.row_data:
                return
            row_str = "| " + " | ".join(self.row_data) + " |\n"
            self.markdown += row_str
            # Add Markdown separator line after the header row
            if self.is_header:
                separator = "| " + " | ".join(["---"] * len(self.row_data)) + " |\n"
                self.markdown += separator
                self.is_header = False


def html_to_markdown(html_string: str) -> str:
    """Convert an HTML table string to Markdown pipe-table format."""
    if not html_string or "<table" not in html_string.lower():
        return html_string or ""
    parser = HTMLTableToMarkdown()
    try:
        parser.feed(html_string)
    except Exception:
        return html_string
    return parser.markdown.strip()


# ---------------------------------------------------------------------------
# PDF → PIL images
# ---------------------------------------------------------------------------

def pdf_to_images(pdf_path: str, dpi: int = 200) -> list:
    """Convert each PDF page to a PIL Image using PyMuPDF."""
    import fitz
    doc = fitz.open(pdf_path)
    images = []
    for page in doc:
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        images.append(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
    doc.close()
    return images


# ---------------------------------------------------------------------------
# Core ONNX hybrid pipeline
# ---------------------------------------------------------------------------

def run_onnx_ocr(images: list) -> str:
    """
    Hybrid ONNX pipeline per page using masking to prevent text duplication.
    """
    import numpy as np
    import tempfile
    import os
    import sys

    # Monkeypatch to fix rapid_table dependency missing rapidocr package
    try:
        import rapidocr_onnxruntime
        sys.modules["rapidocr"] = rapidocr_onnxruntime
    except ImportError:
        pass

    from rapid_layout import RapidLayout, ModelType as LayoutModelType
    from rapid_table import ModelType, RapidTable, RapidTableInput
    from rapidocr_onnxruntime import RapidOCR

    layout_engine = RapidLayout(model_type=LayoutModelType.PP_DOC_LAYOUTV3)
    ocr_engine = RapidOCR()
    table_engine = RapidTable(RapidTableInput(model_type=ModelType.PPSTRUCTURE_EN))

    # Wrapper to bridge API mismatch: rapid_table v3 expects an object with boxes/txts/scores
    # but rapidocr_onnxruntime v1.4.x returns a tuple (result_list, elapsed)
    class OcrWrapper:
        def __init__(self, engine):
            self.engine = engine
        def __call__(self, img):
            res = self.engine(img)
            class OcrResObj: pass
            obj = OcrResObj()
            if not res or not res[0]:
                obj.boxes = None
                return obj
            boxes, txts, scores = [], [], []
            for item in res[0]:
                if len(item) == 3:
                    boxes.append(item[0])
                    txts.append(item[1])
                    scores.append(item[2])
            obj.boxes = np.array(boxes)
            obj.txts = txts
            obj.scores = scores
            return obj

    table_engine.ocr_engine = OcrWrapper(ocr_engine)

    all_sections: list[str] = []

    for page_num, pil_img in enumerate(images):
        if len(images) > 1:
            all_sections.append(f"\n---\n*Page {page_num + 1}*\n")

        img_array = np.array(pil_img)
        page_sections: list[str] = []

        # ── 1. Layout detection ────────────────────────────────────────────
        layout_boxes = []      # [[x1,y1,x2,y2], ...]
        layout_classes = []    # ['table', 'text', ...]

        try:
            layout_out = layout_engine(img_array)
            if layout_out.boxes and layout_out.class_names:
                layout_boxes = layout_out.boxes
                layout_classes = layout_out.class_names
        except Exception as e:
            print(f"[layout] page {page_num + 1} failed: {e}")

        # ── 2. Process Tables & Mask Regions ───────────────────────────────
        img_for_ocr = img_array.copy()

        if layout_boxes:
            for bbox, cat in zip(layout_boxes, layout_classes):
                if "table" in cat.lower():
                    x1, y1, x2, y2 = (int(v) for v in bbox[:4])
                    # Ensure bounds are within image to avoid slicing errors
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(img_array.shape[1], x2), min(img_array.shape[0], y2)
                    
                    crop = pil_img.crop((x1, y1, x2, y2))
                    
                    # Table Structure Recognition → Markdown pipe-table
                    try:
                        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                            crop.save(tmp.name, format="PNG")
                            crop_path = tmp.name
                        try:
                            table_res = table_engine(crop_path)
                            if table_res and hasattr(table_res, "pred_htmls") and table_res.pred_htmls:
                                html_str = "".join(table_res.pred_htmls)
                                md = html_to_markdown(html_str)
                                if md.strip():
                                    page_sections.append(md)
                        finally:
                            if os.path.exists(crop_path):
                                os.unlink(crop_path)
                    except Exception as e:
                        print(f"[table] region ({cat}) failed: {e}")

                    # Mask out the table region in the OCR array with white pixels
                    img_for_ocr[y1:y2, x1:x2] = 255

        # ── 3. Full-page OCR on the remaining content ─────────────────────
        try:
            ocr_result = ocr_engine(img_for_ocr)
            lines = _extract_ocr_lines(ocr_result)
            if lines:
                page_sections.append("\n".join(lines))
        except Exception as e:
            print(f"[ocr] page {page_num + 1} failed: {e}")
            import traceback; traceback.print_exc()

        all_sections.extend(page_sections)

    return "\n\n".join(s for s in all_sections if s.strip())


def _extract_ocr_lines(ocr_result, min_score: float = 0.4) -> list[str]:
    """
    Extract text lines from RapidOCR output utilizing bounding box clustering
    to preserve horizontal relationships (keys to values).
    
    RapidOCR.__call__ returns: (result_list, elapsed)
    Each item: [box_points, text_string, score_float]
    box_points is 4 corners: [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
    """
    if ocr_result is None:
        return []
    # Unpack tuple
    result_list = ocr_result[0] if isinstance(ocr_result, (list, tuple)) else ocr_result
    if not result_list:
        return []

    blocks = []
    for item in result_list:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        text = str(item[1]).strip() if len(item) > 1 else ""
        try:
            score = float(item[2]) if len(item) > 2 else 1.0
        except (TypeError, ValueError):
            score = 1.0
            
        if score < min_score or not text:
            continue
            
        box = item[0]
        if not isinstance(box, list) or len(box) != 4:
            blocks.append({"text": text, "x_center": 0, "y_center": 0, "h": 10})
            continue
            
        # Calculate bounding box metrics
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        min_y, max_y = min(ys), max(ys)
        
        blocks.append({
            "text": text,
            "x_center": sum(xs) / 4.0,
            "y_center": sum(ys) / 4.0,
            "h": max(1, max_y - min_y),
            "min_y": min_y,
            "max_y": max_y
        })

    if not blocks:
        return []

    # Sort vertically first
    blocks.sort(key=lambda b: b["y_center"])

    lines = []
    current_line = [blocks[0]]
    
    for i in range(1, len(blocks)):
        curr = blocks[i]
        prev = current_line[-1] # compare to most recent added conceptually
        # Alternatively, compare to the average y of the current line
        line_y = sum(b["y_center"] for b in current_line) / len(current_line)
        line_h = sum(b["h"] for b in current_line) / len(current_line)
        
        # If the current block's vertical center is within roughly half a height of the line's center
        if abs(curr["y_center"] - line_y) < (line_h * 0.6):
            current_line.append(curr)
        else:
            lines.append(current_line)
            current_line = [curr]
            
    if current_line:
        lines.append(current_line)

    # Sort each row horizontally and join
    final_strings = []
    for row in lines:
        row.sort(key=lambda b: b["x_center"])
        # Use tab separation so the LLM intuitively parses the space between columns
        final_strings.append(" \t ".join(b["text"] for b in row))

    return final_strings


# ---------------------------------------------------------------------------
# FastAPI endpoint
# ---------------------------------------------------------------------------

@app.post("/convert")
async def convert(
    file: UploadFile = File(...),
    force_ocr: bool = Form(False),
    use_llm: bool = Form(False),
):
    temp_file_path = None
    try:
        start_time = time.time()
        process = psutil.Process()
        start_mem = process.memory_info().rss / 1024 / 1024

        original_filename = file.filename
        file_content = await file.read()
        file_path = Path(original_filename)
        suffix = file_path.suffix.lower() if file_path.suffix else ".pdf"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_content)
            temp_file_path = tmp.name

        if suffix in (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"):
            try:
                images = [Image.open(temp_file_path).convert("RGB")]
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to open image: {e}")
        elif suffix == ".pdf":
            try:
                images = pdf_to_images(temp_file_path)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to convert PDF: {e}")
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

        try:
            markdown_text = run_onnx_ocr(images)
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"OCR pipeline failed: {e}")

        end_mem = psutil.Process().memory_info().rss / 1024 / 1024

        return {
            "file_name": file_path.stem,
            "original_path": original_filename,
            "content": markdown_text,
            "images": [],
            "images_count": 0,
            "extraction_timestamp": time.time(),
            "processing_time": time.time() - start_time,
            "memory_usage_mb": max(0, end_mem - start_mem),
            "extraction_mode": "onnx_hybrid",
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)


@app.get("/health")
def health_check():
    return {"status": "ok"}
