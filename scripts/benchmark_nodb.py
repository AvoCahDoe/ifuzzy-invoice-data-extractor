#!/usr/bin/env python3
import os, sys, json, time, pathlib, tempfile, asyncio, logging, traceback
from io import BytesIO as _BytesIO

# ---------------- Logging setup ----------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("bench-nodb")

# ---------------- Config (env) ----------------
DATA_DIR   = os.getenv("DATA_DIR", "/app/test_invoices")
OUT_DIR    = os.getenv("OUT_DIR", "/app/out")
FORCE_OCR  = os.getenv("FORCE_OCR", "false").lower() == "true"
MODEL_NAME = os.getenv("INVOICE_MODEL_NAME", os.getenv("MODEL_NAME", "mistral"))

# Tame thread usage
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_MAX_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

# ---------------- PDF helpers ----------------
from pypdf import PdfReader

def _pdf_is_readable(pdf_bytes: bytes, min_chars: int = 50, sample_pages: int = 1) -> bool:
    try:
        reader = PdfReader(_BytesIO(pdf_bytes))
        if len(reader.pages) == 0:
            return False
        pages_to_sample = min(len(reader.pages), sample_pages)
        char_count = 0
        for i in range(pages_to_sample):
            txt = reader.pages[i].extract_text() or ""
            char_count += len(txt.strip())
        return char_count >= min_chars
    except Exception:
        return False

def _pdf_extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(_BytesIO(pdf_bytes))
    return "\n\n".join((p.extract_text() or "") for p in reader.pages).strip()

def extract_markdown_content(rendered_obj):
    if hasattr(rendered_obj, "text"): return rendered_obj.text
    if hasattr(rendered_obj, "markdown"): return rendered_obj.markdown
    s = str(rendered_obj)
    if s.startswith('markdown="') and '" images=' in s:
        start_idx = len('markdown="'); end_idx = s.find('" images=')
        if end_idx != -1:
            md = s[start_idx:end_idx]
            return md.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
    return s

# ---------------- Marker (OCR) ----------------
from marker.config.parser import ConfigParser
from marker.models import create_model_dict
from marker.logger import configure_logging, get_logger

configure_logging()
marker_log = get_logger()
_marker_models = None

async def get_marker_models():
    global _marker_models
    if _marker_models is None:
        log.info("[LOAD] Loading Marker models…")
        _marker_models = create_model_dict()
        log.info("[LOAD] Marker models ready")
    return _marker_models

async def extract_content_from_bytes(file_content: bytes, original_filename: str, force_ocr: bool = False):
    models = await get_marker_models()
    suffix = pathlib.Path(original_filename).suffix.lower() or ".pdf"

    t0 = time.time()
    mode = "marker_ocr"

    # Fast text-layer path for PDFs
    if not force_ocr and suffix == ".pdf" and _pdf_is_readable(file_content):
        try:
            txt = _pdf_extract_text(file_content)
            dt = time.time() - t0
            log.info(f"[EXTRACT] {original_filename} | mode=pdf_text_layer | chars={len(txt)} | {dt:.2f}s")
            return dict(
                file_name=pathlib.Path(original_filename).stem,
                original_path=original_filename,
                content=txt,
                images=[], images_count=0,
                extraction_timestamp=time.time(),
                processing_time=dt,
                extraction_mode="pdf_text_layer",
            )
        except Exception as e:
            log.warning(f"[EXTRACT] {original_filename} | text-layer failed → fallback OCR | err={e}")

    # OCR via Marker
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_content); tmp_path = tmp.name
    try:
        try:
            cfg = dict(
                output_format="markdown",
                extract_images=True,
                batch_multiplier=1,
                max_pages=None,
                langs=None,
                output_dir=None,
                debug=False,
            )
            config_parser = ConfigParser(cfg)
            converter_cls = config_parser.get_converter_cls()
            converter = converter_cls(
                config=config_parser.generate_config_dict(),
                artifact_dict=models,
                processor_list=config_parser.get_processors(),
                renderer=config_parser.get_renderer(),
                llm_service=config_parser.get_llm_service(),
            )
            rendered = converter(tmp_path)
        except Exception as e:
            log.warning(f"[EXTRACT] {original_filename} | Marker config failed, trying fallback | err={e}")
            from marker.convert import convert_single_pdf
            r = convert_single_pdf(tmp_path, models)
            content = r[0] if isinstance(r, tuple) and len(r) >= 1 else str(r)
            class _R: 
                def __init__(self,c): self.text=c; self.markdown=c
            rendered = _R(content)

        md = extract_markdown_content(rendered) or ""
        dt = time.time() - t0
        log.info(f"[EXTRACT] {original_filename} | mode={mode} | chars={len(md)} | {dt:.2f}s")
        return dict(
            file_name=pathlib.Path(original_filename).stem,
            original_path=original_filename,
            content=md,
            images=[], images_count=0,
            extraction_timestamp=time.time(),
            processing_time=dt,
            extraction_mode=mode,
        )
    finally:
        try: os.unlink(tmp_path)
        except Exception: pass

