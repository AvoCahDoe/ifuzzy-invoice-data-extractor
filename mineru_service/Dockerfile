FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install MinerU (magic-pdf)
# Using [full] to get all dependencies
RUN pip install --no-cache-dir magic-pdf[full]
RUN pip install --no-cache-dir fastapi uvicorn python-multipart Pillow psutil

# Copy config
COPY magic-pdf.json /root/magic-pdf.json

COPY mineru_extractor.py .
COPY main.py .

# Create a default configuration if needed, or rely on defaults
# wget mineru config if necessary

EXPOSE 8002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
