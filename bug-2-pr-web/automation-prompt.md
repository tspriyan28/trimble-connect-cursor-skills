# Cursor Automation prompt — Bug 2 PR (paste into Automations editor)

Use this as the **Instructions** body for a webhook-triggered automation that mirrors `/bug-fix` in local chat.

---

You were started by the **Bug 2 PR** webhook. The HTTP POST body is your source of truth. Read every field before acting.

## Webhook payload (expected JSON)

- `jiraTicketUrl` — full Jira URL
- `repositoryGitHubUrl` — GitHub repo to fix
- `branch` — base branch for the fix PR (checkout / PR target)
- `env` — `int` | `qa` | `stage` | `prod` (default `int`)
- `additionalContext` — optional free text
- `skillInvocation` — equivalent `/bug-fix ...` line for logging

If any required field is missing, stop and reply with what is missing. Do not guess.

## Skill to follow

Execute the **bug-fix** skill from this repository:

`trimble-connect-cursor-skills/.cursor/skills/bug-fix/SKILL.md` (this skills repo in your Cloud environment)

Follow every step in order (Jira → repro → CloudWatch via AWS MCP → code fix → tests → PR → summary).

**Cloud agent adaptations:**

1. Use `repositoryGitHubUrl` and `branch` from the payload instead of local `C:\Work\...` paths.
2. For Jira, use the connected Jira MCP (@[MCP: Jira] or your team’s Jira server name).
3. For CloudWatch, use the team AWS MCP for stage/prod accounts per the skill’s profile table. Run AWS auth if tools return credential errors.
4. For TCWEB bearer token: use **Computer use** (browser) against the TCWEB URL for `env`, or document in the summary if login blocks automation.
5. Use **Pull request creation** (enabled) and `/raise-pr` instructions from the skill’s Step 13 via GitHub tools.

## Repository selection

Work only in the repository matching `repositoryGitHubUrl`. Do not modify other repos in the environment.

## Final reply format (required)

End with a markdown bug summary using the template sections:

- Root Cause Analysis
- Problematic Code (paths + snippets)
- Applied Fix
- How the Fix Resolves the Bug
- PR Details (branch + **PR URL**)

Also include a JSON block the UI can parse:

```json
{
  "rca": "...",
  "problematicCode": "...",
  "appliedFix": "...",
  "explanation": "...",
  "prUrl": "https://github.com/..."
}
```
