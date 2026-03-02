from fastapi import FastAPI, UploadFile, File, HTTPException, Body, Form
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import uuid
import httpx
import os
import time
import json
import re
import logging
import asyncio
from pathlib import Path
from pymongo import MongoClient
import gridfs
import unicodedata
import base64

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backend")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Service URLs from Docker Compose
MINERU_SERVICE_URL   = os.getenv("MINERU_SERVICE_URL",   "http://mineru_service:8002")
MARKER_SERVICE_URL   = os.getenv("MARKER_SERVICE_URL",   "http://marker_service:8004")
RAPIDOCR_SERVICE_URL = os.getenv("RAPIDOCR_SERVICE_URL", "http://rapidocr_service:8005")

LLAMA_CPP_HOST = os.getenv("LLAMA_CPP_HOST", "http://llamacpp:8003/v1")
LLAMA_CPP_API_URL = (
    f"{LLAMA_CPP_HOST}/chat/completions"
    if LLAMA_CPP_HOST.endswith("/v1")
    else f"{LLAMA_CPP_HOST}/v1/chat/completions"
)

ENGINE_URLS = {
    "mineru":   MINERU_SERVICE_URL,
    "marker":   MARKER_SERVICE_URL,
    "rapidocr": RAPIDOCR_SERVICE_URL,
}

# Database
MONGO_URI = os.getenv("MONGODB_URI", "mongodb://mongodb:27017")
mongo_client = MongoClient(MONGO_URI)
db = mongo_client.invoice_db
files_col = db.files
tasks_col = db.tasks

