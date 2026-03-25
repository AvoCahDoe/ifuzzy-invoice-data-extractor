"""
Rule-based structuring using spatial anchors and fuzzy matching.
Extracts vendor_name, vendor_address, customer_name, payment_method, line_items
without using an LLM.
"""
import re
from typing import Optional
from sklearn.cluster import DBSCAN
import numpy as np

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False

# Anchor patterns — spatial labels used to locate vendor/customer/payment blocks
# Enriched for fuzzy matching across EN/FR/ES and OCR variations (no-space, typos)
VENDOR_ANCHORS = [
    "BILL FROM", "FROM:", "EMETTEUR", "Émetteur", "Fournisseur", "Expéditeur", "Vendeur",
    "Seller", "Vendor", "Supplier", "Sold by", "Issued by", "Issuer", "Provider", "Sender",
    "Bill from", "From", "Vendedor", "Lieferant", "Fornitore", "Expediteur",
    "Vendu a", "Sold To", "Sold a", "Facturé par", "Facture par",
    "Service Provider", "From:", "Issuer:", "Billed From"
]
CUSTOMER_ANCHORS = [
    "BILL TO", "TO:", "DESTINATAIRE", "Client", "Acheteur", "Buyer",
    "Customer", "Bill to", "Ship to", "Livré à", "Recipient", "Purchaser",
    "Ordered by", "Order for", "Billed to", "Deliver to", "Livraison à",
    "À l'attention de", "Attention", "Destinatario", "Cliente",
    "Client Information", "Livré a", "Vendu a",
    "Ship To", "Deliver To", "Invoice To", "Facturé à", "Facture a",
    "Bill To:", "To:", "Billed To"
]
PAYMENT_ANCHORS = [
    "Payment", "Paiement", "Mode de paiement", "Règlement", "Payment method",
    "Payment terms", "Méthode de paiement", "Paymentmethod", "Methode",
    "Terms", "Settlement", "Moyen de paiement", "Mode de règlement",
    "Payment mode", "Method of payment", "Paid by", "Payment by",
    "Conditions de paiement", "Payment conditions", "Net 15", "Net 30",
    "Payment details", "Paymentdetails", "Mode règlement", "Modalités de paiement",
    "Termes de paiement", "Payment Info", "Pay by", "Paying by"
]
LINE_ITEM_HEADER_ANCHORS = [
    "Description", "Désignation", "Qty", "Qté", "Quantity", "Unit Price",
    "PU", "Prix unitaire", "Total", "Montant", "Amount", "Item", "Article",
    "Service", "Libellé", "Product", "Hrs", "Hours", "Rate", "Networth",
    "Gross worth", "SERVICE DESCRIPTION", "HOURS", "RATE", "AMOUNT"
]
SUMMARY_KEYWORDS = [
    "subtotal", "sub total", "s.total", "total", "tax", "tva", "net", "amount due", "balance",
    "remise", "discount", "shipping", "frais", "gross", "grand total",
    "total due", "net à payer", "total ttc", "total ht", "montant ttc",
    "balance due", "vat", "taxable", "total amount", "net payable",
    "hst", "gst", "pst", "iva", "sales tax", "invoice total",
    "bill amount", "net total", "amount before tax", "pre-tax",
    "shipping and handling", "shipping & handling", "handling"
]

