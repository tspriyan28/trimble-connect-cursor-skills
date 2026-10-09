# trimble-connect-cursor-skills

Shared [Cursor Agent Skills](https://cursor.com/docs) for Trimble Connect engineering.

**Repository:** https://github.com/tspriyan28/trimble-connect-cursor-skills

## Clone

```powershell
git clone https://github.com/tspriyan28/trimble-connect-cursor-skills.git C:\Work\trimble-connect-cursor-skills
```

Open this folder in Cursor (alone or as a workspace root) so skills under `.cursor/skills/` are available. Do **not** copy skills into `%USERPROFILE%\.cursor\skills\` — use this repo as the single source of truth.

## Layout

```
.cursor/skills/
└── bug-fix/              # /bug-fix — JIRA → repro → CloudWatch → fix → PR
bug-fix/                  # Same skill (legacy path; kept in sync with .cursor/skills/bug-fix)
bug-2-pr-web/             # Local UI → Cursor Automation webhook
```

## Skills

| Skill | Invoke | Description |
|-------|--------|-------------|
| `bug-fix` | `/bug-fix {jiraUrl} --repo {path} --branch {branch} [--env int]` | End-to-end Connect bug-fix workflow (see `.cursor/skills/bug-fix/SKILL.md`) |

## Bug 2 PR (webhook UI)

Local form that triggers a **Cursor Automation** webhook and streams cloud agent output:

1. Follow [bug-2-pr-web/SETUP.md](bug-2-pr-web/SETUP.md) to create the automation on cursor.com.
2. Configure `bug-2-pr-web/.env` from `.env.example`.
3. Run `python server.py` and open http://127.0.0.1:8765.

## Cloud Agent / multi-repo environments

Add this repository to your **Cloud Agent environment** so automations can read `.cursor/skills/bug-fix/SKILL.md` without a per-user local skill copy.
