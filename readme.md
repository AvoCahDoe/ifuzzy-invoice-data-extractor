# 🧾 Invoice Data Extractor

A full-stack, AI-powered invoice data extraction pipeline. This project takes images of invoices, performs highly accurate Optical Character Recognition (OCR) and Table Structure Recognition (TSR), and uses a local Large Language Model (LLM) to extract structured JSON data (like total amount, invoice number, line items, etc.).

All models are local and optimized for CPU inference via ONNX and `llama.cpp`.

## 🏗️ Architecture

The system consists of several microservices orchestrated via Docker Compose:

- **Frontend (`frontend/`)**: An Angular SSR application that provides a clean UI for uploading invoices and visualizing the extracted JSON side-by-side with the original document.
- **Backend Orchestrator (`backend/`)**: A FastAPI service that manages the workflow: saving uploads, sending images to the OCR service, and formatting the prompt for the LLM.
- **OCR Service (`rapidocr_service/`)**: A dedicated FastAPI wrapper around [RapidOCR](https://github.com/RapidAI/RapidOCR) and [RapidTable](https://github.com/RapidAI/RapidTable). It uses `PP_DOC_LAYOUTV3` to detect tables, processes them into Markdown pipe-tables, masks them, and runs full-page OCR on the remaining text to preserve horizontal line grouping.
- **LLM Engine (`llamacpp`)**: Runs the `LFM2-1.2B-Extract-Q8_0.gguf` model using the official `llama.cpp` server, configured with an 8192 context window to easily handle long markdown invoice strings.
- **Database (`mongodb`)**: Stores file metadata and extraction task statuses.

_(Note: Legacy `marker_service` and `mineru_service` containers are also present but standard usage relies on the optimized ONNX RapidOCR pipeline)._

## 🚀 Getting Started

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/)

### Installation & Execution

1. **Clone the repository** and navigate to the project directory:

   ```bash
   cd old-invoice-data-extractor
   ```

2. **Download the LLM Model:**
   The `llamacpp` container expects the `LFM2-1.2B-Extract-Q8_0.gguf` model. When you first start the container, the `start_llama.sh` script in the `models/` directory will automatically download the 1.2B model from HuggingFace to the mounted `/models` volume if it doesn't already exist.

3. **Start the Stack:**
   Build and spin up the entire microservice architecture:

   ```bash
   docker-compose up --build -d
   ```

4. **Access the Application:**
   Once everything is running, open your browser and navigate to:
   👉 **http://localhost:4001**

## 🧩 How It Works (The Pipeline)

1. **Upload:** User uploads an invoice image `(.jpg/.png)` via the Angular frontend.
2. **Layout Detection:** The backend forwards the image to `rapidocr_service`. The `PP_DOC_LAYOUTV3` layout model scans the image to explicitly locate borderless and bordered tables.
3. **Table Structure Recognition (TSR):** Detected table regions are cropped and sent to `RapidTable (SLANet_plus)` which reconstructs the rows/columns and generates HTML, easily converted to Markdown tables.
4. **Full-Page OCR:** To preserve the horizontal line structure of scattered text (addresses, totals), the parsed tables are masked out (painted white) from the image. `RapidOCR (ONNX)` then grabs the remaining text perfectly. The Markdown tables and raw text are stitched together.
5. **LLM Extraction:** The resulting Markdown is injected into a strict prompt template and sent to the local `llama.cpp` server. The `LFM2 1.2B` model parses the markdown and outputs a strictly validated JSON object containing the invoice fields.

## 🛠️ API Endpoints

- **Frontend:** `http://localhost:4001`
- **Backend API:** `http://localhost:8001` (Swagger UI: `http://localhost:8001/docs`)
- **RapidOCR API:** `http://localhost:8005`
- **Llama.cpp API:** `http://localhost:8003/v1/chat/completions`

## 📂 Project Structure

```text
├── backend/                  # FastAPI orchestrator and LLM prompt logic
├── frontend/                 # Angular 20 UI
├── models/                   # Llama.cpp start scripts and downloaded GGUF weights
├── rapidocr_service/         # ONNX RapidOCR & RapidTable FastAPI wrapper
├── mineru_service/           # Alternative OCR engine (Legacy)
├── marker_service/           # Alternative OCR engine (Legacy)
├── processed_output/         # Volumes for OCR Markdown and final JSON results
├── dataset/                  # Sample invoices for testing
└── docker-compose.yml        # Multi-container orchestration
```