# Output directory for processed files
OUTPUT_DIR = Path("/app/processed_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
(OUTPUT_DIR / "ocr").mkdir(exist_ok=True)
(OUTPUT_DIR / "structure").mkdir(exist_ok=True)
(OUTPUT_DIR / "images").mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Health & file management
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/files")
async def list_files():
    files = []
    for doc in files_col.find().sort("upload_date", -1):
        files.append({
            "id": str(doc["_id"]),
            "filename": doc["filename"],
            "upload_date": doc["upload_date"],
            "processed": doc.get("processed", False),
        })
    return {"files": files}

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    file_id = str(uuid.uuid4())
    content = await file.read()

    fs = gridfs.GridFS(db)
    fs.put(content, filename=file.filename, _id=file_id)

    files_col.insert_one({
        "_id": file_id,
        "filename": file.filename,
        "upload_date": datetime.utcnow().isoformat(),
        "processed": False,
    })
    return {"file_id": file_id, "filename": file.filename}

@app.delete("/files/{file_id}")
async def delete_file(file_id: str):
    result = files_col.delete_one({"_id": file_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="File not found")
    fs = gridfs.GridFS(db)
    if fs.exists(_id=file_id):
        fs.delete(file_id)
        
    tasks_col.delete_many({"file_id": file_id})
    db.ocr_data.delete_many({"file_id": file_id})
    db.structured_data.delete_many({"file_id": file_id})
    db.extractions.delete_many({"file_id": file_id}) # For good measure
    
    return {"status": "deleted", "file_id": file_id}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_markdown(text: str) -> str:
    """Remove excessive whitespace and control characters from OCR output."""
    if not text:
        return ""
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C" or ch in "\n\t\r")
    return text.strip()

def extract_json_from_llm(content: str) -> dict:
    """Robustly extract JSON from LLM output (handles markdown fences, stray text, truncation)."""
    # 1. Direct parse
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # 2. Markdown code fence
    match = re.search(r"```(?:json)?\s*(\{[^`]*\})\s*```", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    # 3. Largest {...} block between first { and last }
    start = content.find('{')
    end = content.rfind('}')
    if start != -1 and end != -1:
        try:
            return json.loads(content[start:end + 1])
        except Exception:
            pass
    # 4. Truncation recovery — LLM hit token limit mid-JSON.
    #    Find the start, then surgically close open structures.
    if start != -1:
        fragment = content[start:]
        # Close open string if needed
        if fragment.count('"') % 2 == 1:
            fragment += '"'
        # Count open brackets/braces and close them
        depth_brace = fragment.count('{') - fragment.count('}')
        depth_bracket = fragment.count('[') - fragment.count(']')
        # Drop the last incomplete item (trailing comma or partial key-value)
        fragment = re.sub(r',\s*$', '', fragment.rstrip())
        fragment = re.sub(r',\s*"[^"]*"\s*:\s*[^,}\]]*$', '', fragment)
        fragment += ']' * max(0, depth_bracket) + '}' * max(0, depth_brace)
        try:
            return json.loads(fragment)
        except Exception:
            pass
    logger.error(f"LLM produced invalid JSON parsing fallback text:\n{content}")
    raise ValueError(f"Could not extract valid JSON from LLM output. Raw Output was: {content[:500]}")

async def update_task_status(task_id: str, status: str, error: str = None):
    update = {"status": status, "updated_at": datetime.utcnow().isoformat()}
    if error:
        update["error"] = error
    tasks_col.update_one({"_id": task_id}, {"$set": update})

async def call_extraction_service(engine: str, filename: str, file_content: bytes) -> dict:
    """Call the chosen OCR engine service and return its JSON response."""
    url = ENGINE_URLS.get(engine)
    if not url:
        raise ValueError(f"Unknown engine: {engine}. Valid: {list(ENGINE_URLS)}")

    files = {"file": (filename, file_content, "application/octet-stream")}
    data  = {"force_ocr": "false", "use_llm": "false"}

    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(f"{url}/convert", files=files, data=data)
        if resp.status_code != 200:
            raise Exception(f"Engine '{engine}' returned {resp.status_code}: {resp.text[:300]}")
        return resp.json()

# ---------------------------------------------------------------------------
# Task endpoints
# ---------------------------------------------------------------------------

@app.post("/task/send")
async def task_send(payload: dict = Body(...)):
    file_id    = payload.get("file_id")
    engine     = payload.get("engine", "rapidocr")   # default → RapidOCR
    do_structure = payload.get("do_structure", True)

    task_id = str(uuid.uuid4())
    tasks_col.insert_one({
        "_id": task_id,
        "file_id": file_id,
        "status": "queued",
        "engine": engine,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    })

    asyncio.create_task(run_task(task_id, file_id, engine, do_structure))
    return {"task_id": task_id, "status": "queued"}

async def run_task(task_id, file_id, engine, do_structure):
    try:
        await update_task_status(task_id, "initializing")

        fs = gridfs.GridFS(db)
        if not fs.exists(_id=file_id):
            raise ValueError(f"File {file_id} not found in GridFS")

        grid_out     = fs.get(file_id)
        file_content = grid_out.read()
        filename     = grid_out.filename

        # ── 1. Extraction (with automatic fallback) ──────────────────────────
        await update_task_status(task_id, f"extracting_with_{engine}")
        start_ext = time.time()

        try:
            extract_data = await call_extraction_service(engine, filename, file_content)
        except Exception as primary_err:
            logger.warning(f"Engine '{engine}' failed: {primary_err}. Trying fallback.")
            # Fallback order: rapidocr → marker → mineru
            fallback_order = [e for e in ("rapidocr", "marker", "mineru") if e != engine]
            extract_data = None
            for fallback in fallback_order:
                await update_task_status(task_id, f"fallback_to_{fallback}")
                try:
                    extract_data = await call_extraction_service(fallback, filename, file_content)
                    engine = fallback
                    break
                except Exception as fb_err:
                    logger.warning(f"Fallback '{fallback}' also failed: {fb_err}")
            if extract_data is None:
                raise Exception(f"All engines failed. Last error: {primary_err}")

        ext_time = time.time() - start_ext

        # ── 2. Clean text ─────────────────────────────────────────────────────
        await update_task_status(task_id, "cleaning_text")
        cleaned_md = clean_markdown(extract_data.get("content", ""))
        extract_data["content"] = cleaned_md

        # Save OCR markdown
        ocr_path = OUTPUT_DIR / "ocr" / f"{Path(filename).stem}_{task_id[:8]}.md"
        ocr_path.write_text(cleaned_md, encoding="utf-8")

        # Save extracted images (base64 → file)
        images = extract_data.get("images", [])
        saved_img_paths = []
        for idx, img in enumerate(images):
            try:
                img_bytes = base64.b64decode(img["data"])
                ext       = img.get("format", "png").lower()
                img_path  = OUTPUT_DIR / "images" / f"{Path(filename).stem}_{task_id[:8]}_{idx}.{ext}"
                img_path.write_bytes(img_bytes)
                saved_img_paths.append(str(img_path))
            except Exception as e:
                logger.error(f"Failed to save image {idx}: {e}")

        db.extractions.update_one(
            {"file_id": file_id},
            {"$set": {
                "result": extract_data,
                "engine": engine,
                "processing_time": ext_time,
                "local_ocr_path": str(ocr_path),
                "local_images_paths": saved_img_paths,
            }},
            upsert=True,
        )
        tasks_col.update_one({"_id": task_id}, {"$set": {"status": "extracted", "processing_time": ext_time}})

        # ── 3. LLM Structuring ────────────────────────────────────────────────
        if do_structure:
            await update_task_status(task_id, "structuring_with_llm")

            ctx = cleaned_md[:15000] + "\n...[truncated]" if len(cleaned_md) > 15000 else cleaned_md
            
            # --- Regex Hint System ---
            hints = []
            if re.search(r'\$|USD', ctx, re.IGNORECASE): hints.append("Currency is USD")
            elif re.search(r'CAD', ctx, re.IGNORECASE): hints.append("Currency is CAD")
            elif re.search(r'€|EUR', ctx, re.IGNORECASE): hints.append("Currency is EUR")
            elif re.search(r'£|GBP', ctx, re.IGNORECASE): hints.append("Currency is GBP")
                
            dates = re.findall(r'(?i)(?:Invoice date|Date|Completed)[\s:]*([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})', ctx)
            if dates: hints.append(f"Date is likely {dates[0].strip()}")
                
            inv_nums = re.findall(r'(?i)(?:Invoice(?: No\.?|#|:)?|Meld\s*#|Ref\s*#)[\s]*([A-Za-z0-9_-]+)', ctx)
            if inv_nums: hints.append(f"Invoice Number is likely {inv_nums[0].strip()}")
                
            totals = re.findall(r'(?i)Total[\s:]*\$?([0-9,.]+)', ctx)
            if totals: hints.append(f"Total Amount is likely {totals[-1].strip()}")
                
            subtotals = re.findall(r'(?i)(?:Subtotal)[\s:]*\$?([0-9,.]+)', ctx)
            if subtotals: hints.append(f"Subtotal is likely {subtotals[0].strip()}")
                
            hint_str = ""
            if hints:
                hint_str = "=== CONFIDENCE HINTS ===\nBased on pattern matching, use these values if applicable:\n"
                for h in set(hints): hint_str += f"- {h}\n"
                hint_str += "========================\n\n"
            # -------------------------

            start_struct = time.time()

            schema_obj = {
                "type": "object",
                "properties": {
                    "document_type":  {"type": "string", "enum": ["Facture", "Devis", "Bon de commande", "Other"]},
                    "invoice_number": {"type": ["string", "null"]},
                    "date":           {"type": ["string", "null"]},
                    "total_amount":   {"type": ["number", "null"]},
                    "currency":       {"type": ["string", "null"], "enum": ["USD", "CAD", "EUR", "GBP", "null"]},
                    "vendor":         {"type": ["string", "null"]},
                    "vendor_address": {"type": ["string", "null"]},
                    "vendor_tax_id":  {"type": ["string", "null"]},
                    "customer_name":  {"type": ["string", "null"]},
                    "due_date":       {"type": ["string", "null"]},
                    "payment_method": {"type": ["string", "null"]},
                    "subtotal":       {"type": ["number", "null"]},
                    "tax_amount":     {"type": ["number", "null"]},
                    "line_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "quantity":    {"type": ["number", "null"]},
                                "unit_price":  {"type": ["number", "null"]},
                                "total_price": {"type": ["number", "null"]},
                            },
                            "additionalProperties": False
                        },
                    },
                },
                "required": ["total_amount", "currency"],
                "additionalProperties": False
            }

            prompt = (
                "Extract the invoice details from the OCR text below into a JSON object.\n"
                "- Return ONLY a JSON object matching the schema. If missing, use null. Do NOT hallucinate.\n"
                "\nCRITICAL FIELD RULES:\n"
                "1. Document Type: Usually the title (e.g. 'Invoice', 'Receipt').\n"
                "2. Invoice Number: Often labeled 'Invoice No', 'Meld #', 'Ref #', or a standalone string at top right.\n"
                "3. Vendor: The billing company issuing the invoice (often largest text at top, or labeled 'From').\n"
                "4. Customer Name: The billed party receiving the invoice (often labeled 'Bill To', or the secondary company).\n"
                "5. Date: The primary format is YYYY-MM-DD. (e.g. 'Invoice date' vs 'Ticket Completed').\n"
                "6. Subtotal & Tax: Only extract if explicitly listed. Do NOT duplicate 'Total' into 'Subtotal' if there's no tax.\n"
                "7. Line Items: Extract FULL descriptions accurately. Do not mistake '50ml' or '3M' for Quantity. Ensure total_price = quantity * unit_price.\n\n"
                "Text format may be chaotic. Connect values logically.\n\n"
                f"{hint_str}Text:\n\n{ctx}"
            )

            async with httpx.AsyncClient(timeout=600.0) as client:
                l_resp = await client.post(
                    LLAMA_CPP_API_URL,
                    json={
                        "model": "LFM2-1.2B-Extract-Q8_0",
                        "messages": [
                            {"role": "system", "content": "You are a precise JSON extraction AI. You excel at interpreting messy semantic OCR text, recognizing ambiguous headers (like 'Meld #' as invoice number), and strictly following the provided schema without hallucinating."},
                            {"role": "user",   "content": prompt},
                        ],
                        "temperature": 0.0,
                        "max_tokens": 8000,
                        "response_format": {"type": "json_object", "schema": schema_obj},
                    },
                )
                if l_resp.status_code != 200:
                    raise Exception(f"LLM failed: {l_resp.text}")

                content_str    = l_resp.json()["choices"][0]["message"]["content"]
                structured_data = extract_json_from_llm(content_str)

            struct_time = time.time() - start_struct

            struct_path = OUTPUT_DIR / "structure" / f"{Path(filename).stem}_{task_id[:8]}.json"
            struct_path.write_text(json.dumps(structured_data, indent=2, ensure_ascii=False), encoding="utf-8")

            db.structured_data.update_one(
                {"file_id": file_id},
                {"$set": {
                    "structured_json": structured_data,
                    "structuring_time": struct_time,
                    "local_json_path": str(struct_path),
                }},
                upsert=True,
            )
            tasks_col.update_one({"_id": task_id}, {
                "$set": {
                    "status": "completed",
                    "structuring_time": struct_time,
                    "updated_at": datetime.utcnow().isoformat(),
                }
            })

    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}")
        import traceback; traceback.print_exc()
        await update_task_status(task_id, "error", str(e))

