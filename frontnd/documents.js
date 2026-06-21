const BACKEND_URL = "http://127.0.0.1:8000";
const currentUserEmail = localStorage.getItem("calhelpr_email") || "guest";
const CATEGORY_OPTIONS = new Set([
    "food", "healthcare", "housing", "utility", "cash_assistance", "disability",
    "veteran", "education", "childcare", "employment", "disaster_relief",
    "legal_aid", "general"
]);
let workflowState = {};

document.addEventListener("DOMContentLoaded", () => {
    fetchUploadedDocuments();
});

function bindWorkflowControls() {
    const runIntakeBtn = document.getElementById("runIntakeBtn");
    const saveProfileBtn = document.getElementById("saveProfileBtn");
    const matchProfileBtn = document.getElementById("matchProfileBtn");
    const saveRawProfileBtn = document.getElementById("saveRawProfileBtn");
    const runMatchBtn = document.getElementById("runMatchBtn");
    const generateGuideBtn = document.getElementById("generateGuideBtn");
    const sendFollowupBtn = document.getElementById("sendFollowupBtn");

    if (runIntakeBtn) runIntakeBtn.addEventListener("click", runIntake);
    if (saveProfileBtn) saveProfileBtn.addEventListener("click", () => saveProfile(false));
    if (matchProfileBtn) matchProfileBtn.addEventListener("click", () => saveProfile(true));
    if (saveRawProfileBtn) saveRawProfileBtn.addEventListener("click", saveRawProfile);
    if (runMatchBtn) runMatchBtn.addEventListener("click", runMatching);
    if (generateGuideBtn) generateGuideBtn.addEventListener("click", generateGuidance);
    if (sendFollowupBtn) sendFollowupBtn.addEventListener("click", sendFollowup);
}

async function apiFetch(path, options = {}) {
    const response = await fetch(`${BACKEND_URL}${path}`, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(data.detail || "Server error");
    }
    return data;
}

async function loadWorkflowState() {
    try {
        workflowState = await apiFetch(`/api/workflow/state?email=${encodeURIComponent(currentUserEmail)}`);
        renderWorkflowState();
    } catch (error) {
        console.error("Workflow state load failed:", error);
    }
}

function renderWorkflowState() {
    renderStageRail();
    renderProfile(workflowState.profile);
    renderMatchBoard(workflowState.report, workflowState.status_counts || {});
    renderDeadlines(workflowState.deadlines);
    renderGuidance(workflowState.guide_md);
    renderFollowupHistory(workflowState.chat_history || "");
    renderArtifacts(workflowState);
}

function setStage(id, ready) {
    const stage = document.getElementById(id);
    if (!stage) return;
    stage.classList.toggle("ready", Boolean(ready));
    const span = stage.querySelector("span");
    if (span) span.innerText = ready ? "ready" : "waiting";
}

function renderStageRail() {
    setStage("stage-intake", workflowState.parsed_text || workflowState.profile);
    setStage("stage-profile", workflowState.profile);
    setStage("stage-match", workflowState.report);
    setStage("stage-deadlines", workflowState.deadlines);
    setStage("stage-guide", workflowState.guide_md);
    setStage("stage-followup", workflowState.chat_history);
}

function profileValue(profile, key, fallback = "") {
    return profile && profile[key] !== null && profile[key] !== undefined ? profile[key] : fallback;
}

function renderProfile(profile) {
    const rawBox = document.getElementById("rawProfileJson");
    if (rawBox) rawBox.value = profile ? JSON.stringify(profile, null, 2) : "";
    if (!profile) return;

    const location = typeof profile.location === "object" && profile.location ? profile.location : {};
    setField("profileCity", location.city || "");
    setField("profileState", location.state || "");
    setField("profileZip", location.zip || "");
    setField("profileHousehold", profileValue(profile, "household_size"));
    setField("profileAnnualIncome", profileValue(profile, "annual_income"));
    setField("profileMonthlyIncome", profileValue(profile, "monthly_income"));
    setField("profileEmployment", profileValue(profile, "employment_status"));
    setField("profileAge", profileValue(profile, "age"));
    setField("profileDependents", profileValue(profile, "dependents"));
    setField("profileDisability", profileValue(profile, "disability_status"));
    setField("profileVeteran", profileValue(profile, "veteran_status"));
    setField("profileCitizenship", profileValue(profile, "citizenship_status"));
    setField("profileNeeds", profileValue(profile, "stated_needs"));
    setField("profileCategories", Array.isArray(profile.needed_categories) ? profile.needed_categories.join(", ") : "");
    setField("profileSummary", profileValue(profile, "summary"));
}

