#!/usr/bin/env python3
"""
assistance_finder.py — Assistance Program Matcher & Eligibility Analyzer
==========================================================================
Takes the structured output of process_parse.py plus chatbot conversation
history, builds an applicant profile using a local SLM, matches it against
a static database of assistance programs, and produces a DETAILED
eligibility analysis for each candidate program (never a flat yes/no).

If nothing in the static database looks like a real match, it falls back
to a web search (DuckDuckGo HTML — no API key needed) for other, often
non-governmental/nonprofit, programs that might help.

Everything runs locally except the optional web-search fallback step,
which only ever sends generic category/location search terms to the
search engine — never the applicant's personal details.

This tool is NOT a benefits caseworker and produces NO official
determinations. See DISCLAIMER below; it is attached to every report.

Usage
-----
  # From a file (process_parse.py output) + conversation transcripts
  python assistance_finder.py profile.json \\
      --conversation-file chat1.txt --conversation-file chat2.txt \\
      --location California -o report.md

  # Piped straight from process_parse.py
  python process_parse.py parsed.txt --format json | \\
      python assistance_finder.py - --conversation-text "I lost my job last month..."

As a library
------------
  from assistance_finder import find_assistance
  from process_parse import build_backend

  backend = build_backend("ollama", None, "llama3.2:3b", 0.1, 2048, 120, quiet=True)
  report = find_assistance(
      document_data=extractor_output,      # dict or str from process_parse.py
      conversations=[chat1_text, chat2_text],
      location="TX",
      backend=backend,
  )

Options
-------
  doc_output               Path to process_parse.py output, or '-' for stdin
  --conversation-file PATH Conversation transcript file (repeatable)
  --conversation-text TEXT Conversation transcript as inline text (repeatable)
  --location STATE         Override/seed location (state name or abbreviation)
  --db PATH                Path to programs_db.json (default: auto-locate)
  --backend NAME           ollama | lmstudio | llamacpp | openai (default: auto)
  --host URL                Override backend base URL
  --model NAME              Model name/tag
  --temperature F           Sampling temperature (default: 0.1)
  --max-tokens N             Max tokens per response (default: 2048)
  --timeout S                HTTP timeout seconds (default: 120)
  --max-db-programs N        Cap on database candidates assessed (default: 8)
  --max-web-programs N       Cap on web-search programs assessed (default: 4)
  --no-web-fallback          Disable the web-search fallback step
  -o, --output PATH          Save report to file (default: print to stdout)
  --output-format FMT        markdown | json (default: markdown)
  --quiet / -q                Suppress progress messages
  -h, --help                  Show this help
"""

import argparse
import html
import json
import re
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional


# Import LLM backend infrastructure from process_parse.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from process_parse import (
        BACKENDS,
        build_backend,
        chunk_text,
        _try_parse_json,
        _fix_stdout_encoding,
    )
except ImportError:
    sys.exit(
        "[ERROR] Could not import process_parse.py.\n"
        "Place assistance_finder.py in the same directory as process_parse.py,\n"
        "or make sure process_parse.py is importable on your PYTHONPATH."
    )


# Import deterministic FPL/rules engine.
# These functions keep income-threshold math out of the LLM.
try:
    from fpl_calculator import enrich_profile_with_fpl, deterministic_program_checks
except ImportError:
    sys.exit(
        "[ERROR] Could not import fpl_calculator.py.\n"
        "Place fpl_calculator.py in the same directory as assistance_finder.py."
    )


# Logging
def log(msg: str, quiet: bool = False):
    if not quiet:
        print(f"[assistance_finder] {msg}", file=sys.stderr, flush=True)


# Constants
CATEGORIES = [
    "food", "healthcare", "housing", "utility", "cash_assistance",
    "disability", "veteran", "education", "childcare", "employment",
    "disaster_relief", "legal_aid", "general",
]

