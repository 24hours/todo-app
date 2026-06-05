"""Sentry -> GitHub issue poller.

Polls the Sentry API for unresolved issues and opens a GitHub issue for each
one not already filed. No public URL / tunnel needed (unlike the webhook
bridge) — it only makes *outbound* calls, so it runs fine on a dev machine.

Dedup is double-guarded:
  1. a local state file of Sentry issue IDs we've already filed, and
  2. a GitHub search for a hidden `sentry-issue-id:<id>` marker before creating,
so restarts or a deleted state file won't produce duplicates.

Run:
    export GITHUB_REPO=24hours/todo-app    # optional, this is the default
    python sentry_github_poller.py         # loop forever, polling every 60s
    python sentry_github_poller.py --once  # single pass (used for testing)

Auth: reads SENTRY_AUTH_TOKEN, else the token from ~/.sentryclirc.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_dotenv() -> None:
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

SENTRY_API = "https://sentry.io/api/0"
ORG = os.environ.get("SENTRY_ORG", "vitrox")
PROJECTS = [
    p.strip()
    for p in os.environ.get("SENTRY_PROJECTS", "todo-backend,todo-frontend").split(",")
    if p.strip()
]
GITHUB_REPO = os.environ.get("GITHUB_REPO", "24hours/todo-app")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
STATE_FILE = HERE / ".sentry_poller_state.json"


def get_token() -> str:
    token = os.environ.get("SENTRY_AUTH_TOKEN")
    if token:
        return token
    rc = Path.home() / ".sentryclirc"
    if rc.exists():
        for line in rc.read_text().splitlines():
            line = line.strip()
            if line.startswith("token="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("No Sentry token: set SENTRY_AUTH_TOKEN or configure ~/.sentryclirc")


TOKEN = get_token()


def sentry_get(path: str, params: dict) -> list:
    url = f"{SENTRY_API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_state() -> dict:
    """State holds the set of filed Sentry issue IDs and a `cutoff` timestamp.

    Issues first seen at or before `cutoff` are never filed (the high-water
    mark), so old/pre-existing issues are not swept up — even after a reset.
    """
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            return {"filed": set(data.get("filed", [])), "cutoff": data.get("cutoff")}
        except (json.JSONDecodeError, OSError):
            pass
    return {"filed": set(), "cutoff": None}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(
        {"filed": sorted(state["filed"]), "cutoff": state["cutoff"]}, indent=2))


def parse_ts(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def log(msg: str) -> None:
    """Timestamped stdout line. flush=True so logs surface promptly when the
    poller runs in the background (e.g. under `make run`)."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    print(f"[poller {ts}] {msg}", flush=True)


def already_on_github(marker: str) -> bool:
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "--repo", GITHUB_REPO,
             "--state", "all", "--search", marker, "--json", "number"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return bool(json.loads(out.stdout or "[]"))
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return False


def create_github_issue(issue: dict) -> bool:
    """Open a GitHub issue for one Sentry issue. Returns True if filed."""
    sentry_id = str(issue.get("id", ""))
    marker = f"sentry-issue-id:{sentry_id}"
    if already_on_github(marker):
        log(f"  {issue.get('shortId')} already on GitHub; skipping")
        return True  # treat as handled so we record it in state

    title = issue.get("title") or "Sentry error"
    level = issue.get("level", "error")
    culprit = issue.get("culprit", "")
    permalink = issue.get("permalink", "")
    project = (issue.get("project") or {}).get("slug", "")
    count = issue.get("count", "")

    body_lines = [
        f"**Sentry detected a new `{level}` error.**",
        "",
        f"- **Issue:** {issue.get('shortId', '')}",
        f"- **Title:** {title}",
    ]
    if culprit:
        body_lines.append(f"- **Culprit:** `{culprit}`")
    if project:
        body_lines.append(f"- **Project:** {project}")
    if count:
        body_lines.append(f"- **Events so far:** {count}")
    if permalink:
        body_lines += ["", f"[View in Sentry]({permalink})"]
    body_lines += ["", f"<!-- {marker} -->"]
    body = "\n".join(body_lines)

    base = ["gh", "issue", "create", "--repo", GITHUB_REPO,
            "--title", f"[Sentry] {title}", "--body", body]
    result = subprocess.run(base + ["--label", "bug"],
                            capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        # Label may not exist; retry without it.
        result = subprocess.run(base, capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        log(f"  -> created GitHub issue for {issue.get('shortId')}: {result.stdout.strip()}")
        return True
    log(f"  -> gh issue create FAILED for {issue.get('shortId')}: {result.stderr.strip()}")
    return False


def poll_once(state: dict) -> None:
    filed = state["filed"]
    cutoff = parse_ts(state.get("cutoff"))
    log(f"polling triggered — checking {len(PROJECTS)} project(s): {', '.join(PROJECTS)}")
    new_count = 0
    for project in PROJECTS:
        try:
            issues = sentry_get(
                f"/projects/{ORG}/{project}/issues/",
                {"query": "is:unresolved", "sort": "new", "statsPeriod": "14d"},
            )
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            log(f"  failed to fetch {project} issues: {e}")
            continue
        log(f"  {project}: {len(issues)} unresolved issue(s) returned")
        for issue in issues:
            sentry_id = str(issue.get("id", ""))
            if not sentry_id or sentry_id in filed:
                continue
            # High-water mark: never file issues first seen at/before the cutoff.
            first_seen = parse_ts(issue.get("firstSeen"))
            if cutoff and first_seen and first_seen <= cutoff:
                continue
            log(f"  NEW EVENT detected in {project}: {issue.get('shortId')} "
                f"\"{(issue.get('title') or '')[:70]}\" (firstSeen {issue.get('firstSeen')})")
            new_count += 1
            if create_github_issue(issue):
                filed.add(sentry_id)
                save_state(state)
    log(f"poll complete — {new_count} new event(s) this pass")


def main() -> None:
    once = "--once" in sys.argv
    state = load_state()
    if not state.get("cutoff"):
        # First run: set the high-water mark so only issues first seen from now
        # on are filed. Override with SENTRY_POLL_CUTOFF (ISO8601) to backfill
        # from an earlier point.
        state["cutoff"] = (
            os.environ.get("SENTRY_POLL_CUTOFF")
            or datetime.now(timezone.utc).isoformat()
        )
        save_state(state)
    log(f"started — org={ORG} projects={PROJECTS} repo={GITHUB_REPO} "
        f"interval={POLL_INTERVAL}s cutoff={state['cutoff']} once={once}")
    while True:
        poll_once(state)
        if once:
            break
        log(f"sleeping {POLL_INTERVAL}s until next poll")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