function setField(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value || "";
}

function readProfileForm() {
    const existing = workflowState.profile || {};
    const categories = (document.getElementById("profileCategories").value || "")
        .split(",")
        .map(item => item.trim())
        .filter(item => CATEGORY_OPTIONS.has(item));
    return {
        ...existing,
        location: {
            city: document.getElementById("profileCity").value || null,
            state: document.getElementById("profileState").value || null,
            zip: document.getElementById("profileZip").value || null,
        },
        household_size: numberOrNull("profileHousehold", true),
        annual_income: numberOrNull("profileAnnualIncome"),
        monthly_income: numberOrNull("profileMonthlyIncome"),
        employment_status: document.getElementById("profileEmployment").value || null,
        age: numberOrNull("profileAge", true),
        dependents: numberOrNull("profileDependents", true),
        disability_status: document.getElementById("profileDisability").value || null,
        veteran_status: document.getElementById("profileVeteran").value || null,
        citizenship_status: document.getElementById("profileCitizenship").value || null,
        stated_needs: document.getElementById("profileNeeds").value || null,
        needed_categories: categories,
        summary: document.getElementById("profileSummary").value || null,
    };
}

function numberOrNull(id, integer = false) {
    const raw = document.getElementById(id).value;
    if (raw === "") return null;
    const value = integer ? parseInt(raw, 10) : parseFloat(raw);
    return Number.isFinite(value) ? value : null;
}

async function runIntake() {
    const status = document.getElementById("intakeStatus");
    const filename = document.getElementById("intakeDocSelect").value;
    const manualNotes = document.getElementById("manualNotes").value;
    status.innerText = "Extracting applicant profile with the local model...";
    try {
        const data = await apiFetch("/api/workflow/intake", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: currentUserEmail, filename, manual_notes: manualNotes })
        });
        workflowState.profile = data.profile;
        workflowState.parsed_text = data.parsed_text;
        status.innerText = "Profile extracted. Review and correct it below.";
        await loadWorkflowState();
    } catch (error) {
        status.innerText = `Intake failed: ${error.message}`;
    }
}

async function saveProfile(runMatching) {
    const status = document.getElementById("profileStatus");
    status.innerText = runMatching ? "Saving profile and running matching..." : "Saving profile...";
    try {
        const data = await apiFetch("/api/workflow/profile", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: currentUserEmail, profile: readProfileForm(), run_matching: runMatching })
        });
        status.innerText = runMatching ? "Profile saved and match board updated." : "Profile saved.";
        workflowState.profile = data.profile || workflowState.profile;
        await loadWorkflowState();
    } catch (error) {
        status.innerText = `Profile save failed: ${error.message}`;
    }
}

async function saveRawProfile() {
    const status = document.getElementById("profileStatus");
    status.innerText = "Saving raw JSON profile...";
    try {
        await apiFetch("/api/workflow/profile/raw", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: currentUserEmail, raw_json: document.getElementById("rawProfileJson").value })
        });
        status.innerText = "Raw JSON profile saved.";
        await loadWorkflowState();
    } catch (error) {
        status.innerText = `Raw JSON save failed: ${error.message}`;
    }
}

async function runMatching() {
    const board = document.getElementById("matchBoard");
    board.innerText = "Running database/RAG matching with the local model...";
    try {
        await apiFetch("/api/workflow/match", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: currentUserEmail, location: document.getElementById("profileState").value || "CA" })
        });
        await loadWorkflowState();
    } catch (error) {
        board.innerText = `Matching failed: ${error.message}`;
    }
}

