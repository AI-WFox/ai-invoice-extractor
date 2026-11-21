"""Generate synthetic invoices with known ground truth.

Because we generate the documents ourselves, the golden labels are exact.
Layouts and quirks vary on purpose: European number format, VND without
decimals, missing due date, a skewed "scan" image, and one invoice whose
printed total is wrong (validation should catch it).

    python scripts/make_samples.py --out samples --n 12
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

# Built-in PDF fonts cannot draw Vietnamese or the dong sign, so use DejaVu when present.
_FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
if (_FONT_DIR / "DejaVuSans.ttf").exists():
    pdfmetrics.registerFont(TTFont("Body", str(_FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("Bold", str(_FONT_DIR / "DejaVuSans-Bold.ttf")))
    BODY, BOLD, ITAL = "Body", "Bold", "Body"
else:
    BODY, BOLD, ITAL = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"

VENDORS = [
    ("Northwind Logistics LLC", "USD", "$", "us"),
    ("Brightline Office Supply Inc.", "USD", "$", "us"),
    ("Kessler Maschinenbau GmbH", "EUR", "€", "eu"),
    ("Atelier Lumière SARL", "EUR", "€", "eu"),
    ("Công ty TNHH Sao Việt", "VND", "₫", "vnd"),
]
ITEMS = [
    ("Freight handling, pallet", 45.0), ("Warehouse storage (week)", 120.0),
    ("A4 copy paper, box of 5 reams", 32.5), ("Toner cartridge 85A", 68.9),
    ("Consulting hours", 95.0), ("Hydraulic seal kit", 210.4),
    ("Installation service", 350.0), ("Safety gloves, pack of 12", 18.75),
]
CUSTOMERS = ["Acme Retail Pte Ltd", "Blue Harbor Foods", "Delta Clinics Group", "Orion Studio"]


def money(x: float, style: str) -> str:
    if style == "vnd":
        return f"{x:,.0f}".replace(",", ".")
    if style == "eu":
        return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{x:,.2f}"


def fmt_date(d: date, style: str) -> str:
    return d.strftime("%m/%d/%Y") if style == "us" else d.strftime("%d.%m.%Y" if style == "eu" else "%d/%m/%Y")


def make_invoice(i: int, rng: random.Random) -> tuple[dict, dict]:
    vendor, cur, sym, style = VENDORS[i % len(VENDORS)]
    scale = 25000 if style == "vnd" else 1
    items = []
    for desc, price in rng.sample(ITEMS, rng.randint(2, 5)):
        qty = rng.randint(1, 12)
        unit = round(price * scale, 0 if style == "vnd" else 2)
        items.append({"description": desc, "quantity": qty, "unit_price": unit,
                      "amount": round(qty * unit, 2)})
    subtotal = round(sum(it["amount"] for it in items), 2)
    rate = {"us": 0.08, "eu": 0.19, "vnd": 0.10}[style]
    tax = round(subtotal * rate, 0 if style == "vnd" else 2)
    total = round(subtotal + tax, 2)
    issued = date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))
    due = None if i % 4 == 3 else issued + timedelta(days=30)
    gold = {
        "vendor_name": vendor, "invoice_number": f"INV-{2026}-{1000 + i * 37}",
        "invoice_date": issued.isoformat(), "due_date": due.isoformat() if due else None,
        "currency": cur, "bill_to": rng.choice(CUSTOMERS), "line_items": items,
        "subtotal": subtotal, "tax": tax, "total": total, "uncertain_fields": [],
    }
    render = {"style": style, "sym": sym, "rate": rate,
              "printed_total": total + (50 * scale if i == 7 else 0)}  # #7 has a misprinted total
    return gold, render


def draw_pdf(path: Path, g: dict, r: dict) -> None:
    s, sym = r["style"], r["sym"]
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont(BODY, 9)
    w, h = A4
    c.setFont(BOLD, 16)
    c.drawString(40, h - 60, g["vendor_name"])
    c.setFont(BODY, 9)
    c.drawString(40, h - 75, "123 Example Street, Example City")
    c.setFont(BOLD, 20)
    c.drawRightString(w - 40, h - 60, "INVOICE" if s != "vnd" else "HÓA ĐƠN / INVOICE")
    c.setFont(BODY, 10)
    y = h - 110
    labels = [("Invoice No.", g["invoice_number"]), ("Date", fmt_date(date.fromisoformat(g["invoice_date"]), s))]
    if g["due_date"]:
        labels.append(("Due", fmt_date(date.fromisoformat(g["due_date"]), s)))
    for k, v in labels:
        c.drawRightString(w - 140, y, k + ":")
        c.drawRightString(w - 40, y, v)
        y -= 14
    c.drawString(40, h - 110, "Bill to:")
    c.drawString(40, h - 124, g["bill_to"])
    y = h - 190
    c.setFont(BOLD, 10)
    for x, t in [(40, "Description"), (330, "Qty"), (400, "Unit price"), (500, "Amount")]:
        c.drawString(x, y, t)
    c.line(40, y - 4, w - 40, y - 4)
    c.setFont(BODY, 10)
    for it in g["line_items"]:
        y -= 18
        c.drawString(40, y, it["description"])
        c.drawString(330, y, str(it["quantity"]))
        c.drawString(400, y, money(it["unit_price"], s))
        c.drawString(500, y, money(it["amount"], s))
    y -= 30
    tax_label = {"us": "Sales tax 8%", "eu": "MwSt./TVA 19%", "vnd": "VAT 10%"}[s]
    for k, v in [("Subtotal", g["subtotal"]), (tax_label, g["tax"]), ("TOTAL", r["printed_total"])]:
        c.drawRightString(470, y, k)
        c.drawRightString(w - 40, y, f"{sym} {money(v, s)}")
        y -= 16
    c.setFont(ITAL, 8)
    c.drawString(40, 60, "Thank you for your business. Payment by bank transfer.")
    c.save()


def pdf_to_scan(pdf: Path, png: Path, rng: random.Random) -> None:
    """Render a PDF to a slightly rotated, grey, noisy image, like a phone scan."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageFilter

    img = pdfium.PdfDocument(str(pdf))[0].render(scale=1.6).to_pil().convert("L")
    img = img.rotate(rng.uniform(-2.5, 2.5), expand=True, fillcolor=235)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    noise = Image.effect_noise(img.size, 18).convert("L")
    Image.blend(img, noise, 0.08).save(png)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="samples")
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    docs, gold_dir = Path(a.out) / "docs", Path(a.out) / "gold"
    docs.mkdir(parents=True, exist_ok=True)
    gold_dir.mkdir(parents=True, exist_ok=True)
    for i in range(a.n):
        gold, render = make_invoice(i, rng)
        name = f"invoice_{i:02d}"
        pdf = docs / f"{name}.pdf"
        draw_pdf(pdf, gold, render)
        if i % 5 == 4:  # every fifth document arrives as a phone scan instead of a PDF
            pdf_to_scan(pdf, docs / f"{name}.png", rng)
            pdf.unlink()
        if i == 7:
            gold["total"] = render["printed_total"]  # the label is what's printed; validation must flag it
        (gold_dir / f"{name}.json").write_text(json.dumps(gold, indent=2, ensure_ascii=False))
    print(f"wrote {a.n} invoices to {docs} and labels to {gold_dir}")


if __name__ == "__main__":
    main()
