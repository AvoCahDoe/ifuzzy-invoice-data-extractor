from fastapi import FastAPI, UploadFile, File, HTTPException, Body, Form, Query
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
from rule_extractor import extract_fields_rulebased

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

# Service URL from Docker Compose (RapidOCR is the only extraction engine)
RAPIDOCR_SERVICE_URL = os.getenv("RAPIDOCR_SERVICE_URL", "http://rapidocr_service:8005")

ENGINE_URLS = {
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

def clean_markdown(text) -> str:
    """Remove excessive whitespace and control characters from OCR output."""
    if not isinstance(text, str) or not text:
        return ""
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C" or ch in "\n\t\r")
    return text.strip()


def _parse_money(s) -> float | None:
    """Parse money string (EU or US format) to float, stripping currency suffixes."""
    if not s: return None
    s = str(s).strip()
    # Strip trailing currency codes/symbols
    s = re.sub(r'(?i)\s*(MAD|EUR|USD|GBP|DH|CHF|TND)\s*$', '', s).strip()
    s = re.sub(r'[€$£]', '', s).strip()
    if not s: return None
    # EU format: comma is decimal separator (1.234,56)
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(" ", "").replace(",", ".")
        else:
            s = s.replace(",", "").replace(" ", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "").replace(" ", "")
    else:
        s = s.replace(" ", "")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _normalize_date(s: str) -> str | None:
    """Normalize date string to YYYY-MM-DD."""
    if not s or not isinstance(s, str): return None
    s = s.strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m: return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        y = "20" + y if len(y) == 2 else y
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", s)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        y = "20" + y if len(y) == 2 else y
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    months = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
              "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
              "janv": "01", "fév": "02", "fev": "02", "mars": "03", "avr": "04", "mai": "05",
              "juin": "06", "juil": "07", "août": "08", "aout": "08", "sept": "09", "octo": "10",
              "nov": "11", "déc": "12", "dece": "12"}
    m = re.search(r"(\d{1,2})[\s,]+([A-Za-zÀ-ÿ]+)\.?\s+(\d{4})", s, re.I) or re.search(
        r"([A-Za-zÀ-ÿ]+)\.?\s+(\d{1,2})[\s,]+(\d{4})", s, re.I)
    if m:
        g = m.groups()
        if g[0].isdigit():
            d, mon, y = g[0], g[1][:4].lower(), g[2]
        else:
            mon, d, y = g[0][:4].lower(), g[1], g[2]
        mo = months.get(mon) or next((v for k, v in months.items() if mon.startswith(k)), None)
        if mo: return f"{y}-{mo}-{d.zfill(2)}"
    return s if re.match(r"\d{4}-\d{2}-\d{2}", s) else None