CATEGORY_SEARCH_TERMS = {
    "food": "food assistance pantry",
    "healthcare": "medical financial assistance free clinic",
    "housing": "housing assistance rental aid",
    "utility": "utility bill assistance",
    "cash_assistance": "emergency financial assistance",
    "disability": "disability assistance services",
    "veteran": "veteran assistance services",
    "education": "education scholarship financial aid",
    "childcare": "childcare assistance subsidy",
    "employment": "job training employment assistance",
    "disaster_relief": "disaster relief assistance",
    "legal_aid": "free legal aid assistance",
    "general": "community assistance program",
}

ELIGIBILITY_STATUSES = [
    "eligible",
    "likely_eligible",
    "possibly_eligible_more_info_needed",
    "likely_ineligible",
    "ineligible",
    "insufficient_information",
]

QUALIFYING_STATUSES = {
    "eligible", "likely_eligible", "possibly_eligible_more_info_needed",
}

STATUS_LABELS = {
    "eligible": "ELIGIBLE",
    "likely_eligible": "LIKELY ELIGIBLE",
    "possibly_eligible_more_info_needed": "POSSIBLY ELIGIBLE — more info needed",
    "likely_ineligible": "LIKELY NOT ELIGIBLE",
    "ineligible": "NOT ELIGIBLE",
    "insufficient_information": "INSUFFICIENT INFORMATION",
}

DISCLAIMER = (
    "This report was generated by a local language model for informational "
    "purposes only. It is NOT an official eligibility determination, and the "
    "people building or relying on this tool should treat it as a starting "
    "point, not an authority. Program rules, income limits, and availability "
    "change frequently and vary by state and county. Always confirm details "
    "directly with the administering agency or organization. In the United "
    "States, dialing 2-1-1 connects to a free local helpline that can also "
    "help navigate options and confirm eligibility in person."
)

DDG_SEARCH_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "puerto rico": "PR", "guam": "GU",
}
VALID_STATE_ABBREVS = set(US_STATES.values())


