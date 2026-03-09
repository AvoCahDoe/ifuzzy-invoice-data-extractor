# ⚙️ Backend Orchestrator

The Backend Orchestrator is a FastAPI-powered service that manages the core workflow of the invoice data extraction pipeline. It handles file uploads, coordinates with OCR services, and manages interactions with the LLM for structured data extraction.

## 🚀 Key Features

- **Workflow Management**: Automates the pipeline from raw file upload to structured JSON output.
- **Microservice Coordination**: Routes requests to RapidOCR, Marker, or MinerU services with automatic fallback logic.
- **LLM Integration**: Templates prompts and interfaces with the `llama.cpp` server for high-accuracy extraction.
- **Data Persistence**: Uses MongoDB and GridFS to store file metadata, task statuses, and extraction results.
- **Scoring System**: Calculates confidence scores based on visual OCR quality, LLM semantic logprobs, and mathematical consistency checks.

## 🛠️ API Endpoints

The service runs on port `8001` by default. Swagger documentation is available at `/docs`.

### File Management

- `GET /files`: List all uploaded files and their metadata.
- `POST /upload`: Upload a new invoice file (`.pdf`, `.jpg`, `.png`).
- `GET /files/raw/{file_id}`: Retrieve the original file.
- `DELETE /files/{file_id}`: Delete a file and its associated data.

### Task Management

- `POST /task/send`: Queue a new extraction task.
- `GET /task/list`: List recent tasks and their statuses.
- `GET /task/state/{task_id}`: Get the status and metadata for a specific task.
- `GET /task/data/{task_id}`: Retrieve the extracted OCR text and structured JSON for a task.
- `POST /task/validate/{task_id}`: Mark a task's results as manually validated.

### Data & Results

- `GET /extraction/{file_id}`: Get the latest OCR extraction for a file.
- `PUT /update/{file_id}`: Manually update/correct the structured JSON data.

## 🏗️ Environment Variables

- `PORT`: Service port (default: `8001`).
- `MONGODB_URI`: MongoDB connection string.
- `RAPIDOCR_SERVICE_URL`: URL for the RapidOCR service.
- `LLAMA_CPP_HOST`: URL for the `llama.cpp` server.
- `OUTPUT_DIR`: Directory for storing processed artifacts (OCR markdown, images, etc.).

## 🧪 Development

To run the backend locally (outside of Docker):

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```