function renderMatchBoard(report, counts) {
    const metrics = document.getElementById("matchMetrics");
    const board = document.getElementById("matchBoard");
    const guideSelect = document.getElementById("guideProgramSelect");
    if (!metrics || !board) return;
    if (!report) {
        metrics.innerHTML = "";
        board.innerHTML = "Run matching to populate program recommendations.";
        return;
    }
    const fplPercent = report.fpl_analysis ? report.fpl_analysis.fpl_percent : "unknown";
    metrics.innerHTML = `
        <div class="metric-card"><strong>${(report.matched_programs || []).length}</strong><span>Programs screened</span></div>
        <div class="metric-card"><strong>${(report.needed_categories || []).length}</strong><span>Needed categories</span></div>
        <div class="metric-card"><strong>${report.resolved_state || "unknown"}</strong><span>State</span></div>
        <div class="metric-card"><strong>${fplPercent || "unknown"}</strong><span>FPL percent</span></div>
    `;
    Object.keys(counts || {}).forEach(status => {
        metrics.innerHTML += `<div class="metric-card"><strong>${counts[status]}</strong><span>${escapeHtml(status)}</span></div>`;
    });

    if (guideSelect) guideSelect.innerHTML = "";
    board.innerHTML = "";
    (report.matched_programs || []).forEach(item => {
        const name = item.program_name || item.name || "Program";
        if (guideSelect) {
            const opt = document.createElement("option");
            opt.value = name;
            opt.textContent = name;
            guideSelect.appendChild(opt);
        }
        const analysis = item.eligibility_analysis || {};
        const card = document.createElement("div");
        card.className = "program-card";
        card.innerHTML = `
            <div class="program-card-head">
                <h4>${escapeHtml(name)}</h4>
                <span>${escapeHtml((analysis.status || "unknown").replaceAll("_", " "))}</span>
            </div>
            <p>${escapeHtml(analysis.summary || "No summary available.")}</p>
            ${renderList("Missing information", analysis.missing_information)}
            ${renderList("Next steps", analysis.recommended_next_steps)}
            ${item.official_url ? `<a href="${escapeHtml(item.official_url)}" target="_blank" class="inline-link">Open program page</a>` : ""}
        `;
        board.appendChild(card);
    });
}