# State normalization
def normalize_state(raw: Optional[str]) -> Optional[str]:
    """Turn 'California', 'ca', 'Houston, TX' etc. into a 2-letter code."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    upper = raw.upper()
    if upper in VALID_STATE_ABBREVS:
        return upper
    lower = raw.lower()
    if lower in US_STATES:
        return US_STATES[lower]
    m = re.search(r",\s*([A-Za-z]{2})\b", raw)
    if m and m.group(1).upper() in VALID_STATE_ABBREVS:
        return m.group(1).upper()
    for name, abbrev in US_STATES.items():
        if name in lower:
            return abbrev
    return None



# Static program database


def _default_db_path() -> str:
    candidates = [
        Path.cwd() / "programs_db.json",
        Path(__file__).resolve().parent / "programs_db.json",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return str(candidates[0])


def load_database(path: Optional[str] = None) -> list:
    path = path or _default_db_path()
    p = Path(path)
    if not p.exists():
        sys.exit(
            f"[ERROR] Programs database not found: {path}\n"
            "Generate or point --db at a valid programs_db.json file."
        )
    with open(p, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("programs", data) if isinstance(data, dict) else data


def filter_programs(programs: list, state_code: Optional[str],
                    categories: Optional[list], limit: Optional[int] = None) -> list:
    """
    Filter the static database by state and category.
    If categories is empty or just ["general"], category filtering is
    skipped (broad match) — only the state filter applies.
    """
    cats = set(c.lower() for c in (categories or []))
    cat_filter_active = bool(cats) and cats != {"general"}

    scored = []
    for p in programs:
        p_states = set(s.upper() for s in p.get("applicable_states", ["ALL"]))
        state_ok = "ALL" in p_states or (state_code and state_code in p_states)
        if not state_ok:
            continue

        if cat_filter_active:
            p_categories = set(c.lower() for c in p.get("category", []))
            overlap = p_categories & cats
            if not overlap:
                continue
            score = len(overlap)
        else:
            score = 0

        scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    result = [p for _, p in scored]
    return result[:limit] if limit else result



# HTML → text extraction (stdlib only)
class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip = False
        self.parts: list = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self.parts.append(text)


def html_to_text(raw_html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(raw_html)
    except Exception:
        pass
    text = "\n".join(parser.parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()



# Web search — DuckDuckGo HTML scrape (no API key)

#
# NOTE: this depends on DuckDuckGo's current HTML markup and may break if
# they change it, and may be rate-limited under heavy use. For a more
# robust/production setup, consider running a self-hosted SearXNG instance
# and swapping out duckduckgo_search()'s implementation for one that calls
# its JSON API instead.

def _resolve_ddg_redirect(href: str) -> Optional[str]:
    if href.startswith("//duckduckgo.com/l/") or "uddg=" in href:
        if href.startswith("//"):
            href = "https:" + href
        parsed = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs:
            return urllib.parse.unquote(qs["uddg"][0])
    if href.startswith("http"):
        return href
    return None


def duckduckgo_search(query: str, max_results: int = 5, timeout: int = 15) -> list:
    params = urllib.parse.urlencode({"q": query})
    url = f"{DDG_SEARCH_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            page_html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []

    results = []
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S
    )
    for href, title_html in pattern.findall(page_html):
        url_clean = _resolve_ddg_redirect(href)
        title = html.unescape(re.sub(r"<[^>]+>", "", title_html)).strip()
        if url_clean:
            results.append({"title": title, "url": url_clean})
        if len(results) >= max_results:
            break
    return results


def fetch_page_text(url: str, max_chars: int = 6000, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                return ""
            raw = resp.read(max_chars * 12)
            page_html = raw.decode("utf-8", errors="replace")
    except Exception:
        return ""
    return html_to_text(page_html)[:max_chars]



# LLM JSON call helper (with one retry on parse failure)
def call_llm_json(backend, system: str, user: str, quiet: bool, retries: int = 2):
    raw = backend.chat(system, user)
    parsed = _try_parse_json(raw)
    attempt = 0
    while parsed is None and attempt < retries:
        attempt += 1
        log(f"  JSON parse failed (attempt {attempt}) — retrying with stricter instructions…", quiet)
        stricter = user + (
            "\n\nIMPORTANT: Respond with ONLY a valid JSON object. "
            "No markdown fences, no commentary, nothing outside the JSON."
        )
        raw = backend.chat(system, stricter)
        parsed = _try_parse_json(raw)
    return parsed, raw



# Conversation summarization (for long chat histories)
SYSTEM_PROMPT_CONV_SUMMARY = textwrap.dedent("""\
    You are summarizing a conversation excerpt between a person seeking help
    and an assistant. Extract and retain ONLY facts relevant to determining
    eligibility for public or nonprofit assistance programs: location,
    household size and composition, income, employment status, disability
    status, veteran status, citizenship/immigration status (only if
    explicitly mentioned), age, dependents, and the specific kind of help
    being sought (food, housing, healthcare, utilities, cash, education,
    childcare, disaster relief, legal aid, etc.). Omit small talk and
    anything irrelevant. Write the summary as concise prose, not JSON.
    Do not invent details that were not stated.
""")


def summarize_conversations(conversations: list, backend, quiet: bool,
                            max_chars: int = 6000) -> str:
    if not conversations:
        return ""
    combined = "\n\n---\n\n".join(c for c in conversations if c and c.strip())
    if not combined:
        return ""
    if len(combined) <= max_chars:
        return combined

    log(f"Conversation context is long ({len(combined):,} chars) — summarizing…", quiet)
    chunks = chunk_text(combined, chunk_size=max_chars, overlap=200)
    summaries = []
    for i, chunk in enumerate(chunks, 1):
        log(f"  Summarizing conversation chunk {i}/{len(chunks)}…", quiet)
        summaries.append(backend.chat(SYSTEM_PROMPT_CONV_SUMMARY, chunk))
    return "\n\n".join(summaries)



# Applicant profile builder
SYSTEM_PROMPT_PROFILE = textwrap.dedent("""\
    You build a structured applicant profile for matching people with public
    and nonprofit assistance programs, using ONLY information explicitly
    present in the provided document data and conversation context. Do not
    guess or fabricate values — use null for anything not stated or unclear.

    Respond with ONLY a JSON object using exactly this schema:
    {
      "location": {"city": string|null, "state": string|null, "zip": string|null},
      "household_size": integer|null,
      "household_members": [{"relationship": string, "age": integer|null}] | null,
      "annual_income": number|null,
      "monthly_income": number|null,
      "income_notes": string|null,
      "employment_status": string|null,
      "age": integer|null,
      "disability_status": string|null,
      "veteran_status": string|null,
      "citizenship_status": string|null,
      "dependents": integer|null,
      "special_circumstances": [string] | null,
      "stated_needs": string|null,
      "needed_categories": [string],
      "summary": string,
      "data_confidence": string
    }

    Valid values for "needed_categories" (choose all that apply, based on
    what the person is asking for or clearly needs):
    food, healthcare, housing, utility, cash_assistance, disability,
    veteran, education, childcare, employment, disaster_relief, legal_aid,
    general

    "data_confidence" should briefly note which fields are explicit versus
    inferred versus missing.
