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
