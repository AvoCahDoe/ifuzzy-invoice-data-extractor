# Fuzzy Search and OCR Pipeline

This document explains the **fuzzy search** rule-based extraction, the **RapidOCR service** outputs, and how they integrate in the invoice pipeline.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Flow](#2-flow)
3. [RapidOCR Service](#3-rapidocr-service)
4. [Fuzzy Matching Engine](#4-fuzzy-matching-engine)
5. [Spatial Extraction](#5-spatial-extraction)
6. [Tables and Line Items](#6-tables-and-line-items)
7. [Confidence Scoring](#7-confidence-scoring)
8. [Integration with the Pipeline](#8-integration-with-the-pipeline)
9. [Adding New Anchors](#9-adding-new-anchors)

---

## 1. Overview

The **fuzzy search** in this project refers to the rule-based structuring logic in [`backend/rule_extractor.py`](backend/rule_extractor.py). It extracts vendor, customer, payment method, and line items **without using an LLM**, by combining:

- **Fuzzy string matching** (RapidFuzz) to tolerate OCR errors and label variations
- **Spatial anchors** to locate blocks by their position on the page (using bounding boxes from OCR)
- **Markdown pipe-table parsing** to extract line items from tables
- **Freetext fallback** when tables are not detected

The **RapidOCR service** ([`rapidocr_service/main.py`](rapidocr_service/main.py)) performs layout detection, table recognition, and OCR, producing the `blocks` and `markdown` that the fuzzy extractor consumes.

---

## 2. Flow

*Use this section as a presentation script — the end-to-end flow from upload to structured output.*

### Step 1: Upload
User uploads a document (PDF or image) via the frontend. The backend receives it and stores it in MongoDB.

### Step 2: OCR (RapidOCR Service)
- **Digital PDF:** PyMuPDF extracts embedded text and tables directly — no OCR.
- **Scanned PDF / Image:** Convert to images → layout detection (table vs text regions) → tables go to RapidTable (HTML → markdown) → remaining regions go to RapidOCR → output: **markdown** + **blocks** (text, bbox, confidence).

### Step 3: Structuring (Fuzzy Only)
- **Fuzzy mode:** Rule-based only. Uses `blocks` (with bounding boxes) and `markdown`.

### Step 4: Fuzzy Extraction
1. **Vendor:** Find "BILL FROM" / "Fournisseur" anchor → blocks below → first non-anchor = vendor name; rest = address.
2. **Customer:** Find "BILL TO" / "Client" anchor → blocks below → first non-anchor = customer name.
3. **Payment:** Find "Payment" / "Paiement" anchor → block below or to the right = payment method.
4. **Line items:** Parse markdown pipe tables (header → column roles → data rows) or freetext fallback (4-line groups).

### Step 5: Validation & Confirm
User reviews extracted fields on the validation page (with document preview and zoom). Corrections are saved; user confirms to finalize.

### Flow Diagram (verbal)
```
Upload → OCR (RapidOCR) → markdown + blocks
                              ↓
                    Structuring (Fuzzy Only)
                              ↓
                    vendor, customer, payment, line_items
                              ↓
                    Validation page → User confirms
```

### 2.1 Fuzzy Search Flow (Presentation Script)

*Read this when presenting the fuzzy extraction logic.*

**Input:** OCR gives us two things — `blocks` (text + bounding boxes) and `markdown` (pipe tables and free text).

**Step 1 — Vendor**
- Scan blocks for a label that fuzzy-matches "BILL FROM", "Fournisseur", "Seller", etc.
- Take the blocks *below* that label (within 25% of page height).
- The first block that is *not* another label → that’s the vendor name.
- The next few blocks → vendor address.

**Step 2 — Customer**
- Same idea: find "BILL TO", "Client", "Customer", etc.
- Blocks below → first non-label block = customer name.
- If no spatial match, fall back to regex in the markdown ("Bill to:", "Client:", etc.).

**Step 3 — Payment**
- Find "Payment", "Paiement", "Payment method", etc.
- Blocks below (8% of page) or the block to the *right* on the same line → payment method.
- Reject anything that looks like a price (e.g. "Bill Amount: $6890").
- Fallback: regex in markdown for "Payment method:", "Terms:", etc.

**Step 4 — Line Items**
- Parse markdown pipe tables: `| Description | Qty | Price | Total |`
- Detect column roles (desc, qty, price, total) from headers.
- Skip summary rows (subtotal, tax, total, discount).
- If no tables: freetext fallback — look for 4-line groups (description, qty, price, total).

**Step 5 — Confidence**
- Each anchor match contributes a score (0–1).
- Average score = `_fuzzy_match_score`.

**Output:** `vendor_name`, `vendor_address`, `customer_name`, `payment_method`, `line_items`, plus `_fuzzy_match_score`.

---

## 3. RapidOCR Service

The RapidOCR service is a FastAPI application that converts uploaded documents (PDF or images) into structured text and layout blocks.

### 3.1 Main Endpoint

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/convert` | POST | Convert a document to markdown + blocks |
| `/health` | GET | Health check |

**Function:** [`convert()`](rapidocr_service/main.py) (line 411)

### 3.2 Request Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file` | UploadFile | required | The document (PDF, JPG, PNG, BMP, TIFF, WebP) |
| `force_ocr` | bool | False | Reserved for future use |
| `use_llm` | bool | False | Reserved for future use |

### 3.3 Response Schema

| Field | Type | Description |
|-------|------|-------------|
| `file_name` | str | Base filename without extension |
| `original_path` | str | Original uploaded filename |
| `content` | str | Full markdown text (all pages concatenated) |
| `blocks` | list[dict] | Layout blocks with `text`, `bbox`, `confidence` (see below) |
| `avg_visual_confidence` | float | Average confidence across all blocks (0–1) |
| `images` | list | Empty (reserved) |
| `images_count` | int | 0 |
| `extraction_timestamp` | float | Unix timestamp when extraction completed |
| `processing_time` | float | Seconds elapsed |
| `memory_usage_mb` | float | Approximate memory delta during processing |
| `extraction_mode` | str | One of: `fitz_digital_pdf`, `onnx_hybrid_pdf`, `onnx_hybrid_image` |

### 3.4 Block Structure

Each item in `blocks` has:

| Key | Type | Description |
|-----|------|-------------|
| `text` | str | Recognized text content |
| `bbox` | list[float] | Bounding box `[x0, y0, x1, y1]` in image coordinates |
| `confidence` | float | Recognition confidence (0–1) |
| `page_num` | int | Page index (0-based); present only for multi-page documents |

**Function that builds blocks:** [`_extract_ocr_lines()`](rapidocr_service/main.py) (line 303) for OCR output; [`extract_text_from_pdf()`](rapidocr_service/main.py) (line 97) for digital PDFs.

### 3.5 Extraction Modes

| Mode | When Used | Description |
|------|-----------|-------------|
| `fitz_digital_pdf` | PDF with embedded text | Direct text extraction via PyMuPDF; no OCR. Tables extracted with `page.find_tables()`. |
| `onnx_hybrid_pdf` | Scanned PDF (no embedded text) | PDF → images → layout detection → table recognition + OCR. |
| `onnx_hybrid_image` | JPG, PNG, BMP, TIFF, WebP | Same as `onnx_hybrid_pdf` but input is a single image. |

**Functions:**
- **Digital PDF check:** [`pdf_has_text()`](rapidocr_service/main.py) (line 81)
- **Digital PDF extraction:** [`extract_text_from_pdf()`](rapidocr_service/main.py) (line 97)
- **PDF → images:** [`pdf_to_images()`](rapidocr_service/main.py) (line 168)
- **Hybrid OCR pipeline:** [`run_onnx_ocr()`](rapidocr_service/main.py) (line 225)

### 3.6 Hybrid OCR Pipeline (run_onnx_ocr)

**Function:** [`run_onnx_ocr(images)`](rapidocr_service/main.py) (line 225)

**Returns:** `(markdown_text, all_confidences, all_blocks)`

**Per-page steps:**

1. **Layout detection** — [`layout_engine(img_array)`](rapidocr_service/main.py) (RapidLayout) returns regions classified as `table` or `text`.
2. **Table regions** — For each `table` region:
   - Crop the region and run [`table_engine(crop_array)`](rapidocr_service/main.py) (RapidTable) → HTML
   - Convert HTML to markdown via [`html_to_markdown()`](rapidocr_service/main.py) (line 65)
   - Mask the table region (white) so OCR does not duplicate it
3. **Full-page OCR** — Run [`ocr_engine(img_for_ocr)`](rapidocr_service/main.py) (RapidOCR) on the masked image.
4. **Line extraction** — [`_extract_ocr_lines()`](rapidocr_service/main.py) (line 303) clusters detections by vertical overlap, formats lines with tab separation, and builds `blocks`.

### 3.7 HTML Table → Markdown

**Function:** [`html_to_markdown(html_string)`](rapidocr_service/main.py) (line 65)

Uses [`HTMLTableToMarkdown`](rapidocr_service/main.py) (line 18) to convert HTML tables into Markdown pipe-table format:

```markdown
| Col1 | Col2 | Col3 |
| --- | --- | --- |
| A   | B   | C   |
```

---

## 4. Fuzzy Matching Engine

### 4.1 Core Fuzzy Match

**Function:** [`_fuzzy_match(text, patterns, threshold)`](backend/rule_extractor.py) (line 57)

- **Input:** `text` (OCR string), `patterns` (list of anchor strings), `threshold` (0–100, default 80)
- **Output:** `(best_matching_pattern, score)` or `None`
- **Logic:** Uses `rapidfuzz.fuzz.ratio()` when RapidFuzz is installed; otherwise falls back to `pattern in text`.
- **Thresholds used:** 75–85 for vendor/customer/payment; 70–75 for column headers.

### 4.2 Anchor Lists

Defined in [`backend/rule_extractor.py`](backend/rule_extractor.py) (lines 17–46):

| List | Purpose | Examples |
|------|---------|----------|
| `VENDOR_ANCHORS` | Locate the seller/issuer block | BILL FROM, EMETTEUR, Fournisseur, Seller, Vendor, Issued by |
| `CUSTOMER_ANCHORS` | Locate the buyer/recipient block | BILL TO, DESTINATAIRE, Client, Acheteur, Customer, Ship to |
| `PAYMENT_ANCHORS` | Locate the payment method | Payment, Paiement, Mode de paiement, Payment terms, Net 15 |
| `LINE_ITEM_HEADER_ANCHORS` | Exclude from vendor/customer content | Description, Qty, Unit Price, Total, Montant |
| `SUMMARY_KEYWORDS` | Skip summary rows in line items | subtotal, total, tax, tva, discount, shipping |

---

## 5. Spatial Extraction

### 5.1 Bounding Box Helpers

**Functions:**
- [`_block_bbox(blk)`](backend/rule_extractor.py) (line 137) — Returns `(x0, y0, x1, y1)` from `blk["bbox"]`
- [`_block_cy(blk)`](backend/rule_extractor.py) (line 142) — Vertical center
- [`_block_cx(blk)`](backend/rule_extractor.py) (line 147) — Horizontal center

### 5.2 Blocks Below Anchor

**Function:** [`_blocks_below(anchor_idx, blocks, max_rel_height)`](backend/rule_extractor.py) (line 152)

- Returns blocks spatially **below** the anchor within a fraction of page height (default 25% for vendor/customer, 8% for payment).
- Used to find content under labels like "BILL FROM" or "Payment method".

### 5.3 First Non-Anchor Block

**Function:** [`_first_non_anchor(blocks, exclude_anchors)`](backend/rule_extractor.py) (line 171)

- Returns the first block whose text does **not** fuzzy-match any anchor in `exclude_anchors`.
- Used to skip label rows and get the actual vendor/customer/payment value.

### 5.4 Vendor Extraction

**Location:** [`extract_fields_rulebased()`](backend/rule_extractor.py) (lines 241–258)

1. Find block matching `VENDOR_ANCHORS` via [`_fuzzy_match()`](backend/rule_extractor.py)
2. Get blocks below via [`_blocks_below()`](backend/rule_extractor.py)
3. Take first non-anchor via [`_first_non_anchor()`](backend/rule_extractor.py) → `vendor_name`
4. Remaining blocks (excluding anchors) → `vendor_address` via [`_join_block_texts()`](backend/rule_extractor.py) (line 183)

### 5.5 Customer Extraction

**Location:** [`extract_fields_rulebased()`](backend/rule_extractor.py) (lines 260–271)

Same pattern as vendor: anchor → blocks below → first non-anchor → `customer_name`.

**Markdown fallback:** [`_extract_customer_from_markdown(md)`](backend/rule_extractor.py) (line 201) — regex for "Bill to:", "Client:", "Destinataire:", etc.

### 5.6 Payment Extraction

**Location:** [`extract_fields_rulebased()`](backend/rule_extractor.py) (lines 273–305)

1. Find `PAYMENT_ANCHORS` block
2. Try blocks below (8% page height)
3. If none: **same-line fallback** — block to the right of the anchor (`ox0 >= bx1 - 10`, same vertical band)
4. **Markdown fallback:** [`_extract_payment_from_markdown(md)`](backend/rule_extractor.py) (line 187) — regex for "Payment method:", "Methode:", "Terms:", etc.

### 5.7 Vendor Fallback (No Anchor)

**Location:** [`extract_fields_rulebased()`](backend/rule_extractor.py) (lines 308–313)

If no vendor anchor found, use the top-left block (by `_block_cy`, `_block_cx`) as vendor name, unless it starts with "page", "invoice", "facture", etc.

---

## 6. Tables and Line Items

### 6.1 Main Parser

**Function:** [`_parse_line_items_from_markdown(md)`](backend/rule_extractor.py) (line 328)

Parses markdown pipe tables. If no tables found, falls back to [`_parse_line_items_from_freetext(md)`](backend/rule_extractor.py) (line 426).

### 6.2 Column Role Detection

**Function:** [`_col_role(header_text)`](backend/rule_extractor.py) (line 75)

Classifies a header cell as `desc`, `qty`, `price`, `total`, or `pos` using:
- Exact/substring checks against `_DESC_HEADERS`, `_QTY_HEADERS`, `_PRICE_HEADERS`, `_TOTAL_HEADERS`, `_POS_HEADERS` (lines 49–55)
- Fuzzy fallback (ratio ≥ 70–75) when headers are unclear

**Function:** [`_detect_column_map(header_cells)`](backend/rule_extractor.py) (line 109)

Builds `{role: column_index}` from header row. Uses positional fallbacks (e.g. first column = desc, last = total) when headers don’t resolve.

### 6.3 Data Row Processing

**Function:** [`_process_data_row(cells, col_map, items)`](backend/rule_extractor.py) (line 490)

- Extracts `desc`, `qty`, `price`, `total` by role
- Skips rows matching `SUMMARY_KEYWORDS`
- Handles **continuation rows** (description only) — merges into previous item
- Handles **partial rows** — merges missing fields into previous item
- Uses [`_parse_num_clean()`](backend/rule_extractor.py) (line 575) for numeric parsing (handles EU/US formats, currency suffixes)

### 6.4 Freetext Fallback

**Function:** [`_parse_line_items_from_freetext(md)`](backend/rule_extractor.py) (line 426)

For OCR output without pipe tables. Looks for 4-line groups:

```
Description
Quantity
Unit Price
Total
```

**Helper functions:**
- [`_is_likely_desc_line(text)`](backend/rule_extractor.py) (line 390)
- [`_is_likely_qty_line(text)`](backend/rule_extractor.py) (line 402)
- [`_is_likely_amount_line(text)`](backend/rule_extractor.py) (line 411)

---

## 7. Confidence Scoring

**Location:** [`extract_fields_rulebased()`](backend/rule_extractor.py) (lines 226–227, 323)

- Each anchor match contributes `score / 100` (0–1)
- `_fuzzy_match_score` = average of all anchor match scores
- Default 0.5 when no anchors match

---

## 8. Integration with the Pipeline

| Structuring mode | Fuzzy search usage |
|------------------|---------------------|
| **Fuzzy** | Rule-based only (no LLM). |

**Invocation:** [`extract_fields_rulebased(blocks, markdown)`](backend/rule_extractor.py) (line 214) is called from [`backend/main.py`](backend/main.py). The `blocks` come from the RapidOCR `/convert` response; `markdown` is the `content` field.

---

## 9. Adding New Anchors

To improve extraction for new invoice layouts:

1. **Vendor/Customer/Payment:** Add labels to `VENDOR_ANCHORS`, `CUSTOMER_ANCHORS`, or `PAYMENT_ANCHORS` in [`rule_extractor.py`](backend/rule_extractor.py) (lines 17–34).
2. **Column headers:** Add variants to `_DESC_HEADERS`, `_QTY_HEADERS`, `_PRICE_HEADERS`, or `_TOTAL_HEADERS` (lines 49–55).
3. **Summary rows:** Add terms to `SUMMARY_KEYWORDS` (lines 40–46).
4. **Markdown fallback:** Extend regex in [`_extract_payment_from_markdown()`](backend/rule_extractor.py) (line 187) and [`_extract_customer_from_markdown()`](backend/rule_extractor.py) (line 201).

Keep anchors in the language(s) you expect (EN, FR, etc.) and include common OCR variants (no-space, typos).
