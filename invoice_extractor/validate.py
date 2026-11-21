"""Deterministic checks that decide whether an extraction can skip human review.

The LLM reads the page; plain code checks the arithmetic and the format.
Anything that fails goes to the review queue instead of straight into the books.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .schema import Invoice

TOLERANCE = 0.01  # one cent
SCAN_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def is_scan(filename: str) -> bool:
    """Photos and scans, as opposed to digital PDFs."""
    return any(filename.lower().endswith(s) for s in SCAN_SUFFIXES)


@dataclass
class ValidationResult:
    issues: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return bool(self.issues)


def _is_iso_date(value: str | None) -> bool:
    if not value:
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def validate(inv: Invoice, scan: bool = False) -> ValidationResult:
    """Check one extraction. scan=True adds the scan policy: a model can misread
    every digit the same way on a blurry photo, so the arithmetic still adds up
    and only a person can catch it."""
    r = ValidationResult()
    if scan:
        r.issues.append("scan or photo: a person should confirm the amounts")
    if not inv.vendor_name.strip():
        r.issues.append("vendor_name is empty")
    if not inv.invoice_number.strip():
        r.issues.append("invoice_number is empty")
    if not _is_iso_date(inv.invoice_date):
        r.issues.append(f"invoice_date not a valid date: {inv.invoice_date!r}")
    if inv.due_date and not _is_iso_date(inv.due_date):
        r.issues.append(f"due_date not a valid date: {inv.due_date!r}")
    if _is_iso_date(inv.invoice_date) and _is_iso_date(inv.due_date) and inv.due_date < inv.invoice_date:
        r.issues.append("due_date is before invoice_date")
    if len(inv.currency) != 3 or not inv.currency.isalpha():
        r.issues.append(f"currency not an ISO code: {inv.currency!r}")
    if not inv.line_items:
        r.issues.append("no line items")
    for i, li in enumerate(inv.line_items, 1):
        if abs(li.quantity * li.unit_price - li.amount) > max(TOLERANCE, 0.005 * abs(li.amount)):
            r.issues.append(f"line {i}: quantity x unit_price != amount")
    items_sum = sum(li.amount for li in inv.line_items)
    if inv.line_items and abs(items_sum - inv.subtotal) > TOLERANCE:
        r.issues.append(f"line items sum {items_sum:.2f} != subtotal {inv.subtotal:.2f}")
    if abs(inv.subtotal + inv.tax - inv.total) > TOLERANCE:
        r.issues.append(f"subtotal + tax {inv.subtotal + inv.tax:.2f} != total {inv.total:.2f}")
    for name in inv.uncertain_fields:
        r.issues.append(f"model unsure about {name}")
    return r
