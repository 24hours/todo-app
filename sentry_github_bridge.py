"""Sentry -> GitHub issue bridge.

A small FastAPI receiver that turns Sentry issue-alerts into GitHub issues.

Flow:
    Sentry alert rule ("New error -> create GitHub issue", first-seen only)
      -> internal integration "GitHub Issue Bridge" POSTs here
        -> we verify the signature and run `gh issue create`

Because the alert rule fires only on the *first* occurrence of a distinct
Sentry issue, you get one GitHub issue per new error type. As a second guard
against duplicate webhook deliveries, we embed a hidden marker in each issue
body and skip creation if a matching open/closed issue already exists.

Run:
    export SENTRY_WEBHOOK_SECRET=...        # internal integration client secret
    export GITHUB_REPO=24hours/todo-app     # optional, this is the default
    uv run sentry_github_bridge.py          # listens on :9902

Sentry must be able to reach this receiver. For local dev, expose :9902 with a
tunnel (e.g. `cloudflared tunnel --url http://localhost:9902` or `ngrok http
9902`) and set the internal integration's Webhook URL to <public-url>/webhook.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, Response

HERE = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    """Minimal .env loader so the secret can live in a gitignored file."""
    env_path = HERE / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

WEBHOOK_SECRET = os.environ.get("SENTRY_WEBHOOK_SECRET", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "24hours/todo-app")
PORT = int(os.environ.get("PORT", "9902"))

app = FastAPI(title="Sentry -> GitHub bridge")


def verify_signature(body: bytes, signature: str | None) -> bool:
    """Validate Sentry's HMAC-SHA256 signature over the raw request body."""
    if not WEBHOOK_SECRET:
        # No secret configured -> fail closed; we never trust unsigned calls.
        return False
    if not signature:
        return False
    expected = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def issue_already_filed(marker: str) -> bool:
    """Return True if a GitHub issue carrying this marker already exists."""
    try:
        out = subprocess.run(
            [
                "gh", "issue", "list",
                "--repo", GITHUB_REPO,
                "--state", "all",
                "--search", marker,
                "--json", "number",
            ],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return bool(json.loads(out.stdout or "[]"))
    except (subprocess.SubprocessError, json.JSONDecodeError):
        # On any lookup failure, don't block creation.
        return False


def create_github_issue(event: dict, rule_name: str) -> None:
    """Create a GitHub issue describing the Sentry event (idempotently)."""
    issue_id = str(event.get("issue_id") or event.get("groupID") or "")
    title = event.get("title") or event.get("message") or "Sentry error"
    web_url = event.get("web_url") or event.get("url") or ""
    level = event.get("level", "error")
    culprit = event.get("culprit", "")
    project = event.get("project", "")
    environment = event.get("environment", "")

    marker = f"sentry-issue-id:{issue_id}" if issue_id else ""
    if marker and issue_already_filed(marker):
        print(f"[bridge] issue {issue_id} already filed; skipping")
        return

    body_lines = [
        f"**Sentry detected a new `{level}` error.**",
        "",
        f"- **Title:** {title}",
    ]
    if culprit:
        body_lines.append(f"- **Culprit:** `{culprit}`")
    if project:
        body_lines.append(f"- **Project:** {project}")
    if environment:
        body_lines.append(f"- **Environment:** {environment}")
    if web_url:
        body_lines += ["", f"[View in Sentry]({web_url})"]
    body_lines += ["", f"_Alert rule: {rule_name}_"]
    if marker:
        body_lines += ["", f"<!-- {marker} -->"]
    body = "\n".join(body_lines)

    result = subprocess.run(
        [
            "gh", "issue", "create",
            "--repo", GITHUB_REPO,
            "--title", f"[Sentry] {title}",
            "--body", body,
            "--label", "bug",
        ],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode == 0:
        print(f"[bridge] created GitHub issue: {result.stdout.strip()}")
    else:
        # --label fails if the label doesn't exist; retry without it.
        retry = subprocess.run(
            [
                "gh", "issue", "create",
                "--repo", GITHUB_REPO,
                "--title", f"[Sentry] {title}",
                "--body", body,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if retry.returncode == 0:
            print(f"[bridge] created GitHub issue: {retry.stdout.strip()}")
        else:
            print(f"[bridge] gh issue create failed: {retry.stderr.strip()}")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "repo": GITHUB_REPO, "secret_configured": bool(WEBHOOK_SECRET)}


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    body = await request.body()
    signature = request.headers.get("sentry-hook-signature")
    if not verify_signature(body, signature):
        return Response(status_code=401, content="invalid signature")

    payload = json.loads(body or b"{}")
    resource = request.headers.get("sentry-hook-resource", "")

    # We only act on triggered issue-alerts. Sentry also sends installation and
    # other lifecycle webhooks; acknowledge those with 200 and ignore them.
    if resource == "event_alert" and payload.get("action") == "triggered":
        data = payload.get("data", {})
        event = data.get("event", {})
        rule_name = data.get("triggered_rule", "Sentry alert")
        create_github_issue(event, rule_name)

    return Response(status_code=200, content="ok")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
