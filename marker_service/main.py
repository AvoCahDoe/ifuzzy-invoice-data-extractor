import os
import time
import asyncio
import tempfile
import psutil
import base64
from pathlib import Path
from typing import Optional, List, Any, Dict
from io import BytesIO

from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from pydantic import BaseModel

app = FastAPI(title="Marker Service")

# Global converter
converter = None

@app.on_event("startup")
async def startup_event():
    global converter
    print("Loading marker models (this may take a while on first run)...")
    try:
        from marker.converters.pdf import PdfConverter
        from marker.config.parser import ConfigParser
        from marker.models import create_model_dict

        # Load models (downloads on first run)
        models = await asyncio.to_thread(create_model_dict)
        print(f"Models loaded: {list(models.keys())}")

        config_parser = ConfigParser({"output_format": "markdown"})

        converter = PdfConverter(
            artifact_dict=models,
            processor_list=config_parser.get_processors(),
            renderer=config_parser.get_renderer(),
            config=config_parser.generate_config_dict(),
        )
        print("Marker converter ready.")
    except Exception as e:
        print(f"Failed to load marker models: {e}")
        import traceback
        traceback.print_exc()
        print("Marker service will start but conversion will fail.")


def image_to_base64(img):
    if hasattr(img, 'save'):
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        img_str = base64.b64encode(buffer.getvalue()).decode()
        return {
            "format": "PNG",
            "data": img_str,
            "size": getattr(img, 'size', None),
        }
    elif isinstance(img, str):
        return {"path": img}
    else:
        return {"error": "Unsupported image format"}


def extract_markdown_content(rendered_obj):
    """Extract markdown text from various rendered object formats."""
    if hasattr(rendered_obj, 'text'):
        return rendered_obj.text
    elif hasattr(rendered_obj, 'markdown'):
        return rendered_obj.markdown
    else:
        rendered_str = str(rendered_obj)
        if rendered_str.startswith('markdown="') and '" images=' in rendered_str:
            start_idx = len('markdown="')
            end_idx = rendered_str.find('" images=')
            if end_idx != -1:
                markdown_content = rendered_str[start_idx:end_idx]
                markdown_content = (
                    markdown_content
                    .replace('\\n', '\n')
                    .replace('\\"', '"')
                    .replace('\\\\', '\\')
                )
                return markdown_content
        return rendered_str


@app.post("/convert")
async def convert(
    file: UploadFile = File(...),
    force_ocr: bool = Form(False),
    use_llm: bool = Form(False)
):
    global converter
    if converter is None:
        raise HTTPException(status_code=503, detail="Marker models not loaded yet. Please wait.")

    try:
        start_time = time.time()
        process = psutil.Process()
        start_mem = process.memory_info().rss / 1024 / 1024

        original_filename = file.filename
        file_content = await file.read()

        file_path = Path(original_filename)
        suffix = file_path.suffix.lower() if file_path.suffix else '.pdf'

        # If image, convert to PDF first
        if suffix in ['.jpg', '.jpeg', '.png', '.bmp', '.tiff']:
            from PIL import Image
            img = Image.open(BytesIO(file_content))
            if img.mode == 'RGBA':
                img = img.convert('RGB')
            pdf_buffer = BytesIO()
            img.save(pdf_buffer, format='PDF', resolution=100.0)
            file_content = pdf_buffer.getvalue()
            suffix = '.pdf'

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(file_content)
            temp_file_path = temp_file.name

        try:
            # Run conversion in thread to avoid blocking
            rendered = await asyncio.to_thread(converter, temp_file_path)

            # Process output
            content = extract_markdown_content(rendered)

            serialized_images = []
            if hasattr(rendered, 'images') and rendered.images:
                images_iter = rendered.images.items() if isinstance(rendered.images, dict) else enumerate(rendered.images)
                for key, img_obj in images_iter:
                    try:
                        serialized_img = image_to_base64(img_obj)
                        serialized_img["name"] = str(key)
                        serialized_img["index"] = len(serialized_images)
                        serialized_images.append(serialized_img)
                    except Exception as e:
                        print(f"Could not serialize image {key}: {e}")

            result_data = {
                "file_name": file_path.stem,
                "original_path": original_filename,
                "content": content,
                "images": serialized_images,
                "images_count": len(serialized_images),
                "extraction_timestamp": time.time(),
                "processing_time": time.time() - start_time,
                "memory_usage_mb": max(0, (psutil.Process().memory_info().rss / 1024 / 1024) - start_mem),
                "extraction_mode": "marker_ocr",
            }
            return result_data

        finally:
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error processing file: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health_check():
    return {"status": "ok", "models_loaded": converter is not None}
