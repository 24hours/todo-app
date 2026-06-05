# coding-bot

A tiny coding agent in the spirit of Claude Code, built with [`uv`](https://docs.astral.sh/uv/)
and the [**Claude Agent SDK**](https://code.claude.com/docs/en/agent-sdk/overview)
— the same agent loop, built-in tools, permissions, and context management that
power Claude Code, as a library.

## How it works

The Agent SDK does the heavy lifting: you stream messages out of `query()` and
print them. There's **no tool loop and no tool implementations** — the SDK runs
Claude Code's loop and its built-in tools (`Read`, `Write`, `Edit`, `Bash`,
`Glob`, `Grep`, ...) inside your process.

```python
async for message in query(
    prompt="Find and fix the bug in auth.py",
    options=ClaudeAgentOptions(allowed_tools=["Read", "Edit", "Bash"]),
):
    print(message)   # Claude reads the file, finds the bug, edits it
```

`bot.py` (~120 lines) wraps that in:
- a streaming printer that shows the agent's text and each tool call,
- a REPL that resumes the same session each turn so context persists,
- a one-shot mode for running a single task from the command line.

## Setup

The SDK needs the Claude Code CLI on your PATH (`npm install -g @anthropic-ai/claude-code`)
and authentication — either an API key or an existing Claude Code login.

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or use your existing `claude` login
uv sync
```

## Use

Interactive REPL:

```bash
uv run bot.py
```

One-shot:

```bash
uv run bot.py "add a --json flag to the CLI and write a test for it"
```

Type `exit` (or `quit`) to leave the REPL.

## GitHub issue bot

`gh_bot.py` turns the same agent into an **interactive GitHub bot** with two flows:

- **Q&A** — when someone @-mentions the bot in an issue (or a comment on one), it runs
  the agent and replies with a comment. Each further @-mention on the same issue
  *resumes the same agent session*, so the bot remembers the thread.
- **Bug-fix** — when an issue is opened with (or gets) the **`bug`** label, the agent
  autonomously fixes it in the local checkout, runs the test suite, opens a PR
  (`Fixes #N`) against the default branch, and comments on the issue linking the PR.
  No @-mention needed. If it can't fix it or tests fail, it comments explaining why
  instead of opening a PR. (Override the label with `BOT_BUG_LABEL`.)

It's a small FastAPI receiver (`:9901`). GitHub events reach it in real time via the
`cli/gh-webhook` extension — no public URL or tunnel needed. The agent posts its reply
itself through the official **GitHub MCP server** (Docker stdio, authenticated with your
local `gh` token) and can drive a real browser via **Claude in Chrome** to reproduce and
verify UI behavior — enabled by passing `--chrome` to the underlying CLI
(`extra_args={"chrome": None}` in the Agent SDK options).

```bash
# 0. one-time: pull the GitHub MCP server image (agent uses it to post). For the
#    browser, install the "Claude in Chrome" extension in Chrome/Edge and run the
#    bot under a direct Anthropic plan (Pro/Max/Team/Enterprise). Claude Code sets
#    up the native messaging host on first --chrome use.
docker pull ghcr.io/github/github-mcp-server

# 1. one-time: install the webhook-forwarding extension
gh extension install cli/gh-webhook

# 2. start the receiver AND the webhook forwarder together (Ctrl-C stops both)
make run
```

`make run` runs the receiver (`gh_bot.py`) and `gh webhook forward` together, after a
preflight check (`gh`, `docker`, python, the extension) and clearing any orphaned
forwarder hook from a previous unclean exit. Override defaults like
`make run REPO=owner/name PORT=9901`. Individual pieces: `make bot`, `make forward`,
`make clean-hooks`.

Or run the two steps manually:

```bash
uv run gh_bot.py                              # terminal A: receiver
gh webhook forward --repo=24hours/todo-app \  # terminal B: forwarder
  --events=issues,issue_comment --url=http://localhost:9901/webhook
```

Then open an issue (or comment) containing **@24hours** → the bot replies, and every
further @24hours comment on that issue continues the same conversation.

Notes:
- The trigger handle defaults to your authenticated `gh` login; override with
  `BOT_MENTION=somename`.
- Posting goes through the GitHub MCP server's `add_issue_comment` tool (the only
  write tool pre-approved in `allowed_tools`). Everything else about the repo is read
  from the local `./app` clone. Requires Docker; the token comes from `gh auth token`.
- Loop-safe: the agent is instructed to end every reply with a hidden
  `<!-- claude-bot -->` marker and never @-mention, and the bot ignores any comment
  containing that marker — so it never answers itself, even though it posts under the
  same account you comment from.
- Same coding capabilities as `bot.py` (full agent scoped to `./app`). It only edits
  files when an issue clearly asks it to; otherwise it just explains.
- Browser: the agent uses Claude in Chrome (`mcp__claude-in-chrome__*`, whole server
  pre-approved, enabled via `--chrome`). It can start the app and load
  `http://localhost:5173` to reproduce/verify UI behavior. Note: this drives a *visible*
  Chrome and pauses for a human on login/CAPTCHA, so it's interactive — fine when you're
  watching, less so for fully unattended runs. Needs the Claude in Chrome extension and a
  direct Anthropic plan.
- Images in comments: GitHub's API can't upload attachments, so for an image reply (e.g.
  a before/after screenshot) the agent posts through the **web UI** with Claude in Chrome
  — it navigates to the issue, uploads the file into the comment box, and submits (the
  marker is still typed in). Text-only replies stay on the deterministic MCP tool. The
  browser must be logged into GitHub for this, or it pauses for you.
- Session map is stored in `.gh_bot_sessions.json` (keyed `owner/repo#number`).

## Notes

- `permission_mode="acceptEdits"` and the `allowed_tools` list pre-approve the
  agent's actions so it runs without stopping for confirmation. Remove `Bash`,
  `Write`, and `Edit` from `allowed_tools` to get a read-only agent.
- Because it auto-approves edits and shell commands, run it in a directory you
  trust — ideally a git repo so you can review and revert changes.
- For tighter control, the SDK supports hooks (`PreToolUse`/`PostToolUse`),
  subagents, MCP servers, and custom permission callbacks — see the
  [docs](https://code.claude.com/docs/en/agent-sdk/overview).
```
