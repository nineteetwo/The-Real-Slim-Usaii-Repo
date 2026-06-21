from fastapi.responses import JSONResponse
import database
import os
import shutil
import time
import sys
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, status, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.ext.declarative import declarative_base
from typing import Any, Optional
from pydantic import BaseModel
from database import get_db, User, ChatMessage
from rag import generate_rag_response
from parse_process.doc_parser import parse_document, clean_text
from parse_process.process_parse import extract, build_backend, _try_parse_json
from parse_process.guidance_generator import generate_guidance, format_guidance_markdown
from parse_process.process_parse import build_backend
from parse_process.deadline_tracker import build_deadline_report
from parse_process.fpl_calculator import enrich_profile_with_fpl, calculate_fpl_snapshot
from parse_process.caseworker_notes import build_case_note
from parse_process.assistance_finder import find_assistance, format_report_markdown
import json

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PARSE_PROCESS_DIR = os.path.join(BACKEND_DIR, "parse_process")
if PARSE_PROCESS_DIR not in sys.path:
    sys.path.insert(0, PARSE_PROCESS_DIR)

from parse_process.followup_chat import (
    SYSTEM_PROMPT_FOLLOWUP,
    build_answer_prompt,
    decide_whether_to_rerun,
    extract_profile_seed,
    to_context_text,
)

app = FastAPI(title="CalHelpr Backend")

Base = declarative_base()
database.init_db()

# Local file frontend access configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows browser local files to reach the ports
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directory configuration for user uploaded files and generated workflow artifacts
UPLOAD_DIR = os.path.join(BACKEND_DIR, "uploads")
WORKSPACE_DIR = os.path.join(BACKEND_DIR, "workspaces")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(WORKSPACE_DIR, exist_ok=True)

ROOT_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", ".."))
DEFAULT_PROGRAM_DB = os.path.join(ROOT_DIR, "programs_db.json")
TIMEOUT_SECONDS = 600
CATEGORY_OPTIONS = {
    "food", "healthcare", "housing", "utility", "cash_assistance", "disability",
    "veteran", "education", "childcare", "employment", "disaster_relief",
    "legal_aid", "general",
}

PROFILE_SYSTEM_PROMPT = """
Return ONLY valid JSON. Extract applicant facts for assistance eligibility.
Include location, household_size, annual_income or monthly_income if present,
income_notes, employment_status, age, dependents, disability_status,
veteran_status, citizenship_status if explicitly stated, stated_needs,
needed_categories, deadlines, and missing_information.
Valid needed_categories: food, healthcare, housing, utility, cash_assistance,
disability, veteran, education, childcare, employment, disaster_relief,
legal_aid, general.
The input may contain multiple source documents separated by headers.
Reconcile facts across all sources into one applicant profile.
If sources conflict, prefer the most specific or most recent value and explain
the conflict in income_notes or missing_information. Preserve all deadlines,
program names, application statuses, notices, and document requirements.
Do not invent facts. Use null or an empty list when information is missing.
"""

def safe_email_dir(email: Optional[str]) -> str:
    value = (email or "guest").strip() or "guest"
    return value.replace("@", "_").replace(".", "_").replace(os.sep, "_")

def safe_context_dir(thread_id: Optional[str]) -> str:
    value = (thread_id or "global").strip() or "global"
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in value)

def user_upload_dir(email: Optional[str], thread_id: Optional[str] = None) -> str:
    path = os.path.join(UPLOAD_DIR, safe_email_dir(email), safe_context_dir(thread_id))
    os.makedirs(path, exist_ok=True)
    return path

def user_workspace_dir(email: Optional[str], thread_id: Optional[str] = None) -> str:
    path = os.path.join(WORKSPACE_DIR, safe_email_dir(email), safe_context_dir(thread_id))
    os.makedirs(path, exist_ok=True)
    return path

def artifact_path(email: Optional[str], name: str, thread_id: Optional[str] = None) -> str:
    return os.path.join(user_workspace_dir(email, thread_id), name)

def write_artifact(email: Optional[str], name: str, content: Any, thread_id: Optional[str] = None) -> None:
    path = artifact_path(email, name, thread_id)
    if isinstance(content, (dict, list)):
        data = json.dumps(content, indent=2, ensure_ascii=False)
    else:
        data = str(content or "")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(data)