def extract_fields_hardcoded(ocr_text: str) -> dict:
    """Extract regex-reliable fields from OCR. Bilingual EN/FR. Handles no-space OCR."""
    ctx = (ocr_text or "")[:25000]
    ctx_no_tables = "\n".join(
        line for line in ctx.splitlines()
        if "|" not in line or not re.search(r'^\s*\|.*\|\s*$', line)
    )
    out = {
        "document_type": None, "invoice_number": None, "date": None, "due_date": None,
        "vendor_tax_id": None, "subtotal": None, "tax_amount": None, "total_amount": None,
        "currency": None,
    }
    # Money: handle amounts optionally suffixed with currency (no space), e.g. "3,756.06MAD"
    money_pat = r'((?:\d{1,3}(?:[,. ]\d{3})*[,.]\d{2}|\d+[,.]\d{2}|\d+)(?:\s*(?:MAD|EUR|USD|GBP|DH))?)'
    money_prefix = r'[\s:\t]*'
    money_opt = r'[\$€£]?'
    # Optional parenthetical between label and value, e.g. "Tax (20%):" or "VAT [10%]"
    opt_paren = r'\s*(?:\([^)]*\)|\[[^\]]*\])?\s*'

    if re.search(r'\$|USD', ctx, re.I): out["currency"] = "USD"
    elif re.search(r'MAD|Dirham|DH\b', ctx, re.I): out["currency"] = "MAD"
    elif re.search(r'€|EUR', ctx, re.I): out["currency"] = "EUR"

    # Document type — prioritise more specific types first
    if re.search(r'(?i)(?:Credit\s*Note|CreditNote|Avoir|Note\s*de\s*cr[eé]dit)', ctx): out["document_type"] = "Credit Note"
    elif re.search(r'(?i)(?:Delivery\s*[Oo]rder|Deliveryorder|Bon\s*de\s*[Ll]ivraison|BondelivraisonN)', ctx): out["document_type"] = "Delivery Order"
    elif re.search(r'(?i)(?:Receipt|Re[çc]u|Quittance|Bon\s*de\s*commande)', ctx): out["document_type"] = "Receipt"
    elif re.search(r'(?i)(?:Invoice|Facture)', ctx): out["document_type"] = "Invoice"

    # Invoice number — prefer FactureN°:, InvoiceN°:, then fallback
    # Handle no-space: FactureN°:FAV_2026, Invoice no:61356, FactureN°:Fac_2026
    inv_patterns = [
        # Explicit Facture/Invoice number labels with optional spaces
        r'(?is)Invoice\s*(?:no\.?|#|number|n[°ºo]?)\s*[:\-]?\s*\n?\s*([A-Za-z0-9][A-Za-z0-9_\-/]{2,})',
        r'(?is)Facture\s*(?:no\.?|#|number|n[°ºo]?)\s*[:\-]?\s*\n?\s*([A-Za-z0-9][A-Za-z0-9_\-/]{2,})',
        r'(?i)Facture\s*N[°º°o]?\s*[:\-]?\s*([A-Za-z][A-Za-z0-9_\-]+)',
        r'(?i)Invoice\s*N[°oo]?[o\.]?\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9_\-]+)',
        r'(?i)Invoice\s*(?:no\.?|#|number)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9_\-]+)',
        r'(?i)N[°º]\s*(?:Facture|facture)\s*[:\-]?\s*([A-Za-z0-9_\-]+)',
        r'(?i)Ref\s*[:\-#]?\s*([A-Za-z][A-Za-z0-9_\-]+)',
    ]
    banned_invoice_vals = {"number", "umber", "invoice", "facture", "date", "due"}
    for pat in inv_patterns:
        m = re.search(pat, ctx_no_tables)
        if m:
            candidate = m.group(1).strip()
            candidate_l = candidate.lower()
            # Reject if it's just a delivery-order number prefix
            if (
                len(candidate) >= 3
                and candidate_l not in banned_invoice_vals
                and not re.match(r'(?i)^(BL|BL_|Bon)$', candidate)
            ):
                out["invoice_number"] = candidate
                break
        if not out["invoice_number"]:
            # Last resort: any labeled number
            m2 = re.search(r'(?i)(?:Invoiceno|InvoiceN|FactureN)[°oo]?\s*[:\-]?\s*([A-Za-z0-9_\-/]{3,})', ctx_no_tables)
            if m2:
                c2 = m2.group(1).strip()
                if c2.lower() not in banned_invoice_vals:
                    out["invoice_number"] = c2

    date_labels = r'(?:Invoice\s*date|Date\s*of\s*issue|Date\s*d\'[eé]mission|Date\s*de\s*facture|Date\s*de\s*facturation|Factur[eé]\s*le|[eÉ]mis\s*le|Date|Le\s*:|Date\s*:)'
    date_val = r'([A-Za-zÀ-ÿ]{3,12}\.?\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}\.\d{1,2}\.\d{2,4})'
    dates = re.findall(rf'(?i){date_labels}[\s:]*{date_val}', ctx_no_tables)
    for d in dates:
        normalized = _normalize_date(d.strip())
        if normalized:
            out["date"] = normalized
            break

    due_labels = r'(?:Due\s*date|[Éé]ch[eé]ance|Date\s*d\'[eé]ch[eé]ance|Payment\s*due|[Éé]ch[eé]ance\s*de\s*paiement|[Àa]\s*payer\s*avant|Payable\s*avant|Date\s*limite|Payable\s*le)'
    due = re.findall(rf'(?i){due_labels}[\s:]*([A-Za-z0-9/\-\.\s,]+?)(?:\n|$)', ctx_no_tables)
    for d in due:
        normalized = _normalize_date((d or "").strip())
        if normalized:
            out["due_date"] = normalized
            break

    ice = re.search(r'(?i)ICE\s*[:\-]?\s*(\d{15})\b', ctx_no_tables)
    if ice: out["vendor_tax_id"] = ice.group(1).strip()
    else:
        tax = re.findall(r'(?i)(?:ICE|VAT\s*ID|VATID|TVA|SIRET|SIREN|Tax\s*Id|TaxId|N[°°]\s*TVA)\s*[:\-]?\s*([0-9A-Z\s/\-\.]{6,30})', ctx_no_tables)
        if tax:
            candidate = tax[0].strip().rstrip(".")
            if len(candidate) >= 6 and candidate.lower() not in {"number", "invoice", "tax id"}:
                out["vendor_tax_id"] = candidate

    # Total amount — handle "Total :\t2,890.85 MAD", "Total Due:\n$2,400.00", pipe tables
    total_labels = (
        r'(?<!Sub)(?:'
        r'Grand\s*Total|Invoice\s*Total|Total\s*Amount|Net\s*Total|'
        r'Bill\s*Amount|Montant\s*Total|Montant\s*[àa]\s*payer|'
        r'Total\s*(?:Due|TTC|g[eé]n[eé]ral|[àa]\s*payer|amount)?|'
        r'Net\s*[àa]\s*payer|Montant\s*TTC|NET\s*PAYABLE|'
        r'Amount\s*due|Balance\s*due|Montant\s*d[uû]'
        r')'
    )
    totals = re.findall(rf'(?i){total_labels}' + opt_paren + money_prefix + money_opt + money_pat, ctx)
    totals += re.findall(rf'(?is){total_labels}' + opt_paren + r'\s*[:\-]?\s*\n+\s*' + money_opt + money_pat, ctx)
    totals += re.findall(rf'(?i)\|\s*{total_labels}\s*\|\s*' + money_opt + money_pat + r'\s*\|', ctx)
    # Pipe table with multiple columns: | Total | | $subtotal | $tax | $total | — capture last (Grossworth)
    totals += re.findall(rf'(?i)\|\s*Total\s*\|.*\|\s*' + money_opt + money_pat + r'\s*\|', ctx)
    # Inline table cell: "Total:1,584.09MAD" or "Total : 11.00MAD" (Invorate/no-separator format)
    totals += re.findall(rf'(?i)Total\s*:\s*({money_opt}{money_pat})', ctx)
    # Same row with three amounts: subtotal, tax, total — set all at once
    triple = re.search(rf'(?i)\|\s*Total\s*\|.*\|\s*' + money_opt + money_pat + r'\s*\|\s*' + money_opt + money_pat + r'\s*\|\s*' + money_opt + money_pat + r'\s*\|', ctx)
    if triple:
        g = triple.groups()
        if len(g) >= 3 and not out["subtotal"]: out["subtotal"] = _parse_money(g[0])
        if len(g) >= 3 and not out["tax_amount"]: out["tax_amount"] = _parse_money(g[1])
        if len(g) >= 3 and not out["total_amount"]: out["total_amount"] = _parse_money(g[2])
    if totals:
        last = totals[-1]
        val = last[-1] if isinstance(last, tuple) else last
        out["total_amount"] = out["total_amount"] or _parse_money(str(val).strip())

    subtotal_labels = (
        r'(?:'
        r'Sub[\s\-]?total|S\.Total|Sous[\-\s]?total|'
        r'Total\s*HT|Total\s*partiel|Montant\s*HT|'
        r'Hors\s*Taxe|Networth|Net\s*Amount|Gross\s*Amount|'
        r'Amount\s*Before\s*Tax|Pre[\-\s]?tax'
        r')'
    )
    subtotals = re.findall(rf'(?i){subtotal_labels}' + opt_paren + money_prefix + money_opt + money_pat, ctx)
    subtotals += re.findall(rf'(?is){subtotal_labels}' + opt_paren + r'\s*[:\-]?\s*\n+\s*' + money_opt + money_pat, ctx)
    subtotals += re.findall(rf'(?i)\|\s*{subtotal_labels}\s*\|\s*' + money_opt + money_pat + r'\s*\|', ctx)
    # Inline table cell: "Subtotal:1,329.05MAD" (Invorate format)
    subtotals += re.findall(rf'(?i)Subtotal\s*:\s*({money_opt}{money_pat})', ctx)
    if subtotals and out["subtotal"] is None:
        first = subtotals[0]
        val = first[-1] if isinstance(first, tuple) else first
        out["subtotal"] = _parse_money(str(val).strip())

    tax_labels = (
        r'(?:'
        r'TVA|Tax(?:e)?(?:\s*[\d\.]+%)?|Sales\s*Tax(?:\s*[\d\.]+%)?|'
        r'Montant\s*TVA|Total\s*TVA|'
        r'VAT(?:\s*\[[\d\.]+%\])?|'
        r'HST(?:\s*[\d\.]+%)?|GST(?:\s*[\d\.]+%)?|PST(?:\s*[\d\.]+%)?|'
        r'IVA'
        r')'
    )
    tax_vals = re.findall(rf'(?i){tax_labels}' + opt_paren + money_prefix + money_opt + money_pat, ctx)
    tax_vals += re.findall(rf'(?is){tax_labels}' + opt_paren + r'\s*[:\-]?\s*\n+\s*' + money_opt + money_pat, ctx)
    tax_vals += re.findall(rf'(?i)\|\s*{tax_labels}\s*\|\s*' + money_opt + money_pat + r'\s*\|', ctx)
    if tax_vals and out["tax_amount"] is None:
        last = tax_vals[-1]
        val = last[-1] if isinstance(last, tuple) else last
        out["tax_amount"] = _parse_money(str(val).strip())

    # Ensure tax_amount is always numeric (float) or None
    if out["tax_amount"] is not None and not isinstance(out["tax_amount"], (int, float)):
        out["tax_amount"] = _parse_money(str(out["tax_amount"])) if str(out["tax_amount"]).strip() else None

    return out


