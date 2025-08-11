import os
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GLOG_minloglevel"] = "2"
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket 
from datetime import datetime
import tempfile
import json
import time
import base64
from io import BytesIO
from pathlib import Path
from PIL import Image
from bson import ObjectId
from fastapi.responses import JSONResponse
from pymongo import MongoClient
from bson.json_util import dumps

# Marker imports (your extraction service)
from marker.config.parser import ConfigParser
from marker.config.printer import CustomClickPrinter
from marker.logger import configure_logging, get_logger
from marker.models import create_model_dict

# Configure logging for marker
configure_logging()
logger = get_logger()

app = FastAPI()

# CORS (allow frontend requests)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Works for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connect to MongoDB
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
client = AsyncIOMotorClient(MONGODB_URI)
db = client.fileuploads  # your DB name
fs = AsyncIOMotorGridFSBucket(db)

# Initialize marker models once (expensive operation)
marker_models = None

async def get_marker_models():
    global marker_models
    if marker_models is None:
        logger.info("Loading marker models...")
        marker_models = create_model_dict()
        logger.info("Marker models loaded successfully")
    return marker_models

def image_to_base64(img):
    """Convert PIL Image to base64 string"""
    if hasattr(img, 'save'):  # PIL Image object
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        img_str = base64.b64encode(buffer.getvalue()).decode()
        return {
            "format": "PNG",
            "data": img_str,
            "size": img.size if hasattr(img, 'size') else None
        }
    elif isinstance(img, str):  # If it's already a path or string
        return {"path": img}
    else:
        return {"error": "Unsupported image format"}

def extract_markdown_content(rendered_obj):
    """Extract just the markdown content from rendered object"""
    if hasattr(rendered_obj, 'text'):
        return rendered_obj.text
    elif hasattr(rendered_obj, 'markdown'):
        return rendered_obj.markdown
    else:
        # Parse the string representation to extract markdown content
        rendered_str = str(rendered_obj)
        if rendered_str.startswith('markdown="') and '" images=' in rendered_str:
            # Extract content between markdown=" and " images=
            start_idx = len('markdown="')
            end_idx = rendered_str.find('" images=')
            if end_idx != -1:
                markdown_content = rendered_str[start_idx:end_idx]
                # Unescape common escape sequences
                markdown_content = markdown_content.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
                return markdown_content
        return rendered_str

async def extract_content_from_file(file_content: bytes, original_filename: str):
    """Extract content from file using marker"""
    
    try:
        # Get marker models
        models = await get_marker_models()
        
        # Create temporary file with original extension
        file_path = Path(original_filename)
        suffix = file_path.suffix if file_path.suffix else '.pdf'
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(file_content)
            temp_file_path = temp_file.name
        
        start_time = time.time()
        
        try:
            # Simple approach - try direct conversion first
            try:
                # Initialize marker components with default config
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
            except Exception as config_error:
                logger.warning(f"Config setup failed, trying simpler approach: {config_error}")
                # Fallback to minimal setup
                from marker.convert import convert_single_pdf
                # This is a simplified conversion - adjust based on your marker version
                result = convert_single_pdf(temp_file_path, models)
                
                # Handle simple result format
                if isinstance(result, tuple) and len(result) >= 2:
                    content = result[0] if isinstance(result[0], str) else str(result[0])
                    images = result[1] if len(result) > 1 else {}
                else:
                    content = str(result)
                    images = {}
                
                # Create a simple rendered object
                class SimpleRendered:
                    def __init__(self, content, images):
                        self.text = content
                        self.markdown = content
                        self.images = images
                
                rendered = SimpleRendered(content, images)
            
            # Convert the file - try the configured converter first, then fallback
            try:
                rendered = converter(temp_file_path)
            except Exception as conv_error:
                logger.warning(f"Converter failed, using fallback result: {conv_error}")
                # rendered should be set from the fallback above
                pass
            
            # Extract clean content
            content = extract_markdown_content(rendered)
            
            # Process images if they exist
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
                            serialized_images.append({
                                "index": i,
                                "error": str(e)
                            })
            
            # Create result structure
            result_data = {
                "file_name": file_path.stem,
                "original_path": original_filename,
                "content": content,
                "images": serialized_images,
                "images_count": len(serialized_images),
                "extraction_timestamp": time.time(),
                "processing_time": time.time() - start_time
            }
            
            return result_data
            
        finally:
            # Clean up temporary file
            try:
                os.unlink(temp_file_path)
            except:
                pass
                
    except Exception as e:
        logger.error(f"Error during extraction: {e}")
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        print(f"Receiving file: {file.filename}, Content-Type: {file.content_type}")
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file uploaded")
        metadata = {
            "upload_date": datetime.utcnow(),
            "content_type": file.content_type,
            "original_filename": file.filename,
            "processed": False  # Track processing status
        }
        file_id = await fs.upload_from_stream(file.filename, file.file, metadata=metadata)
        print(f"Upload successful, ID: {file_id}")
        return {"message": "File uploaded successfully", "file_id": str(file_id)}
    except Exception as e:
        print("Error during upload:", str(e))
        raise HTTPException(status_code=500, detail="Failed to upload file")

