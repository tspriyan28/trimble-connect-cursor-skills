# Bug 2 PR — setup guide (Cursor Automation webhook + localhost UI)

This guide wires the **Bug 2 PR** form (`http://127.0.0.1:8765`) to a **Cursor Automation** with a **webhook trigger**, so Submit behaves like running `/bug-fix` in Cursor chat, with **live agent output** in the browser.

---

## Architecture

```text
Browser (Bug 2 PR UI)
    → POST /api/submit (local FastAPI)
    → POST Cursor Automation webhook URL (cloud)
    → Cloud Agent runs bug-fix skill + MCP tools
    ← backgroundComposerId + runUuid
    → SSE /api/runs/stream polls Cursor API conversation
    → RCA / code / fix / PR shown in UI
```

---

## Part A — Prerequisites (one-time)

### A1. Cursor plan and Cloud Agents

- Team or personal account with **Cloud Agents / Automations** enabled ([Automations docs](https://cursor.com/docs/cloud-agent/automations)).
- Billing: automations run as **cloud agent usage** (Max Mode).

### A2. GitHub

- Connect GitHub in **Cursor Dashboard** (same org as your Connect repos).
- Confirm the automation identity can open PRs (Run as **Me** → PRs as your user; **Service account** → PRs as `cursor`).

### A3. Bug-fix skill in the repo

The cloud agent must see the skill in the **checked-out repository**:

- Clone https://github.com/tspriyan28/trimble-connect-cursor-skills and add it to your **Cloud Agent multi-repo environment** (skills live at `.cursor/skills/bug-fix/`).
- Service repos listed in `repositories.json` should also be in the same environment for code changes.

### A4. MCP servers (match local `/bug-fix`)

In **Dashboard → Plugins & MCPs** (team) and/or **cursor.com/agents → MCP**, connect and authenticate:

| Capability | Purpose in bug-fix |
|------------|-------------------|
| **Jira** (E-Tools / user-jira / team HTTP MCP) | Step 2 — ticket details |
| **AWS MCP** (connect-stage / connect-prod) | Step 7 — CloudWatch via `aws___run_script` |
| **GitHub** | Clone, branch, commit, PR |
| **Playwright / Computer use** | Step 3 — TCWEB token (browser) |

Authenticate each MCP **before** saving the automation (OAuth in chat or dashboard). Unauthenticated MCP rows block saving prefilled automations.

**Important:** Cloud agents do **not** use your laptop’s `C:\Work\...` paths or local `mcp.json`. Use **team/dashboard MCP** and **GitHub URLs** from the webhook payload.

### A5. Cloud Agent environment

1. Open **Cursor Dashboard → Cloud Agents → Environments**.
2. Create or edit an environment that includes your Connect repos (or a **multi-repo** environment with all entries from `repositories.json`).
3. Run a test build so dependencies install successfully.
4. Optional: enable **Computer use** (on by default for automations) for TCWEB login flows.

### A6. AWS SSO

Cloud AWS MCP uses team/service credentials, not your local `aws sso login`. Ensure the AWS MCP profile in the skill matches what the team MCP exposes. Document in `additionalContext` if a specific account/region is required.

---

## Part B — Create the Cursor Automation

### B1. Open Automations

1. Go to [cursor.com/automations](https://cursor.com/automations) (or **Agents Window → Automations**).
2. Click **New automation**.

### B2. Trigger — Webhook

1. Add trigger: **Webhook** / **Incoming HTTP webhook**.
2. **Save the automation once** (draft → save). Cursor generates:
   - **Webhook URL** (POST endpoint)
   - **API key** (Bearer token)
3. Copy both into `bug-2-pr-web/.env` (see Part C).  
   - Paste the API key **without a trailing newline** (avoids HTTP 400 parse errors).

### B3. Repository / environment

1. **Repositories:** choose **Multi-repo environment** (recommended) with all Connect services, **or** single repo if you only fix one codebase.
2. Set default branch in the environment to match your usual target (e.g. `develop` for platform).
3. The webhook payload’s `repositoryGitHubUrl` and `branch` tell the agent which repo to use for *this* run.

### B4. Tools (enable these)

| Tool | Why |
|------|-----|
| **Pull request creation** | Default ON — Step 12–13 |
| **MCP server** | Jira + AWS (+ GitHub if not implicit) |
| **Computer use** | TCWEB token / headed browser |
| **Memories** | Optional OFF for untrusted webhook input |

Add each MCP action with the **exact dashboard server name** (from MCP settings), authenticated.

### B5. Model

- Use a capable model (e.g. Composer family). Automations use **max context** (no small-context toggle).

### B6. Instructions (prompt)

1. Open `bug-2-pr-web/automation-prompt.md`.
2. Paste the full prompt into the automation **Instructions** field.
3. Adjust MCP @-mentions to match your team’s server names (e.g. `@[MCP: E-Tools MCP]`).

The prompt explicitly tells the agent to:

- Parse the webhook JSON (`jiraTicketUrl`, `branch`, etc.)
- Run `.cursor/skills/bug-fix/SKILL.md`
- Return markdown + JSON summary with `prUrl`

### B7. Share / identity

| Setting | Recommendation |
|---------|----------------|
| **Run as** | **Me** while testing (your GitHub + MCP OAuth) |
| **Access** | Private until stable |
| **Service account** | Later for team-wide runs; **regenerate webhook API key** after switching |

### B8. Activate

- Set automation to **Active**.
- Send a test POST (Part D) before using the UI.

---

## Part C — Local Bug 2 PR web app

### C1. Install

```powershell
cd C:\Work\trimble-connect-cursor-skills\bug-2-pr-web
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

### C2. Configure `.env`

```env
CURSOR_AUTOMATION_WEBHOOK_URL=<from automation webhook settings>
CURSOR_AUTOMATION_WEBHOOK_API_KEY=<bearer token, no trailing newline>
CURSOR_API_KEY=<Dashboard → API Keys — for live transcript in UI>
```

### C3. Fix repository URLs

Edit `repositories.json` so `githubUrl` matches your real `git remote` URLs (org/repo names).

### C4. Run

```powershell
python server.py
```

Open **http://127.0.0.1:8765**

### C5. Submit flow

1. Fill Jira, repository, branch, optional context.
2. **Submit** → local server POSTs JSON to Cursor webhook.
3. **Agent run** panel streams messages (if `CURSOR_API_KEY` is set) and shows summary when the agent emits the JSON block.
4. **Open full run in Cursor** opens `https://cursor.com/agents?id=<backgroundComposerId>` for the full cloud chat UI.

---

## Part D — Verify webhook (curl)

Replace URL and key from the automation settings:

```powershell
curl -X POST "%CURSOR_AUTOMATION_WEBHOOK_URL%" `
  -H "Authorization: Bearer %CURSOR_AUTOMATION_WEBHOOK_API_KEY%" `
  -H "Content-Type: application/json" `
  -d "{\"jiraTicketUrl\":\"https://jira.trimble.tools/browse/TQARVIS-1\",\"repositoryGitHubUrl\":\"https://github.com/YOUR_ORG/trimble-connect-platform\",\"branch\":\"develop\",\"env\":\"int\",\"additionalContext\":\"test\"}"
```

Expected: HTTP **200** and JSON containing `backgroundComposerId` and `runUuid`.

If you get **400** with `Content-Length` in the body, trim newlines from the API key secret.

---

## Part E — Live output vs “same as IDE chat”

| IDE `/bug-fix` | Webhook automation |
|----------------|-------------------|
| Local MCP (`user-aws-mcp-connect-stage`, Playwright on laptop) | Dashboard / team MCP + cloud VM |
| Local repo path `--repo C:\Work\...` | `repositoryGitHubUrl` + cloud checkout |
| Chat in Cursor IDE | Cloud agent; UI streams via API + link to cursor.com/agents |
| You approve SSO / TCWEB in IDE | Computer use or manual login in cloud VM |

To get **closest parity**:

1. Multi-repo environment with all services and working install scripts.
2. All MCPs authenticated for **Run as** identity.
3. Skill committed on the branch the environment checks out.
4. Optional: add `env` in webhook payload (server already sends `int` by default; extend UI later if needed).

---

## Part F — Optional: status webhooks (PR URL on finish)

Cursor can POST to **your** URL when a run finishes ([Agent webhooks](https://cursor.com/docs/cloud-agent/api/webhooks)). For localhost you need a tunnel (ngrok, Cloudflare Tunnel):

1. Expose `https://<tunnel>/api/cursor-status-webhook`
2. Register that URL when creating agents (API) or per team policy
3. `last-status-webhook.json` is written under `bug-2-pr-web/data/`

Most teams rely on **conversation polling** + **Open in Cursor** instead.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| 503 on Submit | Set webhook URL + API key in `.env` |
| Webhook 400 | Remove trailing newline from Bearer token |
| No live text in UI | Set `CURSOR_API_KEY`; otherwise use “Open full run in Cursor” |
| Agent ignores Jira/repo | Strengthen prompt: “read webhook payload fields X, Y, Z” |
| No CloudWatch logs | AWS MCP auth + correct env namespace in skill |
| No PR | Enable PR creation; check GitHub permissions for Run-as identity |
| Wrong repo | Use multi-repo env + `repositoryGitHubUrl` in payload |

---

## Files in this folder

| File | Purpose |
|------|---------|
| `public/` | Bug 2 PR UI (matches design mockup) |
| `server.py` | Webhook proxy + SSE stream |
| `repositories.json` | Repo dropdown |
| `automation-prompt.md` | Paste into Cursor Automation |
| `SETUP.md` | This guide |
