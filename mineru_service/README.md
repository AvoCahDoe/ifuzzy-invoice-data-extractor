# MinerU Testing Setup

This directory contains a complete setup for testing MinerU, an open-source PDF extraction tool.

## ✅ Installation Status

MinerU has been successfully installed and configured for **CPU-only** mode on Windows.

## Directory Structure

```
mineru_test/
├── mineru_env/              # Python virtual environment with MinerU
├── create_test_pdf.py       # Script to create test PDFs
├── mineru_extractor.py      # Python wrapper for MinerU
├── test_invoice.pdf         # Sample invoice PDF for testing
├── output_dir/              # Output from MinerU extractions
│   └── test_invoice/
│       └── auto/
│           ├── test_invoice.md           # Extracted markdown
│           ├── test_invoice_content_list.json  # Structured content
│           ├── images/                   # Extracted images
│           └── ...
└── README.md
```

## Quick Start

### 1. Activate the Virtual Environment

```bash
# Windows (CMD)
mineru_env\Scripts\activate

# Windows (Git Bash / PowerShell)
source mineru_env/Scripts/activate
```

### 2. Run MinerU on a PDF

```bash
# Using the CLI
mineru -p your_document.pdf -o ./output --backend pipeline

# Using the Python wrapper
python mineru_extractor.py your_document.pdf
```

### 3. Key Command Options

| Option               | Description                                   |
| -------------------- | --------------------------------------------- |
| `-p`                 | Path to PDF file                              |
| `-o`                 | Output directory                              |
| `--backend pipeline` | **Required for CPU** - Uses lighter models    |
| `--backend vlm`      | Uses Vision Language Models (GPU recommended) |

## Configuration

The MinerU configuration is stored at `C:\Users\wombi\mineru.json`:

```json
{
  "device-mode": "cpu",
  "models-dir": {
    "pipeline": "C:\\Users\\wombi\\.cache\\huggingface\\hub\\models--opendatalab--PDF-Extract-Kit-1.0\\..."
  }
}
```

### Important Settings

- **`device-mode`**: Set to `"cpu"` for CPU-only processing
- **`models-dir`**: Points to downloaded model weights

## Python API Usage

```python
from mineru_extractor import extract_pdf, get_tables_as_html

# Extract content from a PDF
result = extract_pdf("document.pdf")

# Access extracted content
print(result["markdown"])           # Full markdown text
print(result["content_list"])       # Structured content with bboxes
print(result["tables"])             # Extracted tables
print(result["images"])             # List of image paths

# Get tables as HTML
tables = get_tables_as_html(result["content_list"])
for table in tables:
    print(table)
```

## Output Files

MinerU generates several output files:

| File                  | Description                                            |
| --------------------- | ------------------------------------------------------ |
| `*.md`                | Extracted content as Markdown                          |
| `*_content_list.json` | Structured content with type, text, and bounding boxes |
| `*_model.json`        | Raw model output                                       |
| `*_layout.pdf`        | PDF with layout annotations                            |
| `*_span.pdf`          | PDF with text spans highlighted                        |
| `images/`             | Extracted images and table snapshots                   |

## Performance Notes

- **First run**: ~20-30 seconds to load models
- **Subsequent runs**: 10-30 seconds per page (depending on complexity)
- **Memory usage**: ~2-4 GB RAM

## Troubleshooting

### Model Download Issues

If models fail to download, retry:

```bash
mineru-models-download
# Select: huggingface → pipeline
```

### Out of Memory

- Use `--backend pipeline` (not vlm)
- Process fewer pages at a time
- Close other applications

### Missing libGL (Linux only)

```bash
sudo apt-get install -y libgl1 libglib2.0-0
```

## Comparison with Marker

| Feature           | MinerU         | Marker             |
| ----------------- | -------------- | ------------------ |
| Table extraction  | ✅ HTML tables | ✅ Markdown tables |
| Formula detection | ✅ LaTeX       | ✅ LaTeX           |
| Layout analysis   | ✅ YOLO-based  | ✅ Surya-based     |
| CPU performance   | Good           | Better             |
| Output format     | MD + JSON      | MD + JSON          |

## Links

- [MinerU GitHub](https://github.com/opendatalab/MinerU)
- [Documentation](https://mineru.readthedocs.io/)
- [HuggingFace Models](https://huggingface.co/opendatalab/PDF-Extract-Kit-1.0)
