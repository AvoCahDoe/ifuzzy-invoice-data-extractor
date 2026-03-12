## 🔧 Structuring Improvements (Invoices, CPU‑Only)

This document proposes focused improvements to the structuring side of the pipeline, assuming:
- **Domain**: only invoices (no general documents).
- **Infra**: **CPU‑only**, no GPU.
- **Current stack**: RapidOCR + layout / tables → Markdown → `llama.cpp` + LFM2 Extract → JSON (see `STRUCTURING_STRATEGY.md`).

The goal is to **reduce latency and errors** while **simplifying** the system around the invoice‑only use case.

---

## 1. Narrow the Schema to “Invoices Only”

- **Collapse `document_type`**:
  - Current enum: `["Facture", "Devis", "Bon de commande", "Other"]`.
  - For invoice‑only, move to a simpler set:
    - `["Invoice", "CreditNote"]` or even a single `"Invoice"` if you don’t need classification.
  - This reduces output entropy, shortens generations, and avoids misclassification work the model does not need to do.

- **Invoice‑specific schema**:
  - Add fields that are **strong signals for invoices**:
    - `buyer`, `buyer_address`, `seller_address`, `vat_number`, `due_date`, `payment_terms`, `iban`, `bic`, `tax_amount`, `subtotal`, `vat_rate`.
  - Mark **truly essential fields** as `required` (e.g. `total_amount`, `currency`, `date`, `invoice_number`).
  - Make the rest nullable and optional rather than forcing recovery at all costs.

- **Split “layout vs. values”**:
  - Keep **layout / line_items** (rows) separate from **header fields** (dates, parties, total).
  - The LLM schema can reflect this:
    - `header` object for global fields.
    - `line_items` array for row‑level fields.

This gives the LLM a **tighter, invoice‑specific output space**, which helps a small CPU‑bound model behave more reliably.

---

## 2. Make the LLM Calls Smaller and Simpler

On CPU, **token budget is the main cost**. Optimizing context and output size yields big speedups.

- **Two‑stage extraction (recommended)**:
  - **Stage 1 – header‑only**:
    - Send only:
      - Top ~30–40% of the page around the logo / addresses / summary.
      - Bottom ~20–30% around totals / payment info.
    - Ask for: `invoice_number`, `date`, `vendor`, `buyer`, `subtotal`, `tax_amount`, `total_amount`, `currency`, `iban`, `due_date`, etc.
  - **Stage 2 – line items**:
    - Send only the **table markdown** that came from RapidTable.
    - Ask for: `line_items` only.
  - Each call has **shorter context** and **smaller JSON**, speeding up CPU inference and reducing failure modes.

- **Aggressive but structured truncation**:
  - Instead of a single `15000`‑character cut, truncate by **regions**:
    - Always keep:
      - First N lines of markdown (header).
      - All table sections.
      - Last N lines (totals / payment block).
  - Drop mid‑page noise (terms & conditions, marketing text).

- **Limit line‑item explosion**:
  - Add a **hard cap** such as `max_line_items = 100` in the schema / post‑processing.
  - If more rows detected by OCR, group “overflow” into a synthetic “OTHER_LINES_AGGREGATED” row.
  - This avoids pathological invoices with hundreds of micro‑lines from dominating generation time.

---

## 3. Use More Deterministic, Invoice‑Specific Prompts

LLM extraction should be made as deterministic and invoice‑aware as possible.

- **System prompt tuned to invoices**:
  - Emphasize:
    - The document is **always an invoice‑type financial document**.
    - Output **only JSON**, strict schema, **no explanations**.
    - When in doubt, **use `null`** instead of guessing.

- **Explicit localization rules**:
  - In the prompt, describe:
    - Numeric formats you expect: `1 234,56` vs `1,234.56`.
    - Date formats commonly seen: `DD/MM/YYYY`, `YYYY-MM-DD`, etc.
  - Ask the LLM to **normalize**:
    - Dates to `YYYY-MM-DD`.
    - Money to a **decimal number** and a **separate currency code**.

- **Few‑shot examples from invoices only**:
  - Provide **2–4 compact examples** of:
    - Raw markdown chunk.
    - Target JSON (using your real schema).
  - Choose examples that illustrate:
    - Tax‑inclusive vs tax‑exclusive totals.
    - Different positions of key fields (top‑left, bottom‑right).
    - Different languages (if applicable).

This strengthens the LLM’s biases toward realistic invoice structures instead of generic document patterns.

---

## 4. Combine Heuristics with the LLM (Hybrid Structuring)

For invoices, many key fields are **highly regular and redundant** (e.g. totals, dates, currencies). We can **pre‑extract candidates** using rules, and let the LLM confirm or map them.