def read_artifact(email: Optional[str], name: str, default: Any = None, thread_id: Optional[str] = None) -> Any:
    path = artifact_path(email, name, thread_id)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    if name.endswith(".json"):
        parsed = _try_parse_json(text)
        return parsed if parsed is not None else default
    return text

def make_local_backend(temperature: float = 0.1, max_tokens: int = 4096):
    return build_backend(
        backend_name=None,
        host=None,
        model=None,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=TIMEOUT_SECONDS,
        quiet=True,
    )

def load_program_db_text() -> str:
    if os.path.exists(DEFAULT_PROGRAM_DB):
        with open(DEFAULT_PROGRAM_DB, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    return ""

def status_counts(report: Optional[dict]) -> dict:
    counts = {}
    for item in (report or {}).get("matched_programs", []):
        status_text = ((item.get("eligibility_analysis") or {}).get("status") or "unknown").replace("_", " ")
        counts[status_text] = counts.get(status_text, 0) + 1
    return counts

def build_report_side_artifacts(email: Optional[str], parsed_text: str, profile: dict, report: dict, thread_id: Optional[str] = None) -> dict:
    report_md = format_report_markdown(report)
    deadline_source = "\n\n".join([
        parsed_text or "",
        json.dumps(profile or {}, ensure_ascii=False),
        json.dumps(report or {}, ensure_ascii=False),
    ])
    deadlines = build_deadline_report(deadline_source, window_days=45)
    case_note = build_case_note(report)
    write_artifact(email, "report.json", report, thread_id)
    write_artifact(email, "report.md", report_md, thread_id)
    write_artifact(email, "deadlines.json", deadlines, thread_id)
    write_artifact(email, "case_note.md", case_note, thread_id)
    return {"report": report, "report_md": report_md, "deadlines": deadlines, "case_note": case_note}

# --- Request Data Validations ---
class UserSignup(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    email: str
    password: str = Field(..., min_length=4)

class UserLogin(BaseModel):
    email: str
    password: str

class ChatInput(BaseModel):
    text: str
    email: Optional[str] = None 
    thread_id: Optional[str] = None
    uploaded_file_path: Optional[str] = "" 

class TrackerStatusUpdate(BaseModel):
    email: str
    thread_id: str
    program_name: str
    status: str  # "applied", "accepted", "rejected"

class IntakeRequest(BaseModel):
    email: Optional[str] = None
    thread_id: Optional[str] = None
    filename: Optional[str] = None
    filenames: Optional[list[str]] = None
    manual_notes: Optional[str] = ""

class ProfileSaveRequest(BaseModel):
    email: Optional[str] = None
    thread_id: Optional[str] = None
    profile: dict
    run_matching: bool = False

class MatchRequest(BaseModel):
    email: Optional[str] = None
    thread_id: Optional[str] = None
    location: Optional[str] = "CA"
    enable_web: bool = True
    max_db_programs: int = 8
    max_web_programs: int = 4

class GuidanceRequest(BaseModel):
    email: Optional[str] = None
    thread_id: Optional[str] = None
    program_name: str
    outcome: str
    location: Optional[str] = "CA"
    enable_web: bool = True
    max_resources: int = 4

class FollowupRequest(BaseModel):
    email: Optional[str] = None
    thread_id: Optional[str] = None
    question: str
    auto_rerun: bool = True
    location: Optional[str] = "CA"
    enable_web: bool = True

class RawJsonRequest(BaseModel):
    email: Optional[str] = None
    thread_id: Optional[str] = None
    raw_json: str

# --- Endpoints ---
@app.post("/api/signup", status_code=status.HTTP_201_CREATED)
def signup(user_data: UserSignup, db: Session = Depends(get_db)):
    """Registers a new user account in the system."""
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")
    
    new_user = User(name=user_data.name, email=user_data.email, password=user_data.password)
    db.add(new_user)
    db.commit()
    
    return {"message": "Account registered successfully!", "email": new_user.email}

@app.post("/api/login")
def login(user_data: UserLogin, db: Session = Depends(get_db)):
    """Authenticates a user attempting to sign in."""
    user = db.query(User).filter(User.email == user_data.email).first()
    if not user or user.password != user_data.password:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    
    return {"message": "Access granted", "email": user.email, "name": user.name}

@app.get("/api/workflow/state")
def get_workflow_state(email: Optional[str] = Query(None), thread_id: Optional[str] = Query(None)):
    profile = read_artifact(email, "profile.json", thread_id=thread_id)
    report = read_artifact(email, "report.json", thread_id=thread_id)
    report_md = read_artifact(email, "report.md", "", thread_id)
    deadlines = read_artifact(email, "deadlines.json", thread_id=thread_id)
    case_note = read_artifact(email, "case_note.md", "", thread_id)
    guide = read_artifact(email, "guide.json", thread_id=thread_id)
    guide_md = read_artifact(email, "guide.md", "", thread_id)
    parsed_text = read_artifact(email, "parsed.txt", "", thread_id)
    chat_history = read_artifact(email, "chat_history.txt", "", thread_id)

    return {
        "profile": profile,
        "parsed_text": parsed_text,
        "report": report,
        "report_md": report_md,
        "deadlines": deadlines,
        "case_note": case_note,
        "guide": guide,
        "guide_md": guide_md,
        "chat_history": chat_history,
        "status_counts": status_counts(report),
        "artifact_names": [
            name for name in [
                "parsed.txt", "profile.json", "report.json", "report.md",
                "deadlines.json", "case_note.md", "guide.json", "guide.md",
                "chat_history.txt",
            ] if os.path.exists(artifact_path(email, name, thread_id))
        ],
    }

@app.post("/api/workflow/intake")
def run_intake_pipeline(payload: IntakeRequest):
    parsed_text = ""
    selected_files = payload.filenames or ([payload.filename] if payload.filename else [])
    selected_files = [name for name in selected_files if name]
    upload_dir = user_upload_dir(payload.email, payload.thread_id)
    if not selected_files and os.path.exists(upload_dir):
        selected_files = [
            name for name in os.listdir(upload_dir)
            if os.path.isfile(os.path.join(upload_dir, name))
        ]

    parsed_parts = []
    for idx, filename in enumerate(selected_files, 1):
        file_path = os.path.join(upload_dir, filename)
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail=f"The file '{filename}' could not be located.")
        raw_text = parse_document(file_path, quiet=True)
        cleaned_text = clean_text(raw_text)
        if cleaned_text.strip():
            parsed_parts.append(
                f"=== Source document {idx} of {len(selected_files)}: {filename} ===\n{cleaned_text}"
            )

    parsed_text = "\n\n".join(parsed_parts)

    if payload.manual_notes and payload.manual_notes.strip():
        parsed_text = "\n\n".join([parsed_text, f"=== Applicant notes ===\n{payload.manual_notes.strip()}"]).strip()

    if not parsed_text.strip():
        raise HTTPException(status_code=400, detail="Add an uploaded document or applicant notes before extraction.")

    backend = make_local_backend(temperature=0.0, max_tokens=4096)
    raw_profile = extract(
        text=parsed_text,
        system_prompt=PROFILE_SYSTEM_PROMPT,
        backend=backend,
        fmt="json",
        chunk_size=18000,
        overlap=800,
        quiet=True,
    )
    profile = _try_parse_json(raw_profile)
    if not isinstance(profile, dict):
        raise HTTPException(status_code=500, detail="The local model did not return a valid applicant JSON profile.")

    state_seed = None
    location = profile.get("location")
    if isinstance(location, dict):
        state_seed = location.get("state")
    profile = enrich_profile_with_fpl(profile, state_seed)

    write_artifact(payload.email, "parsed.txt", parsed_text, payload.thread_id)
    write_artifact(payload.email, "profile.json", profile, payload.thread_id)
    return {"status": "Success", "parsed_text": parsed_text, "profile": profile}

@app.post("/api/workflow/profile")
def save_workflow_profile(payload: ProfileSaveRequest):
    profile = payload.profile
    location = profile.get("location") if isinstance(profile.get("location"), dict) else {}
    state_seed = location.get("state") if isinstance(location, dict) else None
    profile = enrich_profile_with_fpl(profile, state_seed)
    write_artifact(payload.email, "profile.json", profile, payload.thread_id)

    if payload.run_matching:
        return run_program_matching(MatchRequest(email=payload.email, thread_id=payload.thread_id, location=state_seed or "CA"))

    return {"status": "Success", "profile": profile}

@app.post("/api/workflow/profile/raw")
def save_raw_workflow_profile(payload: RawJsonRequest):
    parsed = _try_parse_json(payload.raw_json)
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="That JSON could not be parsed as an applicant profile.")
    write_artifact(payload.email, "profile.json", parsed, payload.thread_id)
    return {"status": "Success", "profile": parsed}

