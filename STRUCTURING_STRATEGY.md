# Structuring Strategy & Implementation

This document outlines the architecture, model, and logic used to convert raw, unstructured OCR data (Markdown) into highly structured, machine-readable data (JSON) within the invoice extraction pipeline.

## 1. Core Structuring Engine

The system relies on a local LLM backend specifically tailored for fast, accurate structured extraction.

- **Engine:** `llama.cpp` (A highly optimized C/C++ inference engine running as an API server)
- **Model:** `LFM2-1.2B-Extract-Q5_K_M.gguf`
  - Specifically fine-tuned by Liquid AI for data extraction and structured output generation (JSON, XML).
  - Uses 5-bit Quantization (Q5_K_M) keeping the model extremely small (~843 MB in memory/disk load), allowing lightning-fast inference strictly on CPU without requiring an external GPU.

## 2. Text Pre-Processing

Before sending the document text to the LLM backend for structuring, it undergoes the following standardizations:

- **Markdown Cleansing:** Control characters and weird unicode byte marks are cleaned utilizing Python's `unicodedata` utility, mapping characters perfectly so that the context tokens for LFM2 aren't confused.
- **Truncation:** If the invoice yields an exceptionally long sequence of OCR tokens, the string is hard-truncated at `15,000` characters (`ctx = cleaned_md[:15000] + "\n...[truncated]"`). This strictly guarantees the LLM's context window is not breached and avoids outright HTTP 500 Out-Of-Memory errors on massive multi-page payloads.

## 3. Strict Schema Enforcement

The core trick to achieving 100% dependable JSON structuring relies on `llama.cpp`'s natively supported **Grammar/Schema Enforcement**.

When the backend hits the `llama.cpp` Chat Completions endpoint, it injects a stringent JSON schema defining the required shapes, types, and required fields.

```json
{
  "response_format": {
    "type": "json_object",
    "schema": {
      "type": "object",
      "properties": {
        "document_type": {
          "type": "string",
          "enum": ["Facture", "Devis", "Bon de commande", "Other"]
        },
        "invoice_number": { "type": ["string", "null"] },
        "date": { "type": ["string", "null"] },
        "total_amount": { "type": ["string", "number", "null"] },
        "currency": { "type": ["string", "null"] },
        "vendor": { "type": ["string", "null"] },
        "line_items": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "description": { "type": "string" },
              "quantity": { "type": ["number", "string", "null"] },
              "amount": { "type": ["number", "string", "null"] }
            }
          }
        }
      },
      "required": ["total_amount", "currency"]
    }
  }
}
```

The underlying generation sampler in `llama.cpp` creates a valid state-machine derived from this JSON schema, strictly allowing only tokens that create valid schema keys and valid types (e.g., stopping generating alphabetical strings when generating an array of integers/floats inside `line_items`).

## 4. Optimized Prompt Engineering

Liquid AI’s LFM2-Extract performs best with rigid, instruction-following behaviors.

**System Prompt:**

> _"You are a data extraction assistant. Extract the requested information and output valid JSON only. Do not include any explanation or markdown."_

**User Prompt:**

> _"Extract all invoice information from the document text below. Output a single valid JSON object with the fields: document_type, invoice_number, date (YYYY-MM-DD), total_amount (number), currency, vendor, and line_items (array of {description, quantity, amount})._
>
> _Document:_
> _[INJECTED OCR CONTEXT]"_

**Generation Constraints:**

- `temperature`: `0.0`. This maximizes the deterministic output, stopping the model from "hallucinating" data when none is visibly present and picking the highest probable entity match.

## 5. Post-Processing & Rescue Parsing

Even when strictly forced to generate JSON, LLMs might wrap their final chunk with markdown delineators like ``json` at the start and ` `` ` at the end, depending on how chat templates overlap.

The backend calls `extract_json_from_llm(content_str)`, a fallback parser that uses RegEx `r'```(?:json)?\s*(.*?)\s*```'` to extract the inner JSON string safely. The final result string is parsed with `json.loads()` and saved to disk (`processed_output/structure/`) and onto the `structured_data` MongoDB collections for frontend surfacing.
