## Invoice Data Extractor

Structured invoice extraction pipeline built on **FastAPI**, **RapidOCR**, **MongoDB**, and an **Angular** frontend. It turns PDFs or images of invoices into validated JSON (header fields + line items), running fully on CPU.

### Features

- **End‑to‑end pipeline**: upload → OCR/layout → structuring → validation UI.
- **Local‑only**: ONNX OCR models (no external APIs).
- **Rule-based structuring**: regex + rule‑based fuzzy extraction.
- **Confidence scoring**: combines visual OCR quality and math checks.
- **Modern UI**: Angular SSR app with side‑by‑side preview and interactive corrections.

---

## Architecture

High‑level data flow:

```text
Browser (Angular) ──► Backend (FastAPI) ──► RapidOCR Service
      │                    │
      └───────────────◄────┴───────── MongoDB (files, tasks, results)
```

### Services (from `docker-compose.yml`)

| Service              | Path                 | Port (host) | Role                                                |
|----------------------|----------------------|-------------|-----------------------------------------------------|
| `frontend`           | `frontend/`          | 80 → 4000   | Angular SSR UI                                      |
| `backend`            | `backend/`           | 8001        | FastAPI API, orchestration, structuring pipeline    |
| `rapidocr_service`   | `rapidocr_service/`  | 8005        | OCR + layout + table recognition to markdown        |
| `mongodb`            | (Docker image)       | 27018 → 27017 | Stores files, tasks, OCR and structured results  |

Data artifacts (on the backend container) are written under:

- `processed_output/ocr/` – cleaned OCR markdown
- `processed_output/structure/` – final structured JSON
- `processed_output/images/` – original uploads and OCR snapshots

For fuzzy matching, OCR outputs, and pipeline flow, see [`FUZZY_SEARCH.md`](FUZZY_SEARCH.md).

---

## How It Works

1. **Upload**
   - User uploads a PDF or image via the frontend (`/upload`).
   - Backend saves it to GridFS (`/upload` endpoint) and creates a task via `/task/send`.

2. **Extraction (RapidOCR)**
   - Backend reads the file from GridFS and calls `rapidocr_service:/convert`.
   - The OCR service:
     - Detects layout and tables.
     - Reconstructs tables as markdown pipe tables.
     - Runs OCR on the remaining regions.
   - It returns markdown `content`, `blocks` with bounding boxes, and visual confidence.

3. **Structuring (Backend + rules)**
   - **Hardcoded regex + rule‑based extractor**
     - `extract_fields_hardcoded` finds totals, dates, currency, etc.
     - `extract_fields_rulebased` (from `rule_extractor.py`) uses spatial anchors and table parsing to extract vendor, customer, payment method, and line items.

4. **Scoring & persistence**
   - A final confidence score is computed:  
     `0.4 × visual + 0.6 × logic (math checks)`.
   - OCR and structured results, together with metadata, are stored in MongoDB (`extractions`, `structured_data`, `tasks`).

5. **Validation UI**
   - The status page (`/status`) lists tasks and scores.
   - The validate page (`/validate/:taskId/:fileId`) shows:
     - PDF/image preview,
     - OCR markdown,
     - structured JSON form and line items,
     - optional raw LLM JSON.
   - User corrections are saved via `PUT /update/{file_id}`, which also marks the task as validated.

Further documentation:

- [`FUZZY_SEARCH.md`](FUZZY_SEARCH.md) — fuzzy rules, RapidOCR outputs, extraction flow
- Service READMEs under `backend/`, `frontend/`, `rapidocr_service/`

---

## Running the Stack

### Prerequisites

- Docker and Docker Compose installed on your machine.

### Quick start

```bash
cd invoice-data-extractor

# Build all images
docker compose build

# Start services in the background
docker compose up -d
```

Once running:

- Frontend UI: `http://localhost`
- Backend health: `http://localhost:8001/health`
- Backend API docs: `http://localhost:8001/docs`
- RapidOCR service: `http://localhost:8005/health`

MongoDB is exposed on `localhost:27018` for debugging (mapped to `27017` in the container).

---

## Development

Each service can be developed independently; see the service‑specific READMEs for details.

- **Backend** – `backend/README.md`
  - FastAPI app, task pipeline, structuring logic, and scoring.
- **Frontend** – `frontend/README.md`
  - Angular SSR app with upload, status, and validation views.
- **RapidOCR service** – `rapidocr_service/README.md`
  - OCR + table reconstruction and markdown output.

---

## License & Attribution

- OCR and table models are powered by **RapidOCR** and **RapidTable**.

Please refer to the upstream projects’ licenses when redistributing models or datasets.

