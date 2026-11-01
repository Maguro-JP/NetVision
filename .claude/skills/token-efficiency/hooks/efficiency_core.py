# 出典: denfry/claude-skills の skills/token-efficiency/hooks（MIT License, Copyright (c) 2026 denfry）。
# Ciel では文言を日本語にし、CORE.md の中身を Ciel の決まりに合わせた。仕組みは元のまま。
"""UserPromptSubmit hook: keep the token-efficiency contract in effect every turn.

A skill body is model-invoked, so it drifts out of effect over a long session. This
re-asserts the core contract deterministically. It is itself subject to the rules it
carries: the full contract lands on turn 1 and every Nth turn after; other turns get a
one-line reminder. When the user explicitly asks for depth, the guardrail is injected
instead of the brevity rules, so the hook never argues against an explicit request.

Env:
  TOKEN_EFFICIENCY_OFF=1     disable entirely (used by the eval harness for A/B runs)
  TOKEN_EFFICIENCY_REFRESH=N re-inject the full contract every N turns (default 10)
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _state  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent

# An explicit request for depth outranks the brevity defaults (SKILL.md §6).
# Stems for the major languages Claude is commonly used in; matching a stem is enough.
DEPTH_REQUEST = re.compile(
    r"\b(thorough(ly)?|exhaustive(ly)?|comprehensive(ly)?|in\s+detail|detailed|"
    r"deep[\s-]?dive|step[\s-]by[\s-]step|full\s+(explanation|review|write[\s-]?up)|"
    r"walk\s+me\s+through|explain\s+(fully|everything))\b"
    # Russian / Ukrainian
    r"|подробн|детальн|развернут|исчерпыв|пошагов|во\s+всех\s+деталях"
    r"|докладн|розгорнут|вичерпн|покроков"
    # Spanish / Portuguese / Italian
    r"|detallad|exhaustiv|a\s+fondo|paso\s+a\s+paso|minucios"
    r"|detalhad|aprofundad|passo\s+a\s+passo"
    r"|dettagliat|approfondit|esaustiv"
    # French
    r"|détaillé|exhaustif|approfondi|étape\s+par\s+étape|en\s+détail"
    # German
    r"|ausführlich|detailliert|gründlich|erschöpfend|schritt\s+für\s+schritt"
    # Chinese / Japanese / Korean
    r"|详细|詳細|全面|深入|逐步|一步一步"
    r"|詳しく|徹底的|網羅|くわしく"
    r"|자세히|상세|철저히|단계별",
    re.IGNORECASE,
)

DEPTH_OVERRIDE = (
    "ユーザーが詳しさを求めている。トークン節約の歯止めが効く場面で、頼まれた分は全部書く。"
    "短さはデフォルトであって、はっきりした依頼より上ではない。前置きと同じことの繰り返しだけは削る。"
)

FALLBACK_CORE = (
    "トークン節約の決まり: 結論から。前置きと締めの要約を書かない。読む前に探し、ファイルは要る範囲だけ読む。"
    "独立したツール呼び出しは同時に出す。ツールの成否は結果で信じ、自分が書いた中身は差分を 1 回だけ読む。出力は file:line で指し、貼り直さない。"
    "正しさ、要る注意書き、検証の実行は、短さと引き換えにしない。"
)


def _read(name: str, fallback: str) -> str:
    try:
        return (SKILL_DIR / name).read_text(encoding="utf-8").strip()
    except Exception:
        return fallback


def main() -> None:
    if _state.disabled():
        return

    event = _state.read_event()
    session_id = event.get("session_id", "unknown")
    prompt = event.get("user_input", "") or ""

    state = _state.load(session_id)
    state["turn"] = int(state.get("turn", 0)) + 1
    turn = state["turn"]
    _state.save(session_id, state)

    if turn == 1:
        _state.prune_old_state()

    try:
        refresh = max(1, int(os.environ.get("TOKEN_EFFICIENCY_REFRESH", "10")))
    except ValueError:
        refresh = 10

    if DEPTH_REQUEST.search(prompt):
        text = DEPTH_OVERRIDE
    elif turn == 1 or turn % refresh == 0:
        text = _read("CORE.md", FALLBACK_CORE)
    else:
        text = _read("CORE_SHORT.md", FALLBACK_CORE)

    _state.emit_context("UserPromptSubmit", text)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # a hook must never break the session
    sys.exit(0)
