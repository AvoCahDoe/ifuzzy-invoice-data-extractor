from fastapi import FastAPI, UploadFile, File, HTTPException, Body, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import uuid
import os
import time
import json
import math
import logging
import asyncio
from pathlib import Path
from pymongo import MongoClient
import gridfs
from rule_extractor import extract_fields_rulebased
from rapidocr_client import call_extraction_service
from services.extraction_service import (
    clean_markdown,
    detect_input_type,
    save_ocr_markdown,
    save_extracted_images,
)
from services.structuring_service import (
    parse_money,
    extract_fields_hardcoded,
    filter_line_items,
    calculate_logic_score,
)

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
    """
    Simple liveness probe for the backend service.

    Returns
    -------
    dict
        A JSON object with a fixed `"status": "ok"` payload used by
        Docker, Kubernetes or external monitors to check that the
        API process is responsive.
    """
    return {"status": "ok"}

@app.get("/files")
async def list_files():
    """
    List all uploaded files known by the backend.

    This endpoint is used by the frontend status page to populate
    the list of invoices that have been uploaded, regardless of
    whether they have already been processed or not.

    Returns
    -------
    dict
        Object with a `files` array. Each element contains:

        - `id` (str): internal file identifier (UUID stored in Mongo / GridFS).
        - `filename` (str): original uploaded filename.
        - `upload_date` (str, ISO 8601): when the file was stored.
        - `processed` (bool): whether at least one extraction task ran.
    """
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
    """
    Upload a new invoice file (PDF or image) into GridFS.

    Parameters
    ----------
    file : UploadFile
        Multipart/form-data file sent by the Angular frontend.
        The raw bytes are stored in MongoDB GridFS and only a
        lightweight metadata document is kept in `files_col`.

    Returns
    -------
    dict
        - `file_id` (str): UUID used for subsequent processing.
        - `filename` (str): original filename, used only for display.
    """
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
    """
    Delete a file and all its associated processing artifacts.

    This removes:
    - the binary file from GridFS,
    - metadata from `files` and `tasks`,
    - OCR / structured / extraction documents from their collections.

    Parameters
    ----------
    file_id : str
        Identifier previously returned by `/upload`.

    Raises
    ------
    HTTPException
        404 if the file does not exist in `files_col`.

    Returns
    -------
    dict
        `{ "status": "deleted", "file_id": <file_id> }` on success.
    """
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

async def update_task_status(task_id: str, status: str, error: str = None, engine: str = None):
    """
    Update the status fields of a task document.

    Parameters
    ----------
    task_id : str
        Identifier of the task in `tasks_col`.
    status : str
        New status string (e.g. `queued`, `extracted`, `completed`, `error`).
    error : str, optional
        Human‑readable error message stored when the task failed.
    engine : str, optional
        Name of the OCR engine used (currently `rapidocr`).

    Notes
    -----
    This helper centralises how we stamp `updated_at` timestamps and
    avoids duplicating MongoDB update logic throughout the pipeline.
    """
    update = {"status": status, "updated_at": datetime.utcnow().isoformat()}
    if error:
        update["error"] = error
    if engine:
        update["engine"] = engine
    tasks_col.update_one({"_id": task_id}, {"$set": update})

# ---------------------------------------------------------------------------
# Task endpoints
# ---------------------------------------------------------------------------

@app.post("/task/send")
async def task_send(payload: dict = Body(...)):
    """
    Enqueue one or more background extraction/structuring tasks.

    This is the main entrypoint called by the upload page. It creates
    task documents in MongoDB and pushes work items onto the in‑process
    asyncio queue consumed by `worker()`.

    Parameters
    ----------
    payload : dict
        JSON body with:
        - `file_id` (str, required): ID returned by `/upload`.
        - `engine` (str, optional): OCR engine, default `rapidocr`.
        - `do_structure` (bool, optional): whether to run structuring.

    Returns
    -------
    dict
        `{ "task_id": <id>, "status": "queued" }`.
    """
    file_id    = payload.get("file_id")
    engine     = payload.get("engine", "rapidocr")
    do_structure = payload.get("do_structure", True)

    tid = str(uuid.uuid4())
    tasks_col.insert_one({
        "_id": tid,
        "file_id": file_id,
        "status": "queued",
        "engine": engine,
        "structuring_mode": "fuzzy",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    })
    task_queue.put_nowait({
        "task_id": tid,
        "file_id": file_id,
        "engine": engine,
        "do_structure": do_structure,
        "structuring_mode": "fuzzy"
    })

    return {"task_id": tid, "status": "queued"}

