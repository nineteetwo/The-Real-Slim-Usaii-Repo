import os
import sys
import json
import builtins

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from parse_process.process_parse import build_backend, extract
from parse_process.assistance_finder import find_assistance, format_report_markdown

def generate_rag_response(current_query: str, full_history: str = "", user_doc_path: str = "") -> str:
    """
    Executes the assistance finder pipeline using the uploaded document data 
    and chat history, returning the exact markdown report.
    """
    
    # Initialize local SLM backend engine 
    print("Initializing RAG backend engine...")
    backend = build_backend(
        backend_name=None,
        host=None,
        model=None, 
        temperature=0.1,
        max_tokens=8192,
        timeout=300,
        quiet=True
    )
    
    # Extract context from the document if the user passed an active file path
    document_data = ""
    if user_doc_path:
        user_doc_path = user_doc_path.strip()

        # Resolve relative paths against the backend directory (where this file lives)
        if not os.path.isabs(user_doc_path):
            resolved_path = os.path.normpath(os.path.join(CURRENT_DIR, user_doc_path))
        else:
            resolved_path = user_doc_path

        print(f"RAG Pipeline: Looking for document at: {resolved_path}")

        if os.path.exists(resolved_path):
            try:
                from parse_process.doc_parser import parse_document, clean_text
                raw_text = parse_document(resolved_path, quiet=True)
                raw_doc_text = clean_text(raw_text)
                print(f"RAG Pipeline: Successfully extracted {len(raw_doc_text)} chars from document.")
                
                # Extract relevant context from the document so it fits in the LLM prompt properly
                system_prompt = (
                    "You are a meticulous assistant helping to extract relevant demographic, financial, health, "
                    "and personal context from a document. Extract any and all information that could be "
                    "relevant to determining eligibility for public or nonprofit assistance programs. "
                    "Output a detailed summary, preserving specific numbers, dates, statuses, and facts."
                )
                
                document_data = extract(
                    text=raw_doc_text,
                    system_prompt=system_prompt,
                    backend=backend,
                    fmt="text",
                    chunk_size=12000,
                    overlap=500,
                    quiet=True
                )
                print("RAG Pipeline: Successfully processed document into context summary.")
            except Exception as e:
                print(f"Error parsing document: {e}")
                document_data = ""
        else:
            print(f"RAG Pipeline: Document not found at: {resolved_path}")

    # Fall back to pre-generated profile.json + report.md when no uploaded doc is available
    if not document_data:
        profile_path = os.path.join(CURRENT_DIR, "parse_process", "profile.json")
        report_path = os.path.join(CURRENT_DIR, "parse_process", "report.md")

        if os.path.exists(profile_path):
            try:
                with open(profile_path, "r", encoding="utf-8") as f:
                    document_data = json.load(f)
                print("RAG Pipeline: Loaded profile.json as fallback context.")
            except Exception as e:
                print(f"RAG Pipeline: Could not load profile.json: {e}")

        if os.path.exists(report_path):
            try:
                with open(report_path, "r", encoding="utf-8") as f:
                    report_context = f.read()
                # Append the pre-generated report as additional conversation context
                if isinstance(document_data, dict):
                    document_data["prior_report"] = report_context
                elif not document_data:
                    document_data = report_context
                print("RAG Pipeline: Loaded report.md as fallback context.")
            except Exception as e:
                print(f"RAG Pipeline: Could not load report.md: {e}")

    # Format conversations cleanly into strings, checking for None types safely
    # Ensure inputs are strings, default to empty string if None
    safe_history = str(full_history) if full_history is not None else ""
    safe_query = str(current_query) if current_query is not None else ""
    
    conversations_list = []
    if safe_history.strip():
        conversations_list.append(safe_history.strip())
        
    if safe_query.strip():
        conversations_list.append(f"User Question: {safe_query.strip()}")

    # If still empty, use a default string
    if not conversations_list:
        conversations_list = ["User requested eligibility options overview."]

    # Define static resource paths and handle missing databases gracefully
    database_path = os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", "programs_db.json"))
    if not os.path.exists(database_path):
        print(f"WARNING: Database file not found at {database_path}. Initializing placeholder array.")
        try:
            with open(database_path, "w", encoding="utf-8") as db_file:
                json.dump([], db_file)
        except Exception as write_err:
            return f"### Database Access Error\n\nFailed to initialize placeholder database: `{str(write_err)}`"

    try:
        print("Running assistance matching analysis...")

        report = find_assistance(
            document_data=document_data,
            conversations=conversations_list,
            location="CA",  # Context seeds defaults to local region
            backend=backend,
            db_path=database_path,
            enable_web_fallback=True,
            quiet=True
        )
        
        # Generate markdown text response
        if not report:
            return "### Analysis Notice\n\nNo program definitions or fallback findings could be parsed for this request."
            
        exact_markdown_output = format_report_markdown(report)
        
        # Append a notice to the output if they are using an empty database file layout
        if os.path.exists(database_path) and os.path.getsize(database_path) <= 2:
            exact_markdown_output += (
                "\n\n---\n"
                "⚠️ **System Note for Development:** The database file `programs_db.json` is currently empty. "
                "Local database program matching was skipped, and results rely on the web fallback system."
            )

        return exact_markdown_output

    except Exception as e:
        print(f"RAG Pipeline error: {str(e)}")
        return (
            "### Service Notification\n\n"
            "I encountered an error analyzing your program eligibility options. "
            f"Details: `{str(e)}`"
        )
