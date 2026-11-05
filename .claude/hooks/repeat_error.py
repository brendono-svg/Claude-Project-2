#!/usr/bin/env python3
"""Deterministic "same error twice" detector. Costs zero tokens.

Runs on every failed tool call (PostToolUseFailure). It normalizes the error
text, counts repeats per session, and on the 2nd identical failure tells
Claude to stop retrying and run /consult instead of burning turns.
Any problem in this script exits 0 silently so it never blocks a session.
"""
import hashlib
import json
import os
import re
import sys
import tempfile

REPEAT_THRESHOLD = 2


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"0x[0-9a-f]+", "<hex>", text)  # addresses
    text = re.sub(r"\d+", "<n>", text)  # line numbers, timings, pids
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:500]


def main() -> None:
    event = json.load(sys.stdin)
    if event.get("is_interrupt"):
        return
    error = event.get("error") or ""
    if not error:
        return

    tool = event.get("tool_name", "?")
    key = hashlib.sha1(f"{tool}|{normalize(error)}".encode()).hexdigest()[:16]

    session = re.sub(r"[^A-Za-z0-9_-]", "", event.get("session_id", "default")) or "default"
    state_dir = os.path.join(tempfile.gettempdir(), "claude-repeat-errors")
    os.makedirs(state_dir, exist_ok=True)
    state_path = os.path.join(state_dir, f"{session}.json")

    try:
        with open(state_path) as f:
            counts = json.load(f)
    except (OSError, ValueError):
        counts = {}
    counts[key] = counts.get(key, 0) + 1
    with open(state_path, "w") as f:
        json.dump(counts, f)

    n = counts[key]
    if n < REPEAT_THRESHOLD:
        return

    message = (
        f"[repeat-error guard] This same {tool} error has now occurred {n} times "
        "this session. Do not retry the same approach. Stop and run the /consult "
        "skill with a short brief (goal, what you tried, the exact error) before "
        "the next attempt."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUseFailure",
            "additionalContext": message,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
