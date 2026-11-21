"""Gemini backend: same input and output as extract.py, using Google's free tier.

Needs GEMINI_API_KEY (from aistudio.google.com). Pick the model with
GEMINI_MODEL; the default is a vision-capable Flash model on the free tier.
"""
from __future__ import annotations

import mimetypes
import os
import time
from pathlib import Path

from .extract import SYSTEM, ExtractionError
from .schema import Invoice

DEFAULT_MODEL = "gemini-3.8-flash"
RETRY_CODES = {429, 500, 503}
SUPPORTED_MIME = {"application/pdf", "image/png", "image/jpeg", "image/webp"}


def _model() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)


def extract_invoice_gemini(path: str | Path, client=None, retries: int = 5) -> Invoice:
    """Extract one invoice with Gemini. Retries on rate limits (429) and overload (500/503)."""
    from google import genai
    from google.genai import errors, types

    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if mime not in SUPPORTED_MIME:
        raise ValueError(f"Unsupported file type: {path.name} ({mime})")
    client = client or genai.Client()
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM,
        response_mime_type="application/json",
        response_schema=Invoice,
        temperature=0,
    )
    contents = [types.Part.from_bytes(data=path.read_bytes(), mime_type=mime), "Extract this invoice."]

    for attempt in range(retries + 1):
        try:
            response = client.models.generate_content(model=_model(), contents=contents, config=config)
            break
        except errors.APIError as e:
            if getattr(e, "code", None) in RETRY_CODES and attempt < retries:
                time.sleep(min(60, 5 * 2 ** attempt))
                continue
            raise
    if not response.text:
        raise ExtractionError(f"{path.name}: empty response from {_model()}")
    return Invoice.model_validate_json(response.text)
