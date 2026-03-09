from fastapi import FastAPI, UploadFile, File, HTTPException, Body, Form
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import uuid
import httpx
import os
import time
import json
import math
import re
import logging
import asyncio
from pathlib import Path
from pymongo import MongoClient
import gridfs
import unicodedata
import base64
from prompts import SYSTEM_PROMPT, EXTRACTION_PROMPT_TEMPLATE, SMALL_MODEL_SYSTEM_PROMPT

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backend")

app = FastAPI()
task_queue = asyncio.Queue()

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

LLAMA_CPP_HOST = os.getenv("LLAMA_CPP_HOST", "http://llamacpp:8080/v1")
# Base URL for llama-server. We will change the port dynamically.
LLAMA_CPP_BASE = LLAMA_CPP_HOST.split(":80")[0] # e.g. http://llamacpp

PRECISION_PORTS = {
    "4":    "8081",
    "5":    "8082",
    "8":    "8080",
    "16":   "8083",
    "350m": "8084"
}

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

def clean_markdown(text) -> str:
    """Remove excessive whitespace and control characters from OCR output."""
    if not isinstance(text, str) or not text:
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
            return json.loads(content[start:end+1])
        except Exception:
            pass
    return {}

def calculate_logic_score(data: dict) -> float:
    """
    Calculates a 0.0-1.0 score based on mathematical consistency.
    Checks:
    1. Line items: quantity * unit_price == total_price
    2. Sum of line items == total_amount (or subtotal)
    """
    if not data:
        return 0.0
    
    score = 1.0
    penalties = 0
    total_checks = 0
    
    # 1. Line Item Math
    line_items = data.get("line_items", [])
    li_math_correct = True
    if line_items:
        for item in line_items:
            q = item.get("quantity")
            u = item.get("unit_price")
            t = item.get("total_price")
            
            if q is not None and u is not None and t is not None:
                total_checks += 1
                if abs((float(q) * float(u)) - float(t)) > 0.05:
                    li_math_correct = False
                    penalties += 1

    # 2. Total Sum Math
    total_amount = data.get("total_amount")
    if total_amount is not None and line_items:
        total_checks += 1
        li_sum = sum(float(item.get("total_price", 0) or 0) for item in line_items)
        if abs(li_sum - float(total_amount)) > 0.05:
            penalties += 1
            
    if total_checks > 0:
        score = max(0.0, 1.0 - (penalties / total_checks))
    
    return score

async def update_task_status(task_id: str, status: str, error: str = None, engine: str = None):
    update = {"status": status, "updated_at": datetime.utcnow().isoformat()}
    if error:
        update["error"] = error
    if engine:
        update["engine"] = engine
    tasks_col.update_one({"_id": task_id}, {"$set": update})

async def call_extraction_service(engine: str, filename: str, file_content: bytes) -> dict:
    url = ENGINE_URLS.get(engine)
    if not url:
        raise ValueError(f"Unknown engine: {engine}. Valid: {list(ENGINE_URLS)}")

    files = {"file": (filename, file_content, "application/octet-stream")}
    data  = {"force_ocr": "false", "use_llm": "false"}

    # Retry logic for connection issues (common during service cold starts)
    max_retries = 30
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                resp = await client.post(f"{url}/convert", files=files, data=data)
                if resp.status_code != 200:
                    raise Exception(f"Engine '{engine}' returned {resp.status_code}: {resp.text[:300]}")
                return resp.json()
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            if attempt == max_retries - 1:
                raise
            logger.warning(f"Connection to {engine} failed (attempt {attempt+1}/{max_retries}): {e}. Retrying...")
            await asyncio.sleep(3)

# ---------------------------------------------------------------------------
# Task endpoints
# ---------------------------------------------------------------------------

@app.post("/task/send")
async def task_send(payload: dict = Body(...)):
    file_id    = payload.get("file_id")
    engine     = payload.get("engine", "rapidocr")
    do_structure = payload.get("do_structure", True)
    precision  = str(payload.get("precision", "8"))
    num_runs   = int(payload.get("num_runs", 1))
    
    # Cap runs at 10 for safety
    num_runs = max(1, min(10, num_runs))

    # Support for "All Models" comparison
    precisions_to_run = [precision]
    if precision == "all":
        precisions_to_run = ["4", "5", "8", "16", "350m"]

    task_ids = []
    for p in precisions_to_run:
        for _ in range(num_runs):
            tid = str(uuid.uuid4())
            tasks_col.insert_one({
                "_id": tid,
                "file_id": file_id,
                "status": "queued",
                "engine": engine,
                "precision": p,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            })
            task_queue.put_nowait({
                "task_id": tid,
                "file_id": file_id,
                "engine": engine,
                "do_structure": do_structure,
                "precision": p
            })
            task_ids.append(tid)

    if precision == "all" or num_runs > 1:
        return {"task_ids": task_ids, "status": "queued_all"}
    return {"task_id": task_ids[0], "status": "queued"}

