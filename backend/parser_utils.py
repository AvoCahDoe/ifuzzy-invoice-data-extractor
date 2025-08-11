import json
import ollama

async def markdown_to_json(markdown_text: str):
    prompt = f"""
You are an intelligent invoice parser.

Extract the following information from the Markdown invoice:

1. **Client information**:
   - Name
   - Address (if available)
   - Email or phone (if available)

2. **Company information**:
   - Company name
   - Address
   - Contact

3. **Invoice details**:
   - Invoice number
   - Date
   - Due date (if available)

4. **Table of products/services**:
   - Description
   - Quantity
   - Unit price
   - Total for each item

5. **Totals**:
   - Subtotal
   - Tax
   - Total amount

Return your result in a valid JSON structure.

Here is the Markdown content:

{markdown_text}

Return only the JSON as shown above.
"""

    response = ollama.chat(model="mistral", messages=[
        {"role": "user", "content": prompt}
    ])

    result = response["message"]["content"]
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        print("⚠️ Could not parse JSON:", result)
        return None
