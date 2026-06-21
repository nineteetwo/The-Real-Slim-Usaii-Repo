# CalHelpr FastAPI App

FastAPI backend plus the original vanilla HTML/CSS/JS frontend for the CalHelpr benefits application helper.

This is the maintained app UI. It does not use Streamlit.

WE BELIEVE IN U WENDY U CAN DO DIS

## Run

From this folder:

```powershell
.\start.bat
```

The script installs dependencies, starts the API server, starts a simple frontend server, and opens the app.

```text
Backend API:   http://127.0.0.1:8000
Frontend app:  http://localhost:9292/index.html
```

Manual startup:

```powershell
pip install -r requirements.txt
cd .\backend
python -m uvicorn main:app --port 8000 --reload
```

In another terminal:

```powershell
cd .\frontnd
python -m http.server 9292
```

## Current UI Flow

The main workflow is on `frontnd/index.html`.

Each new chat creates a new thread context. Documents, parsed text, profile JSON, match reports, deadlines, guidance, and follow-up history are stored under that chat context so data from one chat does not leak into another chat.

The Application Helper includes:

- Attach Documents: uploads one or more documents into the current chat context.
- Prepare: select one or more attached documents, add notes, and extract an applicant profile.
- Profile: review and edit extracted applicant fields before matching.
- Matches: run program matching against the local program database and FPL calculation.
- Next Steps: review deadlines, generate outcome guidance, and inspect artifacts.
- Follow-up Chat: right-side panel that unlocks only after matching creates a report.

The bottom global chat box was removed. Follow-up conversation now happens in the right-side Follow-up Chat panel after application context exists.

## Documents Page

`frontnd/documents.html` is only a general document library.

For application analysis, attach files inside the active chat from the Application Helper. Chat-attached files are stored separately by user and thread.

## Backend Storage

Uploads:

```text
backend/uploads/<safe_user_email>/<thread_id>/
```

Generated workflow artifacts:

```text
backend/workspaces/<safe_user_email>/<thread_id>/
```

Typical artifacts:

```text
parsed.txt
profile.json
report.json
report.md
deadlines.json
case_note.md
guide.json
guide.md
chat_history.txt
```

## Important API Routes

Auth and chat history:

```text
POST /api/signup
POST /api/login
GET  /api/history
GET  /api/thread
```

Chat-scoped documents:

```text
POST   /api/upload?email=<email>&thread_id=<thread>
GET    /api/documents?email=<email>&thread_id=<thread>
DELETE /api/documents?email=<email>&thread_id=<thread>&filename=<name>
POST   /api/documents/process?email=<email>&thread_id=<thread>&filename=<name>
GET    /api/documents/deadlines?email=<email>&thread_id=<thread>&filename=<name>
```

Application workflow:

```text
GET  /api/workflow/state?email=<email>&thread_id=<thread>
POST /api/workflow/intake
POST /api/workflow/profile
POST /api/workflow/profile/raw
POST /api/workflow/match
POST /api/workflow/guidance
POST /api/workflow/followup
GET  /api/workflow/artifact?email=<email>&thread_id=<thread>&name=<artifact>
```

Utilities:

```text
POST /api/fpl/calculate
POST /api/caseworker/notes
POST /api/tracker/update
```

## Multi-Document Intake

`POST /api/workflow/intake` accepts multiple selected documents:

```json
{
  "email": "guest",
  "thread_id": "thread_123",
  "filenames": [
    "01_assistance_intake.pdf",
    "02_pay_stub.pdf",
    "03_lease_notice.pdf"
  ],
  "manual_notes": "Applicant also needs utility help."
}
```

The backend parses each selected document, labels each source in the merged context, appends manual notes, and sends the combined text to the local extraction model.

## Follow-Up Chat

The follow-up panel stays locked until `/api/workflow/match` produces a report for the active thread.

After matching, follow-up questions go to:

```text
POST /api/workflow/followup
```

The follow-up assistant uses the current report, generated guidance, local program database, and thread chat history. If the user gives new eligibility-changing facts, it can rerun matching and refresh artifacts.

## Local Model Requirements

The backend uses the existing parser and local SLM/LLM modules in `backend/parse_process/`.

For Ollama, start the service and pull a model:

```powershell
ollama serve
ollama pull llama3.2:3b
```

The local model handles extraction and explanations. Deterministic Python code handles FPL math.

## Privacy Notes

The default workflow is local-first:

- uploaded documents are saved locally
- generated profiles and reports are saved locally
- model calls are intended for local backends such as Ollama, LM Studio, or llama.cpp

Optional web fallback/resource search may contact external websites. Use it only when current external resource discovery is needed.

## Disclaimer

CalHelpr is not an official eligibility system and does not approve, deny, or certify benefits. It organizes information, estimates possible matches, calculates FPL percentages, and suggests next steps. Users should verify details with the official agency, a caseworker, legal aid provider, or 2-1-1.
