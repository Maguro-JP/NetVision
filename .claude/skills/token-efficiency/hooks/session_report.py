# 出典: denfry/claude-skills の skills/token-efficiency/hooks（MIT License, Copyright (c) 2026 denfry）。
# Ciel では文言を日本語にし、CORE.md の中身を Ciel の決まりに合わせた。仕組みは元のまま。
"""Stop hook: per-task usage report, trajectory feedback, and offline metrics.

On every task completion it parses the session transcript and shows the user a
one-line report — wall-clock time, input/output tokens, cache hit rate, tool
calls — plus one targeted optimization tip. The report goes out as
``systemMessage``: it is displayed in the terminal but never enters the model's
context, so it costs zero tokens.

It also appends a metrics line to state/token-efficiency/metrics.jsonl (what
`evals/measure.py` reads), and speaks *into the conversation* only when enough
new waste has accumulated since the last report to be worth the tokens.

Env:
  TOKEN_EFFICIENCY_OFF=1          disable entirely
  TOKEN_EFFICIENCY_TASK_REPORT=0  disable the per-task usage report
  TOKEN_EFFICIENCY_REPORT_AT=N    in-context report once N new waste events accumulate (default 3)
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _state  # noqa: E402

LABELS = {
    "reread": "件の変わっていないファイルの読み直し",
    "blind_read": "件の探さずに全体を読んだファイル",
    "recheck": "件の確かめ済みのものの再確認",
}


def _parse_ts(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _is_user_prompt(obj: dict) -> bool:
    """A real user turn: typed text, not a tool_result carrier or meta line."""
    if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isSidechain"):
        return False
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        kinds = {c.get("type") for c in content if isinstance(c, dict)}
        return "text" in kinds and "tool_result" not in kinds
    return False


def task_stats(transcript_path: str) -> dict | None:
    """Usage for the span from the last user prompt to the end of the transcript."""
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None

    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except Exception:
            continue

    start = None
    for i, obj in enumerate(records):
        if _is_user_prompt(obj):
            start = i
    if start is None:
        return None

    usage_by_id = {}   # message id -> usage (last wins; streaming repeats the same id)
    tool_calls = 0
    seen_tool_ids = set()
    last_ts = None
    for obj in records[start:]:
        ts = _parse_ts(obj.get("timestamp"))
        if ts:
            last_ts = ts
        if obj.get("type") != "assistant" or obj.get("isSidechain"):
            continue
        msg = obj.get("message") or {}
        usage = msg.get("usage")
        if isinstance(usage, dict):
            usage_by_id[msg.get("id") or id(obj)] = usage
        for c in msg.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "tool_use":
                tid = c.get("id") or id(c)
                if tid not in seen_tool_ids:
                    seen_tool_ids.add(tid)
                    tool_calls += 1

    if not usage_by_id:
        return None

    totals = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0}
    for u in usage_by_id.values():
        totals["input"] += int(u.get("input_tokens") or 0)
        totals["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
        totals["cache_write"] += int(u.get("cache_creation_input_tokens") or 0)
        totals["output"] += int(u.get("output_tokens") or 0)

    start_ts = _parse_ts(records[start].get("timestamp"))
    seconds = (last_ts - start_ts).total_seconds() if start_ts and last_ts else None
    return {
        "seconds": seconds,
        "turns": len(usage_by_id),
        "tool_calls": tool_calls,
        **totals,
    }


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _fmt_duration(seconds) -> str:
    if seconds is None:
        return "?"
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}h{s % 3600 // 60:02d}m"
    if s >= 60:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s}s"


def _tip(stats: dict, waste: dict) -> str:
    """One targeted optimization tip, most impactful first."""
    total_waste = sum(int(v) for v in waste.values())
    if total_waste:
        detail = "、".join(f"{n}{LABELS[k]}" for k, n in waste.items() if n and k in LABELS)
        return f"無駄を減らす: {detail}。行を絞って読み、通ったツールを信じる"
    total_in = stats["input"] + stats["cache_read"] + stats["cache_write"]
    if total_in > 200_000 and stats["cache_read"] / max(total_in, 1) < 0.7:
        return "キャッシュの当たりが低い。ターンの間が空くか、文脈が揺れている。セッションを1つの題に絞り、途中で /clear しない"
    if stats["tool_calls"] >= 8 and stats["tool_calls"] > stats["turns"] * 1.5:
        return "直列のツール呼び出しが多い。独立した読み込みや検索は1ターンにまとめる"
    if stats["turns"] > 15:
        return "長い作業。まとまった調べものはサブエージェントに任せ、文脈を軽く保つ"
    return "無駄の型は出ていない"


def main() -> None:
    if _state.disabled():
        return

    event = _state.read_event()
    session_id = event.get("session_id", "unknown")
    state = _state.load(session_id)
    waste = state.get("waste", {})
    total = sum(int(v) for v in waste.values())

    stats = None
    if os.environ.get("TOKEN_EFFICIENCY_TASK_REPORT", "1").strip() not in ("0", "false"):
        stats = task_stats(event.get("transcript_path", ""))

    _state.append_metric(
        {
            "session": session_id,
            "turn": state.get("turn", 0),
            "searches": state.get("searches", 0),
            "files_read": len(state.get("reads", {})),
            "waste": waste,
            "waste_total": total,
            "task": stats,
        }
    )

    output = {}

    if stats:
        total_in = stats["input"] + stats["cache_read"] + stats["cache_write"]
        cached_pct = round(100 * stats["cache_read"] / total_in) if total_in else 0
        output["systemMessage"] = (
            f"⏱ {_fmt_duration(stats['seconds'])} · "
            f"入力 {_fmt_tokens(total_in)}（キャッシュ {cached_pct}%）· "
            f"出力 {_fmt_tokens(stats['output'])} · "
            f"ツール {stats['tool_calls']} 回 / {stats['turns']} ターン\n"
            f"💡 {_tip(stats, waste)}"
        )

    try:
        threshold = max(1, int(os.environ.get("TOKEN_EFFICIENCY_REPORT_AT", "3")))
    except ValueError:
        threshold = 3

    if total - int(state.get("reported_at", 0)) >= threshold:
        state["reported_at"] = total
        _state.save(session_id, state)
        detail = "、".join(f"{n}{LABELS[k]}" for k, n in waste.items() if n and k in LABELS)
        output["hookSpecificOutput"] = {
            "hookEventName": "Stop",
            "additionalContext": (
                f"節約の点検: このセッションで {detail} が積み重なった。1回の長い返答より、ターンをまたいだ積み重ねの方が高くつく。"
                "この先は、読む前に行を絞り、通ったツールを信じ、依存する何かが変わったときだけ再確認する（token-efficiency の 3）。"
                "この文への返事は要らない。"
            ),
        }

    if output:
        print(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
