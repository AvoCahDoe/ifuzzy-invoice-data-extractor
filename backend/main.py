import os
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GLOG_minloglevel"] = "2"
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"

import asyncio
import json
import time
import base64
import tempfile
from enum import Enum
from io import BytesIO
from io import BytesIO as _BytesIO
from pathlib import Path
from typing import Optional, Dict, Any, List

from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, Response
from fastapi.openapi.utils import get_openapi

from pydantic import BaseModel, Field, constr

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket
from bson import ObjectId

from PIL import Image  
from pypdf import PdfReader

from marker.config.parser import ConfigParser
from marker.logger import configure_logging, get_logger
from marker.models import create_model_dict

configure_logging()
logger = get_logger()

OPENAPI_TAGS = [
    {"name": "Files", "description": "Upload, list, stream and delete files."},
    {"name": "Extraction", "description": "Run OCR/Marker and fetch extraction results / structured JSON."},
    {"name": "Tasks", "description": "Background pipeline: enqueue, state, outputs, validation, listing."},
    {"name": "Health", "description": "Service health & readiness checks."},
]

app = FastAPI(
    title="Invoice Extraction API",
    description=(
        "REST API for multi-invoice upload, parallel background processing, "
        "extraction (PDF text-layer or Marker OCR), LLM-based structuring, validation and deletion."
    ),
    version="1.0.0",
    contact={"name": "Digex", "email": "mohamed.baka@digex.ma"},
    openapi_tags=OPENAPI_TAGS,
)



app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
client = AsyncIOMotorClient(MONGODB_URI)
db = client.fileuploads
fs = AsyncIOMotorGridFSBucket(db)
tasks_col = db.tasks

ObjectIdStr = constr(pattern=r"^[a-fA-F0-9]{24}$")


class ErrorResponse(BaseModel):
    detail: str = Field(..., example="File not found")


class UploadResponse(BaseModel):
    message: str = Field("File uploaded successfully")
    file_id: ObjectIdStr


class FileItem(BaseModel):
    id: ObjectIdStr
    filename: str
    upload_date: Optional[str] = Field(None, example="2025-09-08 12:34")
    content_type: Optional[str] = Field(None, example="application/pdf")
    length: Optional[int] = Field(None, example=123456)
    processed: bool = False


class FilesResponse(BaseModel):
    files: List[FileItem] = []


class ExtractionPayload(BaseModel):
    file_name: str
    original_path: str
    content: str
    images: List[Dict[str, Any]] = []
    images_count: int
    extraction_timestamp: float
    processing_time: float
    extraction_mode: str = Field(..., example="pdf_text_layer")


class ProcessResponse(BaseModel):
    message: str = "File processed successfully"
    extraction_id: ObjectIdStr
    extraction: ExtractionPayload


class ExtractionDoc(BaseModel):
    _id: Optional[ObjectIdStr] = None
    file_id: ObjectIdStr
    extraction_data: ExtractionPayload


class GetExtractionResponse(BaseModel):
    extraction: ExtractionDoc


class StructureResponse(BaseModel):
    message: str = "Structured data extracted and saved."
    data: Dict[str, Any]


class UpdateResponse(BaseModel):
    message: str = "Données mises à jour avec succès"


class HealthResponse(BaseModel):
    mongo: bool
    ollama: bool


class TaskSendPayload(BaseModel):
    file_id: str
    force_ocr: bool = False
    do_structure: bool = True
    client_token: Optional[str] = None 


class TaskSendResponse(BaseModel):
    task_id: ObjectIdStr
    status: str


class TaskStateResponse(BaseModel):
    task_id: ObjectIdStr
    status: str
    error: Optional[str] = None
    file_id: Optional[ObjectIdStr] = None
    extraction_id: Optional[ObjectIdStr] = None
    structured_id: Optional[ObjectIdStr] = None
    updated_at: Optional[str] = Field(None, example="2025-09-08 13:45")


