import re


def parse_money(s) -> float | None:
    """Parse money string (EU or US format) to float, stripping currency suffixes."""
    if not s:
        return None
    s = str(s).strip()
    s = re.sub(r"(?i)\s*(MAD|EUR|USD|GBP|DH|CHF|TND)\s*$", "", s).strip()
    s = re.sub(r"[€$£]", "", s).strip()
    if not s:
        return None
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
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
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
    months = {
        "jan": "01",
        "feb": "02",
        "mar": "03",
        "apr": "04",
        "may": "05",
        "jun": "06",
        "jul": "07",
        "aug": "08",
        "sep": "09",
        "oct": "10",
        "nov": "11",
        "dec": "12",
        "janv": "01",
        "fév": "02",
        "fev": "02",
        "mars": "03",
        "avr": "04",
        "mai": "05",
        "juin": "06",
        "juil": "07",
        "août": "08",
        "aout": "08",
        "sept": "09",
        "octo": "10",
        "nov": "11",
        "déc": "12",
        "dece": "12",
    }
    m = re.search(r"(\d{1,2})[\s,]+([A-Za-zÀ-ÿ]+)\.?\s+(\d{4})", s, re.I) or re.search(
        r"([A-Za-zÀ-ÿ]+)\.?\s+(\d{1,2})[\s,]+(\d{4})", s, re.I
    )
    if m:
        g = m.groups()
        if g[0].isdigit():
            d, mon, y = g[0], g[1][:4].lower(), g[2]
        else:
            mon, d, y = g[0][:4].lower(), g[1], g[2]
        mo = months.get(mon) or next((v for k, v in months.items() if mon.startswith(k)), None)
        if mo:
            return f"{y}-{mo}-{d.zfill(2)}"
    return s if re.match(r"\d{4}-\d{2}-\d{2}", s) else None


