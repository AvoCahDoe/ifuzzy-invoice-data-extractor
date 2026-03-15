## RapidOCR Service

FastAPI microservice that turns invoices (PDFs or images) into markdown + layout blocks using RapidOCR and RapidTable.

### What it does

- Detects document layout (tables, text regions).
- Reconstructs tables as markdown pipe tables.
- Runs OCR on the remaining regions with spatial grouping to preserve line structure.
- Returns:
  - `content` – combined markdown (tables + text),
  - `blocks` – per‑block text, bounding boxes, confidence, page index,
  - metadata such as `avg_visual_confidence`, `extraction_mode`, and timing.

The backend uses this output as the single source of truth for both rule‑based and LLM structuring.

### API (port `8005`)

- `GET /health` – basic health check.

- `POST /convert`
  - **Form fields**
    - `file` – uploaded document (`.pdf`, `.jpg`, `.png`, `.webp`, `.tiff`, etc.).
    - `force_ocr` (optional) – when `true`, forces OCR even for digital PDFs.
  - **Response**
    - JSON with `content`, `blocks`, `avg_visual_confidence`, `processing_time`, `extraction_mode`, and optionally inline images.

### Local development

```bash
cd rapidocr_service
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8005 --reload
```

In Docker, the backend points to this service via the `RAPIDOCR_SERVICE_URL` environment variable.

