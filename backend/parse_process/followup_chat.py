#!/usr/bin/env python3

import argparse
import json
import re
from pathlib import Path
from datetime import datetime

from process_parse import build_backend, _try_parse_json, _fix_stdout_encoding
from assistance_finder import find_assistance, format_report_markdown


SYSTEM_PROMPT_FOLLOWUP = """
You are a careful benefits-assistance follow-up assistant.

You are continuing a conversation after an assistance eligibility analysis
or guidance report has already been generated.

Use ONLY the provided context:
- applicant profile or assistance report
- previous guidance report
- optional program database
- previous chat history
- the user's latest question
- any updated assistance report if one was regenerated

Your job:
1. Answer the user's follow-up clearly and practically.
2. Be honest about uncertainty.
3. Do not make official eligibility determinations.
4. If the user gave new important facts, explain that eligibility may have changed.
5. If an updated report was generated, use it.
6. If the user asks what to do next, give concrete next steps.
7. If current program rules matter, tell them to verify with the agency or 2-1-1.
8. Do not invent facts not in the context.
"""


SYSTEM_PROMPT_RERUN_DECIDER = """
You decide whether an assistance eligibility matcher should be rerun.

Return ONLY valid JSON using this exact schema:
{
  "rerun": true or false,
  "reason": string,
  "new_facts": [string],
  "confidence": "low" | "medium" | "high"
}

Rerun should be true if the user provides new information that could change:
- location, state, ZIP, or county
- household size, dependents, children, pregnancy, age
- income, employment, job loss, reduced hours
- rent, eviction notice, homelessness, housing instability
- utility shutoff, past-due bills, medical bills
- disability, veteran status, citizenship/immigration status
- program rejection, approval, denial, new application result
- newly stated needs such as food, rent, utilities, childcare, healthcare, legal aid
- anything that affects eligibility categories or program matching

Rerun should be false if the user is only asking:
- what a term means
- what documents to prepare
- how to apply
- what the previous report said
- general next steps without adding new facts

Be conservative. If the user clearly gives a new eligibility-changing fact, rerun.
"""


def read_text_file(path):
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def parse_json_or_text(path):
    text = read_text_file(path)
    parsed = _try_parse_json(text)
    return parsed if parsed is not None else text


def to_context_text(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False)
    return str(value or "")


def extract_profile_seed(profile_or_report):
    """
    If the input is an assistance_finder report JSON, use applicant_profile.
    Otherwise use the input as-is.
    """
    if isinstance(profile_or_report, dict):
        if "applicant_profile" in profile_or_report:
            return profile_or_report["applicant_profile"]
        if "summary" in profile_or_report and "needed_categories" in profile_or_report:
            return profile_or_report
    return profile_or_report


def append_history(history_path, role, message):
    if not history_path:
        return

    p = Path(history_path)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"\n\n{role}: {message.strip()}\n")

def limit_text(text, max_chars):
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[TRUNCATED: context was shortened to avoid model timeout]"


def fallback_rule_rerun(question):
    """
    Backup if the LLM decider returns malformed JSON.
    """
    q = question.lower()

    trigger_patterns = [
        r"\bi moved\b",
        r"\bmy address\b",
        r"\bnew address\b",
        r"\bchanged.*(state|city|zip|county|address)\b",
        r"\b(income|salary|wage|pay).*(changed|reduced|increased|decreased|now)\b",
        r"\b(lost my job|laid off|fired|unemployed|new job|reduced hours)\b",
        r"\b(eviction|eviction notice|homeless|behind on rent|past due rent)\b",
        r"\b(utility shutoff|shutoff notice|electric bill|gas bill|water bill)\b",
        r"\b(household|dependent|child|children|baby|pregnant).*(changed|now|new)\b",
        r"\b(disability|disabled|veteran|citizenship|immigration)\b",
        r"\b(rejected|denied|approved|accepted).*(program|application|benefit)\b",
        r"\bneed help with\b",
        r"\bi also need\b",
    ]

    for pattern in trigger_patterns:
        if re.search(pattern, q):
            return {
                "rerun": True,
                "reason": "Rule-based fallback detected new eligibility-relevant information.",
                "new_facts": [question],
                "confidence": "medium",
            }

    return {
        "rerun": False,
        "reason": "No obvious eligibility-changing fact detected.",
        "new_facts": [],
        "confidence": "low",
    }


def decide_whether_to_rerun(backend, profile_text, history_text, question, quiet=False):
    user_prompt = f"""
=== Current applicant profile / report context ===
{profile_text[:12000]}

=== Conversation history ===
{history_text[-6000:] if history_text else "(none)"}

=== Latest user message ===
{question}

Should the assistance matcher be rerun?
"""

    try:
        raw = backend.chat(SYSTEM_PROMPT_RERUN_DECIDER, user_prompt)
        parsed = _try_parse_json(raw)
        if isinstance(parsed, dict) and "rerun" in parsed:
            parsed["rerun"] = bool(parsed["rerun"])
            parsed.setdefault("reason", "")
            parsed.setdefault("new_facts", [])
            parsed.setdefault("confidence", "medium")
            return parsed
    except Exception:
        pass

    return fallback_rule_rerun(question)


