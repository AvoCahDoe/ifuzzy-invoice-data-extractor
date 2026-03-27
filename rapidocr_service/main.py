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
        self.separator_added = False
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
            # Add Markdown separator line after the first row (header or first data row)
            if not self.separator_added:
                # Calculate number of columns based on row_data
                cols = len(self.row_data)
                separator = "| " + " | ".join(["---"] * cols) + " |\n"
                self.markdown += separator
                self.separator_added = True
            
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
# Direct Digital PDF Extraction (Fast Path)
# ---------------------------------------------------------------------------

def pdf_has_text(pdf_path: str) -> bool:
    """Check if the PDF has embedded selectable text on any page."""
    import fitz
    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            text = page.get_text("text").strip()
            # If we find at least 20 characters of embedded text, it's a digital PDF
            if len(text) > 20: 
                doc.close()
                return True
        doc.close()
    except Exception as e:
        print(f"pdf_has_text error: {e}")
    return False

def extract_text_from_pdf(pdf_path: str) -> tuple[str, list[dict], list[dict]]:
    """Extract embedded text and tables directly using PyMuPDF (No OCR).

    Returns (markdown, blocks, table_regions). Table bboxes use PyMuPDF page coordinates
    (may not match rasterized preview if the UI shows the native PDF viewer).
    """
    import fitz
    doc = fitz.open(pdf_path)
    all_sections = []
    all_blocks: list[dict] = []
    all_table_regions: list[dict] = []

    for page_num, page in enumerate(doc):
        if len(doc) > 1:
            all_sections.append(f"\n---\n*Page {page_num + 1}*\n")

        page_sections = []

        # 1. Extract tables via PyMuPDF
        tabs = page.find_tables()
        table_bboxes = []
        if tabs and tabs.tables:
            for table in tabs.tables:
                table_bboxes.append(table.bbox)
                tx0, ty0, tx1, ty1 = table.bbox
                all_table_regions.append({
                    "kind": "table",
                    "page_num": page_num,
                    "bbox": [float(tx0), float(ty0), float(tx1), float(ty1)],
                })
                try:
                    import pandas as pd
                    import tabulate
                    df = table.to_pandas()
                    md = df.to_markdown(index=False)
                except ImportError:
                    print("[warning] pandas or tabulate not found; simple string conversion fallback used.")
                    md = str(table.to_pandas())
                if md and md.strip():
                    page_sections.append(md)

        # 2. Extract regular text blocks, omitting those inside tables
        blocks = page.get_text("blocks")
        text_blocks = []
        for b in blocks:
            x0, y0, x1, y1, text, _, block_type = b
            if block_type != 0:  # 0 means text block
                continue

            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            in_table = False
            for t_bbox in table_bboxes:
                tx0, ty0, tx1, ty1 = t_bbox
                if tx0 <= cx <= tx1 and ty0 <= cy <= ty1:
                    in_table = True
                    break

            if not in_table and text.strip():
                text_blocks.append((y0, x0, text.strip()))
                all_blocks.append({
                    "text": text.strip(),
                    "bbox": [x0, y0, x1, y1],
                    "confidence": 1.0,
                    "page_num": page_num
                })

        # Sort top-to-bottom, then left-to-right
        text_blocks.sort(key=lambda x: (x[0], x[1]))

        if text_blocks:
            page_sections.append("\n\n".join([b[2] for b in text_blocks]))

        all_sections.extend(page_sections)

    doc.close()
    return "\n\n".join(all_sections), all_blocks, all_table_regions


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

# Monkeypatch to fix rapid_table dependency missing rapidocr package
import rapidocr_onnxruntime
import sys
sys.modules["rapidocr"] = rapidocr_onnxruntime

from rapid_layout import RapidLayout, ModelType as LayoutModelType
from rapid_table import ModelType, RapidTable, RapidTableInput
from rapidocr_onnxruntime import RapidOCR
import numpy as np

# Global shared engines
layout_engine = RapidLayout(model_type=LayoutModelType.PP_DOC_LAYOUTV3)
ocr_engine = RapidOCR()
table_engine = RapidTable(RapidTableInput(model_type=ModelType.PPSTRUCTURE_EN))

# Wrapper to bridge API mismatch: rapid_table v3 expects an object with boxes/txts/scores
# but rapidocr_onnxruntime v1.4.x returns a tuple (result_list, elapsed)
# Bridge adapter: rapid_table expects an object with specific attributes (boxes, txts, scores)
# but rapidocr_onnxruntime returns a raw tuple. This wrapper formats the results correctly.
class OcrWrapper:
    def __init__(self, engine):
        self.engine = engine
    def __call__(self, img):
        # Run actual OCR on the image
        res = self.engine(img)
        class OcrResObj: pass
        obj = OcrResObj()
        # Handle cases where OCR finds nothing
        if not res or not res[0]:
            obj.boxes = None
            return obj
        
        # Reorganize raw [bbox, text, score] items into separate lists
        boxes, txts, scores = [], [], []
        for item in res[0]:
            if len(item) == 3:
                boxes.append(item[0])
                txts.append(item[1])
                scores.append(item[2])
        
        # Convert to NumPy array for compatibility with rapid_table
        obj.boxes = np.array(boxes)
        obj.txts = txts
        obj.scores = scores
        return obj