@app.get("/files")
async def list_files():
    try:
        cursor = db.fs.files.find({})
        files = await cursor.to_list(length=100)
        if not files:
            return {"files": []}
        # Format files for JSON response
        result = []
        for file in files:
            result.append({
                "id": str(file["_id"]),
                "filename": file.get("filename"),
                "upload_date": file.get("metadata", {}).get("upload_date"),
                "content_type": file.get("metadata", {}).get("content_type"),
                "length": file.get("length"),
                "processed": file.get("metadata", {}).get("processed", False)
            })
        return {"files": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/process/{file_id}")
async def process_file(file_id: str):
    try:
        # Validate ObjectId
        try:
            obj_id = ObjectId(file_id)
        except:
            raise HTTPException(status_code=400, detail="Invalid file ID")
        
        # Check if file exists
        file_doc = await db.fs.files.find_one({"_id": obj_id})
        if not file_doc:
            raise HTTPException(status_code=404, detail="File not found")
        
        # Check if already processed
        if file_doc.get("metadata", {}).get("processed", False):
            # Return existing extraction if available
            existing_extraction = await db.extractions.find_one({"file_id": obj_id})
            if existing_extraction:
                # Convert ObjectId to string for JSON serialization
                existing_extraction["_id"] = str(existing_extraction["_id"])
                existing_extraction["file_id"] = str(existing_extraction["file_id"])
                return {"message": "File already processed", "extraction": existing_extraction}
        
        # Download file content
        grid_out = await fs.open_download_stream(obj_id)
        file_content = await grid_out.read()
        
        original_filename = file_doc.get("filename", "unknown")
        
        # Extract content
        logger.info(f"Starting extraction for file: {original_filename}")
        extraction_result = await extract_content_from_file(file_content, original_filename)
        
        # Store extraction result in database
        extraction_doc = {
            "file_id": obj_id,
            "original_filename": original_filename,
            "extraction_data": extraction_result,
            "created_at": datetime.utcnow()
        }
        
        insertion_result = await db.extractions.insert_one(extraction_doc)
        
        # Update file metadata to mark as processed
        await db.fs.files.update_one(
            {"_id": obj_id},
            {"$set": {"metadata.processed": True, "metadata.processed_at": datetime.utcnow()}}
        )
        
        logger.info(f"Extraction completed and stored for file: {original_filename}")
        
        return {
            "message": "File processed successfully",
            "extraction_id": str(insertion_result.inserted_id),
            "extraction": extraction_result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing file {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

@app.get("/extraction/{file_id}")
async def get_extraction(file_id: str):
    """Get extraction result for a specific file"""
    try:
        obj_id = ObjectId(file_id)
        extraction = await db.extractions.find_one({"file_id": obj_id})
        
        if not extraction:
            raise HTTPException(status_code=404, detail="Extraction not found")
        
        # Convert ObjectId to string for JSON serialization
        extraction["_id"] = str(extraction["_id"])
        extraction["file_id"] = str(extraction["file_id"])
        
        return {"extraction": extraction}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from fastapi import status

@app.delete("/files/{file_id}", status_code=204)
async def delete_file(file_id: str):
    try:
        oid = ObjectId(file_id)

        file_doc = await db.fs.files.find_one({"_id": oid})
        if not file_doc:
            raise HTTPException(status_code=404, detail="File not found")

        await fs.delete(oid)  # This line may raise if the file has no chunks

        await db.processed_data.delete_many({"original_file_id": file_id})
        return
    except Exception as e:
        print("DELETE ERROR:", str(e))  # <== TEMPORARY LOG
        raise HTTPException(status_code=500, detail=str(e))

import ollama

@app.post("/structure/{file_id}")
async def structure_extraction(file_id: str):
    try:
        # Validate ObjectId
        try:
            obj_id = ObjectId(file_id)
        except:
            raise HTTPException(status_code=400, detail="Invalid file ID")

        # Find extracted Markdown from previous step
        extraction_doc = await db.extractions.find_one({"file_id": obj_id})
        if not extraction_doc or "extraction_data" not in extraction_doc:
            raise HTTPException(status_code=404, detail="No extracted data found for this file")

        markdown_text = extraction_doc["extraction_data"]["content"]


        # Format prompt
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


        # print(markdown_text)
        response = ollama.chat(model="mistral", messages=[
            {"role": "user", "content": prompt}
        ])
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
            "message": "✅ Structured data extracted and saved.",
            "data": structured_data
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error structuring file {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Structuring failed: {str(e)}")
    
from fastapi import Body

@app.put("/update/{file_id}")
async def update_structured_data(file_id: str, updated_data: dict = Body(...)):
    try:
        obj_id = ObjectId(file_id)
        result = await db.structured_data.update_one(
            {"file_id": obj_id},
            {"$set": {"structured_json": updated_data}}
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Données structurées non trouvées")

        return {"message": "✅ Données mises à jour avec succès"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur de mise à jour : {str(e)}")
