"""Score extractions against golden labels.

The number a client cares about most is not average accuracy but the
*silent error rate*: documents that passed validation (so nobody looked at
them) and still had a wrong field. That is the cost of trusting automation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .schema import HEADER_FIELDS, Invoice
from .validate import validate


def _norm(value):
    if isinstance(value, str):
        return " ".join(value.lower().split())
    return value


def field_equal(pred, gold) -> bool:
    if isinstance(gold, (int, float)) and isinstance(pred, (int, float)):
        return abs(pred - gold) <= 0.01
    return _norm(pred) == _norm(gold)


def line_items_correct(pred: Invoice, gold: Invoice) -> bool:
    if len(pred.line_items) != len(gold.line_items):
        return False
    return all(
        field_equal(p.amount, g.amount) and field_equal(p.quantity, g.quantity)
        and field_equal(p.unit_price, g.unit_price)
        for p, g in zip(pred.line_items, gold.line_items)
    )


@dataclass
class DocScore:
    name: str
    wrong_fields: list[str]
    needs_review: bool

    @property
    def correct(self) -> bool:
        return not self.wrong_fields


@dataclass
class Report:
    docs: list[DocScore] = field(default_factory=list)
    field_hits: dict[str, int] = field(default_factory=dict)
    failed: int = 0

    def summary(self) -> dict:
        n = len(self.docs)
        if n == 0:
            return {"documents": 0}
        straight_through = [d for d in self.docs if not d.needs_review]
        silent_errors = [d for d in straight_through if not d.correct]
        caught = [d for d in self.docs if d.needs_review and not d.correct]
        wrong = [d for d in self.docs if not d.correct]
        return {
            "documents": n,
            "extraction_failed": self.failed,
            "fully_correct": round(sum(d.correct for d in self.docs) / n, 3),
            "field_accuracy": {f: round(h / n, 3) for f, h in self.field_hits.items()},
            "straight_through_rate": round(len(straight_through) / n, 3),
            "silent_error_rate": round(len(silent_errors) / n, 3),
            "errors_caught_by_validation": f"{len(caught)}/{len(wrong)}",
        }


def score(pred: Invoice, gold: Invoice, name: str, report: Report) -> DocScore:
    wrong = []
    for f in HEADER_FIELDS:
        ok = field_equal(getattr(pred, f), getattr(gold, f))
        report.field_hits[f] = report.field_hits.get(f, 0) + int(ok)
        if not ok:
            wrong.append(f)
    ok = line_items_correct(pred, gold)
    report.field_hits["line_items"] = report.field_hits.get("line_items", 0) + int(ok)
    if not ok:
        wrong.append("line_items")
    doc = DocScore(name=name, wrong_fields=wrong, needs_review=validate(pred).needs_review)
    report.docs.append(doc)
    return doc


def evaluate_dirs(pred_dir: Path, gold_dir: Path) -> Report:
    report = Report()
    for gold_path in sorted(gold_dir.glob("*.json")):
        pred_path = pred_dir / gold_path.name
        gold = Invoice.model_validate_json(gold_path.read_text(encoding="utf-8"))
        record = json.loads(pred_path.read_text(encoding="utf-8")) if pred_path.exists() else {}
        if not record.get("invoice"):
            # A missing or failed extraction counts as wrong on every field;
            # the pipeline sends it to review, so it is never a silent error.
            report.docs.append(DocScore(gold_path.stem, HEADER_FIELDS + ["line_items"], needs_review=True))
            for f in HEADER_FIELDS + ["line_items"]:
                report.field_hits.setdefault(f, 0)
            report.failed += 1
            continue
        pred = Invoice.model_validate(record["invoice"])
        score(pred, gold, gold_path.stem, report)
    return report