@app.get("/task/list")
async def list_tasks(limit: int = 100):
    tasks = []
    for t in tasks_col.find().sort("created_at", -1).limit(limit):
        file_id = t.get("file_id")
        # Fetch filename from files collection
        file_doc = files_col.find_one({"_id": file_id}, {"filename": 1}) if file_id else None
        filename = file_doc.get("filename", "") if file_doc else ""
        tasks.append({
            "task_id":          str(t["_id"]),
            "status":           t.get("status"),
            "file_id":          file_id,
            "filename":         filename,
            "engine":           t.get("engine"),
            "created_at":       t.get("created_at"),
            "updated_at":       t.get("updated_at"),
            "processing_time":  t.get("processing_time"),
            "structuring_time": t.get("structuring_time"),
            "error":            t.get("error"),
        })
    return {"tasks": tasks}

# ---------------------------------------------------------------------------
# Task state / data
# ---------------------------------------------------------------------------

@app.get("/task/state/{task_id}")
async def get_task_state(task_id: str):
    task = tasks_col.find_one({"_id": task_id})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": str(task["_id"]),
        "status": task.get("status"),
        "file_id": task.get("file_id"),
        "engine": task.get("engine"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "processing_time": task.get("processing_time"),
        "structuring_time": task.get("structuring_time"),
        "error": task.get("error"),
    }