# Column header patterns for smart column detection (pipe tables)
# Used to map table columns to desc/qty/price/total/pos roles
_DESC_HEADERS  = ["description", "name", "nom", "designation", "désignation", "item", "article", "libelle", "libellé", "product", "produit", "service", "service description", "product name", "details", "détails", "col0"]
_QTY_HEADERS   = ["qty", "qté", "qte", "quantity", "quantite", "quantité", "qty/hrs", "hours", "hrs", "nb", "nbr", "nombre", "pieces", "pcs", "units", "unités", "unit"]
_PRICE_HEADERS = ["unit price", "unit_price", "unit price (mad)", "prix", "price", "pu", "prix unitaire", "netprice", "unit", "rate", "tarif", "price per unit", "net price", "prix unitaire"]
_TOTAL_HEADERS = ["total", "total ht", "montant", "amount", "networth", "gross worth", "line total", "line total (mad)", "total(ht)", "total (ht)", "net amount", "montant net", "montant total", "line amount", "grossworth", "sub total", "subtotal", "ext. price", "ext price", "extended price", "extended", "amount (ht)", "total (ih)"]
_POS_HEADERS   = ["pos", "no.", "no", "#", "num", "n°", "numero", "numéro", "item no", "ref", "index", "row"]


def _fuzzy_match(text: str, patterns: list, threshold: float = 80) -> Optional[tuple]:
    """Return (best_matching_pattern, score) or None."""
    text_clean = (text or "").strip()
    if not text_clean:
        return None
    best_score = 0.0
    best_pattern = None
    for p in patterns:
        if HAS_RAPIDFUZZ:
            s = fuzz.ratio(text_clean.upper(), p.upper())
        else:
            s = 100.0 if p.upper() in text_clean.upper() else 0.0
        if s > best_score and s >= threshold:
            best_score = s
            best_pattern = p
    return (best_pattern, best_score) if best_pattern else None


def _col_role(header_text: str) -> Optional[str]:
    """Classify a table column header as 'desc', 'qty', 'price', 'total', 'pos', or None."""
    h = (header_text or "").strip().lower()
    if not h:
        return None
    # Exact / substring checks first (faster, handles no-space OCR)
    for pat in _POS_HEADERS:
        if h == pat or h.startswith(pat):
            return "pos"
    for pat in _QTY_HEADERS:
        if h == pat or pat in h:
            return "qty"
    for pat in _TOTAL_HEADERS:
        if h == pat or pat in h:
            return "total"
    for pat in _PRICE_HEADERS:
        if h == pat or pat in h:
            return "price"
    for pat in _DESC_HEADERS:
        if h == pat or pat in h:
            return "desc"
    # Fuzzy fallback
    if HAS_RAPIDFUZZ:
        if fuzz.ratio(h, "description") >= 70 or fuzz.ratio(h, "designation") >= 70:
            return "desc"
        if fuzz.ratio(h, "quantity") >= 75 or fuzz.ratio(h, "quantite") >= 75:
            return "qty"
        if fuzz.ratio(h, "unit price") >= 75 or fuzz.ratio(h, "prix unitaire") >= 75:
            return "price"
        if fuzz.ratio(h, "total") >= 75 or fuzz.ratio(h, "montant") >= 75:
            return "total"
    return None


def _detect_column_map(header_cells: list) -> dict:
    """
    Given the list of header cell strings, return a dict mapping
    role -> column index: {'desc': i, 'qty': j, 'price': k, 'total': l}
    Falls back to positional guessing when headers don't resolve.
    """
    role_map = {}
    for i, cell in enumerate(header_cells):
        role = _col_role(cell)
        if role and role != "pos" and role not in role_map:
            role_map[role] = i
    n = len(header_cells)
    # Positional fallbacks for common patterns
    if not role_map:
        # No headers detected at all: use classic [desc, qty, price, total]
        if n >= 4:
            return {"desc": 0, "qty": 1, "price": 2, "total": 3}
        elif n == 3:
            return {"desc": 0, "price": 1, "total": 2}
        elif n == 2:
            return {"desc": 0, "total": 1}
    if "desc" not in role_map and n >= 1:
        role_map["desc"] = 0
    if "total" not in role_map and n >= 2:
        role_map["total"] = n - 1
    return role_map


def _block_bbox(blk: dict) -> tuple:
    b = blk.get("bbox", [0, 0, 0, 0])
    return (float(b[0]), float(b[1]), float(b[2]), float(b[3]))


def _block_cy(blk: dict) -> float:
    b = blk.get("bbox", [0, 0, 0, 0])
    return (float(b[1]) + float(b[3])) / 2 if len(b) >= 4 else 0


