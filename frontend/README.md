## Frontend (Angular)

Angular SSR application that provides the UI for uploading invoices, tracking tasks, and validating structured results.

### Pages

- **Upload** (`/upload`)
  - Drag‑and‑drop upload (PDF / images).
  - Choose structuring mode: `Regex + LLM`, `Fuzzy`, or `Hybrid`.
  - Select LLM precision (4/5/8/16/350m/all) when relevant.
  - Starts a task via the backend `/task/send` endpoint.

- **Status** (`/status`)
  - Lists recent tasks with:
    - input type, engine, structuring mode,
    - item count,
    - OCR / structuring / total times,
    - final confidence score.
  - Provides navigation to the validation page.
  - Includes a “Clean database” button (calls `/system/cleanup`).

- **Validate** (`/validate/:taskId/:fileId`)
  - Loads task data from `/task/data/{taskId}`.
  - Shows:
    - PDF/image preview (via `/files/raw/{file_id}`); opening the image modal uses the **`bbox-overlay-viewer`** component when overlays are available: OCR lines (confidence‑colored), **table** regions, and **anchor** boxes from **`metadata.anchor_indicators`**.
    - Optional **Fuzzy anchors** row (✓/—) when **`metadata`** includes anchor indicators.
    - OCR markdown,
    - structured fields and line items in editable form,
    - optional raw LLM JSON for inspection.
  - Runs client‑side validation (dates, totals, line items).
  - Sends corrections to `PUT /update/{file_id}`.

### Components

- **`bbox-overlay-viewer`** (`src/app/components/bbox-overlay-viewer/`) – isolated overlay for the image modal: zoom, toggle boxes, tooltips; inputs include `imageUrl`, `blocks`, `tableRegions`, `anchorIndicators`.

### Tech stack

- Angular 17+ with standalone components and SSR.
- RxJS for async flows (`firstValueFrom`, streaming API calls).
- Plain CSS for layouts (`upload`, `status`, `validate` pages).

### Local development

```bash
cd frontend
npm install
npm start   # or: npm run dev:ssr, depending on your scripts
```

The dev server typically runs on `http://localhost:4200/` and proxies API calls to the backend (see environment configuration).

### Configuration

The API base URL is derived at runtime:

- In Docker, via `API_BASE_URL` env var (e.g. `http://backend:8001`).
- In dev, by default it targets `http://localhost:8001`.

