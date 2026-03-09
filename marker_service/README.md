# 🔖 Marker Service

The Marker service is an alternative, high-quality OCR engine based on the [Marker](https://github.com/VikParuchuri/marker) library. It is designed to convert PDFs and images into structured Markdown with a focus on layouts and multi-column document structures.

## 🚀 Key Features

- **Document Parsing**: Specialized in converting complex layouts into clean Markdown.
- **Image Conversion**: Automatically handles images by converting them to PDF before processing.
- **Deep Learning OCR**: Uses deep learning models for layout detection and OCR.
- **Base64 Integration**: Returns extracted images encoded in Base64 for easy consumption by the frontend.

## 🛠️ API Endpoints

The service runs on port `8004` by default.

### `/convert` (POST)

Extracts markdown and images from a document.

- **Parameters**:
  - `file`: The document to process (`.pdf`, `.jpg`, `.png`, `.bmp`, `.tiff`).
  - `force_ocr` (optional): Forces OCR processing.
  - `use_llm` (optional): Enables LLM-based layout refinement if available.
- **Response**: Returns the extracted markdown, extracted images (Base64), and processing metadata.

### `/health` (GET)

Returns the service status and whether the Marker models are loaded.

## 🏗️ Technical Details

1. **Model Loading**: Models are loaded on startup and cached to the `/root/.cache` directory.
2. **Conversion Pipeline**: Converts images to PDF, runs the `PdfConverter` from the `marker` library, and extracts serialized results.
3. **Fallback Strategy**: In the main pipeline, this service serves as a high-quality fallback if the primary RapidOCR engine fails.

## 🧪 Development

To run the service locally:

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8004 --reload
```
