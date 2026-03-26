## Backend (FastAPI)

FastAPI service that orchestrates the invoice extraction pipeline. It owns the HTTP API, manages tasks, talks to RapidOCR and `llama.cpp`, and persists all results in MongoDB.

### Responsibilities

- Accept uploads and store raw files in GridFS.
- Enqueue and execute background extraction/structuring tasks.
- Call the OCR service and, when configured, the LLM service.
- Run rule‑based + regex structuring and optional LLM refinement.
- Compute confidence scores and expose data to the frontend.

### API surface (port `8001`)

Swagger docs are available at `http://localhost:8001/docs`.

- **Health**
  - `GET /health` – simple liveness probe.

- **Files**
  - `GET /files` – list uploaded files and metadata.
  - `POST /upload` – upload a new invoice (`.pdf`, `.jpg`, `.png`, etc.).
  - `GET /files/raw/{file_id}` – stream the original file from GridFS.
  - `DELETE /files/{file_id}` – delete file + associated tasks/results.

- **Tasks**
  - `POST /task/send` – create one or more extraction tasks for a file.
  - `GET /task/list` – list recent tasks with scores and timings.
  - `GET /task/state/{task_id}` – get status and scoring components.
  - `GET /task/data/{task_id}` – full task payload (structured JSON + OCR markdown + metadata).

- **Results**
  - `GET /extraction/{file_id}` – latest OCR result for a file.
  - `PUT /update/{file_id}` – save validated structured data and mark task/file as validated.

- **Maintenance**
  - `POST /system/cleanup` – wipe all files, tasks and results (development only).

For fuzzy extraction, OCR integration, and pipeline flow, see [`../FUZZY_SEARCH.md`](../FUZZY_SEARCH.md) and the root [`README.md`](../README.md).

### Environment

Key variables (see `docker-compose.yml` for defaults):

- `PORT` – HTTP port (default `8001`).
- `MONGODB_URI` – MongoDB connection string.
- `RAPIDOCR_SERVICE_URL` – URL of the RapidOCR service.
- `LLAMA_CPP_HOST` – base URL of the `llama.cpp` server (`http://llamacpp:8080/v1`).

### Local development

Run the backend without Docker:

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

The service expects a running MongoDB and, if you use LLM modes, a reachable `llama.cpp` server configured like the Docker stack.