def extract_fields_hardcoded(ocr_text: str) -> dict:
    """Extract regex-reliable fields from OCR. Bilingual EN/FR. Handles no-space OCR."""
    ctx = (ocr_text or "")[:25000]
    ctx_no_tables = "\n".join(
        line for line in ctx.splitlines() if "|" not in line or not re.search(r"^\s*\|.*\|\s*$", line)
    )
    out = {
        "document_type": None,
        "invoice_number": None,
        "date": None,
        "due_date": None,
        "vendor_tax_id": None,
        "subtotal": None,
        "tax_amount": None,
        "total_amount": None,
        "currency": None,
    }
    money_pat = r"((?:\d{1,3}(?:[,. ]\d{3})*[,.]\d{2}|\d+[,.]\d{2}|\d+)(?:\s*(?:MAD|EUR|USD|GBP|DH))?)"
    money_prefix = r"[\s:\t]*"
    money_opt = r"[\$€£]?"
    opt_paren = r"\s*(?:\([^)]*\)|\[[^\]]*\])?\s*"

    if re.search(r"\$|USD", ctx, re.I):
        out["currency"] = "USD"
    elif re.search(r"MAD|Dirham|DH\b", ctx, re.I):
        out["currency"] = "MAD"
    elif re.search(r"€|EUR", ctx, re.I):
        out["currency"] = "EUR"

    if re.search(r"(?i)(?:Credit\s*Note|CreditNote|Avoir|Note\s*de\s*cr[eé]dit)", ctx):
        out["document_type"] = "Credit Note"
    elif re.search(r"(?i)(?:Delivery\s*[Oo]rder|Deliveryorder|Bon\s*de\s*[Ll]ivraison|BondelivraisonN)", ctx):
        out["document_type"] = "Delivery Order"
    elif re.search(r"(?i)(?:Receipt|Re[çc]u|Quittance|Bon\s*de\s*commande)", ctx):
        out["document_type"] = "Receipt"
    elif re.search(r"(?i)(?:Invoice|Facture)", ctx):
        out["document_type"] = "Invoice"

    inv_patterns = [
        r"(?is)Invoice\s*(?:no\.?|#|number|n[°ºo]?)\s*[:\-]?\s*\n?\s*([A-Za-z0-9][A-Za-z0-9_\-/]{2,})",
        r"(?is)Facture\s*(?:no\.?|#|number|n[°ºo]?)\s*[:\-]?\s*\n?\s*([A-Za-z0-9][A-Za-z0-9_\-/]{2,})",
        r"(?i)Facture\s*N[°º°o]?\s*[:\-]?\s*([A-Za-z][A-Za-z0-9_\-]+)",
        r"(?i)Invoice\s*N[°oo]?[o\.]?\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9_\-]+)",
        r"(?i)Invoice\s*(?:no\.?|#|number)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9_\-]+)",
        r"(?i)N[°º]\s*(?:Facture|facture)\s*[:\-]?\s*([A-Za-z0-9_\-]+)",
        r"(?i)Ref\s*[:\-#]?\s*([A-Za-z][A-Za-z0-9_\-]+)",
    ]
    banned_invoice_vals = {"number", "umber", "invoice", "facture", "date", "due"}
    for pat in inv_patterns:
        m = re.search(pat, ctx_no_tables)
        if m:
            candidate = m.group(1).strip()
            candidate_l = candidate.lower()
            if (
                len(candidate) >= 3
                and candidate_l not in banned_invoice_vals
                and not re.match(r"(?i)^(BL|BL_|Bon)$", candidate)
            ):
                out["invoice_number"] = candidate
                break
        if not out["invoice_number"]:
            m2 = re.search(r"(?i)(?:Invoiceno|InvoiceN|FactureN)[°oo]?\s*[:\-]?\s*([A-Za-z0-9_\-/]{3,})", ctx_no_tables)
            if m2:
                c2 = m2.group(1).strip()
                if c2.lower() not in banned_invoice_vals:
                    out["invoice_number"] = c2

    date_labels = r"(?:Invoice\s*date|Date\s*of\s*issue|Date\s*d\'[eé]mission|Date\s*de\s*facture|Date\s*de\s*facturation|Factur[eé]\s*le|[eÉ]mis\s*le|Date|Le\s*:|Date\s*:)"
    date_val = r"([A-Za-zÀ-ÿ]{3,12}\.?\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}\.\d{1,2}\.\d{2,4})"
    dates = re.findall(rf"(?i){date_labels}[\s:]*{date_val}", ctx_no_tables)
    for d in dates:
        normalized = _normalize_date(d.strip())
        if normalized:
            out["date"] = normalized
            break

    due_labels = r"(?:Due\s*date|[Éé]ch[eé]ance|Date\s*d\'[eé]ch[eé]ance|Payment\s*due|[Éé]ch[eé]ance\s*de\s*paiement|[Àa]\s*payer\s*avant|Payable\s*avant|Date\s*limite|Payable\s*le)"
    due = re.findall(rf"(?i){due_labels}[\s:]*([A-Za-z0-9/\-\.\s,]+?)(?:\n|$)", ctx_no_tables)
    for d in due:
        normalized = _normalize_date((d or "").strip())
        if normalized:
            out["due_date"] = normalized
            break

    ice = re.search(r"(?i)ICE\s*[:\-]?\s*(\d{15})\b", ctx_no_tables)
    if ice:
        out["vendor_tax_id"] = ice.group(1).strip()
    else:
        tax = re.findall(r"(?i)(?:ICE|VAT\s*ID|VATID|TVA|SIRET|SIREN|Tax\s*Id|TaxId|N[°°]\s*TVA)\s*[:\-]?\s*([0-9A-Z\s/\-\.]{6,30})", ctx_no_tables)
        if tax:
            candidate = tax[0].strip().rstrip(".")
            if len(candidate) >= 6 and candidate.lower() not in {"number", "invoice", "tax id"}:
                out["vendor_tax_id"] = candidate

    total_labels = (
        r"(?<!Sub)(?:"
        r"Grand\s*Total|Invoice\s*Total|Quote\s*Total|Order\s*Total|Total\s*Amount|Net\s*Total|"
        r"Bill\s*Amount|Montant\s*Total|Montant\s*[àa]\s*payer|"
        r"Total\s*(?:Due|TTC|g[eé]n[eé]ral|[àa]\s*payer|amount)?|"
        r"Net\s*[àa]\s*payer|Montant\s*TTC|NET\s*PAYABLE|"
        r"Amount\s*due|Balance\s*due|Montant\s*d[uû]"
        r")"
    )
    totals = re.findall(rf"(?i){total_labels}" + opt_paren + money_prefix + money_opt + money_pat, ctx)
    totals += re.findall(rf"(?is){total_labels}" + opt_paren + r"\s*[:\-]?\s*\n+\s*" + money_opt + money_pat, ctx)
    totals += re.findall(rf"(?i)\|\s*{total_labels}\s*\|\s*" + money_opt + money_pat + r"\s*\|", ctx)
    totals += re.findall(rf"(?i)\|\s*Total\s*\|.*\|\s*" + money_opt + money_pat + r"\s*\|", ctx)
    totals += re.findall(rf"(?i)Total\s*:\s*({money_opt}{money_pat})", ctx)
    triple = re.search(rf"(?i)\|\s*Total\s*\|.*\|\s*" + money_opt + money_pat + r"\s*\|\s*" + money_opt + money_pat + r"\s*\|\s*" + money_opt + money_pat + r"\s*\|", ctx)
    if triple:
        g = triple.groups()
        if len(g) >= 3 and not out["subtotal"]:
            out["subtotal"] = parse_money(g[0])
        if len(g) >= 3 and not out["tax_amount"]:
            out["tax_amount"] = parse_money(g[1])
        if len(g) >= 3 and not out["total_amount"]:
            out["total_amount"] = parse_money(g[2])
    if totals:
        last = totals[-1]
        val = last[-1] if isinstance(last, tuple) else last
        out["total_amount"] = out["total_amount"] or parse_money(str(val).strip())

    subtotal_labels = (
        r"(?:"
        r"Sub[\s\-]?total|S\.Total|Sous[\-\s]?total|"
        r"Total\s*HT|Total\s*partiel|Montant\s*HT|"
        r"Hors\s*Taxe|Networth|Net\s*Amount|Gross\s*Amount|"
        r"Amount\s*Before\s*Tax|Pre[\-\s]?tax"
        r")"
    )
    subtotals = re.findall(rf"(?i){subtotal_labels}" + opt_paren + money_prefix + money_opt + money_pat, ctx)
    subtotals += re.findall(rf"(?is){subtotal_labels}" + opt_paren + r"\s*[:\-]?\s*\n+\s*" + money_opt + money_pat, ctx)
    subtotals += re.findall(rf"(?i)\|\s*{subtotal_labels}\s*\|\s*" + money_opt + money_pat + r"\s*\|", ctx)
    subtotals += re.findall(rf"(?i)Subtotal\s*:\s*({money_opt}{money_pat})", ctx)
    if subtotals and out["subtotal"] is None:
        first = subtotals[0]
        val = first[-1] if isinstance(first, tuple) else first
        out["subtotal"] = parse_money(str(val).strip())

    tax_labels = (
        r"(?:"
        r"TVA|Tax(?:e)?(?:\s*[\d\.]+%)?|Sales\s*Tax(?:\s*[\d\.]+%)?|"
        r"Montant\s*TVA|Total\s*TVA|"
        r"VAT(?:\s*\[[\d\.]+%\])?|"
        r"HST(?:\s*[\d\.]+%)?|GST(?:\s*[\d\.]+%)?|PST(?:\s*[\d\.]+%)?|"
        r"IVA"
        r")"
    )
    tax_vals = re.findall(rf"(?i){tax_labels}" + opt_paren + money_prefix + money_opt + money_pat, ctx)
    tax_vals += re.findall(rf"(?is){tax_labels}" + opt_paren + r"\s*[:\-]?\s*\n+\s*" + money_opt + money_pat, ctx)
    tax_vals += re.findall(rf"(?i)\|\s*{tax_labels}\s*\|\s*" + money_opt + money_pat + r"\s*\|", ctx)
    if tax_vals and out["tax_amount"] is None:
        last = tax_vals[-1]
        val = last[-1] if isinstance(last, tuple) else last
        out["tax_amount"] = parse_money(str(val).strip())

    if out["tax_amount"] is not None and not isinstance(out["tax_amount"], (int, float)):
        out["tax_amount"] = parse_money(str(out["tax_amount"])) if str(out["tax_amount"]).strip() else None

    return out


