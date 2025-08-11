# Invoice Data Extraction (Angular + SSR + FastAPI)

This repository contains a full-stack invoice data extraction tool:
- Frontend: Angular Standalone with Server-Side Rendering (SSR)
- Backend: FastAPI service for invoice data extraction

Users can upload invoices in PDF or image format (PNG/JPEG). The frontend sends the file to the backend for parsing and extraction, then renders the extracted fields.

## Features

- Upload invoices in `.pdf`, `.png`, `.jpeg`, or `.jpg`
- Extraction of key fields: document type, currency, payment method, invoice number, invoice date, due date, total amount, tax amount
- Server-Side Rendering for faster first paint and SEO
- Simple, responsive UI

## Demo Video

[Watch the Demo](demo/demo.mp4)

## Prerequisites

- Node.js 18+ and npm
- Python 3.9+

## Getting Started

### 1) Clone
```bash
git clone https://github.com/medbakaaa/invoice-data-extractor.git
cd invoice-data-extractor
```

---

## Frontend (Angular + SSR)

### Install dependencies
```bash
cd frontend
npm install
```

### Development server
Browser mode:
```bash
npm run dev
```
SSR mode:
```bash
npm run dev:ssr
```

### Build
```bash
npm run build
# or
npm run build:ssr
```

### Run production SSR server
```bash
npm run serve:ssr
```

---

## Backend (FastAPI)

### Create and activate a virtual environment
```bash
cd backend
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate
```

### Install dependencies
```bash
pip install -r requirements.txt
```

### Run the server
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The backend will listen on:
```
http://localhost:8000
```

### Expected API endpoints (example)
Your implementation may differ; adjust to your code.
- `POST /extract` – multipart form-data with file field, returns JSON with extracted fields
- `GET /health` – returns a simple health check JSON

---

## Project Notes

- Ensure CORS is enabled in the backend for local development if the frontend runs on a different origin.
- For production, configure a reverse proxy (e.g., Nginx) to route `/api` to FastAPI and serve Angular SSR on `/`.

---



## Contributing

Issues and pull requests are welcome. For major changes, open an issue to discuss your proposal first.

## Author

Developed by Baka Mohamed

Contact: bakamoohamed@gmail.com