@app.post("/api/workflow/match")
def run_program_matching(payload: MatchRequest):
    profile = read_artifact(payload.email, "profile.json", thread_id=payload.thread_id)
    if not isinstance(profile, dict):
        raise HTTPException(status_code=400, detail="Extract or save an applicant profile before matching.")
    if not os.path.exists(DEFAULT_PROGRAM_DB):
        raise HTTPException(status_code=500, detail="The local programs_db.json file could not be found.")

    backend = make_local_backend(temperature=0.1, max_tokens=8192)
    report = find_assistance(
        document_data=profile,
        conversations=[],
        location=payload.location or None,
        backend=backend,
        db_path=DEFAULT_PROGRAM_DB,
        enable_web_fallback=payload.enable_web,
        max_db_programs=payload.max_db_programs,
        max_web_programs=payload.max_web_programs,
        quiet=True,
        timeout=TIMEOUT_SECONDS,
    )
    parsed_text = read_artifact(payload.email, "parsed.txt", "", payload.thread_id)
    artifacts = build_report_side_artifacts(payload.email, parsed_text, profile, report, payload.thread_id)
    return {"status": "Success", **artifacts, "status_counts": status_counts(report)}

@app.post("/api/workflow/guidance")
def run_outcome_guidance(payload: GuidanceRequest):
    report = read_artifact(payload.email, "report.json", thread_id=payload.thread_id)
    if not isinstance(report, dict):
        raise HTTPException(status_code=400, detail="Run program matching before generating outcome guidance.")

    selected_program: Any = {"name": payload.program_name}
    for item in report.get("matched_programs", []):
        if (item.get("program_name") or item.get("name")) == payload.program_name:
            selected_program = item
            break

    backend = make_local_backend(temperature=0.3, max_tokens=4096)
    guide = generate_guidance(
        selected_program,
        payload.outcome,
        report,
        conversations=[],
        location=payload.location or None,
        backend=backend,
        enable_web_search=payload.enable_web,
        max_resources=payload.max_resources,
        quiet=True,
        timeout=TIMEOUT_SECONDS,
    )
    guide_md = format_guidance_markdown(guide)
    write_artifact(payload.email, "guide.json", guide, payload.thread_id)
    write_artifact(payload.email, "guide.md", guide_md, payload.thread_id)
    return {"status": "Success", "guide": guide, "guide_md": guide_md}

