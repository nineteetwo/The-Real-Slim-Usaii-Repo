const CHAT_BACKEND_URL = "http://127.0.0.1:8000";
const CHAT_CATEGORY_OPTIONS = new Set([
    "food", "healthcare", "housing", "utility", "cash_assistance", "disability",
    "veteran", "education", "childcare", "employment", "disaster_relief",
    "legal_aid", "general"
]);

let chatWorkflowState = {};

document.addEventListener("DOMContentLoaded", () => {
    bindChatWorkflowControls();
    refreshChatWorkflow();
});

window.refreshChatWorkflow = refreshChatWorkflow;

function activeEmail() {
    return localStorage.getItem("calhelpr_email") || "guest";
}

function activeThreadId() {
    if (window.getCalhelprThreadId) return window.getCalhelprThreadId();
    return localStorage.getItem("activeThreadId") || "global";
}

async function chatApi(path, options = {}) {
    const response = await fetch(`${CHAT_BACKEND_URL}${path}`, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Server error");
    return data;
}

function bindChatWorkflowControls() {
    const uploadTrigger = document.getElementById("chat-upload-trigger");
    const fileInput = document.getElementById("chat-file-input");
    if (uploadTrigger && fileInput) {
        uploadTrigger.addEventListener("click", () => fileInput.click());
        fileInput.addEventListener("change", uploadChatDocument);
    }

    const bindings = [
        ["runIntakeBtn", runIntake],
        ["quickIntakeBtn", () => switchWorkflowTab("prepare")],
        ["saveProfileBtn", () => saveProfile(false)],
        ["matchProfileBtn", () => saveProfile(true)],
        ["saveRawProfileBtn", saveRawProfile],
        ["runMatchBtn", runMatching],
        ["generateGuideBtn", generateGuidance],
    ];
    bindings.forEach(([id, fn]) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener("click", fn);
    });

    document.querySelectorAll("[data-workflow-tab]").forEach(tab => {
        tab.addEventListener("click", () => switchWorkflowTab(tab.dataset.workflowTab));
    });

    const followupForm = document.getElementById("followupPanelForm");
    if (followupForm) {
        followupForm.addEventListener("submit", sendFollowupQuestion);
    }
}

function switchWorkflowTab(name) {
    document.querySelectorAll("[data-workflow-tab]").forEach(tab => {
        tab.classList.toggle("active", tab.dataset.workflowTab === name);
    });
    document.querySelectorAll("[data-workflow-panel]").forEach(panel => {
        panel.classList.toggle("active", panel.dataset.workflowPanel === name);
    });
}

async function refreshChatWorkflow() {
    await Promise.all([loadWorkflowState(), fetchChatDocuments()]);
}

async function loadWorkflowState() {
    try {
        chatWorkflowState = await chatApi(`/api/workflow/state?email=${encodeURIComponent(activeEmail())}&thread_id=${encodeURIComponent(activeThreadId())}`);
        renderWorkflowState();
    } catch (error) {
        console.error("Workflow state load failed:", error);
    }
}

async function fetchChatDocuments() {
    const container = document.getElementById("fileListContainer");
    const fplSelect = document.getElementById("fplDocSelect");
    const intakeSelect = document.getElementById("intakeDocSelect");
    if (!container) return;

    try {
        const data = await chatApi(`/api/documents?email=${encodeURIComponent(activeEmail())}&thread_id=${encodeURIComponent(activeThreadId())}`);
        const files = data.documents || [];
        if (fplSelect) fplSelect.innerHTML = '<option value="">Select a document...</option>';
        if (intakeSelect) intakeSelect.innerHTML = "";

        if (!files.length) {
            container.innerHTML = `
                <div class="empty-attach-state">
                    <i class="fa-solid fa-paperclip"></i>
                    <strong>No documents attached yet</strong>
                    <span>Use the paperclip beside the chat input to add PDFs, notices, forms, or notes.</span>
                </div>
            `;
            return;
        }

        container.innerHTML = "";
        files.forEach(filename => {
            if (fplSelect) addOption(fplSelect, filename);
            if (intakeSelect) addOption(intakeSelect, filename);
            const row = document.createElement("div");
            row.className = "file-item";
            row.innerHTML = `
                <div class="file-main">
                    <i class="fa-regular fa-file-lines"></i>
                    <span class="file-name" title="${escapeHtml(filename)}">${escapeHtml(filename)}</span>
                </div>
                <div class="file-actions">
                    <button class="action-btn" type="button" data-deadline="${escapeHtml(filename)}">
                        <i class="fa-regular fa-clock"></i>
                        <span>Dates</span>
                    </button>
                    <button class="delete-btn" type="button" data-delete="${escapeHtml(filename)}">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                </div>
            `;
            container.appendChild(row);
        });
        container.querySelectorAll("[data-deadline]").forEach(btn => {
            btn.addEventListener("click", () => extractDeadlines(btn.dataset.deadline));
        });
        container.querySelectorAll("[data-delete]").forEach(btn => {
            btn.addEventListener("click", () => deleteChatDocument(btn.dataset.delete));
        });
    } catch (error) {
        container.innerHTML = `<p class="empty-text">Could not load chat documents: ${escapeHtml(error.message)}</p>`;
    }
}