# ---------------- LLM structuring (Ollama) ----------------
import ollama

STRUCTURE_PROMPT_TEMPLATE = """Vous êtes un assistant intelligent chargé d’extraire les informations essentielles d’une facture ou d’un bon de livraison en format Markdown.

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

def is_invoice(path: pathlib.Path) -> bool:
    return path.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg")

async def run():
    data_dir = pathlib.Path(DATA_DIR)
    out_dir  = pathlib.Path(OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted([p for p in data_dir.glob("**/*") if p.is_file() and is_invoice(p)])
    if not files:
        log.warning(f"[SETUP] No files found in {data_dir}")
        return

    log.info(f"[SETUP] Starting bench | files={len(files)} | force_ocr={FORCE_OCR} | model={MODEL_NAME}")
    last_payload = None
    totals = {"process_s":0.0, "structure_s":0.0, "total_s":0.0, "ok":0, "err":0}

    for f in files:
        try:
            log.info(f"[FILE] {f.name} | size={f.stat().st_size} bytes")
            b = f.read_bytes()

            # Extract
            t0 = time.time()
            extraction = await extract_content_from_bytes(b, f.name, force_ocr=FORCE_OCR)
            t1 = time.time()

            # Structure
            markdown_text = extraction.get("content", "")
            prompt = STRUCTURE_PROMPT_TEMPLATE.format(markdown_text=markdown_text)
            log.info(f"[STRUCT] {f.name} | Starting Ollama model={MODEL_NAME} | chars={len(markdown_text)}")
            t2 = time.time()
            resp = ollama.chat(model=MODEL_NAME, messages=[{"role": "user", "content": prompt}])
            t3 = time.time()

            raw = resp.get("message", {}).get("content", "")
            try:
                structured = json.loads(raw)
                parsed_ok = True
            except Exception:
                structured = None
                parsed_ok = False

            payload = {
                "file": f.name,
                "size_bytes": f.stat().st_size,
                "timings": {
                    "process_s": t1 - t0,
                    "structure_s": t3 - t2,
                    "total_s": t3 - t0,
                },
                "extraction_mode": extraction.get("extraction_mode"),
                "extraction_chars": len(markdown_text or ""),
                "structured_data": structured,
                "structured_raw": None if structured is not None else raw,
            }
            last_payload = payload

            # Per-file summary
            log.info(
                f"[DONE] {f.name} | mode={payload['extraction_mode']} | "
                f"process={payload['timings']['process_s']:.2f}s | "
                f"structure={payload['timings']['structure_s']:.2f}s | "
                f"total={payload['timings']['total_s']:.2f}s | "
                f"json={'ok' if parsed_ok else 'invalid'}"
            )

            totals["process_s"] += payload["timings"]["process_s"]
            totals["structure_s"] += payload["timings"]["structure_s"]
            totals["total_s"] += payload["timings"]["total_s"]
            totals["ok"] += 1

        except Exception as e:
            log.error(f"[ERROR] {f.name} | {e}")
            log.debug(traceback.format_exc())
            totals["err"] += 1

    # Write only the last file's payload
    out_path = out_dir / "last_output.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(last_payload, fh, ensure_ascii=False, indent=2)
    log.info(f"[WRITE] last_output.json → {out_path}")

    # Small run summary
    if totals["ok"]:
        n = totals["ok"]
        log.info(
            f"[SUMMARY] files_ok={totals['ok']} files_err={totals['err']} | "
            f"avg_process={totals['process_s']/n:.2f}s | "
            f"avg_structure={totals['structure_s']/n:.2f}s | "
            f"avg_total={totals['total_s']/n:.2f}s"
        )
    else:
        log.warning(f"[SUMMARY] No successful files | files_err={totals['err']}")

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.warning("[ABORT] Interrupted by user")
