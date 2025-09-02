# Invoice Extraction Benchmark (No-DB)

This benchmark measures:
- **Extraction time**: either fast PDF text-layer extraction or OCR via Marker.  
- **LLM structuring**: sends extracted text to **Ollama** (model: `mistral`) to produce structured JSON fields.

It produces both **per-file metrics** and a **summary report**.  
No database is involved.

---

##  Project Layout (backend)
```bash
backend/
├── benchmark.py # Benchmark script
├── Dockerfile.bench  
├── requirements.bench.txt 
├── docker-compose.bench.yml 
├── test_data/ # Place test PDFs/PNGs/JPGs here
└── out/ # Results (CSV + JSON)
```


---

## How to Run

### 1. Prepare
- Place a few sample invoices in `backend/test_data/`.

### 2. Ensure Ollama is available

**Run Ollama in Compose**

- Start the Ollama service:

```bash
docker compose -f docker-compose.bench.yml up -d ollama
```
- Pull the model inside the container:

```bash
docker exec -it ollama ollama pull mistral
```
### 3. Run the benchmark

From the `backend/` folder:

```bash
docker compose -f docker-compose.bench.yml up --build bench_nodb
```

### 4. Results
Outputs are written to `backend/out/`:

results.csv → streamed row by row as each file finishes:

```bash
filename,ok_extract,mode,extraction_ms,ok_llm,structuring_ms,error
facture1.pdf,True,pdf_text_layer,145.7,True,208.3,
facture2.png,True,marker_ocr,272411.7,True,218.4,
```

results.json → written at the end, containing a full run summary:

```json
{
  "total_files": 3,
  "ok_extract_files": 3,
  "ok_llm_files": 3,
  "error_files": 0,
  "model_load_ms": 2450.33,
  "total_elapsed_ms": 305121.91,
  "avg_extract_ms": 61243.7,
  "median_extract_ms": 210.5,
  "p95_extract_ms": 272411.7,
  "avg_struct_ms": 205.4,
  "median_struct_ms": 206.1,
  "p95_struct_ms": 220.9,
  "runs": [...]
}
```