_SUMMARY_KEYWORDS = ["total", "subtotal", "tax", "tva", "rate", "amount due", "balance", "remise", "discount", "shipping", "frais", "gross"]


def _filter_line_items(items: list, total_amount=None) -> list:
    """
    Remove summary/header rows from line items.
    Works independently of total_amount accuracy.
    """
    filtered = []
    for item in items:
        desc = (item.get("description") or "").strip()
        desc_lower = desc.lower()
        qty = item.get("quantity") or 0.0
        item_total = item.get("total_price") or 0.0
        unit_price = item.get("unit_price") or 0.0

        # Drop rows with null/empty description
        if not desc or desc_lower in ("null", "none", ""):
            continue

        # Drop rows whose description is purely a number (leaked position column)
        if re.match(r"^\d+\.?$", desc_lower.strip()):
            continue

        # Check if it looks like a summary row
        is_summary = any(k in desc_lower for k in _SUMMARY_KEYWORDS)

        if is_summary:
            # Keep if it has real product data (qty > 0 AND unit_price > 0)
            has_real_data = qty > 0 and unit_price > 0
            if not has_real_data:
                continue
            # Also drop if the total matches the invoice total (it IS the grand total row)
            if total_amount and total_amount > 0 and abs(item_total - total_amount) < 0.01:
                continue

        # Drop header-like rows: description matches column header names
        header_like = any(re.match(rf'(?i)^{h}s?$', desc_lower.strip()) for h in [
            "description", "désignation", "designation", "qty", "quantity", "unit price",
            "unit_price", "total", "montant", "amount", "item", "article",
        ])
        if header_like:
            continue

        filtered.append(item)
    return filtered




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