def _block_cx(blk: dict) -> float:
    b = blk.get("bbox", [0, 0, 0, 0])
    return (float(b[0]) + float(b[2])) / 2 if len(b) >= 4 else 0


def _blocks_below(anchor_idx: int, blocks: list, max_rel_height: float = 0.25) -> list:
    """Return blocks that are below the anchor within a relative page height."""
    if not blocks or anchor_idx < 0 or anchor_idx >= len(blocks):
        return []
    anchor = blocks[anchor_idx]
    anchor_cy = _block_cy(anchor)
    page_num = anchor.get("page_num", 0)
    same_page = [b for b in blocks if b.get("page_num", 0) == page_num]
    if not same_page:
        return []
    max_y = max(_block_bbox(b)[3] for b in same_page)
    min_y = min(_block_bbox(b)[1] for b in same_page)
    page_h = max(1, max_y - min_y)
    limit_y = anchor_cy + page_h * max_rel_height
    out = [b for b in same_page if _block_cy(b) > anchor_cy and _block_cy(b) <= limit_y]
    out.sort(key=_block_cy)
    return out


def reconstruct_table_dbscan(blocks: list, header_y: float, footer_y: float, eps: float = 12.0) -> list:
    """
    Cluster table bounding boxes into horizontal rows using DBSCAN on Y-axis centers.
    """
    # 1. Isolate text blocks in the table area
    table_blocks = [b for b in blocks if header_y < _block_cy(b) < footer_y]
    if not table_blocks:
        return []

    # 2. Extract Y-centers
    y_centers = np.array([_block_cy(b) for b in table_blocks]).reshape(-1, 1)

    # 3. Cluster using DBSCAN
    clustering = DBSCAN(eps=eps, min_samples=1).fit(y_centers)
    labels = clustering.labels_

    # 4. Group by cluster and sort by X-center
    rows_dict = {}
    for i, label in enumerate(labels):
        if label not in rows_dict:
            rows_dict[label] = []
        rows_dict[label].append(table_blocks[i])

    rows = []
    for label, row_blocks in rows_dict.items():
        row_blocks.sort(key=_block_cx)
        rows.append(row_blocks)

    # Sort rows top-to-bottom
    rows.sort(key=lambda r: sum(_block_cy(b) for b in r) / len(r))
    return rows


def _first_non_anchor(blocks: list, exclude_anchors: list) -> Optional[dict]:
    """First block whose text is not an anchor label."""
    for b in blocks:
        t = (b.get("text") or "").strip()
        if not t or len(t) < 2:
            continue
        if _fuzzy_match(t, exclude_anchors, 85):
            continue
        return b
    return None


def _join_block_texts(blocks: list, max_blocks: int = 5) -> str:
    return " ".join((b.get("text") or "").strip() for b in blocks[:max_blocks]).strip()


def _is_valid_payment_candidate(text: str) -> bool:
    """Reject payment candidates that look like price fields, bill amount labels, or are too long."""
    if not text or len(text) > 50:
        return False
    # Reject pure numbers
    if re.match(r'^[\d.,\s]+$', text):
        return False
    # Reject strings that look like "Label: $Value" or contain currency amounts
    if re.search(r'(?i)(?:bill\s*amount|total\s*amount|invoice\s*total|grand\s*total|amount\s*due|balance\s*due)', text):
        return False
    if re.search(r'[$€£]\s*[\d,]+', text):
        return False
    if re.search(r'[\d,\.]+\s*(MAD|EUR|USD|GBP|CAD|CHF)\b', text, re.I):
        return False
    return True