""")


def build_applicant_profile(document_data, conversations: list, backend, quiet: bool) -> dict:
    # If the caller already supplied a structured applicant profile/report, avoid
    # asking the LLM to re-create it unless new conversations must be merged.
    if isinstance(document_data, dict):
        if "applicant_profile" in document_data and not conversations:
            return document_data["applicant_profile"]
        looks_like_profile = (
            "location" in document_data
            and "needed_categories" in document_data
            and ("summary" in document_data or "stated_needs" in document_data)
        )
        if looks_like_profile and not conversations:
            return document_data

    if isinstance(document_data, (dict, list)):
        doc_text = json.dumps(document_data, indent=2, ensure_ascii=False)
    else:
        doc_text = str(document_data or "")

    conv_text = summarize_conversations(conversations, backend, quiet)

    user_prompt = (
        f"=== Extracted document data ===\n{doc_text or '(none provided)'}\n\n"
        f"=== Conversation context ===\n{conv_text or '(none provided)'}"
    )

    log("Building applicant profile…", quiet)
    parsed, raw = call_llm_json(backend, SYSTEM_PROMPT_PROFILE, user_prompt, quiet)

    if parsed is None:
        log("WARNING: Could not parse applicant profile as JSON — using fallback.", quiet)
        return {
            "location": {"city": None, "state": None, "zip": None},
            "household_size": None, "household_members": None,
            "annual_income": None, "monthly_income": None, "income_notes": None,
            "employment_status": None, "age": None,
            "disability_status": None, "veteran_status": None,
            "citizenship_status": None, "dependents": None,
            "special_circumstances": None, "stated_needs": None,
            "needed_categories": ["general"],
            "summary": raw.strip()[:1000],
            "data_confidence": "low — profile could not be parsed as structured JSON",
            "_parse_failed": True,
        }

    cats = [c.strip().lower() for c in (parsed.get("needed_categories") or []) if isinstance(c, str)]
    parsed["needed_categories"] = cats or ["general"]
    return parsed


# Eligibility engine
SYSTEM_PROMPT_ELIGIBILITY = textwrap.dedent("""\
    You are a careful, responsible benefits-eligibility analyst. You will be
    given an applicant profile, deterministic screening checks, and the rules
    for ONE assistance program. Produce a detailed, nuanced eligibility
    analysis — NEVER a simple yes/no. Consider missing information honestly: if a required fact is
    not in the profile, mark that criterion "unclear" rather than guessing,
    and list it under missing_information.

    Respond with ONLY a JSON object using exactly this schema:
    {
      "status": one of ["eligible","likely_eligible",
                         "possibly_eligible_more_info_needed",
                         "likely_ineligible","ineligible",
                         "insufficient_information"],
      "confidence": one of ["low","medium","high"],
      "criteria_assessment": [
        {"criterion": string, "assessment": one of ["met","not_met","unclear","not_applicable"],
         "explanation": string}
      ],
      "missing_information": [string],
      "recommended_next_steps": [string],
      "summary": string
    }

    IMPORTANT: Do not perform your own FPL arithmetic. If deterministic FPL
    calculations are present, use those numbers as authoritative and explain
    them. If deterministic checks say a hard requirement is not met, do not
    override it unless the program rules describe an exception.

    Be conservative: do not mark "eligible" or "likely_eligible" unless the
    profile clearly supports it. Prefer "possibly_eligible_more_info_needed"
    or "insufficient_information" when uncertain. This analysis informs a
    real person's decisions, so accuracy and honesty about uncertainty
    matter more than sounding confident.