class TaskTextResponse(BaseModel):
    file_id: ObjectIdStr
    content: str
    images_count: int
    extraction_mode: Optional[str] = None


class TaskDataResponse(BaseModel):
    file_id: ObjectIdStr
    data: Dict[str, Any]


class TaskListItem(BaseModel):
    task_id: ObjectIdStr
    status: str
    file_id: Optional[ObjectIdStr] = None
    filename: Optional[str] = ""
    extraction_id: Optional[ObjectIdStr] = None
    structured_id: Optional[ObjectIdStr] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    client_token: Optional[str] = None


class TaskListResponse(BaseModel):
    tasks: List[TaskListItem] = []

def to_oid(value) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file ID")


def fmt(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.strftime("%Y-%m-%d %H:%M")

def _pdf_is_readable(pdf_bytes: bytes, min_chars: int = 50, sample_pages: int = 1) -> bool:
    try:
        reader = PdfReader(_BytesIO(pdf_bytes))
        if len(reader.pages) == 0:
            return False
        pages_to_sample = min(len(reader.pages), sample_pages)
        char_count = 0
        for i in range(pages_to_sample):
            txt = reader.pages[i].extract_text() or ""
            char_count += len((txt or "").strip())
        return char_count >= min_chars
    except Exception:
        return False


def _pdf_extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(_BytesIO(pdf_bytes))
    texts = []
    for p in reader.pages:
        texts.append(p.extract_text() or "")
    return "\n\n".join(texts).strip()

marker_models = None


async def get_marker_models():
    global marker_models
    if marker_models is None:
        logger.info("Loading marker models...")
        marker_models = await asyncio.to_thread(create_model_dict)
        logger.info("Marker models loaded successfully")
    return marker_models

def image_to_base64(img):
    if hasattr(img, 'save'):  
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        img_str = base64.b64encode(buffer.getvalue()).decode()
        return {
            "format": "PNG",
            "data": img_str,
            "size": getattr(img, 'size', None),
        }
    elif isinstance(img, str):
        return {"path": img}
    else:
        return {"error": "Unsupported image format"}


def extract_markdown_content(rendered_obj):
    if hasattr(rendered_obj, 'text'):
        return rendered_obj.text
    elif hasattr(rendered_obj, 'markdown'):
        return rendered_obj.markdown
    else:
        rendered_str = str(rendered_obj)
        if rendered_str.startswith('markdown="') and '" images=' in rendered_str:
            start_idx = len('markdown="')
            end_idx = rendered_str.find('" images=')
            if end_idx != -1:
                markdown_content = rendered_str[start_idx:end_idx]
                markdown_content = (
                    markdown_content
                    .replace('\\n', '\n')
                    .replace('\\"', '"')
                    .replace('\\\\', '\\')
                )
                return markdown_content
        return rendered_str

async def extract_content_from_file(file_content: bytes, original_filename: str, force_ocr: bool = False):
    try:
        models = await get_marker_models()

        file_path = Path(original_filename)
        suffix = file_path.suffix.lower() if file_path.suffix else '.pdf'

        start_time = time.time()

        if not force_ocr and suffix == '.pdf' and _pdf_is_readable(file_content):
            try:
                content_text = _pdf_extract_text(file_content)
                result_data = {
                    "file_name": file_path.stem,
                    "original_path": original_filename,
                    "content": content_text,
                    "images": [],
                    "images_count": 0,
                    "extraction_timestamp": time.time(),
                    "processing_time": time.time() - start_time,
                    "extraction_mode": "pdf_text_layer",
                }
                return result_data
            except Exception as e:
                logger.warning(f"Readable-PDF path failed, falling back to OCR: {e}")

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or '.pdf') as temp_file:
            temp_file.write(file_content)
            temp_file_path = temp_file.name

        try:
            try:
                config_dict = {
                    'output_format': 'markdown',
                    'extract_images': True,
                    'batch_multiplier': 1,
                    'max_pages': None,
                    'langs': None,
                    'output_dir': None,
                    'debug': False
                }
                config_parser = ConfigParser(config_dict)
                converter_cls = config_parser.get_converter_cls()
                converter = converter_cls(
                    config=config_parser.generate_config_dict(),
                    artifact_dict=models,
                    processor_list=config_parser.get_processors(),
                    renderer=config_parser.get_renderer(),
                    llm_service=config_parser.get_llm_service(),
                )
                rendered = await asyncio.to_thread(converter, temp_file_path)

            except Exception as config_error:
                logger.warning(f"Marker config/init failed, trying simple convert: {config_error}")
                from marker.convert import convert_single_pdf
                result = await asyncio.to_thread(convert_single_pdf, temp_file_path, models)
                if isinstance(result, tuple) and len(result) >= 2:
                    content = result[0] if isinstance(result[0], str) else str(result[0])
                    images = result[1] if len(result) > 1 else {}
                else:
                    content = str(result)
                    images = {}

                class SimpleRendered:
                    def __init__(self, content, images):
                        self.text = content
                        self.markdown = content
                        self.images = images
                rendered = SimpleRendered(content, images)

            content = extract_markdown_content(rendered)

            serialized_images = []
            if hasattr(rendered, 'images') and rendered.images:
                if isinstance(rendered.images, dict):
                    for img_name, img_obj in rendered.images.items():
                        try:
                            serialized_img = image_to_base64(img_obj)
                            serialized_img["name"] = img_name
                            serialized_img["index"] = len(serialized_images)
                            serialized_images.append(serialized_img)
                        except Exception as e:
                            logger.warning(f"Could not serialize image {img_name}: {e}")
                            serialized_images.append({
                                "name": img_name,
                                "index": len(serialized_images),
                                "error": str(e)
                            })
                else:
                    for i, img in enumerate(rendered.images):
                        try:
                            serialized_img = image_to_base64(img)
                            serialized_img["index"] = i
                            serialized_images.append(serialized_img)
                        except Exception as e:
                            logger.warning(f"Could not serialize image {i}: {e}")
                            serialized_images.append({"index": i, "error": str(e)})

            result_data = {
                "file_name": file_path.stem,
                "original_path": original_filename,
                "content": content,
                "images": serialized_images,
                "images_count": len(serialized_images),
                "extraction_timestamp": time.time(),
                "processing_time": time.time() - start_time,
                "extraction_mode": "marker_ocr",
            }
            return result_data

        finally:
            try:
                os.unlink(temp_file_path)
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Error during extraction: {e}")
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")


