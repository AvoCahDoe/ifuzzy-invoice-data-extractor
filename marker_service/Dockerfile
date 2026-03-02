FROM python:3.10-slim

LABEL org.opencontainers.image.source=https://github.com/AvoCahDoe/invoice-data-extractor
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# 1. Install PyTorch CPU-only
RUN pip install "torch<2.9.0" torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# 2. Install server deps
RUN pip install "fastapi" "uvicorn" "python-multipart" "psutil"

# 3. Install marker-pdf and surya-ocr
RUN pip install "marker-pdf" "surya-ocr" --extra-index-url https://download.pytorch.org/whl/cpu

# 4. Force reinstall CPU torch (surya pulls torch 2.10 with CUDA deps which breaks transformers)
RUN pip install "torch<2.9.0" torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu --force-reinstall

# 5. Fix typing_extensions (torch force-reinstall downgrades it, breaking pydantic_core)
RUN pip install "typing_extensions>=4.12.0"

COPY . .

ENV PYTHONPATH=/app

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8004"]