""")


def assess_eligibility(profile: dict, program: dict, backend, quiet: bool) -> dict:
    deterministic = deterministic_program_checks(
        program=program,
        profile=profile,
        state_code=(profile.get("location") or {}).get("state") if isinstance(profile.get("location"), dict) else None,
        needed_categories=profile.get("needed_categories") or [],
    )
    profile_text = json.dumps(profile, indent=2, ensure_ascii=False)
    criteria_text = json.dumps(program.get("eligibility_criteria", []), indent=2, ensure_ascii=False)
    deterministic_text = json.dumps(deterministic, indent=2, ensure_ascii=False)

    user_prompt = (
        f"=== Applicant profile with deterministic FPL analysis ===\n{profile_text}\n\n"
        f"=== Deterministic screening checks — treat these calculations as authoritative ===\n{deterministic_text}\n\n"
        f"=== Program: {program.get('name')} ===\n"
        f"Category: {', '.join(program.get('category', []))}\n"
        f"Description: {program.get('description', '')}\n"
        f"Structured income rules: {json.dumps(program.get('income_rules', {}), ensure_ascii=False)}\n"
        f"Eligibility criteria:\n{criteria_text}\n"
        f"Benefits: {program.get('benefits_summary', '')}\n"
    )

    log(f"  Assessing eligibility: {program.get('name')}…", quiet)
    try:
        parsed, raw = call_llm_json(backend, SYSTEM_PROMPT_ELIGIBILITY, user_prompt, quiet)
    except Exception as exc:
        parsed = None
        raw = f"Eligibility model call failed: {exc}"

    if parsed is None:
        parsed = {
            "status": "insufficient_information",
            "confidence": "low",
            "criteria_assessment": [],
            "missing_information": ["Model output could not be parsed as structured data."],
            "recommended_next_steps": [
                "Review program details manually: " + (program.get("official_url") or "")
            ],
            "summary": raw.strip()[:800],
            "_parse_failed": True,
        }

    if deterministic.get("hard_fail") and parsed.get("status") in {"eligible", "likely_eligible"}:
        parsed["status"] = "likely_ineligible"
        parsed["confidence"] = "medium"
        parsed["summary"] = (
            "Deterministic screening found a hard requirement that appears not to be met. "
            + parsed.get("summary", "")
        )

    return {
        "program_id": program.get("id"),
        "program_name": program.get("name"),
        "category": program.get("category", []),
        "official_url": program.get("official_url"),
        "how_to_apply": program.get("how_to_apply"),
        "deterministic_checks": deterministic,
        "eligibility_analysis": parsed,
    }


# Web fallback: search + extract + assess
SYSTEM_PROMPT_EXTRACT_PROGRAM = textwrap.dedent("""\
    You extract structured information about an assistance program from raw
    web page text. Respond with ONLY a JSON object using this schema:
    {
      "name": string|null,
      "category": [string],
      "description": string,
      "eligibility_criteria": [{"criterion": string, "description": string}],
      "benefits_summary": string,
      "how_to_apply": string,
      "notes": string
    }
    If the page text does not actually describe a specific assistance
    program, respond with {"name": null}.
    Use ONLY information present in the text — do not invent details.
