const JIRA_URL_RE =
  /^(https?:\/\/jira\.trimble\.tools\/browse\/[A-Z][A-Z0-9]+-\d+|[A-Z][A-Z0-9]+-\d+)$/i;

const form = document.getElementById("bugForm");
const formError = document.getElementById("formError");
const repoSelect = document.getElementById("repository");
const repoHint = document.getElementById("repoHint");
const branchInput = document.getElementById("branch");
const clearBtn = document.getElementById("clearBtn");
const submitBtn = document.getElementById("submitBtn");
const runSection = document.getElementById("runSection");
const chatLog = document.getElementById("chatLog");
const runStatus = document.getElementById("runStatus");
const runIdLine = document.getElementById("runIdLine");
const runIdEl = document.getElementById("runId");
const openInCursor = document.getElementById("openInCursor");
const summarySection = document.getElementById("summarySection");

let repos = [];
let eventSource = null;

function showError(msg) {
  formError.textContent = msg;
  formError.classList.add("visible");
}

function clearError() {
  formError.textContent = "";
  formError.classList.remove("visible");
}

function normalizeJiraUrl(raw) {
  const trimmed = raw.trim();
  if (/^[A-Z][A-Z0-9]+-\d+$/i.test(trimmed)) {
    return `https://jira.trimble.tools/browse/${trimmed.toUpperCase()}`;
  }
  return trimmed;
}

async function loadRepos() {
  const res = await fetch("/api/repositories");
  repos = await res.json();
  for (const r of repos) {
    const opt = document.createElement("option");
    opt.value = r.id;
    opt.textContent = r.label;
    repoSelect.appendChild(opt);
  }
}

repoSelect.addEventListener("change", () => {
  const repo = repos.find((r) => r.id === repoSelect.value);
  if (!repo) {
    repoHint.innerHTML =
      "GitHub URL will appear after you select a repository.";
    return;
  }
  repoHint.innerHTML = `GitHub: <a href="${repo.githubUrl}" target="_blank" rel="noopener">${repo.githubUrl}</a>`;
  if (!branchInput.value.trim() && repo.defaultBranch) {
    branchInput.value = repo.defaultBranch;
  }
});

clearBtn.addEventListener("click", () => {
  form.reset();
  repoHint.textContent =
    "GitHub URL will appear after you select a repository.";
  clearError();
});

function setStatus(status) {
  runStatus.textContent = status;
  runStatus.className = "status-pill";
  const s = status.toLowerCase();
  if (s.includes("finish") || s === "completed" || s === "done") {
    runStatus.classList.add("status-pill--finished");
  } else if (s.includes("error") || s.includes("fail")) {
    runStatus.classList.add("status-pill--error");
  } else {
    runStatus.classList.add("status-pill--running");
  }
}

function renderMessages(messages) {
  if (!messages || messages.length === 0) return;
  chatLog.innerHTML = "";
  for (const m of messages) {
    const div = document.createElement("div");
    div.className = `msg msg--${m.role === "user" ? "user" : "assistant"}`;
    const role = document.createElement("div");
    role.className = "msg-role";
    role.textContent = m.role === "user" ? "You / webhook" : "Cursor agent";
    const body = document.createElement("div");
    body.textContent = m.text;
    div.appendChild(role);
    div.appendChild(body);
    chatLog.appendChild(div);
  }
  chatLog.scrollTop = chatLog.scrollHeight;
}

function applySummary(summary) {
  if (!summary) return;
  const hasAny =
    summary.rca ||
    summary.problematicCode ||
    summary.appliedFix ||
    summary.explanation ||
    summary.prUrl;
  if (!hasAny) return;
  summarySection.classList.remove("hidden");
  document.getElementById("outRca").textContent = summary.rca || "—";
  document.getElementById("outProblematic").textContent =
    summary.problematicCode || "—";
  document.getElementById("outFix").textContent = summary.appliedFix || "—";
  document.getElementById("outExplanation").textContent =
    summary.explanation || "—";
  const prLink = document.getElementById("outPr");
  if (summary.prUrl) {
    prLink.href = summary.prUrl;
    prLink.textContent = summary.prUrl;
  } else {
    prLink.textContent = "Not available yet";
    prLink.removeAttribute("href");
  }
}

function startLiveStream(agentId, runUuid) {
  if (eventSource) {
    eventSource.close();
  }
  const q = new URLSearchParams({ agentId });
  if (runUuid) q.set("runUuid", runUuid);
  eventSource = new EventSource(`/api/runs/stream?${q.toString()}`);

  eventSource.addEventListener("status", (e) => {
    const data = JSON.parse(e.data);
    if (data.status) setStatus(data.status);
  });

  eventSource.addEventListener("messages", (e) => {
    const data = JSON.parse(e.data);
    renderMessages(data.messages);
  });

  eventSource.addEventListener("summary", (e) => {
    const data = JSON.parse(e.data);
    applySummary(data.summary);
  });

  eventSource.addEventListener("done", (e) => {
    const data = JSON.parse(e.data);
    if (data.status) setStatus(data.status);
    eventSource.close();
    submitBtn.disabled = false;
  });

  eventSource.onerror = () => {
    /* browser reconnects; terminal state handled by done */
  };
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearError();

  const jiraRaw = document.getElementById("jiraTicket").value.trim();
  const jiraTicketUrl = normalizeJiraUrl(jiraRaw);
  if (!JIRA_URL_RE.test(jiraTicketUrl) && !JIRA_URL_RE.test(jiraRaw)) {
    showError("Enter a valid Jira URL or issue key (e.g. TQARVIS-1234).");
    return;
  }

  const repoId = repoSelect.value;
  if (!repoId) {
    showError("Select a repository.");
    return;
  }

  const branch = branchInput.value.trim();
  if (!branch) {
    showError("Enter a branch name.");
    return;
  }

  const additionalContext = document
    .getElementById("additionalContext")
    .value.trim();

  submitBtn.disabled = true;
  runSection.classList.remove("hidden");
  summarySection.classList.add("hidden");
  chatLog.innerHTML =
    '<p class="chat-empty">Submitting to Cursor automation…</p>';
  setStatus("Submitting");

  try {
    const res = await fetch("/api/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jiraTicketUrl,
        repoId,
        branch,
        additionalContext,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || data.error || "Submit failed");
    }

    const agentId = data.backgroundComposerId || data.agentId;
    const agentUrl =
      data.agentUrl ||
      (agentId ? `https://cursor.com/agents?id=${agentId}` : "#");
    openInCursor.href = agentUrl;

    if (agentId) {
      runIdLine.classList.remove("hidden");
      runIdEl.textContent = agentId;
    }

    setStatus(data.status || "Running");
    chatLog.innerHTML =
      '<p class="chat-empty">Agent started — streaming updates…</p>';

    if (agentId) {
      startLiveStream(agentId, data.runUuid);
    } else {
      showError(
        "Automation started but no agent id was returned. Open the run in Cursor from your Automations dashboard."
      );
      submitBtn.disabled = false;
    }
  } catch (err) {
    showError(err.message);
    submitBtn.disabled = false;
  }
});

loadRepos().catch(() => {
  showError("Could not load repository list. Is the local server running?");
});