function renderList(label, items) {
    if (!items || !items.length) return "";
    return `<strong>${label}</strong><ul>${items.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function renderDeadlines(deadlines) {
    const board = document.getElementById("deadlineBoard");
    if (!board) return;
    const items = deadlines && deadlines.deadlines ? deadlines.deadlines : [];
    if (!items.length) {
        board.innerHTML = deadlines ? "No relevant upcoming dates were found." : "No deadline scan has run yet.";
        return;
    }
    board.innerHTML = items.map(item => `
        <div class="deadline-item">
            <strong>${escapeHtml(item.date || "Unknown date")}</strong>
            <span>${escapeHtml(String(item.days_until ?? "unknown"))} days | ${escapeHtml((item.urgency || "future").replaceAll("_", " "))}</span>
            <p>${escapeHtml(item.context || "")}</p>
        </div>
    `).join("");
}

async function generateGuidance() {
    const board = document.getElementById("guideBoard");
    const programName = document.getElementById("guideProgramSelect").value;
    const outcome = document.getElementById("guideOutcomeSelect").value;
    if (!programName) {
        board.innerText = "Run matching first so a program can be selected.";
        return;
    }
    board.innerText = "Generating outcome guidance...";
    try {
        await apiFetch("/api/workflow/guidance", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: currentUserEmail, program_name: programName, outcome })
        });
        await loadWorkflowState();
    } catch (error) {
        board.innerText = `Guidance failed: ${error.message}`;
    }
}

function renderGuidance(markdown) {
    const board = document.getElementById("guideBoard");
    if (!board) return;
    board.innerText = markdown || "No guidance generated yet.";
}

async function sendFollowup() {
    const input = document.getElementById("followupInput");
    const status = document.getElementById("followupStatus");
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    status.innerText = "Answering follow-up and checking whether matching should refresh...";
    try {
        await apiFetch("/api/workflow/followup", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: currentUserEmail, question, auto_rerun: true })
        });
        status.innerText = "Follow-up answer ready.";
        await loadWorkflowState();
    } catch (error) {
        status.innerText = `Follow-up failed: ${error.message}`;
    }
}

function renderFollowupHistory(history) {
    const container = document.getElementById("followupHistory");
    if (!container) return;
    if (!history) {
        container.innerHTML = `<p class="empty-text">Ask a follow-up after matching has run.</p>`;
        return;
    }
    container.innerText = history;
}

function renderArtifacts(state) {
    const container = document.getElementById("artifactList");
    if (!container) return;
    const artifacts = {
        "profile.json": state.profile,
        "report.json": state.report,
        "report.md": state.report_md,
        "deadlines.json": state.deadlines,
        "case_note.md": state.case_note,
        "guide.md": state.guide_md,
        "chat_history.txt": state.chat_history,
    };
    container.innerHTML = "";
    Object.entries(artifacts).forEach(([name, content]) => {
        const value = typeof content === "object" && content ? JSON.stringify(content, null, 2) : (content || "");
        const card = document.createElement("div");
        card.className = "artifact-preview";
        card.innerHTML = `<h4>${name}</h4><pre>${escapeHtml(value.slice(0, 4000) || "Waiting for this artifact.")}</pre>`;
        container.appendChild(card);
    });
}

function escapeHtml(value) {
    return String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

// Handle file upload selection and submission
document.getElementById('uploadForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const fileInput = document.getElementById('fileInput').files[0];
    const statusDiv = document.getElementById('uploadStatus');

    if (!fileInput) return;

    statusDiv.style.color = "var(--text-secondary)";
    statusDiv.innerText = "Uploading to workspace repository...";

    const formData = new FormData();
    formData.append("file", fileInput);

    try {
        // Matches @app.post("/api/upload") passing email as a Query string parameter
        const response = await fetch(`${BACKEND_URL}/api/upload?email=${encodeURIComponent(currentUserEmail)}`, {
            method: 'POST',
            body: formData
        });

        if (response.ok) {
            const uploadData = await response.json();

            if (uploadData.saved_path) {
                localStorage.setItem('calhelpr_last_upload_path', uploadData.saved_path);
                localStorage.setItem('calhelpr_last_upload_name', uploadData.filename);
            }
            statusDiv.style.color = "#27ae60";
            statusDiv.innerText = `File uploaded: ${uploadData.filename}`;
            document.getElementById('uploadForm').reset();

            // Instantly refresh the repository tracking view container
            fetchUploadedDocuments();
        } else {
            const err = await response.json();
            statusDiv.style.color = "#e53935";
            statusDiv.innerText = `Upload failed: ${err.detail || 'Server error'}`;
        }
    } catch (error) {
        statusDiv.style.color = "#e53935";
        statusDiv.innerText = `Network error: ${error.message}`;
    }
});

// Fetch files inside the user's isolated repository folder namespace
async function fetchUploadedDocuments() {
    const container = document.getElementById('fileListContainer');
    
    try {
        const response = await fetch(`${BACKEND_URL}/api/documents?email=${encodeURIComponent(currentUserEmail)}`);
        
        if (!response.ok) {
            container.innerHTML = `<p class="empty-text" style="color: var(--emergency-bg);">Could not load your workspace file manager storage.</p>`;
            return;
        }

        const data = await response.json();
        const files = data.documents;

        if (!files || files.length === 0) {
            container.innerHTML = `<p class="empty-text">No workspace documents found. Upload a file above to add references for your RAG chatbot assistant.</p>`;
            return;
        }

        container.innerHTML = "";
        
        const fplSelect = document.getElementById('fplDocSelect');
        const intakeSelect = document.getElementById('intakeDocSelect');
        if (fplSelect) {
            fplSelect.innerHTML = '<option value="">Select a document...</option>';
        }
        if (intakeSelect) {
            intakeSelect.innerHTML = '<option value="">Use notes only</option>';
        }

        files.forEach(filename => {
            if (fplSelect) {
                const opt = document.createElement('option');
                opt.value = filename;
                opt.textContent = filename;
                fplSelect.appendChild(opt);
            }
            if (intakeSelect) {
                const opt = document.createElement('option');
                opt.value = filename;
                opt.textContent = filename;
                intakeSelect.appendChild(opt);
            }

            const fileRow = document.createElement('div');
            fileRow.className = 'file-item';
            fileRow.style.display = 'flex';
            fileRow.style.justifyContent = 'space-between';
            fileRow.style.alignItems = 'center';
            
            fileRow.innerHTML = `
                <div>
                    <i class="fa-regular fa-file-lines"></i>
                    <span class="file-name">${filename}</span>
                </div>
                <div>
                    <button class="action-btn" onclick="extractDeadlines('${filename}')" style="background:var(--primary); border:none; color:white; padding: 4px 8px; border-radius: 4px; cursor:pointer; margin-right: 10px; font-size: 0.8rem;">
                        <i class="fa-regular fa-clock"></i> Deadlines
                    </button>
                    <button class="delete-btn" onclick="deleteDocument('${filename}')" style="background:none; border:none; color:var(--emergency-bg, #e53935); cursor:pointer;">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                </div>
            `;
            container.appendChild(fileRow);
        });

    } catch (error) {
        container.innerHTML = `<p class="empty-text" style="color: var(--emergency-bg);">Communication error: ${error.message}</p>`;
    }
}

async function deleteDocument(filename) {
    if (!confirm(`Are you sure you want to delete ${filename}?`)) return;

    try {
        const response = await fetch(`${BACKEND_URL}/api/documents?email=${encodeURIComponent(currentUserEmail)}&filename=${encodeURIComponent(filename)}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            fetchUploadedDocuments(); 
        } else {
            alert("Failed to delete the document.");
        }
    } catch (error) {
        console.error("Error during deletion execution pipeline:", error);
    }
}

