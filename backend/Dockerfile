FROM python:3.10-slim

LABEL org.opencontainers.image.source=https://github.com/AvoCahDoe/invoice-data-extractor
WORKDIR /app

COPY main.py .

RUN pip install fastapi uvicorn python-multipart httpx pymongo

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