# Inject wrapper into the table engine so it can read text inside tables correctly
table_engine.ocr_engine = OcrWrapper(ocr_engine)

def run_onnx_ocr(images: list) -> tuple[str, list[float], list[dict], list[dict]]:
    """
    Hybrid ONNX pipeline per page using masking to prevent text duplication.
    1. Finds tables with Layout engine.
    2. Extracts structured tables into Markdown.
    3. Masks (whites out) table areas so general OCR ignores them.
    4. Runs general OCR on remaining text.

    Returns (markdown, confidences, blocks, table_regions). Table bboxes match the raster
    image used for OCR (same coordinate space as text blocks).
    """
    all_sections: list[str] = []
    all_confidences: list[float] = []
    all_blocks: list[dict] = []
    all_table_regions: list[dict] = []

    for page_num, pil_img in enumerate(images):
        if len(images) > 1:
            all_sections.append(f"\n---\n*Page {page_num + 1}*\n")

        # Convert PIL to NumPy array for AI engines
        img_array = np.array(pil_img)
        page_sections: list[str] = []

        # ── 1. Layout detection ────────────────────────────────────────────
        # Finds regions like 'table', 'text', 'figure', etc.
        layout_boxes = []      
        layout_classes = []    
        layout_scores = []

        try:
            layout_out = layout_engine(img_array)
            if layout_out.boxes and layout_out.class_names:
                layout_boxes = layout_out.boxes
                layout_classes = layout_out.class_names
                if hasattr(layout_out, "scores"):
                    layout_scores = layout_out.scores
        except Exception as e:
            print(f"[layout] page {page_num + 1} failed: {e}")

        # ── 2. Process Tables & Mask Regions ───────────────────────────────
        # Create a copy to mask out tables so the final OCR doesn't double-process them
        img_for_ocr = img_array.copy()

        if layout_boxes:
            for idx, (bbox, cat) in enumerate(zip(layout_boxes, layout_classes)):
                if "table" in cat.lower():
                    # Get integer coordinates and clamp to image bounds
                    x1, y1, x2, y2 = (int(v) for v in bbox[:4])
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(img_array.shape[1], x2), min(img_array.shape[0], y2)

                    all_table_regions.append({
                        "kind": "table",
                        "page_num": page_num,
                        "bbox": [float(x1), float(y1), float(x2), float(y2)],
                    })
                    
                    if idx < len(layout_scores):
                        all_confidences.append(float(layout_scores[idx]))

                    # Table content recognition -> Convert HTML result to Markdown Pipe-Table
                    try:
                        crop_array = img_array[y1:y2, x1:x2]
                        table_res = table_engine(crop_array)
                        
                        if table_res and hasattr(table_res, "pred_htmls") and table_res.pred_htmls:
                            html_str = "".join(table_res.pred_htmls)
                            md = html_to_markdown(html_str)
                            if md.strip():
                                page_sections.append(md)
                    except Exception as e:
                        print(f"[table] region ({cat}) failed: {e}")

                    # MASKING: Paint table region white so general OCR ignores it
                    img_for_ocr[y1:y2, x1:x2] = 255

        # ── 3. Full-page OCR on the remaining (non-table) content ──────────────────
        try:
            ocr_result = ocr_engine(img_for_ocr)
            # Group scattered snippets into horizontal lines
            lines, confs, page_blocks = _extract_ocr_lines(ocr_result)
            if lines:
                page_sections.append("\n".join(lines))
                all_confidences.extend(confs)
            for blk in page_blocks:
                blk["page_num"] = page_num
                all_blocks.append(blk)
        except Exception as e:
            print(f"[ocr] page {page_num + 1} failed: {e}")
            import traceback; traceback.print_exc()

        all_sections.extend(page_sections)

    return "\n\n".join(s for s in all_sections if s.strip()), all_confidences, all_blocks, all_table_regions