@app.post(
    "/upload",
    tags=["Files"],
    summary="Upload a file to GridFS",
    description="Accepts PDF/PNG/JPEG and stores it in MongoDB GridFS.",
    response_model=UploadResponse,
    responses={500: {"model": ErrorResponse}},
)
async def upload_file(file: UploadFile = File(...)):
    try:
        print(f"Receiving file: {file.filename}, Content-Type: {file.content_type}")
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file uploaded")
        metadata = {
            "upload_date": datetime.utcnow(),
            "content_type": file.content_type,
            "original_filename": file.filename,
            "processed": False
        }
        file_id = await fs.upload_from_stream(file.filename, file.file, metadata=metadata)
        print(f"Upload successful, ID: {file_id}")
        return {"message": "File uploaded successfully", "file_id": str(file_id)}
    except Exception as e:
        print("Error during upload:", str(e))
        raise HTTPException(status_code=500, detail="Failed to upload file")


@app.get(
    "/files",
    tags=["Files"],
    summary="List uploaded files",
    response_model=FilesResponse
)
async def list_files():
    try:
        cursor = db.fs.files.find({})
        files = await cursor.to_list(length=100)
        if not files:
            return {"files": []}
        result = []
        for file in files:
            result.append({
                "id": str(file["_id"]),
                "filename": file.get("filename"),
                "upload_date": fmt(file.get("metadata", {}).get("upload_date")),
                "content_type": file.get("metadata", {}).get("content_type"),
                "length": file.get("length"),
                "processed": file.get("metadata", {}).get("processed", False)
            })
        return {"files": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/files/raw/{file_id}",
    tags=["Files"],
    summary="Stream original file bytes"
)
async def file_raw(file_id: str):
    oid = to_oid(file_id)
    file_doc = await db.fs.files.find_one({"_id": oid})
    if not file_doc:
        raise HTTPException(status_code=404, detail="File not found")
    grid_out = await fs.open_download_stream(oid)
    content_type = file_doc.get("metadata", {}).get("content_type") or "application/octet-stream"

    async def _iter():
        while True:
            chunk = await grid_out.readchunk()
            if not chunk:
                break
            yield chunk
    return StreamingResponse(_iter(), media_type=content_type)


