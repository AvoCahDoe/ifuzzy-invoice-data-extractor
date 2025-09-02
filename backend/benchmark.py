import os, sys, time, json, csv, argparse
from pathlib import Path
from statistics import mean, median
from typing import List, Dict, Any, Tuple

from io import BytesIO as _BytesIO
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

from marker.config.parser import ConfigParser
from marker.models import create_model_dict

def extract_with_marker(temp_path: str, models: Dict[str, Any]) -> Tuple[str, str]:
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
        rendered = converter(temp_path)
        if hasattr(rendered, "text") and rendered.text:
            return (rendered.text, "marker_ocr")
        if hasattr(rendered, "markdown") and rendered.markdown:
            return (rendered.markdown, "marker_ocr")
        return (str(rendered), "marker_ocr")
    except Exception:
        from marker.convert import convert_single_pdf
        result = convert_single_pdf(temp_path, models)
        if isinstance(result, tuple) and len(result) >= 1:
            content = result[0] if isinstance(result[0], str) else str(result[0])
        else:
            content = str(result)
        return (content, "marker_ocr")

def extract_content(file_path: Path, models: Dict[str, Any], force_ocr: bool=False) -> Tuple[str, str]:
    suffix = file_path.suffix.lower()
    data = file_path.read_bytes()
    if not force_ocr and suffix == ".pdf" and _pdf_is_readable(data):
        try:
            return (_pdf_extract_text(data), "pdf_text_layer")
        except Exception:
            pass
    import tempfile
    with tempfile.NamedTemporaryFile(delete=True, suffix=suffix or ".pdf") as tf:
        tf.write(data)
        tf.flush()
        return extract_with_marker(tf.name, models)

PROMPT_TMPL = """
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
""".strip()

def run_ollama_structuring(markdown_text: str, model_name: str, attempts: int = 5, base_sleep: float = 1.5) -> Dict[str, Any]:
    import ollama
    prompt = PROMPT_TMPL.format(markdown_text=markdown_text)
    last_err = None
    for i in range(attempts):
        try:
            resp = ollama.chat(model=model_name, messages=[{"role": "user", "content": prompt}], options={"timeout": 60_000})
            content = resp.get("message", {}).get("content", "")
            print(f"[DEBUG] Ollama response (attempt {i+1}): {content[:200]}{'...' if len(content) > 200 else ''}")
            return json.loads(content)
        except Exception as e:
            last_err = e
            time.sleep(base_sleep * (i + 1))
    raise RuntimeError(f"Ollama failed after {attempts} attempts: {last_err}")

def p95(lst: List[float]) -> float:
    if not lst:
        return 0.0
    s = sorted(lst)
    k = int(round(0.95 * (len(s)-1)))
    return float(s[k])

