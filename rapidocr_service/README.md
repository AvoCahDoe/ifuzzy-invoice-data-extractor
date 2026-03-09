# 🖼️ RapidOCR Service

The RapidOCR service is a high-performance FastAPI wrapper around [RapidOCR](https://github.com/RapidAI/RapidOCR) and [RapidTable](https://github.com/RapidAI/RapidTable). It provides a robust OCR and Table Structure Recognition (TSR) pipeline optimized for invoice processing.

## 🚀 Key Features

- **Hybrid ONNX Pipeline**: Uses ONNX-accelerated models for layout detection, OCR, and table recognition.
- **Advanced Layout Analysis**: Employs `PP_DOC_LAYOUTV3` to identify tables, text regions, and other document elements.
- **Table Structure Recognition (TSR)**: Automatically reconstructs detected tables into high-quality Markdown pipe-tables.
- **Spatial OCR Grouping**: Uses region-masking and vertical clustering to preserve the horizontal alignment of text, ensuring accurate extraction of fields like addresses and totals.
- **Fast Digital PDF Path**: Includes high-speed extraction for digital PDFs via PyMuPDF (fitz), bypassing OCR for native text layers.

## 🛠️ API Endpoints

The service runs on port `8005` by default.

### `/convert` (POST)

Extracts text and tables from an uploaded document.

- **Parameters**:
  - `file`: The document to process (`.pdf`, `.jpg`, `.png`, `.webp`, `.tiff`).
  - `force_ocr` (optional): Set to `true` to skip digital PDF text extraction and force OCR.
- **Response**: Returns the extracted markdown content, average visual confidence, and metadata.

### `/health` (GET)

Returns the service status.

## 🏗️ Architecture

1. **Layout Detection**: Scans the image to locate borders and borderless tables.
2. **Table Processing**: Crops table regions and passes them to `RapidTable (SLANet_plus)` for reconstruction into HTML/Markdown.
3. **Region Masking**: Masks out the processed tables from the image.
4. **Full-Page OCR**: Runs `RapidOCR` on the remaining image to extract horizontal text blocks.
5. **Stitching**: Merges tables and text blocks into a single structured Markdown document.

## 🧪 Development

To run the service locally:

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8005 --reload
```