def _extract_payment_from_markdown(md: str) -> Optional[str]:
    """Try to extract payment method from markdown text using regex."""
    m = re.search(
        r'(?i)(?:payment\s*method|methode|paiement|mode\s*de\s*paiement|paymentmethod|methode\s*:|payment\s*:|terms\s*:|conditions?\s*de\s*paiement|payment\s*terms|règlement)\s*[:\-]?\s*([A-Za-z0-9 /\-]+)',
        md
    )
    if m:
        val = m.group(1).strip().split("\n")[0].strip()
        # Ignore very long or numeric strings
        if val and len(val) < 50 and not any(c.isdigit() for c in val[:5]):
            return val
    return None


def _extract_customer_from_markdown(md: str) -> Optional[str]:
    """Try to extract customer from 'Client:' or 'Bill to:' labels in markdown text."""
    m = re.search(
        r'(?i)(?:bill\s*to|billed\s*to|client\s*:|acheteur\s*:|destinataire\s*:|ship\s*to|deliver\s*to|ordered\s*by|recipient\s*:)\s*([A-Za-zÀ-ÿ0-9 &,.\-]+)',
        md
    )
    if m:
        val = m.group(1).strip().split("\n")[0].strip()
        if val and len(val) > 2:
            return val
    return None


def extract_fields_rulebased(blocks: list, markdown: str) -> dict:
    """
    Extract vendor_name, vendor_address, customer_name, payment_method, line_items
    using spatial anchors, fuzzy matching, and markdown parsing.
    Returns dict with '_fuzzy_match_score' (0-1) for confidence.
    """
    out = {
        "vendor_name": None,
        "vendor_address": None,
        "customer_name": None,
        "payment_method": None,
        "line_items": [],
    }
    fuzzy_scores = []

    blocks = blocks or []
    if not blocks:
        out["line_items"] = _parse_line_items_from_markdown(markdown or "")
        # Try markdown-based payment/customer even without spatial blocks
        if not out["payment_method"]:
            out["payment_method"] = _extract_payment_from_markdown(markdown or "")
        if not out["customer_name"]:
            out["customer_name"] = _extract_customer_from_markdown(markdown or "")
        return out

    # Find vendor anchor
    vendor_anchor_idx = None
    for idx, blk in enumerate(blocks):
        m = _fuzzy_match(blk.get("text", ""), VENDOR_ANCHORS, 75)
        if m:
            vendor_anchor_idx = idx
            fuzzy_scores.append(m[1] / 100.0)
            break

    if vendor_anchor_idx is not None:
        below = _blocks_below(vendor_anchor_idx, blocks)
        exclude = VENDOR_ANCHORS + CUSTOMER_ANCHORS + PAYMENT_ANCHORS + LINE_ITEM_HEADER_ANCHORS
        first = _first_non_anchor(below, exclude)
        if first:
            out["vendor_name"] = (first.get("text") or "").strip()
            rest = [b for b in below if b != first][:4]
            addr_parts = []
            for b in rest:
                t = (b.get("text") or "").strip()
                if t and not _fuzzy_match(t, CUSTOMER_ANCHORS + PAYMENT_ANCHORS, 80):
                    addr_parts.append(t)
            if addr_parts:
                out["vendor_address"] = " ".join(addr_parts)

    # Find customer anchor
    customer_anchor_idx = None
    for idx, blk in enumerate(blocks):
        m = _fuzzy_match(blk.get("text", ""), CUSTOMER_ANCHORS, 75)
        if m:
            customer_anchor_idx = idx
            fuzzy_scores.append(m[1] / 100.0)
            break

    if customer_anchor_idx is not None:
        below = _blocks_below(customer_anchor_idx, blocks)
        exclude = VENDOR_ANCHORS + CUSTOMER_ANCHORS + PAYMENT_ANCHORS + LINE_ITEM_HEADER_ANCHORS
        first = _first_non_anchor(below, exclude)
        if first:
            out["customer_name"] = (first.get("text") or "").strip()

    # Find payment anchor (spatial first, then markdown fallback)
    for idx, blk in enumerate(blocks):
        m = _fuzzy_match(blk.get("text", ""), PAYMENT_ANCHORS, 72)
        if m:
            fuzzy_scores.append(m[1] / 100.0)
            below = _blocks_below(idx, blocks, max_rel_height=0.08)
            first = _first_non_anchor(below, VENDOR_ANCHORS + CUSTOMER_ANCHORS + LINE_ITEM_HEADER_ANCHORS)
            if first:
                candidate = (first.get("text") or "").strip()
                if _is_valid_payment_candidate(candidate):
                    out["payment_method"] = candidate
            else:
                # Check right of anchor (same line)
                bx0, by0, bx1, by1 = _block_bbox(blk)
                for jdx, ob in enumerate(blocks):
                    if jdx == idx:
                        continue
                    ox0, oy0, ox1, oy1 = _block_bbox(ob)
                    if abs(oy0 - by0) < 20 and ox0 >= bx1 - 10:
                        candidate = (ob.get("text") or "").strip()
                        if _is_valid_payment_candidate(candidate):
                            out["payment_method"] = candidate
                        break
            break

    # Fallback: extract payment from markdown text
    if not out["payment_method"]:
        out["payment_method"] = _extract_payment_from_markdown(markdown or "")

    # Fallback: extract customer from markdown text when no spatial anchor
    if not out["customer_name"]:
        out["customer_name"] = _extract_customer_from_markdown(markdown or "")

    # Fallback: top-left block as vendor when no anchor found
    if not out["vendor_name"] and blocks:
        top = min(blocks, key=lambda b: (_block_cy(b), _block_cx(b)))
        t = (top.get("text") or "").strip()
        bad_starts = ("page", "invoice", "facture", "delivery", "bon de", "credit note", "receipt", "ordonnance")
        if t and len(t) > 2 and not t.lower().startswith(bad_starts):
            out["vendor_name"] = t

    # Line items: try DBSCAN first, fallback to markdown pipe tables
    dbscan_items = []
    if blocks:
        # Find header Y bounds
        header_y = None
        for blk in blocks:
            if _fuzzy_match(blk.get("text", ""), LINE_ITEM_HEADER_ANCHORS, 80):
                header_y = _block_cy(blk) - 15  # a bit above the center
                break

        # Find footer Y bounds
        footer_y = 99999.0
        if header_y:
            below_header = [b for b in blocks if _block_cy(b) > header_y + 30]
            for blk in below_header:
                if any(kw in (blk.get("text", "").lower()) for kw in SUMMARY_KEYWORDS):
                    footer_y = _block_cy(blk) - 5
                    break

        if header_y and footer_y != 99999.0:
            rows = reconstruct_table_dbscan(blocks, header_y, footer_y, eps=12.0)
            
            # Simple heuristic row-to-item extraction from clustered blocks
            for row in rows:
                if len(row) < 2:
                    continue
                    
                texts = [(b.get("text") or "").strip() for b in row]
                row_str = " | ".join(texts)
                
                # Check if this row looks like a summary/subtotal row
                if any(kw in row_str.lower() for kw in SUMMARY_KEYWORDS):
                    continue
                    
                desc = texts[0]
                # Look for numbers from right to left (Total, Unit Price, Qty)
                numbers = []
                for t in reversed(texts[1:]):
                    num = _parse_num_clean(t)
                    if num is not None:
                        numbers.append(num)
                        
                if len(numbers) >= 2:
                    # Assumes standard layout [desc, ..., qty/price, total]
                    dbscan_items.append({
                        "description": desc,
                        "quantity": numbers[-1] if len(numbers) >= 3 else 1,
                        "unit_price": numbers[1],
                        "total_price": numbers[0]
                    })
                    
    if dbscan_items:
        out["line_items"] = dbscan_items
    else:
        out["line_items"] = _parse_line_items_from_markdown(markdown or "")

    # Fuzzy match score
    out["_fuzzy_match_score"] = sum(fuzzy_scores) / len(fuzzy_scores) if fuzzy_scores else 0.5

    return out