async def call_extraction_service(engine: str, filename: str, file_content: bytes) -> dict:
    """
    Call the external OCR engine (RapidOCR) to convert the file to markdown.

    Parameters
    ----------
    engine : str
        Logical engine name; must exist in `ENGINE_URLS` (currently only `rapidocr`).
    filename : str
        Original filename, forwarded to the OCR service for logging.
    file_content : bytes
        Raw bytes of the uploaded file read from GridFS.

    Returns
    -------
    dict
        Parsed JSON response from the OCR service. It typically contains:
        `content` (markdown text), `blocks` (layout/word boxes), `images`
        (base64 screenshots) and `avg_visual_confidence`.

    Raises
    ------
    ValueError
        If the engine name is unknown.
    Exception
        If the OCR service keeps failing after all retry attempts.
    """
    url = ENGINE_URLS.get(engine)
    if not url:
        raise ValueError(f"Unknown engine: {engine}. Valid: {list(ENGINE_URLS)}")

    files = {"file": (filename, file_content, "application/octet-stream")}
    data  = {"force_ocr": "false"}

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
        input_type = "other"
        if ext == ".pdf":
            if extraction_mode == "fitz_digital_pdf":
                input_type = "pdf_digital"
            elif extraction_mode in ("onnx_hybrid_pdf", None):
                input_type = "pdf_scanned"
        elif ext in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".gif"):
            input_type = "image"

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
            
            structured_data = dict(hardcoded)
            structured_data.update({k: v for k, v in rule_output.items() if v is not None})
            if rule_output.get("line_items"):
                structured_data["line_items"] = rule_output["line_items"]

            # Normalize line-item numbers from Phase 1
            for li in structured_data.get("line_items", []):
                li["quantity"] = _parse_money(li.get("quantity")) if li.get("quantity") is not None else None
                li["unit_price"] = _parse_money(li.get("unit_price")) if li.get("unit_price") is not None else None
                li["total_price"] = _parse_money(li.get("total_price")) if li.get("total_price") is not None else None
            structured_data["line_items"] = _filter_line_items(
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

