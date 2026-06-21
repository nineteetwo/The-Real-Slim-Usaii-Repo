#!/usr/bin/env python3
"""profile_chat.py — build/update a structured applicant profile from chat."""

import argparse
import json
from pathlib import Path
from datetime import datetime

from process_parse import build_backend, _try_parse_json, _fix_stdout_encoding
from fpl_calculator import enrich_profile_with_fpl

EMPTY_PROFILE = {
    "location": {"city": None, "state": None, "zip": None},
    "household_size": None,
    "household_members": None,
    "annual_income": None,
    "monthly_income": None,
    "income_notes": None,
    "employment_status": None,
    "age": None,
    "disability_status": None,
    "veteran_status": None,
    "citizenship_status": None,
    "dependents": None,
    "special_circumstances": [],
    "stated_needs": None,
    "needed_categories": [],
    "summary": None,
    "data_confidence": "empty profile",
    "source_notes": [],
}

SYSTEM_PROMPT_PROFILE_UPDATE = """
You update an applicant profile for assistance-program matching.
Use ONLY the user's latest message and existing profile. Do not invent facts.
If the user does not provide a field, keep the existing value. If they correct
a previous fact, update it. Return ONLY valid JSON using exactly this schema:
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
  "special_circumstances": [string],
  "stated_needs": string|null,
  "needed_categories": [string],
  "summary": string|null,
  "data_confidence": string,
  "source_notes": [string]
}
Valid needed_categories: food, healthcare, housing, utility, cash_assistance,
disability, veteran, education, childcare, employment, disaster_relief,
legal_aid, general.
"""

SYSTEM_PROMPT_NEXT_QUESTION = """
You collect missing information for assistance-program matching. Given the
current applicant profile, ask exactly ONE short, respectful follow-up question
that would most improve eligibility matching. Prioritize location, type of help,
household size, income/employment, and urgent risks. Return only the question.
"""

def load_profile(path):
    p = Path(path)
    if not p.exists():
        return dict(EMPTY_PROFILE)
    parsed = _try_parse_json(p.read_text(encoding="utf-8", errors="replace"))
    return parsed if isinstance(parsed, dict) else dict(EMPTY_PROFILE)

def save_profile(path, profile):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")

def append_history(path, role, message):
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(f"\n\n{role}: {message.strip()}\n")

def update_profile(profile, user_message, backend):
    user_prompt = f"""
=== Existing applicant profile ===
{json.dumps(profile, indent=2, ensure_ascii=False)}

=== Latest user message ===
{user_message}

Update the applicant profile.
"""
    raw = backend.chat(SYSTEM_PROMPT_PROFILE_UPDATE, user_prompt)
    parsed = _try_parse_json(raw)
    if not isinstance(parsed, dict):
        print("[warning] Could not parse profile update. Keeping old profile.")
        return profile
    parsed.setdefault("source_notes", [])
    parsed["source_notes"].append(f"Updated from chat on {datetime.now().isoformat(timespec='seconds')}")
    state = (parsed.get("location") or {}).get("state") if isinstance(parsed.get("location"), dict) else None
    return enrich_profile_with_fpl(parsed, state)

def ask_next_question(profile, backend):
    raw = backend.chat(SYSTEM_PROMPT_NEXT_QUESTION, json.dumps(profile, indent=2, ensure_ascii=False))
    return raw.strip().strip('"')

def main():
    _fix_stdout_encoding()
    ap = argparse.ArgumentParser(description="Build a structured benefits applicant profile from follow-up chat.")
    ap.add_argument("--profile", default="user_profile.json")
    ap.add_argument("--history", default="chat_history.txt")
    ap.add_argument("--backend", choices=["ollama", "lmstudio", "openai", "llamacpp"], default="ollama")
    ap.add_argument("--host")
    ap.add_argument("--model", required=True)
    ap.add_argument("--temperature", type=float, default=0.1)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    backend = build_backend(args.backend, args.host, args.model, args.temperature, args.max_tokens, args.timeout, args.quiet)
    profile = load_profile(args.profile)
    profile = enrich_profile_with_fpl(profile, (profile.get("location") or {}).get("state") if isinstance(profile.get("location"), dict) else None)
    save_profile(args.profile, profile)

    print("Profile chat started. Type 'done' to stop, 'show' to view profile, or 'fpl' to view FPL calculation.\n")
    print("Assistant>", ask_next_question(profile, backend))
    while True:
        msg = input("\nUser> ").strip()
        if msg.lower() in {"done", "exit", "quit", "q"}:
            save_profile(args.profile, profile)
            print(f"Saved profile to {args.profile}")
            break
        if msg.lower() == "show":
            print(json.dumps(profile, indent=2, ensure_ascii=False)); continue
        if msg.lower() == "fpl":
            print(json.dumps(profile.get("fpl_analysis"), indent=2, ensure_ascii=False)); continue
        if not msg:
            continue
        append_history(args.history, "User", msg)
        profile = update_profile(profile, msg, backend)
        save_profile(args.profile, profile)
        q = ask_next_question(profile, backend)
        print("\nAssistant>", q)
        append_history(args.history, "Assistant", q)

if __name__ == "__main__":
    main()
