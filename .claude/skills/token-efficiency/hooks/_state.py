# 出典: denfry/claude-skills の skills/token-efficiency/hooks（MIT License, Copyright (c) 2026 denfry）。
# Ciel では文言を日本語にし、CORE.md の中身を Ciel の決まりに合わせた。仕組みは元のまま。
"""Shared per-session state for the token-efficiency hooks.

State is advisory, not authoritative: hooks fired by batched tool calls in the same
turn can race on read-modify-write, and an occasional lost update just means one
missed warning. Correctness of the session never depends on this file.
"""

import json
import os
import sys
import time
from pathlib import Path

STATE_DIR = Path.home() / ".claude" / "state" / "token-efficiency"
METRICS_FILE = STATE_DIR / "metrics.jsonl"
MAX_STATE_AGE_DAYS = 7

DEFAULT_STATE = {
    "turn": 0,
    "searches": 0,
    "reads": {},          # path -> [[start, end, turn], ...]
    "edited": {},         # path -> prompt_id of the last edit
    "commands": {},       # normalized command -> turn first seen
    "waste": {"reread": 0, "blind_read": 0, "recheck": 0},
    "warnings_emitted": 0,
    "reported_at": 0,     # waste total at the last Stop report
}


def disabled() -> bool:
    return os.environ.get("TOKEN_EFFICIENCY_OFF", "").strip() not in ("", "0", "false")


def read_event() -> dict:
    """Parse the hook payload from stdin. Never raises.

    Decoded from bytes explicitly: on Windows sys.stdin uses the locale codepage, which
    mangles non-ASCII prompts (and with them any non-English keyword matching).
    """
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _path_for(session_id: str) -> Path:
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64] or "unknown"
    return STATE_DIR / f"{safe}.json"


def load(session_id: str) -> dict:
    state = json.loads(json.dumps(DEFAULT_STATE))  # deep copy
    try:
        with _path_for(session_id).open(encoding="utf-8") as fh:
            state.update(json.load(fh))
    except Exception:
        pass
    return state


def save(session_id: str, state: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        target = _path_for(session_id)
        tmp = target.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, target)
    except Exception:
        pass


def prune_old_state() -> None:
    """Drop state files from sessions that ended days ago."""
    cutoff = time.time() - MAX_STATE_AGE_DAYS * 86400
    try:
        for f in STATE_DIR.glob("*.json"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception:
        pass


def emit_context(event_name: str, text: str) -> None:
    """Inject text into Claude's context and exit cleanly."""
    payload = {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }
    print(json.dumps(payload))
    sys.exit(0)


def append_metric(record: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with METRICS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass
