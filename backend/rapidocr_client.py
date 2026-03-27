"""
HTTP client for the RapidOCR microservice.

Keeps OCR transport (URLs, retries, `/convert` contract) out of `main.py`.
"""
import asyncio
import logging
import os
from typing import Any, Dict

import httpx

logger = logging.getLogger(__name__)

RAPIDOCR_SERVICE_URL = os.getenv("RAPIDOCR_SERVICE_URL", "http://rapidocr_service:8005")

ENGINE_URLS: Dict[str, str] = {
    "rapidocr": RAPIDOCR_SERVICE_URL,
}


async def call_extraction_service(engine: str, filename: str, file_content: bytes) -> Dict[str, Any]:
    """
    Call the external OCR engine (RapidOCR) to convert the file to markdown.

    Parameters
    ----------
    engine : str
        Logical engine name; must exist in ``ENGINE_URLS`` (currently only ``rapidocr``).
    filename : str
        Original filename, forwarded to the OCR service for logging.
    file_content : bytes
        Raw bytes of the uploaded file read from GridFS.

    Returns
    -------
    dict
        Parsed JSON response from the OCR service. Typically includes:
        ``content``, ``blocks``, ``avg_visual_confidence``, ``extraction_mode``, etc.

    Raises
    ------
    ValueError
        If the engine name is unknown.
    Exception
        If the OCR service keeps failing after all retry attempts.
    """
    url = ENGINE_URLS.get(engine)
    if not url:
        raise ValueError(f"Unknown engine: {engine}. Valid: {list(ENGINE_URLS)}")

    files = {"file": (filename, file_content, "application/octet-stream")}
    data = {"force_ocr": "false"}

    max_retries = 30
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                resp = await client.post(f"{url}/convert", files=files, data=data)
                if resp.status_code != 200:
                    raise Exception(f"Engine '{engine}' returned {resp.status_code}: {resp.text[:300]}")
                return resp.json()
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            if attempt == max_retries - 1:
                raise
            logger.warning(
                "Connection to %s failed (attempt %s/%s): %s. Retrying...",
                engine,
                attempt + 1,
                max_retries,
                e,
            )
            await asyncio.sleep(3)