@app.post("/api/workflow/followup")
def answer_workflow_followup(payload: FollowupRequest):
    report = read_artifact(payload.email, "report.json", thread_id=payload.thread_id)
    history_text = read_artifact(payload.email, "chat_history.txt", "", payload.thread_id)
    guide_text = read_artifact(payload.email, "guide.md", "", payload.thread_id)
    profile = read_artifact(payload.email, "profile.json", {}, payload.thread_id)
    parsed_text = read_artifact(payload.email, "parsed.txt", "", payload.thread_id)
    backend = make_local_backend(temperature=0.2, max_tokens=4096)
    profile_or_report = report if isinstance(report, dict) else profile
    profile_seed = extract_profile_seed(profile_or_report)
    profile_text = to_context_text(profile_or_report)
    if not profile_text.strip() or profile_text == "{}":
        upload_dir = user_upload_dir(payload.email, payload.thread_id)
        doc_names = []
        if os.path.exists(upload_dir):
            doc_names = [
                name for name in os.listdir(upload_dir)
                if os.path.isfile(os.path.join(upload_dir, name))
            ]
        profile_text = "\n\n".join([
            "No structured profile or match report has been generated yet.",
            f"Attached documents: {', '.join(doc_names) if doc_names else '(none)'}",
            f"Parsed intake text:\n{parsed_text}" if parsed_text else "",
        ]).strip()
    rerun_note = ""

    if payload.auto_rerun and isinstance(report, dict):
        decision = decide_whether_to_rerun(
            backend=backend,
            profile_text=profile_text,
            history_text=history_text,
            question=payload.question,
            quiet=True,
        )
        if decision.get("rerun"):
            updated_report = find_assistance(
                document_data=profile_seed,
                conversations=[history_text, f"User: {payload.question}"],
                location=payload.location or None,
                backend=backend,
                db_path=DEFAULT_PROGRAM_DB,
                enable_web_fallback=payload.enable_web,
                quiet=True,
                timeout=TIMEOUT_SECONDS,
            )
            profile = updated_report.get("applicant_profile", read_artifact(payload.email, "profile.json", {}, payload.thread_id))
            parsed_text = read_artifact(payload.email, "parsed.txt", "", payload.thread_id)
            build_report_side_artifacts(payload.email, parsed_text, profile, updated_report, payload.thread_id)
            write_artifact(payload.email, "profile.json", profile, payload.thread_id)
            profile_or_report = updated_report
            profile_text = to_context_text(updated_report)
            rerun_note = (
                "The match board was refreshed because the latest message may affect eligibility. "
                f"Reason: {decision.get('reason')}"
            )

    answer_prompt = build_answer_prompt(
        profile_text=profile_text,
        guide_text=guide_text,
        db_text=load_program_db_text(),
        history_text=history_text,
        question=payload.question,
        rerun_note=rerun_note,
    )
    answer = backend.chat(SYSTEM_PROMPT_FOLLOWUP, answer_prompt)
    updated_history = "\n\n".join([history_text, f"User: {payload.question}", f"Assistant: {answer}"]).strip()
    write_artifact(payload.email, "chat_history.txt", updated_history, payload.thread_id)
    return {"status": "Success", "answer": answer, "chat_history": updated_history, "rerun_note": rerun_note}

