"""
Bug 2 PR — local UI that triggers a Cursor Automation webhook and streams agent output.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
REPOS_FILE = ROOT / "repositories.json"

CURSOR_WEBHOOK_URL = os.getenv("CURSOR_AUTOMATION_WEBHOOK_URL", "").strip()
CURSOR_WEBHOOK_API_KEY = os.getenv("CURSOR_AUTOMATION_WEBHOOK_API_KEY", "").strip()
CURSOR_API_KEY = os.getenv("CURSOR_API_KEY", "").strip()
CURSOR_API_BASE = os.getenv("CURSOR_API_BASE", "https://api.cursor.com").rstrip("/")

app = FastAPI(title="Bug 2 PR", version="1.0.0")
app.mount("/static", StaticFiles(directory=PUBLIC), name="static")


class SubmitBody(BaseModel):
    jiraTicketUrl: str
    repoId: str
    branch: str
    additionalContext: str = ""
    env: str = Field(default="int", pattern="^(int|qa|stage|prod)$")


def load_repositories() -> list[dict[str, Any]]:
    with REPOS_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def repo_by_id(repo_id: str) -> dict[str, Any]:
    for r in load_repositories():
        if r["id"] == repo_id:
            return r
    raise HTTPException(status_code=400, detail=f"Unknown repository: {repo_id}")


def build_webhook_payload(body: SubmitBody, repo: dict[str, Any]) -> dict[str, Any]:
    ctx = body.additionalContext.strip()
    skill_line = (
        f"/bug-fix {body.jiraTicketUrl}"
        + (f' "{ctx}"' if ctx else "")
        + f" --repo {repo['githubUrl']}"
        + f" --branch {body.branch}"
        + f" --env {body.env}"
    )
    return {
        "source": "bug-2-pr-web",
        "jiraTicketUrl": body.jiraTicketUrl,
        "repositoryId": body.repoId,
        "repositoryGitHubUrl": repo["githubUrl"],
        "branch": body.branch,
        "env": body.env,
        "additionalContext": ctx,
        "skillInvocation": skill_line,
        "requestedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def parse_summary_from_text(text: str) -> dict[str, str]:
    """Best-effort extraction from bug_summary-style markdown in the final reply."""
    summary: dict[str, str] = {}
    if not text:
        return summary

    pr_match = re.search(r"https://github\.com/[^\s)]+/pull/\d+", text)
    if pr_match:
        summary["prUrl"] = pr_match.group(0)

    sections = {
        "rca": r"(?:##\s*Root Cause Analysis|RCA)\s*\n+([\s\S]*?)(?=\n##|\Z)",
        "problematicCode": r"(?:##\s*Problematic Code|Problematic code)\s*\n+([\s\S]*?)(?=\n##|\Z)",
        "appliedFix": r"(?:##\s*Applied Fix|Applied fix)\s*\n+([\s\S]*?)(?=\n##|\Z)",
        "explanation": r"(?:##\s*How the Fix Resolves|Explanation)\s*\n+([\s\S]*?)(?=\n##|\Z)",
    }
    for key, pattern in sections.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            summary[key] = m.group(1).strip()[:8000]

    json_block = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text)
    if json_block:
        try:
            parsed = json.loads(json_block.group(1))
            for k in ("rca", "problematicCode", "appliedFix", "explanation", "prUrl"):
                if parsed.get(k):
                    summary[k] = str(parsed[k])
        except json.JSONDecodeError:
            pass

    return summary


def normalize_conversation(raw: dict[str, Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    conv = raw.get("conversation") or raw.get("messages") or raw
    if isinstance(conv, list):
        for item in conv:
            role = (item.get("role") or item.get("type") or "assistant").lower()
            if role not in ("user", "assistant"):
                role = "assistant"
            text = item.get("text") or item.get("content") or ""
            if isinstance(text, list):
                parts = []
                for block in text:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                text = "\n".join(parts)
            if str(text).strip():
                messages.append({"role": role, "text": str(text).strip()})
    return messages


async def fetch_conversation(agent_id: str) -> tuple[list[dict[str, str]], str | None]:
    if not CURSOR_API_KEY:
        return [], None

    url = f"{CURSOR_API_BASE}/v0/agents/{agent_id}/conversation"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, auth=(CURSOR_API_KEY, ""))
        if resp.status_code == 404:
            return [], None
        if resp.status_code >= 400:
            return [], f"API {resp.status_code}: {resp.text[:200]}"
        data = resp.json()
        return normalize_conversation(data), None


async def fetch_agent_status(agent_id: str) -> str | None:
    if not CURSOR_API_KEY:
        return None
    url = f"{CURSOR_API_BASE}/v0/agents/{agent_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, auth=(CURSOR_API_KEY, ""))
        if resp.status_code >= 400:
            return None
        data = resp.json()
        return (data.get("status") or data.get("state") or "").upper() or None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(PUBLIC / "index.html")


@app.get("/styles.css")
async def styles() -> FileResponse:
    return FileResponse(PUBLIC / "styles.css")


@app.get("/app.js")
async def app_js() -> FileResponse:
    return FileResponse(PUBLIC / "app.js")


@app.get("/api/repositories")
async def api_repositories() -> list[dict[str, Any]]:
    return load_repositories()


@app.post("/api/submit")
async def api_submit(body: SubmitBody) -> dict[str, Any]:
    if not CURSOR_WEBHOOK_URL or not CURSOR_WEBHOOK_API_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "Set CURSOR_AUTOMATION_WEBHOOK_URL and CURSOR_AUTOMATION_WEBHOOK_API_KEY "
                "in bug-2-pr-web/.env (from Cursor Automations → Webhook trigger after save)."
            ),
        )

    repo = repo_by_id(body.repoId)
    payload = build_webhook_payload(body, repo)

    headers = {
        "Authorization": f"Bearer {CURSOR_WEBHOOK_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(CURSOR_WEBHOOK_URL, json=payload, headers=headers)

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Cursor webhook returned {resp.status_code}: {resp.text[:500]}",
        )

    try:
        data = resp.json()
    except json.JSONDecodeError:
        data = {"raw": resp.text}

    agent_id = data.get("backgroundComposerId") or data.get("agentId") or data.get("id")
    run_uuid = data.get("runUuid") or data.get("runId")

    return {
        "success": data.get("success", True),
        "backgroundComposerId": agent_id,
        "runUuid": run_uuid,
        "agentUrl": f"https://cursor.com/agents?id={agent_id}" if agent_id else None,
        "status": "RUNNING",
        "payloadSent": payload,
    }


async def sse_stream(agent_id: str) -> AsyncGenerator[bytes, None]:
    last_msg_count = 0
    last_text_hash = ""
    terminal = {"FINISHED", "COMPLETED", "DONE", "ERROR", "FAILED", "CANCELLED"}
    max_ticks = 600  # ~30 min at 3s

    yield f"event: status\ndata: {json.dumps({'status': 'RUNNING'})}\n\n".encode()

    if not CURSOR_API_KEY:
        yield (
            f"event: messages\ndata: {json.dumps({'messages': [{'role': 'assistant', 'text': 'Add CURSOR_API_KEY to .env to stream conversation here. Use Open full run in Cursor for live transcript.'}]})}\n\n"
        ).encode()
        yield f"event: done\ndata: {json.dumps({'status': 'CONFIGURE_API_KEY'})}\n\n".encode()
        return

    for _ in range(max_ticks):
        messages, err = await fetch_conversation(agent_id)
        if err and not messages:
            yield f"event: status\ndata: {json.dumps({'status': 'WAITING', 'detail': err})}\n\n".encode()
        elif messages:
            text_blob = json.dumps(messages)
            if text_blob != last_text_hash:
                last_text_hash = text_blob
                yield f"event: messages\ndata: {json.dumps({'messages': messages})}\n\n".encode()

            if len(messages) != last_msg_count:
                last_msg_count = len(messages)
                assistant_text = "\n\n".join(
                    m["text"] for m in messages if m["role"] == "assistant"
                )
                summary = parse_summary_from_text(assistant_text)
                if summary:
                    yield f"event: summary\ndata: {json.dumps({'summary': summary})}\n\n".encode()

        status = await fetch_agent_status(agent_id)
        if status:
            yield f"event: status\ndata: {json.dumps({'status': status})}\n\n".encode()
            if status in terminal:
                if messages:
                    assistant_text = "\n\n".join(
                        m["text"] for m in messages if m["role"] == "assistant"
                    )
                    summary = parse_summary_from_text(assistant_text)
                    if summary:
                        yield f"event: summary\ndata: {json.dumps({'summary': summary})}\n\n".encode()
                yield f"event: done\ndata: {json.dumps({'status': status})}\n\n".encode()
                return

        await asyncio.sleep(3)

    yield f"event: done\ndata: {json.dumps({'status': 'TIMEOUT'})}\n\n".encode()


@app.get("/api/runs/stream")
async def api_runs_stream(agentId: str, runUuid: str | None = None) -> StreamingResponse:
    if not agentId:
        raise HTTPException(status_code=400, detail="agentId is required")
    return StreamingResponse(
        sse_stream(agentId),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/api/cursor-status-webhook")
async def cursor_status_webhook(request: Request) -> dict[str, str]:
    """Optional: register this URL on agent runs for FINISHED + prUrl (requires public tunnel)."""
    body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {"raw": body.decode("utf-8", errors="replace")}
    out = ROOT / "data" / "last-status-webhook.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"ok": "true"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8765"))
    uvicorn.run("server:app", host="127.0.0.1", port=port, reload=True)