async function extractDeadlines(filename) {
    const statusDiv = document.getElementById('uploadStatus');
    statusDiv.style.color = "var(--text-secondary)";
    statusDiv.innerText = `Extracting deadlines from ${filename}...`;

    try {
        const response = await fetch(`${BACKEND_URL}/api/documents/deadlines?email=${encodeURIComponent(currentUserEmail)}&filename=${encodeURIComponent(filename)}`);
        if (response.ok) {
            const data = await response.json();
            if (data.deadlines && data.deadlines.length > 0) {
                let report = `Found ${data.deadlines.length} deadlines for ${filename}:\n\n`;
                data.deadlines.forEach(d => {
                    report += `- ${d.date} (${d.days_until} days away): ${d.context}\n`;
                });
                alert(report);
                statusDiv.innerText = "Deadlines extracted successfully.";
            } else {
                alert(`No deadlines found in ${filename}.`);
                statusDiv.innerText = "No deadlines found.";
            }
        } else {
            const err = await response.json();
            alert(`Failed to extract deadlines: ${err.detail || 'Server error'}`);
            statusDiv.innerText = "Failed to extract deadlines.";
        }
    } catch (error) {
        alert(`Network error: ${error.message}`);
    }
}

async function calculateFPLFromDoc(event) {
    event.preventDefault();
    const docName = document.getElementById('fplDocSelect').value;
    const resultDiv = document.getElementById('fplResult');
    
    if (!docName) {
        alert("Please select a document.");
        return;
    }
    
    resultDiv.innerText = "Parsing document for income data... This may take a moment depending on the local model speed.";
    
    try {
        const prompt = "Extract the applicant's annual income, household size, and state abbreviation. Return ONLY a valid JSON object with keys: income (number), household_size (number), state (string). Do not include any other text or markdown.";
        const response = await fetch(`${BACKEND_URL}/api/documents/process?email=${encodeURIComponent(currentUserEmail)}&filename=${encodeURIComponent(docName)}&system_prompt=${encodeURIComponent(prompt)}`, {
            method: 'POST'
        });
        
        if (response.ok) {
            const data = await response.json();
            let extractedData;
            try {
                let rawText = data.slm_analysis;
                let jsonMatch = rawText.match(/\{[\s\S]*\}/);
                extractedData = JSON.parse(jsonMatch ? jsonMatch[0] : rawText);
            } catch (e) {
                resultDiv.innerText = "Could not parse income data from the document. The model output was not valid JSON.";
                return;
            }
            
            const fplResponse = await fetch(`${BACKEND_URL}/api/fpl/calculate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    income: parseFloat(extractedData.income || 0), 
                    household_size: parseInt(extractedData.household_size || 1), 
                    state: extractedData.state || "" 
                })
            });
            
            if (fplResponse.ok) {
                const fplData = await fplResponse.json();
                if (fplData.fpl_percent !== null) {
                    resultDiv.innerHTML = `
                        <strong>Parsed Annual Income:</strong> $${fplData.annual_income}<br>
                        <strong>Parsed Household Size:</strong> ${fplData.household_size}<br>
                        <strong>100% FPL Amount:</strong> $${fplData.fpl_100_amount}<br>
                        <strong>Your FPL Percentage:</strong> ${fplData.fpl_percent}%
                    `;
                } else {
                    resultDiv.innerText = fplData.notes.join(" ");
                }
            } else {
                resultDiv.innerText = "Failed to calculate FPL with parsed values.";
            }
        } else {
            resultDiv.innerText = "Failed to process document. Make sure the local LLM is running.";
        }
    } catch (error) {
        resultDiv.innerText = `Network error: ${error.message}`;
    }
}
