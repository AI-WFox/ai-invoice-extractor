import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from invoice_extractor.evaluate import Report, evaluate_dirs, score
from invoice_extractor.extract import ExtractionError, extract_invoice, invoice_json_schema
from invoice_extractor.schema import Invoice
from invoice_extractor.validate import validate

GOLD = Path(__file__).parent.parent / "samples" / "gold"


def gold(name: str) -> Invoice:
    return Invoice.model_validate_json((GOLD / f"{name}.json").read_text())


def test_clean_invoices_pass_validation():
    for path in GOLD.glob("*.json"):
        if path.stem == "invoice_07":
            continue
        assert validate(gold(path.stem)).issues == [], path.stem


def test_misprinted_total_is_flagged():
    issues = validate(gold("invoice_07")).issues
    assert any("subtotal + tax" in i for i in issues)


def test_wrong_line_amount_is_flagged():
    inv = gold("invoice_00").model_copy(deep=True)
    inv.line_items[0].amount += 10
    assert validate(inv).needs_review


def test_uncertain_field_routes_to_review():
    inv = gold("invoice_01").model_copy(update={"uncertain_fields": ["due_date"]})
    assert validate(inv).needs_review


def test_eval_counts_silent_errors():
    report = Report()
    good = gold("invoice_00")
    score(good, good, "a", report)
    # Wrong vendor name but arithmetic still adds up: passes validation, so it is a silent error.
    bad = good.model_copy(update={"vendor_name": "Someone Else"})
    score(bad, good, "b", report)
    s = report.summary()
    assert s["fully_correct"] == 0.5
    assert s["silent_error_rate"] == 0.5


def test_evaluate_dirs_with_perfect_predictions(tmp_path):
    for p in GOLD.glob("*.json"):
        (tmp_path / p.name).write_text(json.dumps({"invoice": json.loads(p.read_text())}))
    s = evaluate_dirs(tmp_path, GOLD).summary()
    assert s["fully_correct"] == 1.0
    assert s["silent_error_rate"] == 0.0


def test_schema_is_strict():
    schema = invoice_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["$defs"]["LineItem"]["additionalProperties"] is False


class FakeClient:
    """Stands in for anthropic.Anthropic so tests run without an API key."""

    def __init__(self, payload: dict, stop_reason: str = "end_turn"):
        self.calls = []
        create = lambda **kw: (self.calls.append(kw) or SimpleNamespace(
            stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=json.dumps(payload))]))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


def test_extract_sends_pdf_and_parses_result():
    payload = json.loads((GOLD / "invoice_00.json").read_text())
    client = FakeClient(payload)
    inv = extract_invoice(GOLD.parent / "docs" / "invoice_00.pdf", client=client)
    assert inv == gold("invoice_00")
    block = client.calls[0]["messages"][0]["content"][0]
    assert block["type"] == "document"


def test_extract_raises_on_refusal():
    client = FakeClient({}, stop_reason="refusal")
    with pytest.raises(ExtractionError):
        extract_invoice(GOLD.parent / "docs" / "invoice_04.png", client=client)


class FakeGemini:
    """Stands in for google.genai.Client; fails with HTTP 503/429 `rate_limited` times first."""

    def __init__(self, payload: dict, rate_limited: int = 0):
        self.calls = []
        self.rate_limited = rate_limited

        def generate_content(**kw):
            from google.genai import errors

            self.calls.append(kw)
            if self.rate_limited:
                code = 429 if self.rate_limited % 2 else 503
                self.rate_limited -= 1
                raise errors.APIError(code, {"error": {"code": code, "message": "busy", "status": "UNAVAILABLE"}})
            return SimpleNamespace(text=json.dumps(payload))

        self.models = SimpleNamespace(generate_content=generate_content)


def test_gemini_backend_parses_result_and_retries_rate_limits(monkeypatch):
    pytest.importorskip("google.genai")
    from invoice_extractor import extract_gemini

    monkeypatch.setattr(extract_gemini.time, "sleep", lambda s: None)
    payload = json.loads((GOLD / "invoice_00.json").read_text())
    client = FakeGemini(payload, rate_limited=2)
    docs = GOLD.parent / "docs"
    inv = extract_gemini.extract_invoice_gemini(docs / "invoice_00.pdf", client=client)
    assert inv == Invoice.model_validate(payload)
    assert len(client.calls) == 3
    part = client.calls[-1]["contents"][0]
    assert part.inline_data.mime_type == "application/pdf"
    assert client.calls[-1]["config"].response_schema is Invoice
