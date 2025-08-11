import datetime
import json
import ollama

def markdown_to_json(markdown_text: str, output_file: str = "output.json"):
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
    print("Mistral response:", result)
    try:
        data = json.loads(result)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"✅ JSON saved to {output_file}")
    except json.JSONDecodeError:
        print("⚠️ Could not parse Mistral output as valid JSON.")
        print("Raw output:\n", result)

# Example usage
if __name__ == "__main__":
    with open(r"C:\Users\hp\Desktop\digex\output_folder\1131w-_feleypF2o4\1131w-_feleypF2o4.md", "r", encoding="utf-8") as f:
        markdown_input = f.read()
        
    markdown_to_json(markdown_input)