function addOption(select, filename) {
    const opt = document.createElement("option");
    opt.value = filename;
    opt.textContent = filename;
    select.appendChild(opt);
}

async function uploadChatDocument() {
    const fileInput = document.getElementById("chat-file-input");
    const status = document.getElementById("chatUploadStatus");
    const files = Array.from(fileInput.files || []);
    if (!files.length) return;

    status.innerText = files.length > 1
        ? `Uploading ${files.length} documents into this chat context...`
        : "Uploading document into this chat context...";

    try {
        let lastUpload = null;
        for (const file of files) {
            const formData = new FormData();
            formData.append("file", file);
            lastUpload = await chatApi(`/api/upload?email=${encodeURIComponent(activeEmail())}&thread_id=${encodeURIComponent(activeThreadId())}`, {
                method: "POST",
                body: formData
            });
        }
        if (lastUpload) {
            localStorage.setItem("calhelpr_last_upload_path", lastUpload.saved_path);
            localStorage.setItem("calhelpr_last_upload_name", lastUpload.filename);
            updateAttachedDocLabel(files.length > 1 ? `${files.length} documents` : lastUpload.filename);
        }
        status.innerText = files.length > 1
            ? `Attached ${files.length} documents to this chat.`
            : `Attached ${lastUpload.filename} to this chat.`;
        fileInput.value = "";
        await fetchChatDocuments();
        switchWorkflowTab("prepare");
    } catch (error) {
        status.innerText = `Upload failed: ${error.message}`;
    }
}

function updateAttachedDocLabel(filename) {
    const indicator = document.getElementById("attached-document-indicator");
    const name = document.getElementById("attached-document-name");
    if (indicator && name) {
        name.innerText = filename;
        indicator.style.display = "flex";
    }
}

async function deleteChatDocument(filename) {
    if (!confirm(`Delete ${filename} from this chat?`)) return;
    await chatApi(`/api/documents?email=${encodeURIComponent(activeEmail())}&thread_id=${encodeURIComponent(activeThreadId())}&filename=${encodeURIComponent(filename)}`, {
        method: "DELETE"
    });
    await fetchChatDocuments();
}

async function extractDeadlines(filename) {
    const board = document.getElementById("deadlineBoard");
    board.innerText = `Extracting deadlines from ${filename}...`;
    try {
        const data = await chatApi(`/api/documents/deadlines?email=${encodeURIComponent(activeEmail())}&thread_id=${encodeURIComponent(activeThreadId())}&filename=${encodeURIComponent(filename)}`);
        renderDeadlines(data);
    } catch (error) {
        board.innerText = `Deadline extraction failed: ${error.message}`;
    }
}

function renderWorkflowState() {
    renderStageRail();
    renderProfile(chatWorkflowState.profile);
    renderMatchBoard(chatWorkflowState.report, chatWorkflowState.status_counts || {});
    renderDeadlines(chatWorkflowState.deadlines);
    renderGuidance(chatWorkflowState.guide_md);
    renderArtifacts(chatWorkflowState);
    renderFollowupPanel();
}

function setStage(id, ready) {
    const stage = document.getElementById(id);
    if (!stage) return;
    stage.classList.toggle("ready", Boolean(ready));
    const span = stage.querySelector("span");
    if (span) span.innerText = ready ? "ready" : "waiting";
}

function renderStageRail() {
    setStage("stage-intake", chatWorkflowState.parsed_text || chatWorkflowState.profile);
    setStage("stage-profile", chatWorkflowState.profile);
    setStage("stage-match", chatWorkflowState.report);
    setStage("stage-deadlines", chatWorkflowState.deadlines);
    setStage("stage-guide", chatWorkflowState.guide_md);
    updateWorkflowHint();
}

function updateWorkflowHint() {
    const hint = document.getElementById("workflowHint");
    if (!hint) return;
    if (!chatWorkflowState.profile) {
        hint.innerText = "Attach documents or add notes, then extract an applicant profile.";
    } else if (!chatWorkflowState.report) {
        hint.innerText = "Profile is ready. Review it, then run matching.";
    } else if (!chatWorkflowState.guide_md) {
        hint.innerText = "Matches are ready. Generate outcome guidance when an application result is known.";
    } else {
        hint.innerText = "Guidance and artifacts are ready for this chat.";
    }
}

