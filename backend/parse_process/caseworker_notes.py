#!/usr/bin/env python3
"""caseworker_notes.py — deterministic case note generator from a report JSON."""

import argparse
import json
from pathlib import Path
from process_parse import _try_parse_json


def money(x):
    return f"${x:,.2f}" if isinstance(x, (int, float)) else "unknown"


def build_case_note(report):
    profile = report.get("applicant_profile", report if isinstance(report, dict) else {})
    fpl = report.get("fpl_analysis") or profile.get("fpl_analysis") or {}
    lines = ["# Caseworker Intake Note", ""]
    lines.append("## Applicant Snapshot")
    lines.append(f"- Summary: {profile.get('summary') or 'n/a'}")
    loc = profile.get("location") or {}
    lines.append(f"- Location: {loc.get('city') or 'unknown'}, {loc.get('state') or report.get('resolved_state') or 'unknown'} {loc.get('zip') or ''}".strip())
    lines.append(f"- Household size: {profile.get('household_size') or 'unknown'}")
    lines.append(f"- Employment: {profile.get('employment_status') or 'unknown'}")
    lines.append(f"- Stated needs: {profile.get('stated_needs') or ', '.join(profile.get('needed_categories') or []) or 'unknown'}")
    lines.append("")
    lines.append("## Deterministic Income/FPL")
    lines.append(f"- Annual income used: {money(fpl.get('annual_income'))}")
    lines.append(f"- 100% FPL amount: {money(fpl.get('fpl_100_amount'))}")
    lines.append(f"- FPL percentage: {fpl.get('fpl_percent') if fpl.get('fpl_percent') is not None else 'unknown'}%")
    lines.append(f"- Calculation source: {((fpl.get('source') or {}).get('name')) or 'n/a'}")
    lines.append("")
    lines.append("## Program Screening Summary")
    programs = report.get("matched_programs") or []
    if not programs:
        lines.append("- No database programs matched the current state/category filters.")
    for m in programs:
        ea = m.get("eligibility_analysis") or {}
        lines.append(f"- {m.get('program_name')}: {ea.get('status')} ({ea.get('confidence')}) — {ea.get('summary')}")
    lines.append("")
    lines.append("## Follow-up Needed")
    missing = []
    for m in programs:
        missing.extend((m.get("eligibility_analysis") or {}).get("missing_information") or [])
    if missing:
        for item in sorted(set(missing)):
            lines.append(f"- {item}")
    else:
        lines.append("- Confirm details directly with administering agencies; this is not an official eligibility determination.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Create caseworker-facing notes from assistance report JSON.")
    ap.add_argument("--report", required=True)
    ap.add_argument("-o", "--output", default="case_note.md")
    args = ap.parse_args()
    raw = Path(args.report).read_text(encoding="utf-8", errors="replace")
    parsed = _try_parse_json(raw)
    if not isinstance(parsed, dict):
        raise SystemExit("Report must be JSON for deterministic case notes.")
    note = build_case_note(parsed)
    Path(args.output).write_text(note, encoding="utf-8")
    print(f"Saved {args.output}")

if __name__ == "__main__":
    main()
