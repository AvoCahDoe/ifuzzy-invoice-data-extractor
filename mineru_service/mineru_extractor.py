"""
MinerU PDF Extraction Wrapper

This module provides a simple Python interface for extracting content from PDFs
using MinerU. It handles all the configuration and provides structured output.

Usage:
    from mineru_extractor import extract_pdf
    
    result = extract_pdf("path/to/document.pdf")
    print(result["markdown"])
    print(result["content_list"])
"""

import json
import subprocess
import os
from pathlib import Path
from typing import Dict, Any, Optional
import tempfile
import shutil


def extract_pdf(
    pdf_path: str,
    output_dir: Optional[str] = None,
    backend: str = "pipeline",
    cleanup: bool = True,
    python_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Extract content from a PDF using MinerU.
    
    Args:
        pdf_path: Path to the PDF file to extract
        output_dir: Directory to save output files (default: temp directory)
        backend: MinerU backend to use ("pipeline" for CPU, "auto" for GPU if available)
        cleanup: Whether to clean up temporary files after extraction
        python_path: Path to the Python executable with MinerU installed
                    (default: uses current Python)
    
    Returns:
        Dictionary containing:
            - markdown: Extracted text as markdown
            - content_list: Structured content list with bounding boxes
            - images: List of extracted image paths
            - tables: List of extracted tables
            - output_path: Path to the output directory
    """
    pdf_path = Path(pdf_path).resolve()
    
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    
    # Determine output directory
    use_temp = output_dir is None
    if use_temp:
        output_dir = tempfile.mkdtemp(prefix="mineru_")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Determine Python path
    if python_path is None:
        # Use the mineru executable directly
        mineru_exe = shutil.which("mineru")
        if mineru_exe is None:
            # Try to find in the virtual environment
            script_dir = Path(__file__).parent / "mineru_env" / "Scripts"
            mineru_exe = script_dir / "mineru.exe"
            if not mineru_exe.exists():
                raise RuntimeError("MinerU executable not found. Please install MinerU.")
    else:
        mineru_exe = Path(python_path).parent / "mineru.exe"
    
    # Run MinerU
    cmd = [
        str(mineru_exe),
        "-p", str(pdf_path),
        "-o", str(output_dir),
        "--backend", backend
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"MinerU extraction failed: {e.stderr}")
    
    # Find output files
    pdf_name = pdf_path.stem
    auto_dir = output_dir / pdf_name / "auto"
    
    if not auto_dir.exists():
        raise RuntimeError(f"Output directory not found: {auto_dir}")
    
    # Read markdown
    markdown_file = auto_dir / f"{pdf_name}.md"
    markdown_content = ""
    if markdown_file.exists():
        markdown_content = markdown_file.read_text(encoding="utf-8")
    
    # Read content list
    content_list_file = auto_dir / f"{pdf_name}_content_list.json"
    content_list = []
    if content_list_file.exists():
        content_list = json.loads(content_list_file.read_text(encoding="utf-8"))
    
    # Get images
    images_dir = auto_dir / "images"
    images = []
    if images_dir.exists():
        images = [str(p) for p in images_dir.glob("*")]
    
    # Extract tables from content list
    tables = [item for item in content_list if item.get("type") == "table"]
    
    result = {
        "markdown": markdown_content,
        "content_list": content_list,
        "images": images,
        "tables": tables,
        "output_path": str(auto_dir)
    }
    
    # Cleanup if requested and using temp directory
    if cleanup and use_temp:
        # Note: We don't cleanup here to allow access to files
        # User should handle cleanup after processing
        pass
    
    return result


def get_text_blocks(content_list: list) -> list:
    """Extract text blocks from content list."""
    return [item for item in content_list if item.get("type") == "text"]


def get_tables_as_html(content_list: list) -> list:
    """Extract tables as HTML from content list."""
    tables = []
    for item in content_list:
        if item.get("type") == "table":
            tables.append(item.get("table_body", ""))
    return tables


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python mineru_extractor.py <pdf_path>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    print(f"Extracting content from: {pdf_path}")
    
    try:
        result = extract_pdf(pdf_path, cleanup=False)
        print("\n" + "=" * 50)
        print("EXTRACTED MARKDOWN:")
        print("=" * 50)
        print(result["markdown"])
        print("\n" + "=" * 50)
        print(f"Content blocks: {len(result['content_list'])}")
        print(f"Tables found: {len(result['tables'])}")
        print(f"Images found: {len(result['images'])}")
        print(f"Output directory: {result['output_path']}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
