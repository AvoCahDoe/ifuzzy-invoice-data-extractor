import base64
import logging
import re
import unicodedata
from pathlib import Path


logger = logging.getLogger(__name__)


def clean_markdown(text) -> str:
    """Remove excessive whitespace and control characters from OCR output."""
    if not isinstance(text, str) or not text:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C" or ch in "\n\t\r")
    return text.strip()


def detect_input_type(filename: str, extraction_mode: str | None) -> str:
    """Determine input type from extension + OCR extraction mode."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        if extraction_mode == "fitz_digital_pdf":
            return "pdf_digital"
        if extraction_mode in ("onnx_hybrid_pdf", None):
            return "pdf_scanned"
        return "other"
    if ext in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".gif"):
        return "image"
    return "other"


def save_ocr_markdown(output_dir: Path, filename: str, task_id: str, markdown: str) -> Path:
    """Persist cleaned OCR markdown under processed_output/ocr."""
    ocr_path = output_dir / "ocr" / f"{Path(filename).stem}_{task_id[:8]}.md"
    ocr_path.write_text(markdown, encoding="utf-8")
    return ocr_path


def save_extracted_images(output_dir: Path, filename: str, task_id: str, images: list) -> list[str]:
    """Persist extracted base64 images and return local file paths."""
    saved_img_paths = []
    for idx, img in enumerate(images or []):
        try:
            img_bytes = base64.b64decode(img["data"])
            ext = img.get("format", "png").lower()
            img_path = output_dir / "images" / f"{Path(filename).stem}_{task_id[:8]}_{idx}.{ext}"
            img_path.write_bytes(img_bytes)
            saved_img_paths.append(str(img_path))
        except Exception as e:
            logger.error("Failed to save image %s: %s", idx, e)
    return saved_img_paths
