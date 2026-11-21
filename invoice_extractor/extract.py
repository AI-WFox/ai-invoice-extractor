"""Call Claude on a PDF or image and get back a validated Invoice."""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path

import anthropic

from .schema import Invoice

MODEL = "claude-opus-5-5"

SYSTEM = """You extract structured data from invoices for an accounts-payable team.
Rules:
- Copy values exactly as printed; never invent a value that is not on the page.
- Dates as YYYY-MM-DD. Amounts as plain numbers without thousands separators.
- If a field is absent, use null (or an empty string for required text) and list
  its name in uncertain_fields. Also list any field you had to guess at.
- currency is an ISO 4217 code inferred from symbols or text on the page."""


def _content_block(path: Path) -> dict:
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if mime == "application/pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": mime, "data": data}}
    if mime in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
    raise ValueError(f"Unsupported file type: {path.name} ({mime})")


class ExtractionError(RuntimeError):
    pass


def extract_invoice(path: str | Path, client: anthropic.Anthropic | None = None,
                    effort: str = "medium") -> Invoice:
    """Extract one invoice. Raises ExtractionError if the model declines or is cut off."""
    path = Path(path)
    client = client or anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # re-run on a fallback model if a safety classifier declines
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": invoice_json_schema()},
        },
        messages=[{
            "role": "user",
            "content": [_content_block(path), {"type": "text", "text": "Extract this invoice."}],
        }],
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise ExtractionError(f"{path.name}: stop_reason={response.stop_reason}")
    text = next(b.text for b in response.content if b.type == "text")
    return Invoice.model_validate(json.loads(text))


def invoice_json_schema() -> dict:
    """Pydantic schema tightened for structured outputs (no extra keys, all required)."""
    schema = Invoice.model_json_schema()

    def tighten(node: dict) -> None:
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
        for value in node.values():
            if isinstance(value, dict):
                tighten(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        tighten(item)

    tighten(schema)
    for definition in schema.get("$defs", {}).values():
        tighten(definition)
    return schema