def _extract_ocr_lines(ocr_result, min_score: float = 0.4) -> tuple[list[str], list[float], list[dict]]:
    """
    Robust line extraction using spatial clustering (vertical overlap) and
    physical distance-based tab separation to preserve table column layout.
    Returns (lines, confidences, blocks) where blocks have text, bbox, confidence for rule-based structuring.
    """
    if ocr_result is None:
        return [], [], []
    result_list = ocr_result[0] if isinstance(ocr_result, (list, tuple)) else ocr_result
    if not result_list:
        return [], [], []

    blocks = []
    all_confs = []
    for item in result_list:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        text = str(item[1]).strip()
        try:
            score = float(item[2])
        except (TypeError, ValueError):
            score = 1.0
            
        if score < min_score or not text:
            continue
            
        all_confs.append(score)
        box = item[0]
        if not isinstance(box, list) or len(box) != 4:
            continue
            
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        
        blocks.append({
            "text": text,
            "bbox": [min_x, min_y, max_x, max_y],
            "confidence": score,
            "min_x": min_x,
            "max_x": max_x,
            "min_y": min_y,
            "max_y": max_y,
            "cx": (min_x + max_x) / 2,
            "cy": (min_y + max_y) / 2,
            "h": max(1, max_y - min_y),
            "w": max(1, max_x - min_x)
        })

    if not blocks:
        return [], all_confs, []

    # 1. Vertical Clustering (Lines)
    # Sort blocks by vertical center
    blocks.sort(key=lambda b: b["cy"])
    lines = []
    
    for b in blocks:
        added = False
        for line in lines:
            line_min_y = min(lb["min_y"] for lb in line)
            line_max_y = max(lb["max_y"] for lb in line)
            
            overlap = min(b["max_y"], line_max_y) - max(b["min_y"], line_min_y)
            if overlap > (b["h"] * 0.4):
                line.append(b)
                added = True
                break
        
        if not added:
            lines.append([b])

    # 2. Horizontal Formatting & Tab Separation
    final_output = []
    lines.sort(key=lambda line: sum(b["cy"] for b in line)/len(line))

    for line in lines:
        line.sort(key=lambda b: b["cx"])
        
        formatted_line = ""
        prev_max_x = None
        
        for b in line:
            if prev_max_x is None:
                formatted_line = b["text"]
            else:
                gap = b["min_x"] - prev_max_x
                char_w = b["w"] / max(1, len(b["text"]))
                num_tabs = max(1, int(gap / (char_w * 2)))
                num_tabs = min(8, num_tabs)
                
                formatted_line += ("\t" * num_tabs) + b["text"]
            
            prev_max_x = b["max_x"]
            
        final_output.append(formatted_line)

    # Return blocks in API format: text, bbox, confidence (drop internal keys for response)
    api_blocks = [{"text": b["text"], "bbox": b["bbox"], "confidence": b["confidence"]} for b in blocks]
    return final_output, all_confs, api_blocks


# ---------------------------------------------------------------------------
# FastAPI endpoint
# ---------------------------------------------------------------------------

@app.post("/convert")
def convert(
    file: UploadFile = File(...),
    force_ocr: bool = Form(False),
):
    temp_file_path = None
    try:
        start_time = time.time()
        process = psutil.Process()
        start_mem = process.memory_info().rss / 1024 / 1024

        original_filename = file.filename
        file_content = file.file.read()
        file_path = Path(original_filename)
        suffix = file_path.suffix.lower() if file_path.suffix else ".pdf"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_content)
            temp_file_path = tmp.name

        extraction_mode = "onnx_hybrid"
        all_confidences = []
        all_blocks = []
        table_regions: list[dict] = []

        if suffix in (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"):
            try:
                images = [Image.open(temp_file_path).convert("RGB")]
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to open image: {e}")
            markdown_text, all_confidences, all_blocks, table_regions = run_onnx_ocr(images)
            extraction_mode = "onnx_hybrid_image"
        elif suffix == ".pdf":
            try:
                if pdf_has_text(temp_file_path):
                    markdown_text, all_blocks, table_regions = extract_text_from_pdf(temp_file_path)
                    extraction_mode = "fitz_digital_pdf"
                    all_confidences = [1.0]
                else:
                    images = pdf_to_images(temp_file_path)
                    markdown_text, all_confidences, all_blocks, table_regions = run_onnx_ocr(images)
                    extraction_mode = "onnx_hybrid_pdf"
            except Exception as e:
                import traceback
                traceback.print_exc()
                raise HTTPException(status_code=500, detail=f"PDF extraction failed: {e}")
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

        end_mem = psutil.Process().memory_info().rss / 1024 / 1024

        # Calculate visual confidence (fallback to 1.0 if no blocks found or direct extraction)
        if all_confidences:
            avg_viz_conf = sum(all_confidences) / len(all_confidences)
        else:
            avg_viz_conf = 1.0

        return {
            "file_name": file_path.stem,
            "original_path": original_filename,
            "content": markdown_text,
            "blocks": all_blocks,
            "table_regions": table_regions,
            "avg_visual_confidence": avg_viz_conf,
            "images": [],
            "images_count": 0,
            "extraction_timestamp": time.time(),
            "processing_time": time.time() - start_time,
            "memory_usage_mb": max(0, end_mem - start_mem),
            "extraction_mode": extraction_mode,
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
