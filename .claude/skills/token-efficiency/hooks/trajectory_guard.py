# 出典: denfry/claude-skills の skills/token-efficiency/hooks（MIT License, Copyright (c) 2026 denfry）。
# Ciel では文言を日本語にし、CORE.md の中身を Ciel の決まりに合わせた。仕組みは元のまま。
"""PostToolUse hook: turn the tool-use rules in SKILL.md §4 into measured feedback.

Advice in a skill body is a suggestion; this observes the trajectory and says something
only when a concrete, checkable waste pattern just happened:

  re-read      the same file range was already read this session and hasn't changed
  edit-recheck a file was read right after this turn's own successful edit to it
  blind-read   a large file was read whole with no search performed first
  recheck      a build/test command that already ran this session was run again

It stays quiet otherwise, and caps itself so the guard never becomes the bloat.

Env:
  TOKEN_EFFICIENCY_OFF=1        disable entirely
  TOKEN_EFFICIENCY_MAX_WARN=N   cap warnings per session (default 12)
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _state  # noqa: E402

WHOLE_FILE = 10**9
BLIND_READ_LINES = 200  # below this, reading a file whole is cheaper than searching it

# Only commands whose reruns are genuinely redundant — not `git status`, which is cheap
# and whose answer legitimately changes between calls.
VERIFICATION_CMD = re.compile(
    r"\b(pytest|jest|vitest|mocha|tsc|mypy|ruff|eslint|"
    r"cargo\s+(test|build|check|clippy)|go\s+(test|build|vet)|"
    r"npm\s+(test|run\s+(test|build|lint|typecheck))|"
    r"pnpm\s+(test|build|lint)|yarn\s+(test|build|lint)|"
    r"make(\s|$)|gradlew?\s|mvn\s|dotnet\s+(test|build))",
    re.IGNORECASE,
)


def _normalize(cmd: str) -> str:
    return " ".join(str(cmd).split())


def _response_lines(event: dict) -> int:
    resp = event.get("tool_response")
    text = resp.get("text", "") if isinstance(resp, dict) else str(resp or "")
    return text.count("\n")


def _check_read(event: dict, state: dict, turn: int, prompt_id: str):
    tool_input = event.get("tool_input") or {}
    path = str(tool_input.get("file_path") or "").replace("\\", "/")
    if not path:
        return None

    offset = tool_input.get("offset")
    limit = tool_input.get("limit")
    start = int(offset) if offset else 1
    end = start + int(limit) - 1 if limit else WHOLE_FILE

    edited_in = state["edited"].get(path)
    if edited_in and edited_in == prompt_id:
        state["waste"]["recheck"] += 1
        return (
            f"節約: {path} はこのターンで編集したばかりで、いま読み直している。編集が入らなければツールが失敗していた。"
            "読み直すのは、その変更の先で確かめることがあるときだけ（token-efficiency の 3）。"
        )

    prior = state["reads"].get(path, [])
    for p_start, p_end, p_turn in prior:
        if p_start <= end and start <= p_end:
            state["waste"]["reread"] += 1
            span = "全体" if p_end >= WHOLE_FILE else f"{p_start}〜{p_end} 行"
            state["reads"][path] = prior + [[start, end, turn]]
            return (
                f"節約: {path} の{span}は {p_turn} ターン目に読んでいて、その後変わっていない。"
                "文脈にあるものを使い、読み直さない（token-efficiency の 3）。"
            )

    state["reads"][path] = prior + [[start, end, turn]]

    if end >= WHOLE_FILE and state.get("searches", 0) == 0:
        lines = _response_lines(event)
        if lines >= BLIND_READ_LINES:
            state["waste"]["blind_read"] += 1
            return (
                f"節約: {path} を探さずに全体（約 {lines} 行）読んだ。特定の名前や節を探しているなら、"
                "Grep か Glob で行を絞ってその範囲だけ読む。全体を読むと、使わない行に注意の予算を使う（token-efficiency の 3）。"
            )
    return None


def _check_bash(event: dict, state: dict, turn: int):
    cmd = _normalize((event.get("tool_input") or {}).get("command", ""))
    if not cmd or not VERIFICATION_CMD.search(cmd):
        return None
    seen = state["commands"].get(cmd)
    state["commands"][cmd] = seen if seen else turn
    if seen is None:
        return None
    state["waste"]["recheck"] += 1
    return (
        f"節約: `{cmd[:80]}` は {seen} ターン目に実行済み。もう一度回すのは、落ちたとき、不安定なとき、"
        "依存する何かが変わったときだけ。通ったあとの再実行は根拠を増やさない（token-efficiency の 3）。"
    )


def main() -> None:
    if _state.disabled():
        return

    event = _state.read_event()
    tool = event.get("tool_name", "")
    session_id = event.get("session_id", "unknown")
    prompt_id = event.get("prompt_id", "")

    state = _state.load(session_id)
    turn = int(state.get("turn", 0))
    message = None

    if tool in ("Grep", "Glob"):
        state["searches"] = int(state.get("searches", 0)) + 1
    elif tool == "Read":
        message = _check_read(event, state, turn, prompt_id)
    elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str((event.get("tool_input") or {}).get("file_path") or "").replace("\\", "/")
        if path:
            state["edited"][path] = prompt_id
            state["reads"].pop(path, None)  # content changed; a later read is legitimate
    elif tool == "Bash":
        message = _check_bash(event, state, turn)

    try:
        max_warn = max(0, int(os.environ.get("TOKEN_EFFICIENCY_MAX_WARN", "12")))
    except ValueError:
        max_warn = 12

    if message and state.get("warnings_emitted", 0) >= max_warn:
        message = None
    if message:
        state["warnings_emitted"] = int(state.get("warnings_emitted", 0)) + 1

    _state.save(session_id, state)

    if message:
        _state.emit_context("PostToolUse", message)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
