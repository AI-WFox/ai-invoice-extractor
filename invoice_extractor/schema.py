"""Data model for an extracted invoice.

The same model is used for the LLM's structured output, the golden labels,
and the review queue, so every stage speaks one format.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    amount: float


class Invoice(BaseModel):
    vendor_name: str
    invoice_number: str
    invoice_date: str = Field(description="ISO date, YYYY-MM-DD")
    due_date: Optional[str] = Field(default=None, description="ISO date, YYYY-MM-DD, or null")
    currency: str = Field(description="ISO 4217 code, e.g. USD, EUR, VND")
    bill_to: Optional[str] = None
    line_items: list[LineItem]
    subtotal: float
    tax: float
    total: float
    uncertain_fields: list[str] = Field(
        default_factory=list,
        description="Names of fields the model could not read with confidence "
        "(blurry, missing, ambiguous). Empty if everything was clear.",
    )


# Fields compared one-to-one in evals (line items are scored separately).
HEADER_FIELDS = [
    "vendor_name",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "subtotal",
    "tax",
    "total",
]
