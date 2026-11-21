"""Human review queue: approve or correct extractions that failed validation.

    streamlit run review_app.py -- --results out --docs samples/docs
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import streamlit as st

from invoice_extractor.schema import Invoice
from invoice_extractor.validate import validate

ap = argparse.ArgumentParser()
ap.add_argument("--results", default="out")
ap.add_argument("--docs", default="samples/docs")
args, _ = ap.parse_known_args()
results, docs = Path(args.results), Path(args.docs)
approved_dir = results / "approved"
approved_dir.mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="Invoice review", layout="wide")
records = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(results.glob("*.json"))}
queue = [k for k, r in records.items() if r["status"] == "needs_review" and not (approved_dir / f"{k}.json").exists()]
done = len(list(approved_dir.glob("*.json")))
auto = sum(r["status"] == "auto_approved" for r in records.values())

st.title("Invoice review queue")
c1, c2, c3 = st.columns(3)
c1.metric("Auto-approved", auto)
c2.metric("Waiting for review", len(queue))
c3.metric("Reviewed", done)
if not queue:
    st.success("Nothing left to review.")
    st.stop()

name = st.sidebar.radio("Documents", queue)
rec = records[name]
left, right = st.columns([1, 1])

with left:
    src = docs / rec["source"]
    if src.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        st.image(pdfium.PdfDocument(str(src))[0].render(scale=1.5).to_pil(), width="stretch")
    else:
        st.image(str(src), width="stretch")

with right:
    for issue in rec["issues"]:
        st.warning(issue)
    inv = rec["invoice"] or Invoice(vendor_name="", invoice_number="", invoice_date="", currency="",
                                     line_items=[], subtotal=0, tax=0, total=0).model_dump()
    with st.form("edit"):
        inv["vendor_name"] = st.text_input("Vendor", inv["vendor_name"])
        inv["invoice_number"] = st.text_input("Invoice number", inv["invoice_number"])
        inv["invoice_date"] = st.text_input("Invoice date (YYYY-MM-DD)", inv["invoice_date"])
        inv["due_date"] = st.text_input("Due date", inv["due_date"] or "") or None
        inv["currency"] = st.text_input("Currency", inv["currency"])
        edited = st.data_editor(inv["line_items"], num_rows="dynamic", width="stretch")
        inv["subtotal"] = st.number_input("Subtotal", value=float(inv["subtotal"]))
        inv["tax"] = st.number_input("Tax", value=float(inv["tax"]))
        inv["total"] = st.number_input("Total", value=float(inv["total"]))
        if st.form_submit_button("Approve"):
            inv["line_items"] = edited
            inv["uncertain_fields"] = []
            checked = Invoice.model_validate(inv)
            remaining = validate(checked).issues
            (approved_dir / f"{name}.json").write_text(json.dumps(
                {"source": rec["source"], "invoice": checked.model_dump(), "reviewer_notes": remaining},
                indent=2, ensure_ascii=False), encoding="utf-8")
            st.rerun()