@app.post(
    "/process/{file_id}",
    tags=["Extraction"],
    summary="Process a file now",
    description="Runs fast text-layer extraction for readable PDFs, else Marker OCR.",
    response_model=ProcessResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def process_file(file_id: str, force_ocr: bool = False):
    try:
        obj_id = to_oid(file_id)

        file_doc = await db.fs.files.find_one({"_id": obj_id})
        if not file_doc:
            raise HTTPException(status_code=404, detail="File not found")

        if file_doc.get("metadata", {}).get("processed", False) and not force_ocr:
            existing_extraction = await db.extractions.find_one({"file_id": obj_id})
            if existing_extraction:
                existing_extraction["_id"] = str(existing_extraction["_id"])
                existing_extraction["file_id"] = str(existing_extraction["file_id"])
                return {"message": "File already processed", "extraction": existing_extraction}

        grid_out = await fs.open_download_stream(obj_id)
        file_content = await grid_out.read()

        original_filename = file_doc.get("filename", "unknown")

        logger.info(f"Starting extraction for file: {original_filename} (force_ocr={force_ocr})")
        extraction_result = await extract_content_from_file(file_content, original_filename, force_ocr=force_ocr)

        extraction_doc = {
            "file_id": obj_id,
            "original_filename": original_filename,
            "extraction_data": extraction_result,
            "created_at": datetime.utcnow()
        }

        existing = await db.extractions.find_one({"file_id": obj_id})
        if existing and force_ocr:
            await db.extractions.update_one(
                {"_id": existing["_id"]},
                {"$set": extraction_doc}
            )
            insertion_id = existing["_id"]
        elif existing:
            insertion_id = existing["_id"]
        else:
            insert_res = await db.extractions.insert_one(extraction_doc)
            insertion_id = insert_res.inserted_id

        await db.fs.files.update_one(
            {"_id": obj_id},
            {"$set": {"metadata.processed": True, "metadata.processed_at": datetime.utcnow()}}
        )

        logger.info(f"Extraction completed and stored for file: {original_filename}")

        return {
            "message": "File processed successfully",
            "extraction_id": str(insertion_id),
            "extraction": extraction_result
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing file {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")


@app.get(
    "/extraction/{file_id}",
    tags=["Extraction"],
    summary="Get extraction result",
    response_model=GetExtractionResponse,
    responses={404: {"model": ErrorResponse}}
)
async def get_extraction(file_id: str):
    try:
        obj_id = to_oid(file_id)
        extraction = await db.extractions.find_one({"file_id": obj_id})

        if not extraction:
            raise HTTPException(status_code=404, detail="Extraction not found")

        extraction["_id"] = str(extraction["_id"])
        extraction["file_id"] = str(extraction["file_id"])

        return {"extraction": extraction}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


import ollama


@app.post(
    "/structure/{file_id}",
    tags=["Extraction"],
    summary="LLM structuring to JSON",
    description="Uses Ollama (mistral) to convert extracted Markdown into strict JSON fields.",
    response_model=StructureResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def structure_extraction(file_id: str):
    try:
        obj_id = to_oid(file_id)

        extraction_doc = await db.extractions.find_one({"file_id": obj_id})
        if not extraction_doc or "extraction_data" not in extraction_doc:
            raise HTTPException(status_code=404, detail="No extracted data found for this file")

        markdown_text = extraction_doc["extraction_data"]["content"]

        prompt = f"""
Vous êtes un assistant intelligent chargé d’extraire les informations essentielles d’une facture ou d’un bon de livraison en format Markdown.

Le texte contient une ou plusieurs images, des sections, des tableaux et des montants. Analysez uniquement le contenu textuel et ignorez les images.

Retournez le résultat **au format JSON** contenant les champs suivants :

- "document_type" : Type de document (exemple : "Facture", "Bon de livraison", etc.)
- "currency" : Devise utilisée (exemple : "MAD", "EUR", etc.)
- "payment_method" : Méthode de paiement (exemple : "Virement Bancaire", "Espèces", etc.)
- "invoice_number" : Numéro de facture ou de bon de livraison
- "invoice_date" : Date du document (format : JJ.MM.AAAA)
- "due_date" : Date d’échéance (si elle existe)
- "total_amount" : Montant total TTC
- "tax_amount" : Montant total de la TVA
- "line_items" : Une liste d’objets représentant les lignes du tableau d’articles avec les champs suivants :
  - name : Nom ou désignation de l’article
  - quantity : Quantité
  - unit_price : Prix unitaire
  - packaging : Emballage
  - unit : Unité
  - total_ht : Total HT pour cette ligne

Si une information n’est pas présente, retournez une chaîne vide.

Voici le texte à analyser :
---
{markdown_text}
---
Rends uniquement un objet JSON valide avec les noms de champs exacts ci-dessus, sans texte explicatif, sans commentaire.
"""
        response = await asyncio.to_thread(
            ollama.chat,
            model="mistral",
            messages=[{"role": "user", "content": prompt}],
        )
        result = response["message"]["content"]

        try:
            structured_data = json.loads(result)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="Ollama returned invalid JSON")

        await db.structured_data.insert_one({
            "file_id": obj_id,
            "structured_json": structured_data,
            "created_at": datetime.utcnow()
        })

        return {
            "message": "Structured data extracted and saved.",
            "data": structured_data
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error structuring file {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Structuring failed: {str(e)}")


@app.put(
    "/update/{file_id}",
    tags=["Extraction"],
    summary="Update structured JSON",
    description="Save user-edited fields after validation.",
    response_model=UpdateResponse,
    responses={404: {"model": ErrorResponse}},
)
async def update_structured_data(
    file_id: str,
    updated_data: dict = Body(
        ...,
        example={
            "document_type": "Facture",
            "currency": "MAD",
            "invoice_number": "INV-2025-001",
            "invoice_date": "08.09.2025",
            "line_items": [{"name": "Article A", "quantity": 2, "unit_price": "50", "total_ht": "100"}],
            "total_amount": "120",
            "tax_amount": "20",
            "payment_method": "Virement",
        },
    ),
):
    try:
        obj_id = to_oid(file_id)
        result = await db.structured_data.update_one(
            {"file_id": obj_id},
            {"$set": {"structured_json": updated_data}}
        )
        await db.tasks.update_one(
            {"file_id": obj_id},
            {"$set": {"status": TaskStatus.VALIDATED, "validated_at": datetime.utcnow()}}
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Données structurées non trouvées")

        return {"message": "Données mises à jour avec succès"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur de mise à jour : {str(e)}")


@app.get(
    "/health",
    tags=["Health"],
    summary="Health check",
    response_model=HealthResponse
)
async def health():
    ok = {"mongo": False, "ollama": False}
    try:
        await db.command("ping")
        ok["mongo"] = True
    except Exception:
        pass
    try:
        _ = ollama.list()
        ok["ollama"] = True
    except Exception:
        pass
    return JSONResponse(ok, status_code=200 if all(ok.values()) else 503)


class TaskStatus(str, Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    STRUCTURING = "structuring"
    DONE = "done"
    VALIDATED = "validated"
    ERROR = "error"


async def _task_set(task_id: ObjectId, **patch):
    patch.setdefault("updated_at", datetime.utcnow())
    await tasks_col.update_one({"_id": task_id}, {"$set": patch})


async def _get_file_doc_or_404(file_id: str):
    obj_id = to_oid(file_id)
    file_doc = await db.fs.files.find_one({"_id": obj_id})
    if not file_doc:
        raise HTTPException(status_code=404, detail="File not found")
    return obj_id, file_doc


async def _run_task(task_id: ObjectId):
    task = await tasks_col.find_one({"_id": task_id})
    if not task:
        return

    file_id: ObjectId = task["file_id"]
    force_ocr: bool = task.get("force_ocr", False)
    do_structure: bool = task.get("do_structure", True)

    try:
        await _task_set(task_id, status=TaskStatus.EXTRACTING)

        grid_out = await fs.open_download_stream(file_id)
        file_content = await grid_out.read()
        file_doc = await db.fs.files.find_one({"_id": file_id})
        original_filename = file_doc.get("filename", "unknown")

        extraction_result = await extract_content_from_file(
            file_content, original_filename, force_ocr=force_ocr
        )

        extraction_doc = {
            "file_id": file_id,
            "original_filename": original_filename,
            "extraction_data": extraction_result,
            "created_at": datetime.utcnow(),
        }
        existing = await db.extractions.find_one({"file_id": file_id})
        if existing:
            await db.extractions.update_one({"_id": existing["_id"]}, {"$set": extraction_doc})
            extraction_id = existing["_id"]
        else:
            ins = await db.extractions.insert_one(extraction_doc)
            extraction_id = ins.inserted_id

        await db.fs.files.update_one(
            {"_id": file_id},
            {"$set": {"metadata.processed": True, "metadata.processed_at": datetime.utcnow()}},
        )

        await _task_set(task_id, status=TaskStatus.EXTRACTED, extraction_id=extraction_id)

        if do_structure:
            await _task_set(task_id, status=TaskStatus.STRUCTURING)

            markdown_text = extraction_result["content"]
            prompt = f"""
Vous êtes un assistant intelligent chargé d’extraire les informations essentielles d’une facture ou d’un bon de livraison en format Markdown.

Le texte contient une ou plusieurs images, des sections, des tableaux et des montants. Analysez uniquement le contenu textuel et ignorez les images.

Retournez le résultat **au format JSON** contenant les champs suivants :

- "document_type" : Type de document (exemple : "Facture", "Bon de livraison", etc.)
- "currency" : Devise utilisée (exemple : "MAD", "EUR", etc.)
- "payment_method" : Méthode de paiement (exemple : "Virement Bancaire", "Espèces", etc.)
- "invoice_number" : Numéro de facture ou de bon de livraison
- "invoice_date" : Date du document (format : JJ.MM.AAAA)
- "due_date" : Date d’échéance (si elle existe)
- "total_amount" : Montant total TTC
- "tax_amount" : Montant total de la TVA
- "line_items" : Une liste d’objets représentant les lignes du tableau d’articles avec les champs suivants :
  - name : Nom ou désignation de l’article
  - quantity : Quantité
  - unit_price : Prix unitaire
  - packaging : Emballage
  - unit : Unité
  - total_ht : Total HT pour cette ligne

Si une information n’est pas présente, retournez une chaîne vide.

Voici le texte à analyser :
---
{markdown_text}
---
Rends uniquement un objet JSON valide avec les noms de champs exacts ci-dessus, sans texte explicatif, sans commentaire.
"""
            response = await asyncio.to_thread(
                ollama.chat,
                model="mistral",
                messages=[{"role": "user", "content": prompt}],
            )
            result = response["message"]["content"]
            try:
                structured_data = json.loads(result)
            except json.JSONDecodeError:
                raise RuntimeError("Ollama returned invalid JSON")

            sdoc = {"file_id": file_id, "structured_json": structured_data, "created_at": datetime.utcnow()}
            existing_s = await db.structured_data.find_one({"file_id": file_id})
            if existing_s:
                await db.structured_data.update_one({"_id": existing_s["_id"]}, {"$set": sdoc})
                structured_id = existing_s["_id"]
            else:
                ins_s = await db.structured_data.insert_one(sdoc)
                structured_id = ins_s.inserted_id

            await _task_set(task_id, structured_id=structured_id)

        await _task_set(task_id, status=TaskStatus.DONE)

    except Exception as e:
        logger.exception(f"[task {task_id}] error: {e}")
        await _task_set(task_id, status=TaskStatus.ERROR, error=str(e))


@app.post(
    "/task/send",
    tags=["Tasks"],
    summary="Enqueue background task",
    description="Creates a task that runs extraction and structuring.",
    response_model=TaskSendResponse,
)
async def task_send(payload: TaskSendPayload = Body(
    ...,
    examples={
        "basic": {
            "summary": "Run extraction and structuring",
            "value": {"file_id": "64f1c0f1a4e2f3d6c0a1b2c3", "force_ocr": False, "do_structure": True}
        }
    }
)):
    file_obj_id, file_doc = await _get_file_doc_or_404(payload.file_id)
    filename_full = file_doc.get("filename", "") or ""
    filename = Path(filename_full).stem

    task_doc = {
        "file_id": file_obj_id,
        "filename": filename,           
        "force_ocr": payload.force_ocr,
        "do_structure": payload.do_structure,
        "client_token": payload.client_token,
        "status": TaskStatus.QUEUED,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    ins = await tasks_col.insert_one(task_doc)

    asyncio.create_task(_run_task(ins.inserted_id))

    return {"task_id": str(ins.inserted_id), "status": TaskStatus.QUEUED}


@app.get(
    "/task/state/{task_id}",
    tags=["Tasks"],
    summary="Get task state",
    response_model=TaskStateResponse,
    responses={404: {"model": ErrorResponse}}
)
async def task_state(task_id: str):
    try:
        tid = ObjectId(task_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid task ID")
    t = await tasks_col.find_one({"_id": tid})
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": task_id,
        "status": t.get("status"),
        "error": t.get("error"),
        "file_id": str(t.get("file_id")) if t.get("file_id") else None,
        "extraction_id": str(t.get("extraction_id")) if t.get("extraction_id") else None,
        "structured_id": str(t.get("structured_id")) if t.get("structured_id") else None,
        "updated_at": fmt(t.get("updated_at")),
    }


@app.get(
    "/task/text/{task_id}",
    tags=["Tasks"],
    summary="Get extracted text/markdown",
    response_model=TaskTextResponse,
    responses={404: {"model": ErrorResponse}}
)
async def task_text(task_id: str):
    try:
        tid = ObjectId(task_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid task ID")
    t = await tasks_col.find_one({"_id": tid})
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")

    file_id = t.get("file_id")
    if not file_id:
        raise HTTPException(status_code=404, detail="No file associated to task")

    ex = await db.extractions.find_one({"file_id": file_id})
    if not ex:
        raise HTTPException(status_code=404, detail="Extraction not available yet")

    return {
        "file_id": str(file_id),
        "content": ex["extraction_data"]["content"],
        "images_count": ex["extraction_data"].get("images_count", 0),
        "extraction_mode": ex["extraction_data"].get("extraction_mode"),
    }


@app.get(
    "/task/data/{task_id}",
    tags=["Tasks"],
    summary="Get structured invoice JSON",
    response_model=TaskDataResponse,
    responses={404: {"model": ErrorResponse}}
)
async def task_data(task_id: str):
    try:
        tid = ObjectId(task_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid task ID")
    t = await tasks_col.find_one({"_id": tid})
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")

    file_id = t.get("file_id")
    if not file_id:
        raise HTTPException(status_code=404, detail="No file associated to task")

    s = await db.structured_data.find_one({"file_id": file_id})
    if not s:
        raise HTTPException(status_code=404, detail="Structured data not available yet")
    return {"file_id": str(file_id), "data": s["structured_json"]}


@app.get(
    "/task/list",
    tags=["Tasks"],
    summary="List recent tasks",
    response_model=TaskListResponse
)
async def task_list(limit: int = Query(20, ge=1, description="Max items to return")):
    cur = tasks_col.find({}).sort("created_at", -1).limit(min(max(limit, 1), 100))
    tasks: List[Dict[str, Any]] = []
    async for t in cur:
        fid = t.get("file_id")
        file_doc = await db.fs.files.find_one({"_id": fid}) if fid else None

        if fid and not file_doc:
            await tasks_col.delete_one({"_id": t["_id"]})
            continue

        tasks.append({
            "task_id": str(t["_id"]),
            "status": t.get("status"),
            "file_id": str(fid) if fid else None,
            "filename": t.get("filename", "") or (file_doc or {}).get("filename", ""),
            "extraction_id": str(t.get("extraction_id")) if t.get("extraction_id") else None,
            "structured_id": str(t.get("structured_id")) if t.get("structured_id") else None,
            "created_at": fmt(t.get("created_at")),
            "updated_at": fmt(t.get("updated_at")),
            "client_token": t.get("client_token"),
        })
    return {"tasks": tasks}


@app.post(
    "/task/validate/{task_id}",
    tags=["Tasks"],
    summary="Mark task as validated",
    response_model=TaskStateResponse,
    responses={404: {"model": ErrorResponse}}
)
async def task_validate(task_id: str):
    try:
        tid = ObjectId(task_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid task ID")

    t = await tasks_col.find_one({"_id": tid})
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")

    await _task_set(
        tid,
        status=TaskStatus.VALIDATED,
        validated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
    )
    t = await tasks_col.find_one({"_id": tid})
    return {
        "task_id": str(t["_id"]),
        "status": t.get("status"),
        "error": t.get("error"),
        "file_id": str(t.get("file_id")) if t.get("file_id") else None,
        "extraction_id": str(t.get("extraction_id")) if t.get("extraction_id") else None,
        "structured_id": str(t.get("structured_id")) if t.get("structured_id") else None,
        "updated_at": fmt(t.get("updated_at")),
    }


@app.delete(
    "/files/{file_id}",
    status_code=204,
    tags=["Files"],
    summary="Delete file and related data",
    description="Hard-deletes GridFS file, tasks, extractions, and structured data.",
    responses={404: {"model": ErrorResponse}, 204: {"description": "Deleted"}},
)
async def delete_file(file_id: str):
    try:
        oid = to_oid(file_id)
        file_doc = await db.fs.files.find_one({"_id": oid})
        if not file_doc:
            raise HTTPException(status_code=404, detail="File not found")

        await fs.delete(oid)

        ex_oid = await db.extractions.delete_many({"file_id": oid})
        ex_str = await db.extractions.delete_many({"file_id": file_id})

        sd_oid = await db.structured_data.delete_many({"file_id": oid})
        sd_str = await db.structured_data.delete_many({"file_id": file_id})

        tk_oid = await tasks_col.delete_many({"file_id": oid})
        tk_str = await tasks_col.delete_many({"file_id": file_id})

        pd = await db.processed_data.delete_many({"original_file_id": file_id})

        logger.info(
            "DELETE %s -> tasks(oid:%d,str:%d) extr(oid:%d,str:%d) struct(oid:%d,str:%d) legacy:%d",
            file_id, tk_oid.deleted_count, tk_str.deleted_count,
            ex_oid.deleted_count, ex_str.deleted_count,
            sd_oid.deleted_count, sd_str.deleted_count,
            pd.deleted_count
        )
        return
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("DELETE ERROR")
        raise HTTPException(status_code=500, detail=str(e))

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=OPENAPI_TAGS,
    )
    openapi_schema["servers"] = [
        {"url": "http://localhost:8000", "description": "Local"},
    ]
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

@app.get("/openapi.yaml", include_in_schema=False)
def openapi_yaml():
    import yaml
    return Response(yaml.safe_dump(app.openapi()), media_type="application/yaml")