function setField(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value || "";
}

function renderProfile(profile) {
    const rawBox = document.getElementById("rawProfileJson");
    if (rawBox) rawBox.value = profile ? JSON.stringify(profile, null, 2) : "";
    if (!profile) return;
    const location = typeof profile.location === "object" && profile.location ? profile.location : {};
    setField("profileCity", location.city);
    setField("profileState", location.state);
    setField("profileZip", location.zip);
    setField("profileHousehold", profile.household_size);
    setField("profileAnnualIncome", profile.annual_income);
    setField("profileMonthlyIncome", profile.monthly_income);
    setField("profileEmployment", profile.employment_status);
    setField("profileAge", profile.age);
    setField("profileDependents", profile.dependents);
    setField("profileDisability", profile.disability_status);
    setField("profileVeteran", profile.veteran_status);
    setField("profileCitizenship", profile.citizenship_status);
    setField("profileNeeds", profile.stated_needs);
    setField("profileCategories", Array.isArray(profile.needed_categories) ? profile.needed_categories.join(", ") : "");
    setField("profileSummary", profile.summary);
}

function readProfileForm() {
    const existing = chatWorkflowState.profile || {};
    const categories = (document.getElementById("profileCategories").value || "")
        .split(",")
        .map(item => item.trim())
        .filter(item => CHAT_CATEGORY_OPTIONS.has(item));
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
    const selectedFiles = Array.from(document.getElementById("intakeDocSelect").selectedOptions || [])
        .map(option => option.value)
        .filter(Boolean);
    status.innerText = selectedFiles.length > 1
        ? `Extracting applicant profile from ${selectedFiles.length} documents...`
        : "Extracting applicant profile with this chat context...";
    try {
        await chatApi("/api/workflow/intake", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                email: activeEmail(),
                thread_id: activeThreadId(),
                filenames: selectedFiles,
                manual_notes: document.getElementById("manualNotes").value
            })
        });
        status.innerText = "Profile extracted. Review it below.";
        await loadWorkflowState();
        switchWorkflowTab("profile");
    } catch (error) {
        status.innerText = `Intake failed: ${error.message}`;
    }
}

async function saveProfile(runMatching) {
    const status = document.getElementById("profileStatus");
    status.innerText = runMatching ? "Saving profile and matching..." : "Saving profile...";
    try {
        await chatApi("/api/workflow/profile", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                email: activeEmail(),
                thread_id: activeThreadId(),
                profile: readProfileForm(),
                run_matching: runMatching
            })
        });
        status.innerText = runMatching ? "Profile saved and match board updated." : "Profile saved.";
        await loadWorkflowState();
        if (runMatching) switchWorkflowTab("matches");
    } catch (error) {
        status.innerText = `Profile save failed: ${error.message}`;
    }
}

async function saveRawProfile() {
    const status = document.getElementById("profileStatus");
    try {
        await chatApi("/api/workflow/profile/raw", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                email: activeEmail(),
                thread_id: activeThreadId(),
                raw_json: document.getElementById("rawProfileJson").value
            })
        });
        status.innerText = "Raw JSON profile saved.";
        await loadWorkflowState();
    } catch (error) {
        status.innerText = `Raw JSON save failed: ${error.message}`;
    }
}

