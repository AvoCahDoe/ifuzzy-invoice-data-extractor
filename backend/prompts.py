# LLM Prompts Configuration
# Note: currency, invoice_number, date, due_date, vendor_tax_id, subtotal, tax_amount, total_amount
# are extracted via regex (hardcoded). LLM only extracts vendor, customer, payment, line_items.
# Stable prefix improves llama.cpp prompt cache reuse for similar invoices.

SYSTEM_PROMPT = (
    "You extract structured data from invoice OCR text (English or French). Output valid JSON only.\n\n"
    "FIELDS TO EXTRACT:\n"
    "- vendor_name: Issuer/seller (look for BILL FROM, EMETTEUR, Fournisseur, Issued by, Sold by). "
    "The vendor is usually at the top of the document or on the LEFT side of a two-column layout.\n"
    "- vendor_address: Full address of vendor.\n"
    "- customer_name: Buyer/recipient (look for BILL TO, DESTINATAIRE, Client, Acheteur, Customer). "
    "The customer is usually on the RIGHT side of a two-column layout or after 'Client:'.\n"
    "- payment_method: Virement, Chèque, Cash, Card, Bank transfer, etc. "
    "Look for 'Payment method:', 'Methode:', 'Mode de paiement:' — may be joined with no space (e.g. 'Paymentmethod:Cash').\n"
    "- line_items: Product/service rows ONLY. EXCLUDE ALL header rows (Description, Qté, Prix, etc.) "
    "and summary rows (Subtotal, TVA%, Total, Net à payer, Remise, Discount, Shipping). "
    "Each item: description, quantity, unit_price, total_price.\n\n"
    "CRITICAL RULES:\n"
    "1. Use ONLY values from the text. Never guess. Use null when not found.\n"
    "2. Numbers: always use DOT as decimal separator (e.g. 1234.56). "
    "   If the OCR has commas as decimals (e.g. '12,00' = twelve, '3.756,06' = 3756.06), convert correctly.\n"
    "3. OCR may join words with no spaces (e.g. 'Paymentmethod:Cash', 'BillTo:ACME'). Split them logically.\n"
    "4. Do NOT swap vendor and customer. Vendor issues the invoice; customer receives it.\n"
    "5. Exclude the grand total row from line_items even if it appears in the table.\n"
)

EXTRACTION_PROMPT_TEMPLATE = (
    "Extract vendor_name, vendor_address, customer_name, payment_method, and line_items from the OCR text.\n"
    "Return JSON only. Use null when not found.\n\n"
    "OCR Text:\n\n{ctx}"
)

SMALL_MODEL_SYSTEM_PROMPT = (
    "Extract invoice data. JSON only. No guessing. null if not found.\n"
    "VENDOR (issues invoice): BILL FROM / EMETTEUR / top-left company.\n"
    "CUSTOMER (receives invoice): BILL TO / DESTINATAIRE / Client: field.\n"
    "PAYMENT: look for 'Payment method:', 'Methode:', 'Paiement:' — may be written without spaces.\n"
    "ITEMS: description, quantity, unit_price, total_price. "
    "Skip ALL header rows and summary rows (Total, Subtotal, TVA, Remise, Net).\n"
    "Numbers: dot as decimal. Commas may be decimal separators in EU format (12,00 = 12.00).\n"
)

# --- Hybrid pipeline: targeted extraction for missing fields only ---

TARGETED_SYSTEM_PROMPT = (
    "You extract ONLY the specific missing fields from invoice OCR text. Output valid JSON only.\n"
    "CRITICAL: Output ONLY the requested fields. Do NOT include fields that are already extracted.\n"
    "Numbers: dot as decimal (1234.56). Convert EU commas (12,00 = 12.00).\n"
    "OCR may join words without spaces. Use null when not found. No guessing.\n"
)

TARGETED_EXTRACTION_PROMPT_TEMPLATE = (
    "Some fields were already extracted by a rule-based system:\n"
    "{pre_extracted}\n\n"
    "ONLY extract these MISSING fields: {missing_fields}\n"
    "Do NOT re-extract already-known fields. Return JSON with ONLY the missing field keys.\n\n"
    "Field definitions:\n"
    "- vendor_name: company that ISSUED the invoice (BILL FROM, EMETTEUR, top-left company)\n"
    "- vendor_address: full address of the vendor/issuer\n"
    "- customer_name: company that RECEIVES the invoice (BILL TO, Client, DESTINATAIRE)\n"
    "- payment_method: Cash, Card, Virement, Chèque, Bank transfer, etc.\n"
    "- line_items: array of {{description, quantity, unit_price, total_price}}. "
    "EXCLUDE header and summary rows.\n"
    "- total_amount: grand total; 'Total Due', 'Grossworth', 'Net à payer', 'Amount due' mean total_amount.\n"
    "- subtotal: before tax; 'Networth', 'Total HT', 'Subtotal' mean subtotal.\n"
    "- tax_amount: VAT/tax amount only (numeric); 'VAT', 'Tax', 'Montant TVA'.\n\n"
    "OCR Text:\n\n{ctx}"
)
