# Invoice Data Extractor  

This project is a full-stack web application for extracting structured data from invoices (PDF, PNG, JPEG) using **FastAPI**, **Angular (SSR)**, **MongoDB GridFS**, and **Ollama (LLM)**.  

---

## Features
- Upload invoices (PDF/PNG/JPEG).  
- OCR + text extraction using [Marker].  
- LLM structuring via **Ollama** (e.g. Mistral model).  
- Store extractions in MongoDB (GridFS).  
- Validate and update structured JSON through the web UI.  
- Task manager for background processing.  

---

## Requirements
- [Docker](https://docs.docker.com/get-docker/) + [Docker Compose](https://docs.docker.com/compose/)  
- At least **8GB RAM** recommended  
- No GPU required (runs in CPU mode by default)  

---

## Setup & Run  

### 1. Clone the repo
```bash
git clone https://github.com/medbakaaa/invoice-data-extractor.git
cd invoice-data-extractor
```

### 2. Build & start services
bash
Copier le code
docker compose up -d --build
This will start:

backend → FastAPI app on http://localhost:8000

frontend → Angular SSR app on http://localhost:4000

mongo → MongoDB with GridFS

ollama → Ollama LLM server on http://localhost:11434

### 3. Pull the LLM model in Ollama
Inside the Ollama container, run:

```bash
docker compose exec ollama ollama pull mistral
```

### 4. Access the app
Open http://localhost:4000 → Upload page

Backend API docs: http://localhost:8000/docs

## Development
Rebuild only the backend

```bash
docker compose up -d --no-deps --build backend
```

Rebuild only the frontend

```bash
docker compose up -d --no-deps --build frontend
```