def _parse_line_items_from_markdown(md: str) -> list:
    """
    Parse line items from markdown pipe tables.
    Detects column roles from the header row so any column order works.
    Skips summary rows and rows without meaningful data.
    """
    items = []
    lines = (md or "").strip().split("\n")

    col_map = None      # will be set from header
    header_found = False
    in_table = False    # True after we've seen a header + separator

    for line in lines:
        line = line.strip()
        if not line:
            in_table = False
            col_map = None
            header_found = False
            continue
        if "|" not in line:
            in_table = False
            col_map = None
            header_found = False
            continue

        # Keep empty middle cells so column indexes remain aligned
        cells = [c.strip() for c in line.split("|")]
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]

        # Separator row (---|---|...)
        if re.match(r"^[\|\-:\s]+$", line):
            if header_found:
                in_table = True
            continue

        # If we're past the separator, parse a data row
        if in_table and col_map is not None:
            _process_data_row(cells, col_map, items)
            continue

        # Potential header row (not a separator, contains pipe)
        if not in_table:
            candidate_map = _detect_column_map(cells)
            if candidate_map:
                col_map = candidate_map
                header_found = True
            # Even if we couldn't classify columns, mark as header candidate
            # so the next separator triggers in_table
            elif len(cells) >= 2:
                col_map = _detect_column_map(cells)
                header_found = True

    if items:
        return items
    # Fallback for OCR outputs where line items are plain text blocks
    return _parse_line_items_from_freetext(md)


