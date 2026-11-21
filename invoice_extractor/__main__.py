"""Command line: run extraction on a folder, or evaluate results.

    python -m invoice_extractor run samples/docs --out out                    # Claude
    python -m invoice_extractor run samples/docs --out out --backend gemini   # Gemini free tier
    python -m invoice_extractor eval out samples/gold
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SUPPORTED = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


def _make_extractor(args):
    """Return (extract_fn, errors_to_catch) for the chosen backend."""
    from .extract import ExtractionError

    if args.backend == "gemini":
        from google import genai
        from google.genai import errors

        from .extract_gemini import extract_invoice_gemini

        client = genai.Client()
        return (lambda path: extract_invoice_gemini(path, client=client),
                (ExtractionError, errors.APIError, ValueError))

    import anthropic

    from .extract import extract_invoice

    client = anthropic.Anthropic()
    return (lambda path: extract_invoice(path, client=client, effort=args.effort),
            (ExtractionError, anthropic.APIError, ValueError))


def cmd_run(args) -> int:
    from .validate import validate

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in Path(args.input).iterdir() if p.suffix.lower() in SUPPORTED)
    extract, failures = _make_extractor(args)
    workers = args.workers or (1 if args.backend == "gemini" else 4)

    def work(path: Path):
        try:
            inv = extract(path)
        except failures as e:
            return path, None, [f"extraction failed: {e}"]
        return path, inv, validate(inv).issues

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for path, inv, issues in pool.map(work, files):
            status = "needs_review" if issues else "auto_approved"
            record = {"source": path.name, "status": status, "issues": issues,
                      "invoice": inv.model_dump() if inv else None}
            (out / f"{path.stem}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
            rows.append({"file": path.name, "status": status,
                         "vendor": inv.vendor_name if inv else "", "invoice_number": inv.invoice_number if inv else "",
                         "date": inv.invoice_date if inv else "", "currency": inv.currency if inv else "",
                         "total": inv.total if inv else "", "issues": "; ".join(issues)})
            print(f"{status:14} {path.name}" + (f"  ({issues[0]})" if issues else ""))

    with open(out / "results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["file"])
        writer.writeheader()
        writer.writerows(rows)
    auto = sum(r["status"] == "auto_approved" for r in rows)
    print(f"\n{len(rows)} documents, {auto} auto-approved, {len(rows) - auto} sent to review -> {out}/results.csv")
    return 0


def cmd_eval(args) -> int:
    from .evaluate import evaluate_dirs

    report = evaluate_dirs(Path(args.pred), Path(args.gold))
    print(json.dumps(report.summary(), indent=2))
    for d in report.docs:
        if d.wrong_fields:
            flag = "caught" if d.needs_review else "SILENT"
            print(f"  {flag:7} {d.name}: {', '.join(d.wrong_fields)}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="invoice_extractor")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="extract every PDF/image in a folder")
    r.add_argument("input")
    r.add_argument("--out", default="out")
    r.add_argument("--backend", default="claude", choices=["claude", "gemini"],
                   help="claude (ANTHROPIC_API_KEY) or gemini (GEMINI_API_KEY, free tier)")
    r.add_argument("--workers", type=int, default=None,
                   help="parallel requests (default 4 for Claude, 1 for Gemini's free tier)")
    r.add_argument("--effort", default="medium", choices=["low", "medium", "high"],
                   help="Claude only")
    r.set_defaults(func=cmd_run)
    e = sub.add_parser("eval", help="score extractions against golden labels")
    e.add_argument("pred")
    e.add_argument("gold")
    e.set_defaults(func=cmd_eval)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
