## Invoice Data Extractor

Structured invoice extraction pipeline built on **FastAPI**, **RapidOCR**, **llama.cpp**, **MongoDB**, and an **Angular** frontend. It turns PDFs or images of invoices into validated JSON (header fields + line items), running fully on CPU with local models.

### Features

- **End‑to‑end pipeline**: upload → OCR/layout → structuring → validation UI.
- **Local‑only AI**: ONNX OCR and `llama.cpp` models (no external APIs).
- **Hybrid structuring**: regex + rule‑based extraction, with optional LLM refinement for missing fields.
- **Confidence scoring**: combines visual OCR quality, LLM semantic score, and math checks.
- **Modern UI**: Angular SSR app with side‑by‑side preview and interactive corrections.

---

## Architecture

High‑level data flow:

```text
Browser (Angular) ──► Backend (FastAPI) ──► RapidOCR Service
      │                    │                     │
      │                    └──────────────► llama.cpp (LLM)
      │                                      │
      └───────────────◄────────────── MongoDB (files, tasks, results)
```

### Services (from `docker-compose.yml`)

| Service              | Path                 | Port (host) | Role                                                |
|----------------------|----------------------|-------------|-----------------------------------------------------|
| `frontend`           | `frontend/`          | 80 → 4000   | Angular SSR UI                                      |
| `backend`            | `backend/`           | 8001        | FastAPI API, orchestration, structuring pipeline    |
| `rapidocr_service`   | `rapidocr_service/`  | 8005        | OCR + layout + table recognition to markdown        |
| `llamacpp`           | `models/` (mounted)  | 8003, 8031–8034 | `llama.cpp` server running LFM2 extraction models |
| `mongodb`            | (Docker image)       | 27018 → 27017 | Stores files, tasks, OCR and structured results  |

Data artifacts (on the backend container) are written under:

- `processed_output/ocr/` – cleaned OCR markdown
- `processed_output/structure/` – final structured JSON
- `processed_output/images/` – original uploads and OCR snapshots

For deeper technical details, see `TECHNICAL.md`.

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

3. **Structuring (Backend + rules + LLM)**
   - Phase 1: **hardcoded regex + rule‑based extractor**
     - `extract_fields_hardcoded` finds totals, dates, currency, etc.
     - `extract_fields_rulebased` (from `rule_extractor.py`) uses spatial anchors and table parsing to extract vendor, customer, payment method, and line items.
   - Phase 2: **optional LLM (`llama.cpp`)**
     - In `hybrid` / `regex_llm` modes, the backend builds a reduced context and calls a local LFM2 model through `llama.cpp`.
     - A JSON schema is passed so the LLM outputs strict JSON for only the missing fields.

4. **Scoring & persistence**
   - A final confidence score is computed:  
     `0.3 × visual + 0.3 × semantic + 0.4 × logic (math checks)`.
   - OCR and structured results, together with metadata, are stored in MongoDB (`extractions`, `structured_data`, `tasks`).

5. **Validation UI**
   - The status page (`/status`) lists tasks and scores.
   - The validate page (`/validate/:taskId/:fileId`) shows:
     - PDF/image preview,
     - OCR markdown,
     - structured JSON form and line items,
     - optional raw LLM JSON.
   - User corrections are saved via `PUT /update/{file_id}`, which also marks the task as validated.

More implementation details and LLM/structuring internals are documented in:

- `TECHNICAL.md`
- `STRUCTURING_STRATEGY.md`
- `STRUCTURING_REPORT.md`
- `STRUCTURING_IMPROVEMENTS.md`

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

> The first start may take longer while the `models/` container downloads LFM2 GGUF weights.

---

## Development

Each service can be developed independently; see the service‑specific READMEs for details.

- **Backend** – `backend/README.md`
  - FastAPI app, task pipeline, structuring logic, and scoring.
- **Frontend** – `frontend/README.md`
  - Angular SSR app with upload, status, and validation views.
- **RapidOCR service** – `rapidocr_service/README.md`
  - OCR + table reconstruction and markdown output.
- **Models / llama.cpp** – `models/README.md`
  - LFM2 model management and `llama.cpp` server configuration.
- **LLM service (legacy)** – `llm_service/README.md`
  - Optional Ollama‑based deployment (not used in the default Docker stack).

---

## Modes & Configuration (high level)

The structuring behavior is controlled by the **structuring mode** and **precision** selected on the upload page:

- **Modes**
  - `Regex + LLM` – regex for obvious fields, LLM for everything else.
  - `Fuzzy` – rules + fuzzy anchors only (no LLM).
  - `Hybrid` – rules first; LLM is called only for fields that are missing or low‑confidence.

- **Precision presets**
  - `4`, `5`, `8`, `16` – different quantizations of the 1.2B LFM2 model.
  - `350m` – smaller 350M model for targeted refinements.
  - `all` – run all models (for comparison / benchmarking).

These map to specific `llama.cpp` ports; see `TECHNICAL.md` and `models/README.md` for exact model/port mappings.

---

## License & Attribution

- OCR and table models are powered by **RapidOCR** and **RapidTable**.
- LLM extraction uses **Liquid AI’s LFM2** models served via **llama.cpp**.

Please refer to the upstream projects’ licenses when redistributing models or datasets.

