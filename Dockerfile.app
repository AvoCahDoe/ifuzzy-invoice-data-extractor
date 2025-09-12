FROM python:3.11-slim

ENV PIP_DEFAULT_TIMEOUT=120 PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1

# native deps used by your stack (tesseract/poppler/Pillow/etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential poppler-utils tesseract-ocr libgl1 git curl\
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) install base Python deps (from requirements.txt) — NO VERSIONS
COPY backend/requirements.txt /app/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-compile --prefer-binary -r requirements.txt

# 2) install CPU-only Torch (NO VERSIONS) from official CPU index
#    (keeps you off the CUDA wheels so you don't see nvidia-cuda* packages)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-compile --prefer-binary \
      --index-url https://download.pytorch.org/whl/cpu \
      torch torchvision

# If you really don't need torchvision, you can drop it to make the image smaller.

# 3) copy your app code last (so edits don't blow the heavy layers)
COPY backend /app

ENV GRPC_VERBOSITY=ERROR GLOG_minloglevel=2 PYTORCH_ENABLE_MPS_FALLBACK=1 \
    MONGODB_URI=mongodb://mongo:27017/fileuploads \
    OLLAMA_HOST=http://ollama:11434 OLLAMA_KEEP_ALIVE=5m INVOICE_MODEL_NAME=mistral

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host=0.0.0.0", "--port=8000", "--log-level=info"]