# Substrings must not match inside normal words (e.g. "rate" in "accurate").
_SUMMARY_KEYWORDS = [
    "subtotal",
    "sub total",
    "grand total",
    "invoice total",
    "quote total",
    "total due",
    "amount due",
    "balance due",
    "remise",
    "discount",
    "shipping",
    "frais",
    "gross",
]
_SUMMARY_ROW_RE = re.compile(
    r"(?i)(?:^|\s)(?:tax|tva|vat)\s*[:\-]|"
    r"\b(?:subtotal|grand\s+total|invoice\s+total|quote\s+total|total\s+amount)\b"
)


def filter_line_items(items: list, total_amount=None) -> list:
    """Remove summary/header rows from line items."""
    filtered = []
    for item in items:
        desc = (item.get("description") or "").strip()
        desc_lower = desc.lower()
        qty = item.get("quantity") or 0.0
        item_total = item.get("total_price") or 0.0
        unit_price = item.get("unit_price") or 0.0

        if not desc or desc_lower in ("null", "none", ""):
            continue
        if re.match(r"^\d+\.?$", desc_lower.strip()):
            continue

        is_summary = any(k in desc_lower for k in _SUMMARY_KEYWORDS) or bool(
            _SUMMARY_ROW_RE.search(desc_lower)
        )
        if is_summary:
            has_real_data = qty > 0 and unit_price > 0
            if not has_real_data:
                continue
            if total_amount and total_amount > 0 and abs(item_total - total_amount) < 0.01:
                continue

        header_like = any(
            re.match(rf"(?i)^{h}s?$", desc_lower.strip())
            for h in [
                "description",
                "désignation",
                "designation",
                "qty",
                "quantity",
                "unit price",
                "unit_price",
                "total",
                "montant",
                "amount",
                "item",
                "article",
            ]
        )
        if header_like:
            continue

        filtered.append(item)
    return filtered


def calculate_logic_score(data: dict) -> float:
    """Calculate a 0.0-1.0 score based on math consistency."""
    if not data:
        return 0.0

    penalties = 0
    total_checks = 0
    line_items = data.get("line_items", [])
    if line_items:
        for item in line_items:
            q = item.get("quantity")
            u = item.get("unit_price")
            t = item.get("total_price")
            if q is not None and u is not None and t is not None:
                total_checks += 1
                if abs((float(q) * float(u)) - float(t)) > 0.05:
                    penalties += 1

    total_amount = data.get("total_amount")
    if total_amount is not None and line_items:
        total_checks += 1
        li_sum = sum(float(item.get("total_price", 0) or 0) for item in line_items)
        if abs(li_sum - float(total_amount)) > 0.05:
            penalties += 1

    if total_checks > 0:
        return max(0.0, 1.0 - (penalties / total_checks))
    return 1.0
