#!/usr/bin/env python3
"""bug-first のフックが共に使う部品。止める設定、状態の置き場、帳簿の読み、会話の記録の読み手を 1 か所に置く。

会話の記録（transcript）の読み方は、common-sense の _cs.py に合わせる（人の発言、通知、Stop フックの差し戻しの見分け）。
同じ見分けを 2 か所に書くと、片方だけ直ってずれる。common-sense が無いリポジトリでは cs() が None を返し、
記録を読むフックは黙って通る（配った先で、足りないスキルのせいで全部のターンが止まるのを避ける）。

フックの入力と出力の形は、公式の文書（https://code.claude.com/docs/en/hooks 、2026-11-21 に読んだ）で確かめた。
  - UserPromptSubmit と SessionStart は、標準出力の文がそのまま文脈に入る。入力の prompt に、送られた文が入る
  - PostToolUse は、ツールが成功したあとにだけ動く。Claude に見せる文は hookSpecificOutput.additionalContext で返す
  - Bash の tool_response は stdout、stderr、interrupted、isImage を持つ
  - Stop は、終了コード 2 と標準エラーの文で差し戻す。最後の返答は last_assistant_message にある

環境変数:
  BUG_FIRST_OFF=1        3 本のフックを全部止める
  BUG_FIRST_STATE=dir    状態の置き場（既定は ~/.claude/state/bug-first）
"""

import importlib
import json
import os
import re
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
LEDGER = Path("docs") / "reports" / "bugs.md"

sys.path.insert(0, str(SKILL / "scripts"))
import bugs  # noqa: E402


def off() -> bool:
    return os.environ.get("BUG_FIRST_OFF") == "1"


def read_event() -> dict:
    """フックの入力。壊れていれば空（フックは止めずに通す）。"""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        d = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def emit(event: str, text: str) -> None:
    """PostToolUse の標準出力は Claude に見えないので、additionalContext で返す。"""
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}, ensure_ascii=False))


def cs():
    """common-sense の _cs（会話の記録の読み手）。無ければ None。"""
    d = SKILL.parent / "common-sense" / "hooks"
    if not (d / "_cs.py").is_file():
        return None
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))
    try:
        return importlib.import_module("_cs")
    except Exception:  # noqa: BLE001 読み手が壊れていても、フックは通す
        return None


def ledger_bugs(root=None) -> list:
    """帳簿の節。帳簿が無いリポジトリでは空。フックは起動役（run.sh）がリポジトリの根に移してから動く。"""
    return bugs.load(Path(root) if root else Path.cwd())


def short(b: dict, n: int = 30) -> str:
    t = f"{b['id']} {b['title']}"
    return t if len(t) <= n else t[:n - 1] + "…"


# ---- 状態 ----------------------------------------------------------------
def _state_file(session_id, name: str) -> Path:
    base = os.environ.get("BUG_FIRST_STATE")
    root = Path(base) if base else Path.home() / ".claude" / "state" / "bug-first"
    sid = re.sub(r"[^A-Za-z0-9_-]", "", str(session_id or ""))[:40] or "session"
    return root / f"{sid}.{name}.json"


def load(session_id, name: str) -> dict:
    try:
        d = json.loads(_state_file(session_id, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def save(session_id, name: str, data: dict) -> None:
    """状態は目安。書けなくてもフックは続ける。"""
    f = _state_file(session_id, name)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        pass