@app.get("/task/data/{task_id}")
async def get_task_data(task_id: str):
    task = tasks_col.find_one({"_id": task_id})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    file_id = task.get("file_id")
    struct  = db.structured_data.find_one({"file_id": file_id}) if file_id else None
    ext     = db.extractions.find_one({"file_id": file_id}) if file_id else None

    return {
        "task_id":          str(task["_id"]),
        "status":           task.get("status"),
        "ocr_time":         task.get("processing_time"),
        "structuring_time": task.get("structuring_time"),
        "data":             struct.get("structured_json") if struct else None,
        "ocr_content":      ext.get("result", {}).get("content", "") if ext else "",
    }

# ---------------------------------------------------------------------------
# Raw file serving
# ---------------------------------------------------------------------------

from fastapi.responses import Response

@app.get("/files/raw/{file_id}")
async def get_raw_file(file_id: str):
    fs = gridfs.GridFS(db)
    if not fs.exists(_id=file_id):
        raise HTTPException(status_code=404, detail="File not found")
    grid_out = fs.get(file_id)
    content  = grid_out.read()
    filename = grid_out.filename or "file"
    ext      = Path(filename).suffix.lower()
    mime_map = {
        ".pdf":  "application/pdf",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tiff": "image/tiff",
        ".tif":  "image/tiff",
        ".bmp":  "image/bmp",
        ".gif":  "image/gif",
        ".webp": "image/webp",
    }
    mime = mime_map.get(ext, "application/octet-stream")
    return Response(content=content, media_type=mime,
                    headers={"Content-Disposition": f"inline; filename=\"{filename}\""})