def main():
    parser = argparse.ArgumentParser(description="Benchmark invoice extraction (+ optional LLM structuring) — no DB.")
    parser.add_argument("--input", "-i", required=True, help="Input folder with PDFs/PNGs/JPGs")
    parser.add_argument("--pattern", "-p", default="*.pdf,*.png,*.jpg,*.jpeg", help="Comma-separated glob(s)")
    parser.add_argument("--force-ocr", action="store_true", help="Force OCR path even for readable PDFs")
    parser.add_argument("--with-llm", action="store_true", help="Also run structuring via Ollama")
    parser.add_argument("--ollama-model", default="mistral", help="Model name for Ollama (e.g., mistral)")
    parser.add_argument("--json-out", default="/out/results.json", help="Where to write JSON summary")
    parser.add_argument("--csv-out", default="/out/results.csv", help="Where to write per-file CSV (streamed)")
    args = parser.parse_args()

    in_dir = Path(args.input)
    if not in_dir.exists():
        print(f"[ERR] Input folder not found: {in_dir}", file=sys.stderr)
        sys.exit(2)

    patterns = [p.strip() for p in args.pattern.split(",") if p.strip()]
    files: List[Path] = []
    for pat in patterns:
        files.extend(sorted(in_dir.rglob(pat)))
    files = [f for f in files if f.is_file()]

    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.csv_out).parent.mkdir(parents=True, exist_ok=True)

    csv_path = Path(args.csv_out)
    write_header = (not csv_path.exists()) or (csv_path.stat().st_size == 0)
    csv_file = open(args.csv_out, "a", newline="", encoding="utf-8")
    csv_writer = csv.writer(csv_file)
    if write_header:
        csv_writer.writerow(["filename","ok_extract","mode","extraction_ms","ok_llm","structuring_ms","error"])
        csv_file.flush()

    if not files:
        print(f"[WARN] No files matched in {in_dir} for patterns {patterns}")
        with open(args.json_out, "w", encoding="utf-8") as jf:
            json.dump({"total_files": 0, "runs": []}, jf, ensure_ascii=False, indent=2)
        csv_file.close()
        sys.exit(0)

    print(f"[INFO] Loading Marker models...")
    t0_models = time.perf_counter()
    models = create_model_dict()
    t1_models = time.perf_counter()
    model_load_ms = (t1_models - t0_models) * 1000.0
    print(f"[INFO] Marker models loaded in {model_load_ms:.1f} ms")
    print("[INFO] Found %d files to process in %s" % (len(files), in_dir))

    results: List[Dict[str, Any]] = []
    extract_times_ok: List[float] = []
    struct_times_ok: List[float] = []
    total_start = time.perf_counter()

    for i, f in enumerate(files, 1):
        ok_e = True
        ok_l = (not args.with_llm)   
        mode = ""
        err = ""

        t0 = time.perf_counter()
        try:
            content, mode = extract_content(f, models, force_ocr=args.force_ocr)
        except Exception as e:
            ok_e = False
            content = ""
            err = f"EXTRACT: {e}"
        t1 = time.perf_counter()
        extraction_ms = (t1 - t0) * 1000.0
        if ok_e:
            extract_times_ok.append(extraction_ms)

        struct_ms = 0.0
        if args.with_llm and ok_e:
            t2 = time.perf_counter()
            try:
                _ = run_ollama_structuring(content, args.ollama_model)
                ok_l = True
            except Exception as e:
                ok_l = False
                err = (err + " | " if err else "") + f"LLM: {e}"
            t3 = time.perf_counter()
            struct_ms = (t3 - t2) * 1000.0
            if ok_l:
                struct_times_ok.append(struct_ms)

        rec = {
            "filename": str(f.relative_to(in_dir)),
            "ok_extract": ok_e,
            "mode": mode or ("error" if not ok_e else ""),
            "extraction_ms": round(extraction_ms, 2),
            "ok_llm": ok_l,
            "structuring_ms": round(struct_ms, 2),
            "error": err
        }
        results.append(rec)

        csv_writer.writerow([
            rec["filename"],
            rec["ok_extract"],
            rec["mode"],
            rec["extraction_ms"],
            rec["ok_llm"],
            rec["structuring_ms"],
            rec["error"],
        ])
        csv_file.flush()

        print(f"[{i}/{len(files)}] {f.name} -> {rec['mode'] or 'ERR'} | extract {extraction_ms:.1f} ms"
              + (f" | LLM {struct_ms:.1f} ms" if args.with_llm else "")
              + (f"  !! {err}" if err else ""))

    csv_file.close()

    total_end = time.perf_counter()
    total_ms = (total_end - total_start) * 1000.0

    summary = {
        "total_files": len(files),
        "ok_extract_files": len([r for r in results if r["ok_extract"]]),
        "ok_llm_files": len([r for r in results if r["ok_llm"]]),
        "error_files": len([r for r in results if (not r["ok_extract"]) or (args.with_llm and not r["ok_llm"])]),
        "model_load_ms": round(model_load_ms, 2),
        "total_elapsed_ms": round(total_ms, 2),
        "avg_extract_ms": round(mean(extract_times_ok), 2) if extract_times_ok else 0.0,
        "median_extract_ms": round(median(extract_times_ok), 2) if extract_times_ok else 0.0,
        "p95_extract_ms": round(p95(extract_times_ok), 2) if extract_times_ok else 0.0,
        "avg_struct_ms": round(mean(struct_times_ok), 2) if struct_times_ok else 0.0,
        "median_struct_ms": round(median(struct_times_ok), 2) if struct_times_ok else 0.0,
        "p95_struct_ms": round(p95(struct_times_ok), 2) if struct_times_ok else 0.0,
        "runs": results
    }

    with open(args.json_out, "w", encoding="utf-8") as jf:
        json.dump(summary, jf, ensure_ascii=False, indent=2)

    print("\n==== SUMMARY ====")
    _print = {k: v for k, v in summary.items() if k != "runs"}
    print(json.dumps(_print, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
    os.environ.setdefault("GLOG_minloglevel", "2")
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    main()