@app.get("/api/workflow/artifact")
def get_workflow_artifact(email: Optional[str] = Query(None), name: str = Query(...), thread_id: Optional[str] = Query(None)):
    allowed = {
        "parsed.txt", "profile.json", "report.json", "report.md", "deadlines.json",
        "case_note.md", "guide.json", "guide.md", "chat_history.txt",
    }
    if name not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported artifact name.")
    content = read_artifact(email, name, thread_id=thread_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return {"name": name, "content": content}

@app.post("/api/chat")
def handle_chat_query(payload: ChatInput, db: Session = Depends(get_db)):
    try:
        # Use existing thread or fallback safely
        assigned_thread_id = payload.thread_id if payload.thread_id else f"thread_{int(time.time())}"

        # Gather all past logs in this thread to analyze state context
        history_records = (
            db.query(ChatMessage)
            .filter(ChatMessage.thread_id == assigned_thread_id)
            .order_by(ChatMessage.id.asc())
            .all()
        )

        has_unhandled_rejection = False
        rejected_program_name = ""
        conversations_list = []

        for msg in history_records:
            # Look for the tracker seed token we dropped in Step 2
            if "[System Event: Application Rejected from" in msg.user_query:
                has_unhandled_rejection = True
                try:
                    rejected_program_name = msg.user_query.split("from ")[1].replace("]", "")
                except Exception:
                    rejected_program_name = "the evaluated program"
            
            conversations_list.append(f"User: {msg.user_query}\nAI: {msg.ai_response}")

        # ROUTE A: Post-Rejection Community Guidance Web Scraping
        if has_unhandled_rejection:
            print(f"[Engine] Found rejection marker for {rejected_program_name}. Launching fallback resource finder...")
            
            local_slm = build_backend(
                backend_name=None, host=None, model=None, 
                temperature=0.3, max_tokens=8192, timeout=300, quiet=True
            )
            
            # Combine chat records into text representations
            conversation_history_string = "\n".join(conversations_list)

            # Fire your guidance module's web search scraping pipeline
            guidance_data = generate_guidance(
                program_applied_to=rejected_program_name,
                application_result="rejected",
                previous_chat_profile=conversation_history_string or "Applicant looking for mutual aid resources.",
                conversations=conversations_list,
                location="CA",
                backend=local_slm,
                enable_web_search=True, # ◄ Kicks off the local scraping system context
                max_resources=4
            )
            
            guidance_markdown = format_guidance_markdown(guidance_data)
            
            # Save this execution step to the permanent chat logs
            new_chat_log = ChatMessage(
                user_email=payload.email,
                thread_id=assigned_thread_id,
                user_query=payload.text,
                ai_response=guidance_markdown
            )
            db.add(new_chat_log)
            db.commit()

            return {
                "response": guidance_markdown,
                "thread_id": assigned_thread_id,
                "id": new_chat_log.id
            }

        # ROUTE B: Standard Document Verification / Eligibility Check (Default)
        print("[Engine] Routing interaction to standard RAG pipeline...")
        eligibility_report = generate_rag_response(
            current_query=payload.text,
            full_history="\n".join(conversations_list),
            user_doc_path=payload.uploaded_file_path or ""
        )

        new_chat_log = ChatMessage(
            user_email=payload.email,
            thread_id=assigned_thread_id,
            user_query=payload.text,
            ai_response=eligibility_report
        )
        db.add(new_chat_log)
        db.commit()

        return {
            "response": eligibility_report,
            "thread_id": assigned_thread_id,
            "id": new_chat_log.id
        }

    except Exception as e:
        print(f"[CRITICAL ERROR] Chat endpoint crashed: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Internal Server error: {str(e)}"}
        )