async function runMatching() {
    const board = document.getElementById("matchBoard");
    board.innerText = "Running matching for this chat...";
    try {
        await chatApi("/api/workflow/match", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                email: activeEmail(),
                thread_id: activeThreadId(),
                location: document.getElementById("profileState").value || "CA"
            })
        });
        await loadWorkflowState();
        switchWorkflowTab("matches");
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
        <div class="metric-card"><strong>${escapeHtml(report.resolved_state || "unknown")}</strong><span>State</span></div>
        <div class="metric-card"><strong>${escapeHtml(fplPercent || "unknown")}</strong><span>FPL percent</span></div>
    `;
    Object.keys(counts || {}).forEach(status => {
        metrics.innerHTML += `<div class="metric-card"><strong>${counts[status]}</strong><span>${escapeHtml(status)}</span></div>`;
    });

    if (guideSelect) guideSelect.innerHTML = "";
    board.innerHTML = "";
    (report.matched_programs || []).forEach(item => {
        const name = item.program_name || item.name || "Program";
        if (guideSelect) addOption(guideSelect, name);
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
    if (!programName) {
        board.innerText = "Run matching first so a program can be selected.";
        return;
    }
    board.innerText = "Generating outcome guidance...";
    try {
        await chatApi("/api/workflow/guidance", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                email: activeEmail(),
                thread_id: activeThreadId(),
                program_name: programName,
                outcome: document.getElementById("guideOutcomeSelect").value
            })
        });
        await loadWorkflowState();
        switchWorkflowTab("next");
    } catch (error) {
        board.innerText = `Guidance failed: ${error.message}`;
    }
}

function renderGuidance(markdown) {
    const board = document.getElementById("guideBoard");
    if (board) board.innerText = markdown || "No guidance generated yet.";
}

function renderFollowupPanel() {
    const panel = document.getElementById("followupPanel");
    const hint = document.getElementById("followupPanelHint");
    const icon = document.getElementById("followupLockIcon");
    const input = document.getElementById("followupPanelInput");
    const send = document.getElementById("followupPanelSend");
    const messages = document.getElementById("followupPanelMessages");
    if (!panel || !hint || !input || !send || !messages) return;

    panel.classList.remove("locked");
    panel.classList.add("unlocked");
    input.disabled = false;
    send.disabled = false;
    hint.innerText = chatWorkflowState.report
        ? "Ask about this chat's matches, documents, deadlines, or next steps."
        : "Ask questions now. Answers improve as profile and matching context is added.";
    if (icon) icon.className = "fa-solid fa-comments";

    const history = chatWorkflowState.chat_history || "";
    if (!history.trim()) {
        messages.innerHTML = `
            <div class="followup-empty">
                <i class="fa-solid fa-circle-question"></i>
                <strong>Ready for questions</strong>
                <span>Ask about uploaded documents, missing profile facts, matching, deadlines, or what to do next.</span>
            </div>
        `;
        return;
    }

    messages.innerHTML = "";
    history.split(/\n\n+/).forEach(block => {
        const trimmed = block.trim();
        if (!trimmed) return;
        const isUser = trimmed.toLowerCase().startsWith("user:");
        const bubble = document.createElement("div");
        bubble.className = `followup-bubble ${isUser ? "user" : "assistant"}`;
        bubble.innerText = trimmed.replace(/^(User|Assistant):\s*/i, "");
        messages.appendChild(bubble);
    });
    messages.scrollTop = messages.scrollHeight;
}

async function sendFollowupQuestion(event) {
    event.preventDefault();
    const input = document.getElementById("followupPanelInput");
    const messages = document.getElementById("followupPanelMessages");
    const question = input.value.trim();
    if (!question) return;
    input.value = "";

    const userBubble = document.createElement("div");
    userBubble.className = "followup-bubble user";
    userBubble.innerText = question;
    messages.appendChild(userBubble);

    const loading = document.createElement("div");
    loading.className = "followup-bubble assistant";
    loading.innerText = "Thinking...";
    messages.appendChild(loading);
    messages.scrollTop = messages.scrollHeight;

    try {
        const data = await chatApi("/api/workflow/followup", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                email: activeEmail(),
                thread_id: activeThreadId(),
                question,
                auto_rerun: true
            })
        });
        loading.innerText = data.answer || "No answer returned.";
        await loadWorkflowState();
    } catch (error) {
        loading.innerText = `Follow-up failed: ${error.message}`;
    }
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

async function calculateFPLFromDoc(event) {
    event.preventDefault();
    const docName = document.getElementById("fplDocSelect").value;
    const resultDiv = document.getElementById("fplResult");
    if (!docName) return;
    resultDiv.innerText = "Parsing document for income data...";
    try {
        const prompt = "Extract the applicant's annual income, household size, and state abbreviation. Return ONLY a valid JSON object with keys: income (number), household_size (number), state (string).";
        const data = await chatApi(`/api/documents/process?email=${encodeURIComponent(activeEmail())}&thread_id=${encodeURIComponent(activeThreadId())}&filename=${encodeURIComponent(docName)}&system_prompt=${encodeURIComponent(prompt)}`, {
            method: "POST"
        });
        const rawText = data.slm_analysis || "";
        const jsonMatch = rawText.match(/\{[\s\S]*\}/);
        const extracted = JSON.parse(jsonMatch ? jsonMatch[0] : rawText);
        const fplData = await chatApi("/api/fpl/calculate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                income: parseFloat(extracted.income || 0),
                household_size: parseInt(extracted.household_size || 1),
                state: extracted.state || ""
            })
        });
        resultDiv.innerHTML = `
            <strong>Parsed Annual Income:</strong> $${escapeHtml(fplData.annual_income)}<br>
            <strong>Parsed Household Size:</strong> ${escapeHtml(fplData.household_size)}<br>
            <strong>100% FPL Amount:</strong> $${escapeHtml(fplData.fpl_100_amount)}<br>
            <strong>Your FPL Percentage:</strong> ${escapeHtml(fplData.fpl_percent)}%
        `;
    } catch (error) {
        resultDiv.innerText = `FPL calculation failed: ${error.message}`;
    }
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}