async def worker():
    logger.info("Task worker started")
    while True:
        # Get a "work item" out of the queue.
        item = await task_queue.get()
        try:
            logger.info(f"Worker picking up task: {item['task_id']}")
            await run_task(
                item["task_id"],
                item["file_id"],
                item["engine"],
                item["do_structure"],
                item["precision"]
            )
        except Exception as e:
            logger.error(f"Worker error processing task {item['task_id']}: {e}")
        finally:
            # Notify the queue that the "work item" has been processed.
            task_queue.task_done()
            logger.info(f"Worker finished task: {item['task_id']}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(worker())

async def run_task(task_id, file_id, engine, do_structure, precision="8"):
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
                await update_task_status(task_id, f"fallback_to_{fallback}", engine=fallback)
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
            {"task_id": task_id},
            {"$set": {
                "file_id": file_id,
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
            elif re.search(r'MAD|Dirham|DH', ctx, re.IGNORECASE): hints.append("Currency is MAD")
            elif re.search(r'€|EUR', ctx, re.IGNORECASE): hints.append("Currency is EUR")
                
            dates = re.findall(r'(?i)(?:Invoice date|Date|Completed|Période|Le:)[\s:]*([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})', ctx)
            if dates: hints.append(f"Date is likely {dates[0].strip()}")
                
            inv_nums = re.findall(r'(?i)(?:Invoice(?: No\.?|#|:)?|Meld\s*#|Ref\s*#|Facture\s*nº)[\s]*([A-Za-z0-9_-]+)', ctx)
            if inv_nums: hints.append(f"Invoice Number is likely {inv_nums[0].strip()}")
                
            # Tax IDs (ICE, VAT, etc)
            tax_ids = re.findall(r'(?i)(?:ICE|VAT|IF|CNSS|R\.C\.|Patente|T\.P\.)[\s:]*([0-9\s/-]+)', ctx)
            if tax_ids: hints.append(f"Tax ID / ICE is likely {tax_ids[0].strip()}")
            
            # ICE specific (15 digits usually)
            ice_match = re.search(r'(?i)ICE[\s:]*(\d{15,})', ctx)
            if ice_match: hints.append(f"ICE is confirmed as {ice_match.group(1).strip()}")

            # Contact Info
            emails = re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', ctx)
            if emails: hints.append(f"Vendor Email is likely {emails[0].strip()}")
            
            phones = re.findall(r'(?i)(?:Tel|Phone|GSM|Fixe)[\s:]*([+0-9\s.-]{8,})', ctx)
            if phones: hints.append(f"Phone number: {phones[0].strip()}")

            # Flexible regex to handle both dot and comma (e.g. 33.816,00 or 33,816.00)
            money_pattern = r'(\d{1,3}(?:[,. ]\d{3})*[,.]\d{2}|\d+[,.]\d{2}|\d+)'
            totals = re.findall(rf'(?i)(?:Total|Net à payer|Montant TTC|NET PAYABLE)[\s:]*[\d,.]*\$?{money_pattern}', ctx)
            if totals: hints.append(f"Total Amount is likely {totals[-1].strip()}")
                
            subtotals = re.findall(rf'(?i)(?:Subtotal|Total HT|Hors Taxe)[\s:]*[\d,.]*\$?{money_pattern}', ctx)
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
                    "document_type":  {"type": ["string", "null"]},
                    "invoice_number": {"type": ["string", "null"]},
                    "date":           {"type": ["string", "null"]},
                    "due_date":       {"type": ["string", "null"]},
                    "vendor_name":    {"type": ["string", "null"]},
                    "vendor_address": {"type": ["string", "null"]},
                    "vendor_tax_id":  {"type": ["string", "null"]},
                    "customer_name":  {"type": ["string", "null"]},
                    "payment_method": {"type": ["string", "null"]},
                    "subtotal":       {"type": ["number", "null"]},
                    "tax_amount":     {"type": ["number", "null"]},
                    "total_amount":   {"type": ["number", "null"]},
                    "currency":       {"type": ["string", "null"], "enum": ["USD", "MAD", "EUR", "null"]},
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

            prompt = EXTRACTION_PROMPT_TEMPLATE.format(hint_str=hint_str, ctx=ctx)

            # Dynamic LLM URL based on precision
            port = PRECISION_PORTS.get(precision, "8080")
            llm_url = f"{LLAMA_CPP_BASE}:{port}/v1/chat/completions"
            # Model name and prompt selection
            sys_prompt = SYSTEM_PROMPT
            current_schema = schema_obj

            if precision == "350m":
                model_name = "LFM2-350M-Extract-Q8_0"
                sys_prompt = SMALL_MODEL_SYSTEM_PROMPT
                # Add a more directive note to the user prompt for 350M
                prompt = "STRICT MAPPING MODE: Look for 'BILL FROM' and 'BILL TO' specifically.\n\n" + prompt
                
                # Simplified/Relaxed Schema for 350M
                current_schema = json.loads(json.dumps(schema_obj)) # deep copy
                # Make numeric fields strings to prevent small model confusion
                for prop in ["subtotal", "tax_amount", "total_amount"]:
                    current_schema["properties"][prop]["type"] = ["string", "null"]
                
                li_props = current_schema["properties"]["line_items"]["items"]["properties"]
                for prop in ["quantity", "unit_price", "total_price"]:
                    li_props[prop]["type"] = ["string", "null"]
            else:
                model_name = f"LFM2-1.2B-Extract-{precision if precision == 'f16' else 'Q' + precision + '_0'}"

            async with httpx.AsyncClient(timeout=600.0) as client:
                l_resp = await client.post(
                    llm_url,
                    json={
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user",   "content": prompt},
                        ],
                        "temperature": 0.0,
                        "max_tokens": 8000,
                        "response_format": {"type": "json_object", "schema": current_schema},
                        "logprobs": True,
                        "top_logprobs": 1
                    },
                )
                if l_resp.status_code != 200:
                    raise Exception(f"LLM failed: {l_resp.text}")

                resp_json = l_resp.json()
                content_str = resp_json["choices"][0]["message"]["content"]
                
                # Semantic Score: Average logprobs
                semantic_score = 1.0
                try:
                    # llama.cpp returns logprobs in choices[0].logprobs.content[i].logprob
                    lp_list = resp_json["choices"][0].get("logprobs", {}).get("content", [])
                    if lp_list:
                        # Only average logprobs of tokens that aren't structural or whitespace if possible,
                        # but simple average of all completion tokens is a good proxy.
                        avg_lp = sum(item.get("logprob", 0) for item in lp_list) / len(lp_list)
                        semantic_score = math.exp(avg_lp)
                except Exception as e:
                    logger.warning(f"Failed to calculate semantic score: {e}")

                structured_data = extract_json_from_llm(content_str)

                # ── Post-process results to convert strings to numbers ────────────────
                def clean_num(v):
                    if v is None: return None
                    if isinstance(v, (int, float)): return float(v)
                    # Remove commas, currency symbols, and spaces
                    s = str(v).replace(",", "").replace("$", "").replace("MAD", "").replace("EUR", "").strip()
                    try:
                        return float(s)
                    except (ValueError, TypeError):
                        return 0.0

                structured_data["subtotal"] = clean_num(structured_data.get("subtotal"))
                structured_data["tax_amount"] = clean_num(structured_data.get("tax_amount"))
                structured_data["total_amount"] = clean_num(structured_data.get("total_amount"))
                if "line_items" in structured_data:
                    for li in structured_data["line_items"]:
                        li["quantity"] = clean_num(li.get("quantity"))
                        li["unit_price"] = clean_num(li.get("unit_price"))
                        li["total_price"] = clean_num(li.get("total_price"))

                # Filter out items that look like totals or headers (Universal)
                filtered_items = []
                ignore_keywords = ["total", "subtotal", "tax", "rate", "amount", "due", "notes"]
                for item in structured_data.get("line_items", []):
                    desc = (item.get("description") or "").lower()
                    item_total = item.get("total_price") or 0.0
                    total_amt = structured_data.get("total_amount") or 0.0
                    
                    # If description is a header or summary, and has 0.0 values, skip it
                    is_summary = any(k in desc for k in ignore_keywords)
                    has_data = (item.get("quantity") or 0.0) > 0 or item_total > 0
                    
                    if is_summary and not has_data:
                        continue
                        
                    # Aggressive: if it's a summary keyword and it matches the global total, it's a duplicate row
                    if is_summary and abs(item_total - total_amt) < 0.01 and total_amt > 0:
                        continue

                    # If description is just 'Hours' but quantity is 0, might be a header
                    if desc == "hours" and not has_data:
                        continue
                    filtered_items.append(item)
                structured_data["line_items"] = filtered_items

            struct_time = time.time() - start_struct
            struct_path = OUTPUT_DIR / "structure" / f"{Path(filename).stem}_{task_id[:8]}.json"
            struct_path.write_text(json.dumps(structured_data, indent=2, ensure_ascii=False), encoding="utf-8")

            # Logic Score
            logic_score = calculate_logic_score(structured_data)
            
            # Visual Score (from the extraction result)
            visual_score = extract_data.get("avg_visual_confidence", 1.0)
            
            # Final Weighted Score (30% Visual, 30% Semantic, 40% Logic)
            final_confidence = (visual_score * 0.3) + (semantic_score * 0.3) + (logic_score * 0.4)
            final_percentage = round(final_confidence * 100, 2)

            logger.info(f"Scoring -> Viz: {visual_score:.2f}, Sem: {semantic_score:.2f}, Log: {logic_score:.2f} | Final: {final_percentage}%")

            # Single robust update for extractions
            db.extractions.update_one(
                {"task_id": task_id},
                {"$set": {
                    "file_id": file_id,
                    "result": extract_data,
                    "engine": engine,
                    "processing_time": ext_time,
                    "avg_visual_confidence": visual_score,
                    "local_ocr_path": str(ocr_path),
                    "local_images_paths": saved_img_paths,
                    "timestamp": time.time()
                }},
                upsert=True
            )

            # Update structured data with full scoring metadata
            db.structured_data.update_one(
                {"task_id": task_id},
                {"$set": {
                    "structured_json": structured_data,
                    "metadata": {
                        "model": model_name,
                        "precision": precision,
                        "logic_score": logic_score,
                        "semantic_score": semantic_score,
                        "visual_score": visual_score,
                        "confidence_score": final_percentage,
                        "structuring_time": struct_time,
                        "local_json_path": str(struct_path)
                    },
                    "timestamp": time.time()
                }},
                upsert=True
            )

            # Final task update
            tasks_col.update_one(
                {"_id": task_id},
                {"$set": {
                    "status": "completed",
                    "confidence_score": final_percentage,
                    "structuring_time": struct_time,
                    "updated_at": datetime.utcnow().isoformat(),
                }}
            )

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
        # Fetch scores from structured_data if available
        struct_doc = db.structured_data.find_one({"task_id": str(t["_id"])}, {"metadata": 1})
        metadata = struct_doc.get("metadata", {}) if struct_doc else {}
        
        tasks.append({
            "task_id":          str(t["_id"]),
            "status":           t.get("status"),
            "file_id":          file_id,
            "filename":         filename,
            "engine":           t.get("engine"),
            "precision":        t.get("precision"),
            "created_at":       t.get("created_at"),
            "updated_at":       t.get("updated_at"),
            "processing_time":  t.get("processing_time"),
            "structuring_time": t.get("structuring_time"),
            "error":            t.get("error"),
            "confidence_score": t.get("confidence_score") or metadata.get("confidence_score"),
            "score_viz":        metadata.get("visual_score"),
            "score_sem":        metadata.get("semantic_score"),
            "score_log":        metadata.get("logic_score"),
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
    # Fetch scores
    struct_doc = db.structured_data.find_one({"task_id": task_id}, {"metadata": 1})
    metadata = struct_doc.get("metadata", {}) if struct_doc else {}

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
        "confidence_score": task.get("confidence_score") or metadata.get("confidence_score"),
        "score_viz": metadata.get("visual_score"),
        "score_sem": metadata.get("semantic_score"),
        "score_log": metadata.get("logic_score"),
    }

@app.get("/task/data/{task_id}")
async def get_task_data(task_id: str):
    task = tasks_col.find_one({"_id": task_id})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    struct  = db.structured_data.find_one({"task_id": task_id})
    ext     = db.extractions.find_one({"task_id": task_id})

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