@app.get("/api/history")
def get_user_history_logs(email: Optional[str] = None, db: Session = Depends(get_db)):
    if not email:
        return {"history": []}
        
    all_messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_email == email)
        .order_by(ChatMessage.id.asc())
        .all()
    )
    
    # Group messages by thread_id to build individual conversation links
    grouped_threads = {}
    for msg in all_messages:
        tid = msg.thread_id if msg.thread_id else f"legacy_{msg.id}"
        if tid not in grouped_threads:
            grouped_threads[tid] = {
                "id": msg.id,
                "thread_id": tid,
                "user_query": msg.user_query, # Uses first query as sidebar title
                "ai_response": msg.ai_response
            }
            
    return {"history": list(grouped_threads.values())}

@app.get("/api/thread")
def get_specific_thread(id: int, db: Session = Depends(get_db)):
    # Find the target message first to find its thread group string
    base_message = db.query(ChatMessage).filter(ChatMessage.id == id).first()
    
    if not base_message:
        return {"messages": [], "thread_id": None}
        
    # Handle matching criteria safely for rows that don't have a thread_id yet
    if base_message.thread_id:
        all_turns = (
            db.query(ChatMessage)
            .filter(ChatMessage.thread_id == base_message.thread_id)
            .order_by(ChatMessage.id.asc())
            .all()
        )
        target_thread_id = base_message.thread_id
    else:
        # Fallback for old single-turn records left behind in the database
        all_turns = [base_message]
        target_thread_id = f"legacy_{base_message.id}"
    
    formatted_messages = []
    for turn in all_turns:
        formatted_messages.append({"text": turn.user_query, "sender": "user"})
        formatted_messages.append({"text": turn.ai_response, "sender": "bot"})
        
    return {"messages": formatted_messages, "thread_id": target_thread_id}