- **Pre‑extraction heuristics on OCR text**:
  - Run a lightweight **regex layer** on the Markdown **before** sending to the LLM:
    - Currency amounts: `\b\d[\d\s.,]*\b` with nearby tokens like `TOTAL`, `TTC`, `TVA`, `VAT`, `HT`, `TAX`.
    - Dates: patterns like `\d{2}[./-]\d{2}[./-]\d{2,4}` and `\d{4}-\d{2}-\d{2}`.
    - IBAN/BIC: known regexes for SEPA.
    - VAT IDs: country‑specific VAT patterns.
  - Store these as **candidates** in a small structure (e.g. `regex_hints`).

- **Pass hints into the LLM**:
  - Include a short preface in the user prompt:
    - “We pre‑detected the following candidates: ... Use them if they look consistent with the invoice; otherwise ignore.”
  - This makes the model’s job closer to **selection / validation** (cheap) rather than **full free‑form discovery** (expensive).

- **Post‑validation logic**:
  - After JSON comes back:
    - Re‑validate totals (`subtotal + tax_amount ≈ total_amount`).
    - Re‑check that the chosen currency appears in raw text.
    - If inconsistencies are detected, either:
      - Apply **simple corrections** (e.g. swapped subtotal/total).
      - Or mark a **low‑confidence flag** instead of re‑calling the LLM.

This hybrid approach improves robustness **without extra model calls** and is friendly to CPU‑only setups.

---

## 5. Confidence Scoring and Fallbacks

On CPU, you want to **avoid expensive retries**. Instead, estimate confidence and only retry when truly necessary.

- **LLM‑internal confidence hooks**:
  - Use:
    - `logprobs` (if available in your `llama.cpp` build) or
    - Heuristic signals (e.g. high ratio of `null` fields, missing required keys).
  - Derive a simple **confidence score** based on:
    - Presence of required fields.
    - Valid numeric / date / currency formats.
    - Structural coherence of `line_items`.

- **Targeted, partial retries**:
  - Instead of re‑running the **entire** extraction:
    - If header is weak but line_items are fine, call a **header‑only** refinement prompt.
    - If just totals are inconsistent, run a **tiny correction prompt** that only sees totals and tax fields.
  - Always reuse the **already‑extracted candidates** and regex hints.

This keeps the number of tokens generated per invoice under control while still correcting obvious failures.

---

## 6. CPU‑Friendly llama.cpp Tuning

Given there is **no GPU**, `llama.cpp` should be tuned specifically for throughput on your hardware.

- **Right quantization and model choice**:
  - Prefer **smaller, extraction‑tuned models** (e.g. 1–3B, Q4–Q5) rather than bigger generic chat models.
  - Given the work is largely **schema‑constrained JSON**, extra parameters bring diminishing returns compared with:
    - Better schema.
    - Better prompt.
    - Better truncation.

- **Server parameters**:
  - Ensure `n_threads` is set close to **logical CPU cores**.
  - Keep `temperature = 0.0`, `top_p` low (≤ 0.9) to minimize branching.
  - Use shorter `max_tokens` thanks to the simplified schema and two‑stage extraction.

- **Batching / pipelining**:
  - If you process multiple invoices in a row:
    - Use an **internal queue** that sends them in sequence to the same llama.cpp server to keep the model “warm”.
    - Avoid repeatedly starting / stopping the server.

---

## 7. Data‑Driven Schema & Prompt Refinement

Once the above structural changes exist, close the loop using your real invoice dataset.

- **Error logging**:
  - For each invoice, store:
    - Raw OCR markdown.
    - Final JSON.
    - Any validation failures (e.g. sum mismatch, invalid date).
  - Tag these with simple labels: `OK`, `PARTIAL_OK`, `FAIL_HEADER`, `FAIL_LINES`.

- **Feedback‑driven tweaks**:
  - For the worst‑offending categories:
    - Add **new few‑shot examples**.
    - Adjust regex heuristics.
    - Tighten / relax specific schema fields.

- **Optional fine‑tuning later**:
  - If you collect enough structured pairs, you can fine‑tune a small, CPU‑friendly model specifically for your invoice schema, but most of the benefit already comes from the **structuring and prompt changes above**.

---

## 8. Summary of Recommended Steps

- **Short term (low effort, high impact)**:
  - Simplify schema to be **invoice‑only** and add invoice‑specific fields.
  - Switch to **two‑stage extraction** (header vs. line_items).
  - Improve truncation policy to always keep **header, tables, totals**.
  - Add **regex‑based pre‑extraction** for totals, dates, and IBAN / VAT.

- **Medium term**:
  - Implement **confidence scoring** and **partial retries**.
  - Tighten prompts with **localization rules** and **few‑shot examples** from your data.
  - Tune `llama.cpp` parameters (threads, max_tokens, quantization) for your actual CPU box.

These changes are all compatible with **CPU‑only infrastructure** and are focused on leveraging the fact that **you only ever process invoices**, which lets you aggressively specialize both the schema and the structuring logic.

