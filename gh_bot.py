"""Interactive GitHub issue bot, built on the Claude Agent SDK.

When someone @-mentions the bot in a GitHub issue (or in a comment on one), the
bot runs the same agent that powers `bot.py` and replies with a comment. Each
further @-mention on the same issue *resumes the same agent session*, so the
conversation stays interactive and the bot remembers the thread. The agent posts
its reply itself via the GitHub MCP server (run over Docker stdio).

Delivery is real-time webhooks: `gh webhook forward` (the `cli/gh-webhook`
extension) tunnels GitHub `issues` and `issue_comment` events to this FastAPI
receiver — no public URL needed. See the README for the exact run steps.

    uv run gh_bot.py            # this receiver on :9901
    gh webhook forward --repo=<owner/repo> \
        --events=issues,issue_comment --url=http://localhost:9901/webhook
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

from fastapi import FastAPI, Request

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ResultMessage,
    SystemMessage,
)

# Reuse bot.py's agent config (full coding agent, cwd scoped to ./app) and its
# terminal colors. Importing bot is side-effect-free — main() runs under __main__.
from bot import SYSTEM_PROMPT, WORKDIR, DIM, BOLD, BLUE, GREEN, YELLOW, RESET

# Stream the agent's text + tool calls to the terminal so you can watch what it's
# doing. Set GH_BOT_VERBOSE=0 to quiet it down to just lifecycle lines.
VERBOSE = os.environ.get("GH_BOT_VERBOSE", "1").lower() not in ("0", "false", "no")

HERE = Path(__file__).resolve().parent
SESSIONS_FILE = HERE / ".gh_bot_sessions.json"

# Hidden marker the agent is instructed to append to every reply. We skip any
# comment that contains it, so the bot never answers its own replies. This is
# account-agnostic — it works even though `gh` is authed as the same login a
# human comments from.
MARKER = "<!-- claude-bot -->"


def _default_mention() -> str:
    """The @-handle that triggers the bot, default = the authed gh login."""
    if env := os.environ.get("BOT_MENTION"):
        return env if env.startswith("@") else f"@{env}"
    try:
        login = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if login:
            return f"@{login}"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return "@bot"


MENTION = _default_mention()

# An issue carrying this label triggers the autonomous fix → test → PR → reply
# flow (no @-mention needed). Override with BOT_BUG_LABEL.
BUG_LABEL = os.environ.get("BOT_BUG_LABEL", "bug").lower()


# --- agent config (GitHub MCP for posting + Claude in Chrome for the browser) --

# The agent posts its reply through the official GitHub MCP server (Docker stdio),
# authenticated with the local `gh` token. `add_issue_comment` is the only GitHub
# write tool we pre-approve; the repo itself is read from the local ./app clone.
GH_COMMENT_TOOL = "mcp__github__add_issue_comment"

# Browser automation via "Claude in Chrome": we pass --chrome through to the
# underlying Claude Code CLI (extra_args below). That drives a real Chrome with the
# Claude extension installed, so the agent can load the running app, reproduce UI
# bugs, and verify fixes. `mcp__claude-in-chrome` pre-approves the whole tool set
# (navigate / computer / read_page / screenshot / ...).
#
# Requirements (see README): Google Chrome/Edge + the Claude in Chrome extension,
# the native messaging host installed (Claude Code does this on first `--chrome`
# use), and a direct Anthropic plan (Pro/Max/Team/Enterprise — not Bedrock/Vertex).
# Browser actions run in a VISIBLE window and pause for the human on login/CAPTCHA.
BROWSER_MCP = "mcp__claude-in-chrome"


def _gh_token() -> str:
    return subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=True
    ).stdout.strip()


MCP_SERVERS = {
    "github": {
        "command": "docker",
        "args": [
            "run", "-i", "--rm",
            "-e", "GITHUB_PERSONAL_ACCESS_TOKEN",
            "ghcr.io/github/github-mcp-server",
        ],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": _gh_token()},
    },
}


def build_options(resume: str | None) -> ClaudeAgentOptions:
    """Full coding agent (scoped to ./app) + GitHub comment tool + Claude in Chrome."""
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        cwd=str(WORKDIR),
        allowed_tools=[
            "Read", "Write", "Edit", "Bash", "Glob", "Grep",
            GH_COMMENT_TOOL, BROWSER_MCP,
        ],
        permission_mode="acceptEdits",
        mcp_servers=MCP_SERVERS,
        # Enable the Claude in Chrome browser integration in the underlying CLI.
        extra_args={"chrome": None},
        resume=resume,
    )


app = FastAPI(title="GitHub issue bot")

# Serialize agent runs across concurrent webhook events: two runs editing files
# in ./app at once would race. A single lock keeps them sequential.
_run_lock = asyncio.Lock()

# Issues already taken through the bug-fix flow this session — guards against the
# near-simultaneous opened + labeled events that can both carry the "bug" label,
# which would otherwise open two PRs.
_handled_bugs: set[str] = set()


# --- session store -------------------------------------------------------

def load_sessions() -> dict[str, str]:
    if SESSIONS_FILE.exists():
        try:
            return json.loads(SESSIONS_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_session(key: str, session_id: str) -> None:
    sessions = load_sessions()
    sessions[key] = session_id
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2))


# --- terminal output -----------------------------------------------------

def log(tag: str, msg: str) -> None:
    """A lifecycle line, always printed (flushed for live/redirected output)."""
    print(f"{BOLD}{GREEN}[{tag}]{RESET} {msg}", flush=True)


def render_stream(message, tag: str) -> None:
    """Print one streamed agent message live: its text and each tool call."""
    if not VERBOSE:
        return
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                text = block.text.strip()
                if text:
                    print(f"{BOLD}{BLUE}[{tag}] bot{RESET} {text}", flush=True)
            elif isinstance(block, ToolUseBlock):
                # MCP tools come through as mcp__<server>__<tool>; shorten for display.
                name = block.name.replace("mcp__github__", "github:").replace("mcp__claude-in-chrome__", "chrome:")
                preview = ", ".join(f"{k}={v!r}"[:60] for k, v in block.input.items())
                print(f"  {YELLOW}[{tag}] ⚙ {name}{RESET}({preview})", flush=True)
    elif isinstance(message, ResultMessage):
        secs = message.duration_ms / 1000
        cost = f", ${message.total_cost_usd:.4f}" if message.total_cost_usd else ""
        status = "error" if message.is_error else "done"
        print(f"  {DIM}[{tag}] {status} — {message.num_turns} turns, {secs:.1f}s{cost}{RESET}", flush=True)


# --- agent ---------------------------------------------------------------

async def run_agent(prompt: str, resume: str | None, tag: str = "agent") -> tuple[str, str | None]:
    """Run one agent turn to completion. Returns (answer_text, session_id).

    Streams the agent's text and tool calls to the terminal as they happen.
    Resuming with a session_id loads that conversation's history from disk, so
    follow-up turns on the same issue keep full context.
    """
    opts = build_options(resume)
    session_id = resume
    final = ""
    fallback_chunks: list[str] = []
    async for message in query(prompt=prompt, options=opts):
        render_stream(message, tag)
        if isinstance(message, SystemMessage) and message.subtype == "init":
            session_id = message.data.get("session_id") or session_id
            if VERBOSE:
                model = message.data.get("model", "?")
                log(tag, f"{DIM}session {session_id} · model {model}{RESET}")
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    fallback_chunks.append(block.text)
        elif isinstance(message, ResultMessage):
            session_id = message.session_id or session_id
            if not message.is_error and message.result:
                final = message.result
    answer = final or "\n".join(fallback_chunks).strip()
    return answer or "(the agent finished without producing a reply)", session_id


# --- github --------------------------------------------------------------

def image_comment_howto(owner: str, name: str, number: int) -> str:
    """Instructions for posting a comment that includes an image. GitHub's API
    can't upload attachments, so an image comment has to go through the web UI
    via Claude-in-Chrome instead of the `add_issue_comment` tool."""
    return (
        " If your reply should include an image (e.g. a before/after screenshot), "
        f"do NOT use {GH_COMMENT_TOOL} for that comment — GitHub's API cannot attach "
        "images. Instead post it through the web UI with the Claude-in-Chrome tools: "
        "save the image as a local PNG file, navigate to "
        f"https://github.com/{owner}/{name}/issues/{number}, click into the comment "
        "box, attach the file (mcp__claude-in-chrome__file_upload, or drag-drop) and "
        "WAIT for GitHub to finish uploading and insert its ![](...) markdown, then "
        f"type your text — still include the marker `{MARKER}` and no @-mention — and "
        "click 'Comment'. (If the browser isn't logged into GitHub it will pause for "
        "the human.) Use this only when there's an image to show; text-only replies "
        f"should still go through {GH_COMMENT_TOOL}."
    )


async def handle(repo: str, number: int, prompt: str) -> None:
    """Background worker: run the agent for one issue. The agent posts its own
    reply by calling the GitHub MCP `add_issue_comment` tool."""
    key = f"{repo}#{number}"
    owner, _, name = repo.partition("/")
    resume = load_sessions().get(key)
    async with _run_lock:
        try:
            log(key, f"running agent ({'resuming' if resume else 'new'} session)…")
            preamble = (
                f"You are the assistant for GitHub issue #{number} in {repo}. "
                "Investigate using the local checkout in ./app (Read/Grep/Bash); "
                "only modify files if the request clearly asks you to. "
                "If a question is about runtime/UI behavior, you may start the app "
                "(see ./app/Makefile or README) and use the Claude-in-Chrome browser "
                "tools (mcp__claude-in-chrome__*, e.g. navigate to "
                "http://localhost:5173) to check it. "
                "When done, post your answer as a GitHub comment by calling the "
                f"`{GH_COMMENT_TOOL}` tool with owner=\"{owner}\", repo=\"{name}\", "
                f"issue_number={number}. Post exactly ONE comment. "
                f"End the comment body with the exact marker `{MARKER}` on its own "
                "line, and do NOT @-mention anyone (both prevent reply loops). "
                "Keep it concise; markdown is fine."
                + image_comment_howto(owner, name, number)
            )
            answer, session_id = await run_agent(f"{preamble}\n\n---\n\n{prompt}", resume, tag=key)
            if session_id:
                save_session(key, session_id)
            log(key, f"posted reply ✓ {DIM}(session {session_id}){RESET}")
        except Exception as e:  # noqa: BLE001 — keep the receiver alive
            log(key, f"{YELLOW}error: {e!r}{RESET}")


async def handle_bug(repo: str, number: int, prompt: str) -> None:
    """Background worker for issues labeled `bug`: the agent fixes the bug in the
    local ./app checkout, runs the tests, opens a PR, then replies to the issue."""
    key = f"{repo}#{number}"
    owner, _, name = repo.partition("/")
    branch = f"fix/issue-{number}"
    async with _run_lock:
        try:
            log(key, f"{YELLOW}bug-fix flow{RESET}: fix → test → PR → reply")
            preamble = (
                f"You are an autonomous bug-fix agent for GitHub issue #{number} in "
                f"{repo}, which is labeled \"{BUG_LABEL}\". The repository is checked "
                "out locally at your working directory (./app). Work through these "
                "steps with the Read/Grep/Edit/Bash tools:\n"
                "1. Understand and reproduce the bug from the issue text below. For a "
                "UI bug you can run the app (see ./app/Makefile / README) and use the "
                "Claude-in-Chrome browser tools (mcp__claude-in-chrome__*, e.g. "
                "navigate to http://localhost:5173 — the frontend dev server) to "
                "reproduce it.\n"
                "2. Start from a clean, up-to-date base: `git fetch origin`, then "
                f"`git checkout -B {branch} origin/HEAD` (the default branch). Make a "
                "minimal fix — change only what the bug requires.\n"
                "3. Verify the fix: run the test suite — backend: "
                "`cd backend && pytest -q` (use ./backend/.venv if it exists); "
                "frontend: `cd frontend && npm test` — and, for a UI bug, re-check in "
                "the browser that the original symptom is gone.\n"
                f"4. Only if tests pass: commit, `git push -u origin {branch}`, and open "
                "a PR against the default branch with `gh pr create` — the PR body MUST "
                f"include \"Fixes #{number}\". Capture the PR URL it prints.\n"
                "5. Post exactly ONE comment on the issue by calling the "
                f"`{GH_COMMENT_TOOL}` tool (owner=\"{owner}\", repo=\"{name}\", "
                f"issue_number={number}): summarize the root cause and fix and link the "
                "PR. For a UI fix, a before/after screenshot is valuable — see the image "
                "instructions below. If you could NOT fix it or tests fail, do NOT open a "
                "PR — instead comment explaining what you found and what's blocking.\n"
                f"End the comment body with the exact marker `{MARKER}` on its own line, "
                "and do NOT @-mention anyone. Be concise; markdown is fine."
                + image_comment_howto(owner, name, number)
            )
            answer, session_id = await run_agent(f"{preamble}\n\n---\n\n{prompt}", None, tag=key)
            if session_id:
                save_session(key, session_id)
            log(key, f"bug-fix flow done ✓ {DIM}(session {session_id}){RESET}")
        except Exception as e:  # noqa: BLE001 — keep the receiver alive
            log(key, f"{YELLOW}error in bug-fix flow: {e!r}{RESET}")


# --- webhook -------------------------------------------------------------

@app.post("/webhook")
async def webhook(request: Request):
    event = request.headers.get("X-GitHub-Event", "")
    payload = await request.json()
    action = payload.get("action")
    repo = payload.get("repository", {}).get("full_name")

    # bug_now = the "bug" label is present at open or was just added.
    bug_now = False
    if event == "issues" and action in ("opened", "labeled"):
        issue = payload.get("issue", {})
        number = issue.get("number")
        text = f"{issue.get('title', '')}\n\n{issue.get('body') or ''}"
        labels = {l.get("name", "").lower() for l in issue.get("labels", [])}
        added = payload.get("label", {}).get("name", "").lower()
        bug_now = BUG_LABEL in labels or added == BUG_LABEL
    elif event == "issue_comment" and action == "created":
        number = payload.get("issue", {}).get("number")
        text = payload.get("comment", {}).get("body") or ""
    else:
        return {"ok": True, "skipped": "event/action not handled"}

    if not repo or number is None:
        return {"ok": True, "skipped": "missing repo/number"}
    key = f"{repo}#{number}"

    # Bug-fix flow takes precedence and needs no @-mention. Dedupe so the
    # opened+labeled pair for one issue doesn't open two PRs.
    if bug_now:
        if key in _handled_bugs:
            return {"ok": True, "skipped": "bug already handled this session"}
        _handled_bugs.add(key)
        log(key, f"{event}/{action} labeled '{BUG_LABEL}' → bug-fix queued")
        asyncio.create_task(handle_bug(repo, number, text))
        return {"ok": True, "queued": f"{key} (bug-fix)"}

    # Otherwise: the @-mention Q&A flow.
    if MARKER in text:
        if VERBOSE:
            log(key, f"{DIM}skip: own comment (marker present){RESET}")
        return {"ok": True, "skipped": "own comment (marker present)"}
    if MENTION.lower() not in text.lower():
        if VERBOSE:
            log(key, f"{DIM}skip: {MENTION} not mentioned{RESET}")
        return {"ok": True, "skipped": f"not mentioned ({MENTION})"}

    # Return 200 right away; do the (possibly long) agent run in the background
    # so gh webhook forward never times out waiting on us.
    log(key, f"{event}/{action} mentions {MENTION} → queued")
    asyncio.create_task(handle(repo, number, text))
    return {"ok": True, "queued": f"{repo}#{number}"}


@app.get("/health")
async def health():
    return {"ok": True, "mention": MENTION}


if __name__ == "__main__":
    import uvicorn

    print(f"[gh-bot] listening on :9901, trigger handle = {MENTION}")
    uvicorn.run(app, host="127.0.0.1", port=9901)