async def worker():
    """
    Background worker coroutine that processes tasks from the queue.

    This function runs once at application startup (see `startup_event`)
    and loops forever, pulling items from `task_queue` and delegating
    the heavy lifting to `run_task()`.

    It ensures that any exception inside `run_task()` does not crash
    the whole process and that `task_queue.task_done()` is always called.
    """
    logger.info("Task worker started")
    while True:
        # Get a "work item" out of the queue.
        item = await task_queue.get()
        try:
            logger.info(f"Worker picking up task: {item['task_id']}")
            await run_task(
                item["task_id"],
                item["file_id"],
                item.get("engine", "rapidocr"),
                item["do_structure"]
            )
        except Exception as e:
            logger.error(f"Worker error processing task {item['task_id']}: {e}")
        finally:
            # Notify the queue that the "work item" has been processed.
            task_queue.task_done()
            logger.info(f"Worker finished task: {item['task_id']}")

@app.on_event("startup")
async def startup_event():
    """
    FastAPI startup hook that launches the background worker.

    This ensures that the task consumer is running as soon as the
    application starts, without requiring an external worker process.
    """
    asyncio.create_task(worker())

async def run_task(task_id, file_id, engine, do_structure):
    """
    End‑to‑end pipeline for a single extraction+structuring task.

    This function:
    1. Loads the file from GridFS.
    2. Calls the OCR service (RapidOCR) to produce markdown and blocks.
    3. Writes OCR artifacts to `processed_output/`.
    4. Optionally runs structuring (fuzzy rules only).
    5. Computes confidence scores and stores results in MongoDB.

    Parameters
    ----------
    task_id : str
        Identifier of the task document in `tasks_col`.
    file_id : str
        Identifier of the uploaded file in GridFS / `files_col`.
    engine : str
        OCR engine name (`rapidocr`).
    do_structure : bool
        Whether to run structuring after OCR. When `False`, only OCR
        artifacts are saved and the task stops after extraction.

    Returns
    -------
    None
        Results are persisted to MongoDB and `processed_output/`.

    Notes
    -----
    Any uncaught exception is logged and the task status is set to `"error"`.
    """
    try:
        await update_task_status(task_id, "initializing")

        fs = gridfs.GridFS(db)
        if not fs.exists(_id=file_id):
            raise ValueError(f"File {file_id} not found in GridFS")

        grid_out     = fs.get(file_id)
        file_content = grid_out.read()
        filename     = grid_out.filename

        # Save original uploaded file (PDF or image) to processed_output/images
        ext = Path(filename).suffix.lower() or ".bin"
        if ext in (".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".gif"):
            orig_path = OUTPUT_DIR / "images" / f"{Path(filename).stem}_{task_id[:8]}_original{ext}"
            try:
                orig_path.write_bytes(file_content)
                logger.info(f"Saved original file to {orig_path}")
            except Exception as e:
                logger.warning(f"Failed to save original file to images: {e}")

        # ── 1. Extraction (with automatic fallback) ──────────────────────────
        await update_task_status(task_id, f"extracting_with_{engine}")
        start_ext = time.time()

        try:
            # Force engine to rapidocr (only supported engine)
            engine = "rapidocr"
            extract_data = await call_extraction_service(engine, filename, file_content)
        except Exception as primary_err:
            logger.error(f"Engine '{engine}' failed and no other engines are available: {primary_err}")
            raise

        ext_time = time.time() - start_ext

        # Determine input type using file extension + OCR extraction_mode
        extraction_mode = extract_data.get("extraction_mode")
        input_type = detect_input_type(filename, extraction_mode)

        # ── 2. Clean text ─────────────────────────────────────────────────────
        await update_task_status(task_id, "cleaning_text")
        cleaned_md = clean_markdown(extract_data.get("content", ""))
        extract_data["content"] = cleaned_md

        # Save OCR markdown + extracted images
        ocr_path = save_ocr_markdown(OUTPUT_DIR, filename, task_id, cleaned_md)
        saved_img_paths = save_extracted_images(
            OUTPUT_DIR, filename, task_id, extract_data.get("images", [])
        )

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

        # ── 3. Structuring
        if do_structure:
            raw_ctx = cleaned_md
            hardcoded = extract_fields_hardcoded(raw_ctx)
            start_struct = time.time()

            blocks = extract_data.get("blocks", [])
            model_name = "fuzzy-rule-based"

            await update_task_status(task_id, "structuring_fuzzy")
            rule_output = extract_fields_rulebased(blocks, raw_ctx)
            fuzzy_score = rule_output.pop("_fuzzy_match_score", 0.5)
            anchor_indicators = rule_output.pop("anchor_indicators", {})

            structured_data = dict(hardcoded)
            structured_data.update({k: v for k, v in rule_output.items() if v is not None})
            if rule_output.get("line_items"):
                structured_data["line_items"] = rule_output["line_items"]

            # Normalize line-item numbers from Phase 1
            for li in structured_data.get("line_items", []):
                li["quantity"] = parse_money(li.get("quantity")) if li.get("quantity") is not None else None
                li["unit_price"] = parse_money(li.get("unit_price")) if li.get("unit_price") is not None else None
                li["total_price"] = parse_money(li.get("total_price")) if li.get("total_price") is not None else None
            structured_data["line_items"] = filter_line_items(
                structured_data.get("line_items", []), structured_data.get("total_amount")
            )

            struct_time = time.time() - start_struct
            logger.info(f"Fuzzy Structuring complete in {round(struct_time*1000)}ms | fuzzy={fuzzy_score:.2f}")

            struct_path = OUTPUT_DIR / "structure" / f"{Path(filename).stem}_{task_id[:8]}.json"
            struct_path.write_text(json.dumps(structured_data, indent=2, ensure_ascii=False), encoding="utf-8")

            # Logic Score
            logic_score = calculate_logic_score(structured_data)
            
            # Visual Score (from the extraction result)
            visual_score = extract_data.get("avg_visual_confidence", 1.0)
            
            # Final Weighted Score (40% Visual, 60% Logic)
            final_confidence = (visual_score * 0.4) + (logic_score * 0.6)
            final_percentage = round(final_confidence * 100, 2)

            logger.info(f"Scoring -> Viz: {visual_score:.2f}, Log: {logic_score:.2f} | Final: {final_percentage}%")

            # Single robust update for extractions
            db.extractions.update_one(
                {"task_id": task_id},
                {"$set": {
                    "file_id": file_id,
                    "result": extract_data,
                    "engine": engine,
                    "processing_time": ext_time,
                    "avg_visual_confidence": visual_score,
                    "extraction_mode": extraction_mode,
                    "input_type": input_type,
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
                    "file_id": file_id,
                    "structured_json": structured_data,
                    "metadata": {
                        "model": model_name,
                        "structuring_mode": "fuzzy",
                        "logic_score": logic_score,
                        "visual_score": visual_score,
                        "confidence_score": final_percentage,
                        "structuring_time": struct_time,
                        "local_json_path": str(struct_path),
                        "input_type": input_type,
                        "extraction_mode": extraction_mode,
                        "anchor_indicators": anchor_indicators,
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
    """
    Return a summary list of recent tasks with scores and timings.

    Parameters
    ----------
    limit : int, optional
        Maximum number of tasks to return, ordered by `created_at` desc.

    Returns
    -------
    dict
        `{ "tasks": [...] }` where each entry aggregates data from
        `tasks`, `files` and `structured_data` (confidence scores,
        structuring mode, line item count, etc.).
    """
    tasks = []
    for t in tasks_col.find().sort("created_at", -1).limit(limit):
        file_id = t.get("file_id")
        # Fetch filename from files collection
        file_doc = files_col.find_one({"_id": file_id}, {"filename": 1}) if file_id else None
        filename = file_doc.get("filename", "") if file_doc else ""
        # Fetch scores from structured_data if available
        struct_doc = db.structured_data.find_one({"task_id": str(t["_id"])}, {"metadata": 1})
        metadata = struct_doc.get("metadata", {}) if struct_doc else {}
        structured_json = struct_doc.get("structured_json", {}) if struct_doc else {}
        struct_mode = metadata.get("structuring_mode") or t.get("structuring_mode")
        line_items = structured_json.get("line_items", []) if isinstance(structured_json, dict) else []
        
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
            "confidence_score": t.get("confidence_score") or metadata.get("confidence_score"),
            "score_viz":        metadata.get("visual_score"),
            "score_log":        metadata.get("logic_score"),
            "structuring_mode": "fuzzy",
            "line_items_count": len(line_items) if isinstance(line_items, list) else 0,
        })
    return {"tasks": tasks}

# ---------------------------------------------------------------------------
# Task state / data
# ---------------------------------------------------------------------------

@app.get("/task/state/{task_id}")
async def get_task_state(task_id: str):
    """
    Get the current status and scoring metadata for a task.

    Parameters
    ----------
    task_id : str
        Identifier of the task document.

    Returns
    -------
    dict
        Basic task fields plus confidence components (visual / semantic / logic).

    Raises
    ------
    HTTPException
        404 if the task does not exist.
    """
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
        "score_log": metadata.get("logic_score"),
    }

@app.get("/task/data/{task_id}")
async def get_task_data(task_id: str):
    """
    Fetch full structured data and OCR content for a task.

    This endpoint is used by the validation page to load:
    - human‑readable OCR markdown,
    - the structured JSON payload,
    - basic timings and the original filename.

    Parameters
    ----------
    task_id : str
        Identifier of the task document.

    Returns
    -------
    dict
        A rich response containing task metadata, structured JSON,
        and OCR markdown content.

    Raises
    ------
    HTTPException
        404 if the task does not exist.
    """
    task = tasks_col.find_one({"_id": task_id})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    struct  = db.structured_data.find_one({"task_id": task_id})
    ext     = db.extractions.find_one({"task_id": task_id})
    file_id = task.get("file_id")
    file_doc = files_col.find_one({"_id": file_id}, {"filename": 1}) if file_id else None
    filename = file_doc.get("filename", "") if file_doc else ""

    metadata = struct.get("metadata", {}) if struct else {}
    input_type = metadata.get("input_type") or (ext.get("input_type") if ext else None)

    return {
        "task_id":          str(task["_id"]),
        "status":           task.get("status"),
        "file_id":          file_id,
        "filename":         filename,
        "ocr_time":         task.get("processing_time"),
        "structuring_time": task.get("structuring_time"),
        "data":             struct.get("structured_json") if struct else None,
        "ocr_content":      ext.get("result", {}).get("content", "") if ext else "",
        "input_type":       input_type,
        "blocks":           ext.get("result", {}).get("blocks", []) if ext else [],
        "table_regions":    ext.get("result", {}).get("table_regions", []) if ext else [],
        "metadata":         metadata,
    }

# ---------------------------------------------------------------------------
# Raw file serving
# ---------------------------------------------------------------------------

from fastapi.responses import Response

@app.get("/files/raw/{file_id}")
async def get_raw_file(file_id: str):
    """
    Stream the original uploaded file bytes from GridFS.

    Parameters
    ----------
    file_id : str
        Identifier returned by `/upload`.

    Returns
    -------
    fastapi.Response
        Binary response with the correct `Content-Type` and
        `Content-Disposition` so the browser can preview the file.

    Raises
    ------
    HTTPException
        404 if the file is missing in GridFS.
    """
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
    return Response(
        content=content,
        media_type=mime,
        headers={
            "Content-Disposition": f"inline; filename=\"{filename}\"",
            "Access-Control-Expose-Headers": "Content-Type, Content-Disposition",
        },
    )

# ---------------------------------------------------------------------------
# OCR extraction retrieval
# ---------------------------------------------------------------------------

@app.get("/extraction/{file_id}")
async def get_extraction(file_id: str):
    """
    Return the last OCR extraction result for a given file.

    Parameters
    ----------
    file_id : str
        Identifier returned by `/upload`.

    Returns
    -------
    dict
        `{ "extraction": { "content": ..., "engine": ..., "extraction_data": {...} } }`
        where `content` is the markdown text used later by the structuring
        pipeline and `processing_time` is the OCR duration in seconds.

    Raises
    ------
    HTTPException
        404 if no extraction document is stored for this file.
    """
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
async def update_structured(file_id: str, payload: dict = Body(...), task_id: str = Query(None)):
    """
    Persist user‑edited structured data and mark the task as validated.

    Parameters
    ----------
    file_id : str
        Identifier of the file being validated.
    payload : dict
        The full structured JSON object coming from the validation UI.
    task_id : str, optional
        If provided, only the structured document for this task is updated;
        otherwise the latest document for the file is updated.

    Returns
    -------
    dict
        `{ "status": "updated", "file_id": <file_id> }` upon success.

    Side Effects
    ------------
    - Sets `validated = True` and `validated_at` on the structured data.
    - Updates the corresponding task status to `"validated"`.
    """
    # Prefer task_id for precise update; fallback to file_id (most recent task for that file)
    if task_id:
        result = db.structured_data.update_one(
            {"task_id": task_id},
            {"$set": {"file_id": file_id, "structured_json": payload, "validated": True, "validated_at": datetime.utcnow().isoformat()}},
        )
    else:
        result = db.structured_data.update_one(
            {"file_id": file_id},
            {"$set": {"structured_json": payload, "validated": True, "validated_at": datetime.utcnow().isoformat()}},
        )
    # Mark the task as validated
    if task_id:
        tasks_col.update_one(
            {"_id": task_id},
            {"$set": {"status": "validated", "updated_at": datetime.utcnow().isoformat()}},
        )
    else:
        tasks_col.update_one(
            {"file_id": file_id},
            {"$set": {"status": "validated", "updated_at": datetime.utcnow().isoformat()}},
        )
    return {"status": "updated", "file_id": file_id}

# ---------------------------------------------------------------------------
# System cleanup
# ---------------------------------------------------------------------------

@app.post("/system/cleanup")
async def system_cleanup():
    """
    Danger‑zone endpoint to wipe all stored data.

    This is intended for local development and test environments to
    quickly reset the database. It removes:
    - all GridFS files,
    - all `files`, `tasks`, `extractions`, `structured_data`, and `ocr_data` documents.

    Returns
    -------
    dict
        `{ "status": "cleaned" }` once the collections have been cleared.
    """
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

