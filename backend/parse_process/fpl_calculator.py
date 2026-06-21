#!/usr/bin/env python3
"""
fpl_calculator.py — deterministic Federal Poverty Level calculations
=====================================================================
Keeps income-threshold arithmetic out of the LLM. The LLM may extract messy
facts and explain qualitative criteria, but this module owns FPL math.

The default table is the 2026 HHS Poverty Guidelines published in the Federal
Register on 2026-01-15. Values are annual dollars.

Usage:
  python fpl_calculator.py --income 21600 --household-size 3 --state IL
  python fpl_calculator.py --income 21600 --household-size 3 --state AK --percent 130

Import:
  from fpl_calculator import enrich_profile_with_fpl, deterministic_program_checks
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

FPL_SOURCE = {
    "year": 2026,
    "name": "Annual Update of the HHS Poverty Guidelines",
    "publisher": "U.S. Department of Health and Human Services / Federal Register",
    "published": "2026-01-15",
    "url": "https://www.federalregister.gov/documents/2026/01/15/2026-00755/annual-update-of-the-hhs-poverty-guidelines",
}

# 2026 HHS Poverty Guidelines, annual dollars.
FPL_TABLES = {
    2026: {
        "48_CONTIGUOUS_DC": {
            "label": "48 contiguous states and DC",
            "base": {1: 15960, 2: 21640, 3: 27320, 4: 33000, 5: 38680, 6: 44360, 7: 50040, 8: 55720},
            "addl": 5680,
        },
        "AK": {
            "label": "Alaska",
            "base": {1: 19950, 2: 27050, 3: 34150, 4: 41250, 5: 48350, 6: 55450, 7: 62550, 8: 69650},
            "addl": 7100,
        },
        "HI": {
            "label": "Hawaii",
            "base": {1: 18360, 2: 24890, 3: 31420, 4: 37950, 5: 44480, 6: 51010, 7: 57540, 8: 64070},
            "addl": 6530,
        },
    }
}

ALASKA = {"AK", "ALASKA"}
HAWAII = {"HI", "HAWAII"}

STATE_TO_REGION = {
    "AK": "AK",
    "HI": "HI",
    "AL": "48_CONTIGUOUS_DC", "AZ": "48_CONTIGUOUS_DC", "AR": "48_CONTIGUOUS_DC",
    "CA": "48_CONTIGUOUS_DC", "CO": "48_CONTIGUOUS_DC", "CT": "48_CONTIGUOUS_DC",
    "DE": "48_CONTIGUOUS_DC", "DC": "48_CONTIGUOUS_DC", "FL": "48_CONTIGUOUS_DC",
    "GA": "48_CONTIGUOUS_DC", "ID": "48_CONTIGUOUS_DC", "IL": "48_CONTIGUOUS_DC",
    "IN": "48_CONTIGUOUS_DC", "IA": "48_CONTIGUOUS_DC", "KS": "48_CONTIGUOUS_DC",
    "KY": "48_CONTIGUOUS_DC", "LA": "48_CONTIGUOUS_DC", "ME": "48_CONTIGUOUS_DC",
    "MD": "48_CONTIGUOUS_DC", "MA": "48_CONTIGUOUS_DC", "MI": "48_CONTIGUOUS_DC",
    "MN": "48_CONTIGUOUS_DC", "MS": "48_CONTIGUOUS_DC", "MO": "48_CONTIGUOUS_DC",
    "MT": "48_CONTIGUOUS_DC", "NE": "48_CONTIGUOUS_DC", "NV": "48_CONTIGUOUS_DC",
    "NH": "48_CONTIGUOUS_DC", "NJ": "48_CONTIGUOUS_DC", "NM": "48_CONTIGUOUS_DC",
    "NY": "48_CONTIGUOUS_DC", "NC": "48_CONTIGUOUS_DC", "ND": "48_CONTIGUOUS_DC",
    "OH": "48_CONTIGUOUS_DC", "OK": "48_CONTIGUOUS_DC", "OR": "48_CONTIGUOUS_DC",
    "PA": "48_CONTIGUOUS_DC", "RI": "48_CONTIGUOUS_DC", "SC": "48_CONTIGUOUS_DC",
    "SD": "48_CONTIGUOUS_DC", "TN": "48_CONTIGUOUS_DC", "TX": "48_CONTIGUOUS_DC",
    "UT": "48_CONTIGUOUS_DC", "VT": "48_CONTIGUOUS_DC", "VA": "48_CONTIGUOUS_DC",
    "WA": "48_CONTIGUOUS_DC", "WV": "48_CONTIGUOUS_DC", "WI": "48_CONTIGUOUS_DC",
    "WY": "48_CONTIGUOUS_DC",
}


def parse_money(value: Any) -> Optional[float]:
    """Parse numbers like 21600, '$21,600', '1,420 before taxes'."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isnan(value) if isinstance(value, float) else False:
            return None
        return float(value)
    s = str(value)
    m = re.search(r"-?\$?\s*([0-9][0-9,]*(?:\.\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    m = re.search(r"\d+", str(value))
    return int(m.group(0)) if m else None


def normalize_region(state_or_region: Optional[str]) -> str:
    if not state_or_region:
        return "48_CONTIGUOUS_DC"
    raw = str(state_or_region).strip().upper()
    if raw in {"48", "48_CONTIGUOUS", "48_CONTIGUOUS_DC", "CONTIGUOUS", "DC"}:
        return "48_CONTIGUOUS_DC"
    if raw in ALASKA:
        return "AK"
    if raw in HAWAII:
        return "HI"
    return STATE_TO_REGION.get(raw, "48_CONTIGUOUS_DC")


def fpl_guideline(household_size: int, state: Optional[str] = None, year: int = 2026) -> int:
    household_size = parse_int(household_size) or 1
    if household_size < 1:
        household_size = 1
    region = normalize_region(state)
    table = FPL_TABLES[year][region]
    if household_size <= 8:
        return table["base"][household_size]
    return table["base"][8] + table["addl"] * (household_size - 8)


def fpl_percent(annual_income: Any, household_size: Any, state: Optional[str] = None, year: int = 2026) -> Optional[float]:
    income = parse_money(annual_income)
    hh = parse_int(household_size)
    if income is None or hh is None or hh < 1:
        return None
    base = fpl_guideline(hh, state, year)
    return round((income / base) * 100.0, 1)


def fpl_threshold_amount(percent: float, household_size: Any, state: Optional[str] = None, year: int = 2026) -> Optional[float]:
    hh = parse_int(household_size)
    if hh is None or hh < 1:
        return None
    base = fpl_guideline(hh, state, year)
    return round(base * (float(percent) / 100.0), 2)


def derive_annual_income(profile: dict) -> tuple[Optional[float], str]:
    """
    Return annualized income and source label. Does not infer from prose unless
    the profile explicitly provides annual_income/monthly_income/income fields.
    """
    if not isinstance(profile, dict):
        return None, "missing"

    annual = parse_money(profile.get("annual_income"))
    if annual is not None:
        return annual, "annual_income"

    monthly = parse_money(profile.get("monthly_income"))
    if monthly is not None:
        return monthly * 12.0, "monthly_income_x12"

    income_obj = profile.get("income")
    if isinstance(income_obj, dict):
        annual = parse_money(income_obj.get("annual"))
        if annual is not None:
            return annual, "income.annual"
        monthly = parse_money(income_obj.get("monthly"))
        if monthly is not None:
            return monthly * 12.0, "income.monthly_x12"

    return None, "missing"


def calculate_fpl_snapshot(profile: dict, state: Optional[str] = None, year: int = 2026) -> dict:
    profile = profile or {}
    hh = parse_int(profile.get("household_size"))
    state_used = state
    if not state_used:
        loc = profile.get("location") if isinstance(profile.get("location"), dict) else {}
        state_used = loc.get("state")

    annual, income_source = derive_annual_income(profile)
    region = normalize_region(state_used)

    snapshot = {
        "year": year,
        "source": FPL_SOURCE,
        "state_input": state_used,
        "region": region,
        "household_size": hh,
        "annual_income": round(annual, 2) if annual is not None else None,
        "income_source": income_source,
        "fpl_100_amount": None,
        "fpl_percent": None,
        "thresholds": {},
        "calculation_available": False,
        "notes": [],
    }

    if hh is None or hh < 1:
        snapshot["notes"].append("Household size missing or invalid; FPL cannot be calculated.")
        return snapshot
    snapshot["fpl_100_amount"] = fpl_guideline(hh, state_used, year)
    for pct in (100, 125, 130, 133, 138, 150, 185, 200, 250, 300):
        snapshot["thresholds"][str(pct)] = fpl_threshold_amount(pct, hh, state_used, year)

    if annual is None:
        snapshot["notes"].append("Annual or monthly income missing; FPL percentage cannot be calculated.")
        return snapshot

    snapshot["fpl_percent"] = fpl_percent(annual, hh, state_used, year)
    snapshot["calculation_available"] = True
    return snapshot


def enrich_profile_with_fpl(profile: dict, state: Optional[str] = None, year: int = 2026) -> dict:
    enriched = copy.deepcopy(profile or {})
    enriched["fpl_analysis"] = calculate_fpl_snapshot(enriched, state, year)
    return enriched


def calculate_program_income_check(program: dict, profile: dict, state: Optional[str] = None, year: int = 2026) -> dict:
    rules = (program or {}).get("income_rules") or {}
    limit = rules.get("fpl_limit_percent")
    fpl = (profile or {}).get("fpl_analysis") or calculate_fpl_snapshot(profile, state, year)
    result = {
        "applies": bool(limit),
        "basis": rules.get("basis", "FPL") if limit else None,
        "fpl_limit_percent": limit,
        "applicant_fpl_percent": fpl.get("fpl_percent"),
        "threshold_amount": None,
        "assessment": "not_applicable",
        "explanation": "No structured FPL income rule is defined for this program.",
    }
    if not limit:
        return result
    result["threshold_amount"] = fpl.get("thresholds", {}).get(str(int(limit))) or fpl_threshold_amount(
        float(limit), fpl.get("household_size"), fpl.get("state_input"), year
    )
    if not fpl.get("calculation_available"):
        result["assessment"] = "unclear"
        result["explanation"] = "FPL limit exists, but income or household size is missing, so deterministic income eligibility cannot be calculated."
    elif float(fpl["fpl_percent"]) <= float(limit):
        result["assessment"] = "met"
        result["explanation"] = f"Applicant is at {fpl['fpl_percent']}% FPL, which is at/below the program limit of {limit}% FPL."
    else:
        result["assessment"] = "not_met"
        result["explanation"] = f"Applicant is at {fpl['fpl_percent']}% FPL, which is above the program limit of {limit}% FPL."
    return result


def deterministic_program_checks(program: dict, profile: dict, state_code: Optional[str] = None, needed_categories: Optional[list] = None) -> dict:
    program = program or {}
    profile = profile or {}
    needed_categories = needed_categories or profile.get("needed_categories") or []
    p_states = {str(s).upper() for s in program.get("applicable_states", ["ALL"])}
    categories = {str(c).lower() for c in program.get("category", [])}
    need_set = {str(c).lower() for c in needed_categories}

    state_ok = "ALL" in p_states or (state_code and str(state_code).upper() in p_states)
    cat_overlap = sorted(categories & need_set)
    income_check = calculate_program_income_check(program, profile, state_code)

    checks = [
        {
            "name": "state_scope",
            "assessment": "met" if state_ok else "not_met",
            "explanation": f"Program applies to {sorted(p_states)}; resolved applicant state is {state_code or 'unknown'}.",
        },
        {
            "name": "category_overlap",
            "assessment": "met" if cat_overlap or not need_set else "unclear",
            "explanation": f"Overlap between applicant needs and program categories: {', '.join(cat_overlap) if cat_overlap else 'none found'}.",
        },
    ]
    if income_check["applies"]:
        checks.append({
            "name": "structured_fpl_income_check",
            "assessment": income_check["assessment"],
            "explanation": income_check["explanation"],
        })

    hard_fail = any(c["assessment"] == "not_met" and c["name"] in {"state_scope", "structured_fpl_income_check"} for c in checks)
    return {
        "checks": checks,
        "income_check": income_check,
        "hard_fail": hard_fail,
        "calculation_engine": "fpl_calculator.py",
        "notes": "Deterministic checks are screening aids, not official determinations.",
    }


def main():
    ap = argparse.ArgumentParser(description="Calculate deterministic FPL percentages and thresholds.")
    ap.add_argument("--income", required=True, help="Annual income, e.g. 21600 or '$21,600'.")
    ap.add_argument("--household-size", required=True, type=int)
    ap.add_argument("--state", default="")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--percent", type=float, default=100.0, help="Threshold percent to compute, e.g. 130.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    base = fpl_guideline(args.household_size, args.state, args.year)
    pct = fpl_percent(args.income, args.household_size, args.state, args.year)
    thresh = fpl_threshold_amount(args.percent, args.household_size, args.state, args.year)
    result = {
        "income": parse_money(args.income),
        "household_size": args.household_size,
        "state": args.state,
        "year": args.year,
        "fpl_100_amount": base,
        "fpl_percent": pct,
        f"threshold_{args.percent:g}_percent": thresh,
        "source": FPL_SOURCE,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Income: ${result['income']:,.2f}")
        print(f"Household size: {args.household_size}")
        print(f"100% FPL: ${base:,.2f}")
        print(f"Applicant FPL: {pct}%")
        print(f"{args.percent:g}% FPL threshold: ${thresh:,.2f}")


if __name__ == "__main__":
    main()
