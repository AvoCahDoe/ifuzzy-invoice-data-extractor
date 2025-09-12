import os, time, json, uuid, pathlib, sys
from dotenv import load_dotenv
import httpx
import pandas as pd

load_dotenv()

API_BASE = os.getenv("API_BASE", "http://app:8000")
DATA_DIR = os.getenv("DATA_DIR", "/bench/data")
OUT_DIR  = os.getenv("OUT_DIR", "/bench/out")
DO_STRUCTURE = os.getenv("DO_STRUCTURE", "true").lower() == "true"
TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "900"))

def is_invoice(path: pathlib.Path) -> bool:
    return path.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg")

def main():
    data_dir = pathlib.Path(DATA_DIR)
    files = sorted([p for p in data_dir.glob("**/*") if p.is_file() and is_invoice(p)])
    if not files:
        print(f"[bench] No invoices found in {DATA_DIR}", file=sys.stderr)
        return

    rows = []
    with httpx.Client(timeout=TIMEOUT) as client:
        for f in files:
            rec = {
                "file": f.name,
                "relpath": str(f.relative_to(data_dir)),
                "size_bytes": f.stat().st_size,
                "upload_s": None,
                "process_s": None,
                "structure_s": None,
                "total_s": None,
                "status": "ok",
                "error": ""
            }
            t0 = time.time()
            try:
                # upload
                t_up0 = time.time()
                with open(f, "rb") as fp:
                    r = client.post(f"{API_BASE}/upload", files={"file": (f.name, fp, "application/octet-stream")})
                r.raise_for_status()
                file_id = r.json()["file_id"]
                t_up1 = time.time()
                rec["upload_s"] = t_up1 - t_up0

                # process (auto text-layer vs OCR)
                t_pr0 = time.time()
                r = client.post(f"{API_BASE}/process/{file_id}", params={"force_ocr": False})
                r.raise_for_status()
                _ = r.json()["extraction"]
                t_pr1 = time.time()
                rec["process_s"] = t_pr1 - t_pr0

                # structure (Ollama is required)
                if DO_STRUCTURE:
                    t_st0 = time.time()
                    r = client.post(f"{API_BASE}/structure/{file_id}")
                    r.raise_for_status()
                    _ = r.json()["data"]
                    t_st1 = time.time()
                    rec["structure_s"] = t_st1 - t_st0

                rec["total_s"] = time.time() - t0

            except Exception as e:
                rec["status"] = "error"
                rec["error"] = str(e)

            rows.append(rec)

    df = pd.DataFrame(rows)
    out_dir = pathlib.Path(OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"benchmark_{uuid.uuid4().hex[:8]}.csv"
    df.to_csv(csv_path, index=False)

    ok_df = df[df["status"] == "ok"]
    summary = {
        "count": int(len(df)),
        "ok": int(len(ok_df)),
        "error": int(len(df) - len(ok_df)),
        "avg_upload_s": float(ok_df["upload_s"].mean()) if not ok_df.empty else None,
        "avg_process_s": float(ok_df["process_s"].mean()) if not ok_df.empty else None,
        "avg_structure_s": float(ok_df["structure_s"].dropna().mean()) if DO_STRUCTURE and not ok_df["structure_s"].dropna().empty else None,
        "avg_total_s": float(ok_df["total_s"].mean()) if not ok_df.empty else None,
    }
    with open(out_dir / "summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    print(f"[bench] Saved: {csv_path}")
    print(f"[bench] Summary:\n{json.dumps(summary, indent=2)}")

if __name__ == "__main__":
    main()
