import os
import time
import json
import shutil
import tempfile
import psutil
import base64
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from PIL import Image

app = FastAPI(title="MinerU Extraction Service")


def run_mineru_extraction(pdf_path: str, output_dir: str) -> dict:
    """Run MinerU extraction using the Python API (magic_pdf)."""
    from magic_pdf.pipe.UNIPipe import UNIPipe
    from magic_pdf.rw.DiskReaderWriter import DiskReaderWriter

    # Read PDF bytes
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    # Setup image writer
    image_output_dir = os.path.join(output_dir, "images")
    os.makedirs(image_output_dir, exist_ok=True)
    image_writer = DiskReaderWriter(image_output_dir)

    # jso_useful_key contains config; empty dict uses defaults
    jso_useful_key = {"_pdf_type": "", "model_list": []}

    # Create pipeline
    pipe = UNIPipe(pdf_bytes, jso_useful_key, image_writer, is_debug=False)

    # Run classification (determines if OCR or text-based)
    pipe.pipe_classify()

    # Run parsing
    pipe.pipe_parse()

    # Generate markdown
    md_content = pipe.pipe_mk_markdown(image_output_dir, drop_mode="none")

    # Collect images
    images = []
    if os.path.exists(image_output_dir):
        for img_file in Path(image_output_dir).glob("*"):
            if img_file.is_file():
                images.append(str(img_file))

    return {
        "markdown": md_content,
        "images": images,
        "output_path": output_dir,
    }


@app.post("/convert")
async def convert(
    file: UploadFile = File(...),
    force_ocr: bool = Form(False),
    use_llm: bool = Form(False)
):
    temp_file_path = None
    output_dir = None
    try:
        start_time = time.time()
        process = psutil.Process()
        start_mem = process.memory_info().rss / 1024 / 1024

        original_filename = file.filename
        file_content = await file.read()

        file_path = Path(original_filename)
        suffix = file_path.suffix.lower() if file_path.suffix else '.pdf'

        # Create temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(file_content)
            temp_file_path = temp_file.name

        # If it's an image, convert to PDF because MinerU expects PDF
        if suffix in ['.jpg', '.jpeg', '.png', '.bmp', '.tiff']:
            try:
                image = Image.open(temp_file_path)
                if image.mode != 'RGB':
                    image = image.convert('RGB')

                temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                temp_pdf_path = temp_pdf.name
                temp_pdf.close()

                image.save(temp_pdf_path, "PDF", resolution=100.0)

                os.unlink(temp_file_path)
                temp_file_path = temp_pdf_path
            except Exception as e:
                print(f"Image conversion failed: {e}")
                raise HTTPException(status_code=400, detail=f"Failed to convert image to PDF: {e}")

        # Create output directory
        output_dir = tempfile.mkdtemp(prefix="mineru_out_")

        try:
            result = run_mineru_extraction(temp_file_path, output_dir)

            response_images = []
            saved_images = result.get("images", [])
            for img_path in saved_images:
                try:
                    p = Path(img_path)
                    with open(p, "rb") as img_file:
                        encoded = base64.b64encode(img_file.read()).decode("utf-8")
                        response_images.append({
                            "name": p.name,
                            "data": encoded,
                            "format": p.suffix.replace('.', '').upper()
                        })
                except Exception as e:
                    print(f"Failed to load image {img_path}: {e}")

            return {
                "file_name": file_path.stem,
                "original_path": original_filename,
                "content": result.get("markdown", ""),
                "images": response_images,
                "images_count": len(response_images),
                "extraction_timestamp": time.time(),
                "processing_time": time.time() - start_time,
                "memory_usage_mb": max(0, (psutil.Process().memory_info().rss / 1024 / 1024) - start_mem),
                "extraction_mode": "mineru_pipeline",
            }

        except Exception as e:
            print(f"MinerU Conversion failed: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error processing file: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
        if output_dir and os.path.exists(output_dir):
            shutil.rmtree(output_dir, ignore_errors=True)


@app.get("/health")
def health_check():
    return {"status": "ok"}