# ---------------------------------------------------------------------------
# OCR extraction retrieval
# ---------------------------------------------------------------------------

@app.get("/extraction/{file_id}")
async def get_extraction(file_id: str):
    ext = db.extractions.find_one({"file_id": file_id})
    if not ext:
        raise HTTPException(status_code=404, detail="No extraction found for this file")
    result = ext.get("result", {})
    return {
        "extraction": {
            "content":         result.get("content", ""),
            "engine":          ext.get("engine"),
            "extraction_data": {
                "processing_time": ext.get("processing_time"),
            },
        }
    }

# ---------------------------------------------------------------------------
# Update structured data (validate edits)
# ---------------------------------------------------------------------------

@app.put("/update/{file_id}")
async def update_structured(file_id: str, payload: dict = Body(...)):
    db.structured_data.update_one(
        {"file_id": file_id},
        {"$set": {"structured_json": payload, "validated": True, "validated_at": datetime.utcnow().isoformat()}},
        upsert=True,
    )
    # Also mark the task as validated
    tasks_col.update_one(
        {"file_id": file_id},
        {"$set": {"status": "validated", "updated_at": datetime.utcnow().isoformat()}},
        sort=[("created_at", -1)],
    )
    return {"status": "updated", "file_id": file_id}

# ---------------------------------------------------------------------------
# Task validation
# ---------------------------------------------------------------------------

@app.post("/task/validate/{task_id}")
async def validate_task(task_id: str):
    task = tasks_col.find_one({"_id": task_id})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    tasks_col.update_one(
        {"_id": task_id},
        {"$set": {"status": "validated", "updated_at": datetime.utcnow().isoformat()}}
    )
    return {"status": "validated", "task_id": task_id}

# ---------------------------------------------------------------------------
# System cleanup
# ---------------------------------------------------------------------------

@app.post("/system/cleanup")
async def system_cleanup():
    fs = gridfs.GridFS(db)
    for f in db.fs.files.find():
        try:
            fs.delete(f["_id"])
        except Exception:
            pass
    files_col.delete_many({})
    tasks_col.delete_many({})
    db.extractions.delete_many({})
    db.structured_data.delete_many({})
    db.ocr_data.delete_many({})
    return {"status": "cleaned"}

