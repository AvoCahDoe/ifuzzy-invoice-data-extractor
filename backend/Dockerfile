FROM python:3.10-slim

LABEL org.opencontainers.image.source=https://github.com/AvoCahDoe/invoice-data-extractor
LABEL org.opencontainers.image.description="Invoice Data Extractor Backend Service"
WORKDIR /app

COPY *.py .
RUN pip install fastapi uvicorn python-multipart httpx pymongo rapidfuzz scikit-learn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
