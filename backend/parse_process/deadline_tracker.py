#!/usr/bin/env python3
"""deadline_tracker.py — deterministic deadline and recertification tracker."""

import argparse
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from process_parse import _try_parse_json

MONTHS = {m.lower(): i for i, m in enumerate([
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December"], start=1)}

DATE_PATTERNS = [
    re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b"),
    re.compile(r"\b(" + "|".join(MONTHS.keys()) + r")\s+(\d{1,2}),\s*(20\d{2})\b", re.I),
]
KEYWORDS = ["deadline", "due", "renew", "recert", "redetermination", "expires", "expiration", "appeal", "hearing", "appointment"]

def parse_dates(text):
    found = []
    for pat in DATE_PATTERNS:
        for m in pat.finditer(text):
            try:
                if pat.pattern.startswith('\\b(20'):
                    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                elif '/' in pat.pattern:
                    d = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
                else:
                    d = date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
                start = max(0, m.start() - 160); end = min(len(text), m.end() + 160)
                context = re.sub(r"\s+", " ", text[start:end]).strip()
                found.append({"date": d.isoformat(), "context": context})
            except ValueError:
                pass
    return found

def score_item(context):
    low = context.lower()
    return sum(1 for k in KEYWORDS if k in low)

def build_deadline_report(text, window_days=30):
    today = date.today()
    items = []
    for item in parse_dates(text):
        d = date.fromisoformat(item["date"])
        days = (d - today).days
        if days >= 0:
            item["days_until"] = days
            item["urgency"] = "due_now" if days <= 7 else "soon" if days <= window_days else "future"
            item["relevance_score"] = score_item(item["context"])
            if item["relevance_score"] > 0 or days <= window_days:
                items.append(item)
    items.sort(key=lambda x: (x["days_until"], -x["relevance_score"]))
    return {"generated_on": today.isoformat(), "window_days": window_days, "deadlines": items}

def main():
    ap = argparse.ArgumentParser(description="Extract and track benefit-related dates/deadlines from reports, letters, or parsed text.")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="deadlines.json")
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--markdown", help="Optional markdown output path")
    args = ap.parse_args()
    raw = Path(args.input).read_text(encoding="utf-8", errors="replace")
    parsed = _try_parse_json(raw)
    text = json.dumps(parsed, ensure_ascii=False, indent=2) if parsed is not None else raw
    report = build_deadline_report(text, args.window_days)
    Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.markdown:
        lines = ["# Deadline Watch", "", f"Generated: {report['generated_on']}", ""]
        if not report["deadlines"]:
            lines.append("No relevant upcoming dates found.")
        for d in report["deadlines"]:
            lines.append(f"- **{d['date']}** ({d['days_until']} days, {d['urgency']}): {d['context']}")
        Path(args.markdown).write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved {args.output}")

if __name__ == "__main__":
    main()
