# ⛏️ MinerU Service

The MinerU service is a powerful alternative OCR and PDF extraction engine. It is integrated into the microservice architecture as a fallback engine, optimized for extracting complex layouts and tables from academic and technical documents.

## 🚀 Key Features

- **Layout Analysis**: Uses YOLO-based models for precise region detection.
- **Table Extraction**: Recovers table structures and outputs them as both Markdown and structured JSON.
- **CPU Optimized**: Configured to run in "pipeline" mode for efficient inference on CPU-only environments.
- **Deep Extraction**: Capable of extracting text, tables, and images with high fidelity.

## 🛠️ API Endpoints

The service runs on port `8002` by default.

### `/convert` (POST)

Extracts markdown and structures from an uploaded document.

- **Parameters**:
  - `file`: The document to process (`.pdf`, `.jpg`, `.png`).
- **Response**: Returns the extracted markdown, images_count, and processing metadata.

### `/health` (GET)

Returns the service status.

## 🏗️ Technical Details

- **Docker Integration**: The service is containerized and managed via Docker Compose.
- **Model Cache**: Pre-trained model weights are cached in the `mineru_cache` volume to ensure fast cold-starts.
- **Fallback Role**: In the main backend orchestrator, MinerU is triggered as a high-quality alternative if the primary RapidOCR engine fails to parse a document correctly.

## 🧪 Development

To run the service locally:

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8002 --reload
```
