.PHONY: run bot forward poll check clean-hooks

REPO   ?= 24hours/todo-app
PORT   ?= 9901
EVENTS ?= issues,issue_comment
URL    := http://localhost:$(PORT)/webhook
HEALTH := http://localhost:$(PORT)/health

# Sentry -> GitHub issue poller (sentry_github_poller.py). Outbound-only, so it
# needs no tunnel/public URL. Polls every POLL_INTERVAL seconds.
POLL_INTERVAL ?= 60

# claude_demo is a member of the Desktop uv workspace (shared ../.venv). Invoke
# that venv's python directly — `uv run`/`uv sync` from a member can prune the
# workspace root's packages. Override with `make run PYTHON=python3` if needed.
PYTHON ?= ../.venv/bin/python

# Run the bot receiver and the GitHub webhook forwarder together; Ctrl-C stops both.
# A clean Ctrl-C lets `gh webhook forward` deregister its own hook; clean-hooks
# clears any orphan left by a previous unclean exit (else forward errors "Hook
# already exists").
run: check clean-hooks
	@echo "Starting gh_bot on :$(PORT), Sentry->GitHub poller (every $(POLL_INTERVAL)s), forwarding $(EVENTS) from $(REPO) (Ctrl-C to stop)"
	@trap 'kill 0' INT TERM EXIT; \
	$(PYTHON) gh_bot.py & \
	POLL_INTERVAL=$(POLL_INTERVAL) $(PYTHON) sentry_github_poller.py & \
	echo "waiting for receiver to be ready..."; \
	until curl -sf $(HEALTH) >/dev/null 2>&1; do sleep 0.5; done; \
	gh webhook forward --repo=$(REPO) --events=$(EVENTS) --url=$(URL); \
	wait

# Preflight: the things `make run` needs to exist.
check:
	@command -v gh >/dev/null || { echo "error: gh CLI not found"; exit 1; }
	@command -v docker >/dev/null || { echo "error: docker not found (needed for the GitHub MCP server)"; exit 1; }
	@test -x "$(PYTHON)" || { echo "error: python not found at $(PYTHON) — set PYTHON=..."; exit 1; }
	@gh webhook --help >/dev/null 2>&1 || { echo "error: gh-webhook extension missing. Run: gh extension install cli/gh-webhook"; exit 1; }

# Run just the receiver.
bot:
	$(PYTHON) gh_bot.py

# Run just the forwarder (receiver must already be running).
forward:
	gh webhook forward --repo=$(REPO) --events=$(EVENTS) --url=$(URL)

# Run just the Sentry -> GitHub issue poller.
poll:
	POLL_INTERVAL=$(POLL_INTERVAL) $(PYTHON) sentry_github_poller.py

# Remove any leftover gh-webhook forwarder hooks on the repo (only those pointing
# at GitHub's forwarder service — never your own webhooks).
clean-hooks:
	@gh api repos/$(REPO)/hooks --jq '.[] | select(.config.url | contains("webhook-forwarder.github.com")) | .id' 2>/dev/null \
	  | while read -r id; do echo "removing stale forwarder hook $$id"; gh api -X DELETE repos/$(REPO)/hooks/$$id; done; true
