FROM python:3.10-slim

LABEL org.opencontainers.image.source=https://github.com/AvoCahDoe/invoice-data-extractor
LABEL org.opencontainers.image.description="Invoice Data Extractor RapidOCR Microservice"
WORKDIR /app

# System deps: libGL for image processing
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Pure ONNX-runtime pipeline — no PaddlePaddle C++ backend
# rapid_layout  → layout detection (finds table bounding boxes)
# rapid_table   → table structure recognition → HTML
# rapidocr_onnxruntime → plain text OCR for non-table regions
RUN pip install --no-cache-dir \
    rapidocr_onnxruntime \
    rapid_layout \
    rapid_table \
    onnxruntime \
    pymupdf \
    Pillow \
    numpy \
    pandas \
    tabulate \
    fastapi \
    uvicorn \
    python-multipart \
    psutil

COPY main.py .

EXPOSE 8005
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005"]
