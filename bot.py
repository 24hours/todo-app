"""A tiny Claude Code, built on the Claude Agent SDK.

The SDK ships Claude Code's agent loop, built-in tools (Read/Write/Edit/Bash/
Glob/Grep/...), permissions, and context management. So "build a coding bot"
collapses to: stream messages out of query() and print them. No tool loop,
no tool implementations — the SDK runs all of that for us.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ResultMessage,
    SystemMessage,
)

# ANSI colors for a readable terminal UI
DIM, BOLD, BLUE, GREEN, YELLOW, RESET = (
    "\033[2m", "\033[1m", "\033[34m", "\033[32m", "\033[33m", "\033[0m",
)

SYSTEM_PROMPT = (
    "You are a coding agent working in the user's directory, like Claude Code. "
    "Inspect files before changing them, keep edits minimal and consistent with "
    "the surrounding code, verify your work when reasonable, and be concise."
)

# The directory the agent operates in. Lives next to this script, so it works
# no matter where you launch from. Created on startup if it doesn't exist.
WORKDIR = (Path(__file__).resolve().parent / "app").resolve()


def base_options(**extra) -> ClaudeAgentOptions:
    """Shared config. cwd scopes the agent to WORKDIR; allowed_tools pre-approves
    these so it runs without stopping for permission. Drop a tool for read-only."""
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        cwd=str(WORKDIR),
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        permission_mode="acceptEdits",
        **extra,
    )


def render(message) -> str | None:
    """Print one streamed message. Returns the session_id when we see the init
    event, so the REPL can resume the same session on the next turn."""
    if isinstance(message, SystemMessage) and message.subtype == "init":
        return message.data.get("session_id")
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                print(f"{BOLD}{BLUE}bot{RESET} {block.text}")
            elif isinstance(block, ToolUseBlock):
                preview = ", ".join(f"{k}={v!r}"[:60] for k, v in block.input.items())
                print(f"  {YELLOW}⚙ {block.name}{RESET}({preview})")
    elif isinstance(message, ResultMessage) and message.is_error:
        print(f"{YELLOW}error: {message.result}{RESET}")
    return None


async def ask(prompt: str, resume: str | None = None) -> str | None:
    """Run one turn to completion, streaming output. Returns the session_id."""
    session_id = resume
    opts = base_options(resume=resume) if resume else base_options()
    async for message in query(prompt=prompt, options=opts):
        sid = render(message)
        if sid:
            session_id = sid
    return session_id


async def repl() -> None:
    session_id: str | None = None
    while True:
        try:
            user_input = input(f"{BOLD}{GREEN}you{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return
        if user_input.lower() in {"exit", "quit", ":q"}:
            print("bye")
            return
        if not user_input:
            continue
        # Resume the session each turn so the agent keeps full context.
        session_id = await ask(user_input, resume=session_id)
        print()


def main() -> None:
    WORKDIR.mkdir(parents=True, exist_ok=True)
    print(f"{BOLD}{GREEN}coding-bot{RESET} — a tiny Claude Code, on the Claude Agent SDK")
    print(f"{DIM}working in {WORKDIR}{RESET}")
    print(f"{DIM}type a request, or 'exit' to quit. one-shot: uv run bot.py \"your task\"{RESET}\n")

    if len(sys.argv) > 1:  # one-shot mode
        prompt = " ".join(sys.argv[1:])
        print(f"{BOLD}{GREEN}you{RESET} {prompt}")
        asyncio.run(ask(prompt))
    else:
        asyncio.run(repl())


if __name__ == "__main__":
    main()