@app.post("/api/upload")
async def upload_user_document(
    email: str = Query(...), 
    thread_id: Optional[str] = Query(None),
    file: UploadFile = File(...)
):
    """Receives personal documents and namespaces them safely within /uploads."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file selected.")
        
    allowed_extensions = {".pdf", ".txt", ".docx", ".doc"}
    file_extension = os.path.splitext(file.filename)[1].lower()
    if file_extension not in allowed_extensions:
        raise HTTPException(
            status_code=400, 
            detail=f"Unsupported file type. Allowed options: {', '.join(allowed_extensions)}"
        )
        
    user_upload_directory = user_upload_dir(email, thread_id)
    
    safe_filename = "".join([c for c in file.filename if c.isalnum() or c in "._-"]).strip()
    file_path = os.path.join(user_upload_directory, safe_filename)
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        return {
            "message": "Document uploaded and tracked successfully",
            "filename": safe_filename,
            "saved_path": file_path
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to store file layout structure: {str(e)}")

@app.get("/api/documents")
def list_user_documents(email: str = Query(...), thread_id: Optional[str] = Query(None)):
    """Scans user folder and lists files."""
    user_upload_directory = user_upload_dir(email, thread_id)
    
    if not os.path.exists(user_upload_directory):
        return {"documents": []}
        
    try:
        all_files = [
            f for f in os.listdir(user_upload_directory) 
            if os.path.isfile(os.path.join(user_upload_directory, f))
        ]
        return {"documents": all_files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to query files repository: {str(e)}")

@app.post("/api/documents/process")
async def process_user_document(
    email: str = Query(..., description="The user's email address"),
    filename: str = Query(..., description="The exact filename of the uploaded document"),
    thread_id: Optional[str] = Query(None, description="The active chat thread context"),
    system_prompt: Optional[str] = Query(None, description="Custom processing instructions for the AI")
):
    # Reconstruct the user's specific uploads folder path
    file_path = os.path.join(user_upload_dir(email, thread_id), filename)

    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"The file '{filename}' could not be located."
        )

    try:
        # Extract the raw text from the file using your universal doc_parser
        print(f"Parsing raw text from file: {filename}...")
        raw_text = parse_document(file_path, quiet=True)
        cleaned_doc_text = clean_text(raw_text)

        if not cleaned_doc_text.strip():
            return {
                "filename": filename,
                "status": "Skipped",
                "slm_analysis": "The target document appeared to be empty or unreadable."
            }

        # Handle default fallback for system instructions
        default_prompt = "Extract all core data and summarize the document contents."
        active_prompt = system_prompt if system_prompt else default_prompt

        # Initialize the local backend builder
        local_backend = build_backend(
            backend_name=None,   
            host=None,           
            model=None, # auto-detect model
            temperature=0.1,     
            max_tokens=2048,     
            timeout=120,         
            quiet=False          
        )

        print(f"Running local extraction pipeline for {filename}...")
        
        # Execute processing via local LLM engine
        ai_analysis = extract(
            text=cleaned_doc_text,
            system_prompt=active_prompt,
            backend=local_backend,
            fmt="text",
            chunk_size=4000,
            overlap=300,
            quiet=False
        )

        return {
            "filename": filename,
            "status": "Success",
            "slm_analysis": ai_analysis
        }

    except Exception as e:
        print(f"Pipeline execution crash error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred during processing: {str(e)}"
        )

@app.delete("/api/documents")
def delete_user_document(email: str = Query(...), filename: str = Query(...), thread_id: Optional[str] = Query(None)):
    file_path = os.path.join(user_upload_dir(email, thread_id), filename)
    
    if os.path.exists(file_path):
        os.remove(file_path) # Deletes file from disk storage layout
        return {"message": f"Successfully deleted {filename}"}
    else:
        raise HTTPException(status_code=404, detail="File not found on system disk.")

@app.post("/api/tracker/update")
def update_application_status(payload: TrackerStatusUpdate, db: Session = Depends(get_db)):
    """
    Handles application tracker updates. If 'rejected' is passed, it triggers
    the local SLM and scraping engine to find alternative community programs.
    """
    if payload.status.lower() == "rejected":
        return {
            "status": "Success",
            "message": "Status updated to Rejected.",
            "trigger_followup": True
        }
        
    return {"status": "Success", "message": f"Status updated to {payload.status}"}

class FPLRequest(BaseModel):
    income: float
    household_size: int
    state: Optional[str] = ""

@app.post("/api/fpl/calculate")
def calculate_fpl(req: FPLRequest):
    snapshot = calculate_fpl_snapshot(
        {"annual_income": req.income, "household_size": req.household_size}, 
        state=req.state
    )
    return snapshot

@app.get("/api/documents/deadlines")
def get_document_deadlines(email: str = Query(...), filename: str = Query(...), thread_id: Optional[str] = Query(None)):
    file_path = os.path.join(user_upload_dir(email, thread_id), filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found.")
    
    # We must import parse_document here or it's already imported
    # It is imported at the top: from parse_process.doc_parser import parse_document, clean_text
    raw_text = parse_document(file_path, quiet=True)
    report = build_deadline_report(clean_text(raw_text))
    return report

@app.post("/api/caseworker/notes")
def generate_caseworker_notes(report: dict):
    return {"notes": build_case_note(report)}