def _is_likely_desc_line(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 3:
        return False
    if _parse_num_clean(t) is not None:
        return False
    low = t.lower()
    if any(k in low for k in SUMMARY_KEYWORDS):
        return False
    return any(c.isalpha() for c in t)


def _is_likely_qty_line(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if re.match(r'^\d+(?:[.,]\d+)?(?:\s*(?:hrs?|hours?|qty|qt[eé]?|pcs?|pieces?|unite\(s\)|unit(?:s)?))?$', t):
        return True
    return False


def _is_likely_amount_line(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if not re.search(r'\d', t):
        return False
    num = _parse_num_clean(t)
    if num is None:
        return False
    # Reject lines that are too likely quantities
    if _is_likely_qty_line(t) and not re.search(r'[$€£]|MAD|EUR|USD|GBP|DH|[.,]\d{2}', t, re.I):
        return False
    return True


def _parse_line_items_from_freetext(md: str) -> list:
    """
    Parse line items from free-text OCR blocks:
      Description
      Quantity
      Unit Price
      Total
    """
    lines = [ln.strip() for ln in (md or "").split("\n")]
    lines = [ln for ln in lines if ln]
    items = []
    i = 0

    # Skip to where service/item-like content starts when a header exists
    start_idx = 0
    for idx in range(max(0, len(lines) - 1)):
        window = " ".join(lines[idx:idx + 5]).lower()
        hits = 0
        if re.search(r'\b(description|service|designation|désignation|item|article|libell[eé]|product)\b', window):
            hits += 1
        if re.search(r'\b(qty|quantit[eé]|quantity|hours?|hrs?|units?|pieces?|pcs?)\b', window):
            hits += 1
        if re.search(r'\b(rate|price|unit price|prix|pu|tarif|netprice)\b', window):
            hits += 1
        if re.search(r'\b(amount|total|montant|networth|line total)\b', window):
            hits += 1
        if hits >= 2:
            start_idx = min(len(lines), idx + 4)
            break

    i = start_idx
    while i + 3 < len(lines):
        desc = lines[i]
        qty_line = lines[i + 1]
        price_line = lines[i + 2]
        total_line = lines[i + 3]

        if any(k in desc.lower() for k in SUMMARY_KEYWORDS):
            break

        if (
            _is_likely_desc_line(desc)
            and _is_likely_qty_line(qty_line)
            and _is_likely_amount_line(price_line)
            and _is_likely_amount_line(total_line)
        ):
            qty = _parse_num_clean(qty_line)
            unit_price = _parse_num_clean(price_line)
            total_price = _parse_num_clean(total_line)
            if qty is not None and unit_price is not None and total_price is not None:
                items.append({
                    "description": desc,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "total_price": total_price,
                })
                i += 4
                continue

        i += 1

    return items


def _process_data_row(cells: list, col_map: dict, items: list) -> None:
    """Extract a line item from a data row using the detected column map."""
    if not cells:
        return

    def get(role):
        idx = col_map.get(role)
        if idx is None or idx >= len(cells):
            return None
        return cells[idx]

    desc  = get("desc")
    qty   = _parse_num_clean(get("qty"))
    price = _parse_num_clean(get("price"))
    total = _parse_num_clean(get("total"))

    # If no desc column found, use first cell
    if desc is None and cells:
        desc = cells[0]

    # Skip rows that look like summaries
    desc_lower = (desc or "").lower().strip()
    if any(k in desc_lower for k in SUMMARY_KEYWORDS):
        return

    # Rows that carry continuation text for previous item
    if items and desc and qty is None and price is None and total is None:
        if not any(k in desc_lower for k in SUMMARY_KEYWORDS):
            items[-1]["description"] = f'{items[-1].get("description", "").strip()} {desc.strip()}'.strip()
        return

    # Skip rows with no meaningful numeric data
    if qty is None and price is None and total is None:
        # Only keep if description is non-empty and at least the total is there via implicit calculation
        if not desc or len(desc.strip()) < 2:
            return
        return  # no numbers at all — skip

    # Skip rows whose description is a pure number (position column leaked in)
    if re.match(r"^\d+\.?$", desc_lower.strip()):
        return

    # Skip "null" or empty descriptions
    if not desc or desc.strip().lower() in ("null", "none", ""):
        return

    # Merge partial continuation rows into previous item when OCR splits rows
    if items:
        prev = items[-1]
        partial_row = (
            desc
            and ((qty is not None and price is None and total is None)
                 or (qty is None and ((price is not None) ^ (total is not None))))
        )
        if partial_row:
            if desc and desc.strip() and desc.strip().lower() not in ("null", "none"):
                prev_desc = (prev.get("description") or "").strip()
                if desc.strip() not in prev_desc:
                    prev["description"] = f"{prev_desc} {desc.strip()}".strip()
            if prev.get("quantity") is None and qty is not None:
                prev["quantity"] = qty
            if prev.get("unit_price") is None and price is not None:
                prev["unit_price"] = price
            if prev.get("total_price") is None and total is not None:
                prev["total_price"] = total
            if (
                prev.get("total_price") is None
                and prev.get("quantity") is not None
                and prev.get("unit_price") is not None
            ):
                prev["total_price"] = round(float(prev["quantity"]) * float(prev["unit_price"]), 2)
            return

    # Compute total if missing
    if total is None and qty is not None and price is not None:
        total = round(qty * price, 2)

    items.append({
        "description": desc.strip(),
        "quantity": qty,
        "unit_price": price,
        "total_price": total,
    })


def _parse_num_clean(s) -> Optional[float]:
    """Parse a number, stripping currency suffixes and handling both comma and dot decimals."""
    if s is None:
        return None
    s = str(s).strip()
    # Strip currency codes and symbols
    s = re.sub(r'(?i)\s*(MAD|EUR|USD|GBP|DH|CHF|TND)\s*$', '', s).strip()
    s = re.sub(r'[€$£]', '', s).strip()
    if not s:
        return None
    # Detect EU format: comma is decimal, dot is thousands
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            # EU: 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:
            # US: 1,234.56
            s = s.replace(",", "")
    elif "," in s:
        # Could be either; assume EU decimal if only one comma and <=2 digits after
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    # Remove any remaining spaces
    s = s.replace(" ", "")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


# Keep alias for backward compatibility
def _parse_num(s) -> Optional[float]:
    return _parse_num_clean(s)