def build_answer_prompt(profile_text, guide_text, db_text, history_text, question, rerun_note):
    return f"""
=== Applicant profile / assistance report ===
{limit_text(profile_text, 9000)}

=== Previous guidance report ===
{limit_text(guide_text, 4000)}

=== Program database context ===
{limit_text(db_text, 3000)}

=== Recent conversation history ===
{limit_text(history_text[-5000:] if history_text else "", 5000)}

=== Rerun note ===
{limit_text(rerun_note, 1200) or "(no rerun performed)"}

=== Latest user question ===
{question}

Answer the latest user question using the available context.
Keep the answer practical and concise.
"""

def save_updated_report(report, json_path, markdown_path):
    if json_path:
        p = Path(json_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    if markdown_path:
        p = Path(markdown_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(format_report_markdown(report), encoding="utf-8")


def main():
    _fix_stdout_encoding()

    parser = argparse.ArgumentParser(
        description="Continue follow-up chat and automatically rerun assistance matching when needed."
    )

    parser.add_argument("--profile", required=True, help="Path to report.json, report.md, or applicant profile JSON.")
    parser.add_argument("--guide", help="Path to guide.md or guide.json.")
    parser.add_argument("--db", required=True, help="Path to programs_db.json.")
    parser.add_argument("--history", default="chat_history.txt", help="Persistent chat history file.")

    parser.add_argument("--location", help="State/location seed, e.g. IL or California.")

    parser.add_argument("--backend", choices=["ollama", "lmstudio", "openai", "llamacpp"], default="ollama")
    parser.add_argument("--host")
    parser.add_argument("--model", required=True)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--timeout", type=int, default=120)

    parser.add_argument("--updated-report", default="updated_report.json")
    parser.add_argument("--updated-report-md", default="updated_report.md")

    parser.add_argument("--max-db-programs", type=int, default=8)
    parser.add_argument("--max-web-programs", type=int, default=4)
    parser.add_argument("--no-web-fallback", action="store_true")
    parser.add_argument("--no-auto-rerun", action="store_true")

    parser.add_argument("--quiet", action="store_true")

    args = parser.parse_args()

    backend = build_backend(
        args.backend,
        args.host,
        args.model,
        args.temperature,
        args.max_tokens,
        args.timeout,
        args.quiet,
    )

    profile_or_report = parse_json_or_text(args.profile)
    guide_or_report = parse_json_or_text(args.guide) if args.guide else ""
    db_data = parse_json_or_text(args.db)

    profile_seed = extract_profile_seed(profile_or_report)
    profile_text = to_context_text(profile_or_report)
    guide_text = to_context_text(guide_or_report)
    db_text = to_context_text(db_data)

    print("Follow-up chat started.")
    print("Type 'exit' or 'quit' to stop.")
    print("Auto-rerun is", "OFF" if args.no_auto_rerun else "ON")
    print("Commands: exit, quit, show_fpl")
    print()

    while True:
        question = input("User> ").strip()

        if question.lower() in {"exit", "quit", "q"}:
            print("Chat ended.")
            break

        if question.lower() == "show_fpl":
            parsed_context = _try_parse_json(profile_text)
            if isinstance(parsed_context, dict):
                fpl = parsed_context.get("fpl_analysis") or (parsed_context.get("applicant_profile") or {}).get("fpl_analysis")
                print(json.dumps(fpl or {"message": "No FPL analysis available yet."}, indent=2, ensure_ascii=False))
            else:
                print("No structured FPL analysis available in current context.")
            continue

        if not question:
            continue

        history_text = read_text_file(args.history)
        rerun_note = ""

        if not args.no_auto_rerun:
            decision = decide_whether_to_rerun(
                backend=backend,
                profile_text=profile_text,
                history_text=history_text,
                question=question,
                quiet=args.quiet,
            )

            if decision.get("rerun"):
                print("\n[system] New eligibility-relevant information detected.")
                print(f"[system] Reason: {decision.get('reason')}")
                print("[system] Rerunning assistance matching...\n")

                conversations_for_rerun = [
                    history_text,
                    f"User: {question}",
                ]

                updated_report = find_assistance(
                    document_data=profile_seed,
                    conversations=conversations_for_rerun,
                    location=args.location,
                    backend=backend,
                    db_path=args.db,
                    enable_web_fallback=not args.no_web_fallback,
                    max_db_programs=args.max_db_programs,
                    max_web_programs=args.max_web_programs,
                    quiet=args.quiet,
                )

                save_updated_report(
                    updated_report,
                    json_path=args.updated_report,
                    markdown_path=args.updated_report_md,
                )

                profile_or_report = updated_report
                profile_seed = extract_profile_seed(updated_report)
                profile_text = json.dumps(updated_report, indent=2, ensure_ascii=False)

                rerun_note = (
                    "The assistance matcher was rerun because the latest user message "
                    f"may affect eligibility. Reason: {decision.get('reason')}. "
                    f"Updated report saved to {args.updated_report} and {args.updated_report_md}."
                )

                print(f"[system] Updated report saved to {args.updated_report}")
                print(f"[system] Updated markdown saved to {args.updated_report_md}\n")

        answer_prompt = build_answer_prompt(
            profile_text=profile_text,
            guide_text=guide_text,
            db_text=db_text,
            history_text=history_text,
            question=question,
            rerun_note=rerun_note,
        )

        answer = backend.chat(SYSTEM_PROMPT_FOLLOWUP, answer_prompt)

        print("\nAssistant>")
        print(answer)
        print()

        append_history(args.history, "User", question)
        append_history(args.history, "Assistant", answer)


if __name__ == "__main__":
    main()