""")


def extract_program_from_page(text: str, url: str, category: str, backend, quiet: bool) -> Optional[dict]:
    if not text or len(text) < 200:
        return None

    user_prompt = f"URL: {url}\nLikely category: {category}\n\n=== Page text ===\n{text[:6000]}"
    parsed, _raw = call_llm_json(backend, SYSTEM_PROMPT_EXTRACT_PROGRAM, user_prompt, quiet)

    if not parsed or not parsed.get("name"):
        return None

    parsed["id"] = re.sub(r"[^a-z0-9]+", "-", parsed["name"].lower()).strip("-")
    parsed["official_url"] = url
    parsed["applicable_states"] = ["ALL"]
    parsed.setdefault("category", [category])
    note = parsed.get("notes") or ""
    parsed["notes"] = (note + " [Found via web search — independently verify before relying on this.]").strip()
    return parsed


def web_fallback_search(needed_categories: list, location_str: str, backend, quiet: bool,
                        max_results_per_category: int = 2, max_total: int = 4) -> list:
    log("No qualifying match in the static database — searching the web for other programs…", quiet)
    found = []
    seen_urls = set()

    for category in needed_categories:
        if len(found) >= max_total:
            break
        term = CATEGORY_SEARCH_TERMS.get(category, category)
        query = f"{term} {location_str} nonprofit charity assistance -site:.gov".strip()
        log(f"  Searching: {query}", quiet)
        results = duckduckgo_search(query, max_results=max_results_per_category + 2)

        count_this_category = 0
        for r in results:
            if count_this_category >= max_results_per_category or len(found) >= max_total:
                break
            url = r["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)

            log(f"    Fetching: {url}", quiet)
            text = fetch_page_text(url)
            program = extract_program_from_page(text, url, category, backend, quiet)
            if program:
                found.append(program)
                count_this_category += 1
            time.sleep(0.5)  # be polite to the search engine / target sites

    return found


# Orchestrator
def find_assistance(
    document_data,
    conversations: list,
    location: Optional[str] = None,
    backend=None,
    db_path: Optional[str] = None,
    enable_web_fallback: bool = True,
    max_db_programs: int = 8,
    max_web_programs: int = 4,
    quiet: bool = False,
    backend_name: Optional[str] = None,
    host: Optional[str] = None,
    model: str = "",
    temperature: float = 0.1,
    max_tokens: int = 4096,
    timeout: int = 1000,
) -> dict:
    """
    Main entry point. See module docstring for argument details.
    Returns a report dict — see format_report_markdown() for a printable form.
    """
    if backend is None:
        backend = build_backend(backend_name, host, model, temperature, max_tokens, timeout, quiet)

    profile = build_applicant_profile(document_data, conversations, backend, quiet)

    explicit_state = normalize_state(location) if location else None
    profile_location = profile.get("location") or {}
    profile_state = normalize_state(profile_location.get("state"))
    state_code = explicit_state or profile_state

    # Deterministic FPL enrichment happens before program assessment. The LLM
    # may explain these numbers, but it does not calculate them.
    profile = enrich_profile_with_fpl(profile, state_code)

    needed_categories = profile.get("needed_categories") or ["general"]

    programs = load_database(db_path)
    candidates = filter_programs(programs, state_code, needed_categories, limit=max_db_programs)
    log(f"Found {len(candidates)} candidate program(s) in the static database.", quiet)

    matched_programs = [assess_eligibility(p, p, backend, quiet) if False else
                        assess_eligibility(profile, p, backend, quiet)
                        for p in candidates]

    qualifies = any(
        m["eligibility_analysis"].get("status") in QUALIFYING_STATUSES
        for m in matched_programs
    )

    web_programs = []
    web_fallback_used = False
    if enable_web_fallback and not qualifies:
        web_fallback_used = True
        location_str = state_code or (location or "")
        raw_web_programs = web_fallback_search(
            needed_categories, location_str, backend, quiet, max_total=max_web_programs,
        )
        for program in raw_web_programs:
            result = assess_eligibility(profile, program, backend, quiet)
            result["source_url"] = program.get("official_url")
            result["found_via"] = "web_search"
            web_programs.append(result)

    return {
        "applicant_profile": profile,
        "fpl_analysis": profile.get("fpl_analysis"),
        "resolved_state": state_code,
        "needed_categories": needed_categories,
        "matched_programs": matched_programs,
        "qualifies_for_database_program": qualifies,
        "web_fallback_used": web_fallback_used,
        "web_programs": web_programs,
        "disclaimer": DISCLAIMER,
    }


# Report formatting
def _markdown_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value)
    return text if text else fallback


def _format_program_block(m: dict) -> str:
    ea = m.get("eligibility_analysis") or {}
    status = STATUS_LABELS.get(ea.get("status"), ea.get("status", "unknown"))
    lines = [
        f"### {_markdown_text(m.get('program_name'), 'Unnamed program')}",
        f"**Status:** {_markdown_text(status, 'unknown')}  |  **Confidence:** {_markdown_text(ea.get('confidence'), 'n/a')}",
        "",
        _markdown_text(ea.get("summary")),
        "",
    ]
    det = m.get("deterministic_checks") or {}
    if det.get("checks"):
        lines.append("**Deterministic screening checks:**")
        for c in det.get("checks", []):
            lines.append(f"- *{_markdown_text(c.get('name'), 'Check')}* - {_markdown_text(c.get('assessment'), 'unknown')}: {_markdown_text(c.get('explanation'))}")
        lines.append("")
    lines.extend([
        "**Criteria breakdown:**",
    ])
    for c in ea.get("criteria_assessment", []):
        lines.append(f"- *{_markdown_text(c.get('criterion'), 'Criterion')}* - {_markdown_text(c.get('assessment'), 'unknown')}: {_markdown_text(c.get('explanation'))}")
    if ea.get("missing_information"):
        lines.append("")
        lines.append("**Missing information:**")
        for mi in ea["missing_information"]:
            lines.append(f"- {_markdown_text(mi)}")
    if ea.get("recommended_next_steps"):
        lines.append("")
        lines.append("**Recommended next steps:**")
        for step in ea["recommended_next_steps"]:
            lines.append(f"- {_markdown_text(step)}")
    if m.get("official_url"):
        lines.append("")
        lines.append(f"More info / apply: {_markdown_text(m['official_url'])}")
    if m.get("found_via") == "web_search":
        lines.append("_Found via web search — independently verify before relying on this._")
    lines.append("")
    return "\n".join(_markdown_text(line) for line in lines)


def format_report_markdown(report: dict) -> str:
    profile = report.get("applicant_profile") or {}
    needed_categories = report.get("needed_categories") or []
    needed_categories_text = ", ".join(_markdown_text(c) for c in needed_categories if c) or "unknown"
    lines = ["# Assistance Program Report", "", "## Applicant Summary",
              _markdown_text(profile.get("summary"), "(no summary)"), "",
              f"**Needed categories:** {needed_categories_text}",
              f"**Location:** {report.get('resolved_state') or 'unknown'}", ""]
    fpl = profile.get("fpl_analysis") or {}
    if fpl:
        lines.extend([
            "## Deterministic FPL Calculation",
            f"**Household size:** {fpl.get('household_size') or 'unknown'}",
            f"**Annual income used:** ${fpl.get('annual_income'):,.2f}" if fpl.get('annual_income') is not None else "**Annual income used:** unknown",
            f"**100% FPL amount:** ${fpl.get('fpl_100_amount'):,.2f}" if fpl.get('fpl_100_amount') is not None else "**100% FPL amount:** unknown",
            f"**Applicant FPL percentage:** {fpl.get('fpl_percent')}%" if fpl.get('fpl_percent') is not None else "**Applicant FPL percentage:** unknown",
            ""
        ])

    lines.append("## Programs Found in Database")
    if report.get("matched_programs"):
        for m in report.get("matched_programs", []):
            lines.append(_format_program_block(m))
    else:
        lines.append("_No matching programs found in the static database for this location/category._")
        lines.append("")

    if report.get("web_fallback_used"):
        lines.append("## Additional Programs Found via Web Search")
        lines.append(
            "_No qualifying program was found in the static database, so a web "
            "search was run for other (often nonprofit/non-governmental) "
            "programs that may help. These have not been vetted — verify "
            "independently before relying on them._"
        )
        lines.append("")
        if report.get("web_programs"):
            for m in report.get("web_programs", []):
                lines.append(_format_program_block(m))
        else:
            lines.append("_No additional programs could be found via web search._")
            lines.append("")

    lines.append("---")
    lines.append(_markdown_text(report.get("disclaimer"), DISCLAIMER))
    return "\n".join(_markdown_text(line) for line in lines)


# CLI
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="assistance_finder.py",
        description="Match a person with assistance programs and produce a detailed eligibility report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("doc_output", nargs="?", default="-",
                    help="Path to process_parse.py output, or '-' for stdin (default).")
    ap.add_argument("--conversation-file", action="append", default=[], metavar="PATH",
                    help="Conversation transcript file (repeatable).")
    ap.add_argument("--conversation-text", action="append", default=[], metavar="TEXT",
                    help="Conversation transcript as inline text (repeatable).")
    ap.add_argument("--location", metavar="STATE",
                    help="Override/seed location, e.g. 'California' or 'TX'.")
    ap.add_argument("--db", metavar="PATH",
                    help="Path to programs_db.json (default: auto-locate).")
    ap.add_argument("--backend", choices=list(BACKENDS.keys()),
                    help="LLM backend (default: auto-detect).")
    ap.add_argument("--host", metavar="URL", help="Override backend base URL.")
    ap.add_argument("--model", default="", metavar="NAME", help="Model name/tag.")
    ap.add_argument("--temperature", type=float, default=0.1, metavar="F")
    ap.add_argument("--max-tokens", type=int, default=2048, metavar="N")
    ap.add_argument("--timeout", type=int, default=1000, metavar="S")
    ap.add_argument("--max-db-programs", type=int, default=8, metavar="N")
    ap.add_argument("--max-web-programs", type=int, default=4, metavar="N")
    ap.add_argument("--no-web-fallback", action="store_true",
                    help="Disable the web-search fallback step.")
    ap.add_argument("-o", "--output", metavar="PATH", help="Save report to file.")
    ap.add_argument("--output-format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--quiet", "-q", action="store_true")
    return ap


def main():
    _fix_stdout_encoding()
    ap = build_parser()
    args = ap.parse_args()

    if args.doc_output == "-":
        log("Reading process_parse.py output from stdin…", args.quiet)
        raw_doc = sys.stdin.read()
    else:
        p = Path(args.doc_output)
        if not p.exists():
            sys.exit(f"[ERROR] File not found: {args.doc_output}")
        raw_doc = p.read_text(encoding="utf-8", errors="replace")

    document_data = _try_parse_json(raw_doc)
    if document_data is None:
        document_data = raw_doc

    conversations = list(args.conversation_text)
    for cf in args.conversation_file:
        p = Path(cf)
        if not p.exists():
            sys.exit(f"[ERROR] Conversation file not found: {cf}")
        conversations.append(p.read_text(encoding="utf-8", errors="replace"))

    try:
        backend = build_backend(
            args.backend, args.host, args.model,
            args.temperature, args.max_tokens, args.timeout, args.quiet,
        )
    except RuntimeError as exc:
        sys.exit(f"[ERROR] {exc}")

    log(f"Backend: {backend.name} @ {backend.host}  model={backend.model or '(server default)'}", args.quiet)

    report = find_assistance(
        document_data=document_data,
        conversations=conversations,
        location=args.location,
        backend=backend,
        db_path=args.db,
        enable_web_fallback=not args.no_web_fallback,
        max_db_programs=args.max_db_programs,
        max_web_programs=args.max_web_programs,
        quiet=args.quiet,
    )

    output_text = (
        json.dumps(report, indent=2, ensure_ascii=False)
        if args.output_format == "json"
        else format_report_markdown(report)
    )

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(output_text, encoding="utf-8")
        log(f"Saved: {out}", args.quiet)
    else:
        try:
            print(output_text)
        except UnicodeEncodeError:
            print(output_text.encode("utf-8", errors="replace").decode("utf-8"))


__all__ = [
    "find_assistance", "build_applicant_profile", "assess_eligibility",
    "load_database", "filter_programs", "format_report_markdown",
    "normalize_state", "enrich_profile_with_fpl", "deterministic_program_checks", "DISCLAIMER",
]

if __name__ == "__main__":
    main()
