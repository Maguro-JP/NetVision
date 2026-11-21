#!/usr/bin/env python3
"""common-sense のフックが共に使う部品。

止める設定、状態の置き場、会話の記録（transcript）の読み方、調べる呼び出しの判定を 1 か所に置く。
フックごとに書くと、人の発言の見分け方がずれて、通知や Stop フックの差し戻しを人の発言と数えてしまう。

会話の記録の形（Claude Code 2.1.325 の記録と、公式の文書 https://code.claude.com/docs/en/hooks で確かめた。2026-11-15）:
  - 人の発言は type が user で、origin.kind が human
  - ツールの結果も type が user（content に tool_result が入る）
  - Stop フックの差し戻しも type が user（isMeta が真で、文が「Stop hook feedback」で始まる）
  - 裏の処理の通知も type が user（origin.kind が task-notification）。通知は 1 つの文に複数並ぶことがある
  - 要約は isCompactSummary が真
  - 記録は遅れて書かれる。最後の返答は、Stop の入力の last_assistant_message を先に見る
  - PostToolUse は成功したツールでしか動かない。失敗は PostToolUseFailure に来る

環境変数:
  COMMON_SENSE_OFF=1        5 本のフックを全部止める
  COMMON_SENSE_STATE=dir    状態の置き場（既定は ~/.claude/state/common-sense）
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 日本時間に夏時間は無いので、固定の差で足りる（tzdata の無いコンテナでも動く）
JST = timezone(timedelta(hours=9))


def off() -> bool:
    return os.environ.get("COMMON_SENSE_OFF") == "1"


def today() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def read_event() -> dict:
    """フックの入力。壊れていれば空（フックは止めずに通す）。"""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        d = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def emit(event: str, text: str) -> None:
    """Claude に見える 1 行を差し込む。PostToolUse の標準出力はそのままでは見えないので、additionalContext で返す。"""
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}, ensure_ascii=False))


# ---- 状態 ----------------------------------------------------------------
def _state_file(session_id, name: str) -> Path:
    base = os.environ.get("COMMON_SENSE_STATE")
    root = Path(base) if base else Path.home() / ".claude" / "state" / "common-sense"
    sid = re.sub(r"[^A-Za-z0-9_-]", "", str(session_id or ""))[:40] or "session"
    return root / f"{sid}.{name}.json"


def load(session_id, name: str) -> dict:
    try:
        d = json.loads(_state_file(session_id, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def save(session_id, name: str, data: dict) -> None:
    """状態は目安。書けなくてもフックは続ける（同時に動いた 2 本が競っても、知らせが 1 回抜けるだけ）。"""
    f = _state_file(session_id, name)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        pass


# ---- 会話の記録 ------------------------------------------------------------
def blocks(content) -> list:
    return [x for x in content if isinstance(x, dict)] if isinstance(content, list) else []


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(str(x.get("text", "")) for x in blocks(content) if x.get("type") == "text")


def result_text(block: dict) -> str:
    inner = block.get("content")
    return inner if isinstance(inner, str) else text_of(inner)


# 人の発言に見えて、人の発言ではない行の出だし。origin の無い記録（古い版、手で作った記録）で使う
NOT_HUMAN = re.compile(r"Stop hook feedback|<task-notification>|<wake\b|<event\b|<system-reminder>|<local-command|<command-(?:name|message)>"
                       r"|\[Request interrupted|This session is being continued")


def is_human(e: dict) -> bool:
    """人の発言か。ツールの結果、Stop フックの差し戻し、裏の処理の通知、要約は、同じターンの続きとして扱う。"""
    if e.get("type") != "user" or e.get("isMeta") or e.get("isCompactSummary"):
        return False
    c = (e.get("message") or {}).get("content", [])
    if any(x.get("type") == "tool_result" for x in blocks(c)):
        return False
    origin = e.get("origin")
    kind = origin.get("kind") if isinstance(origin, dict) else None
    if kind:
        return kind == "human"
    t = text_of(c).lstrip()
    return not (NOT_HUMAN.match(t) or "<task-notification>" in t[:600])


def _lines(path: str, tail):
    """記録を後ろから tail バイトだけ読む（None なら全部）。途中から読んだ最初の行は欠けているので捨てる。"""
    size = os.path.getsize(path)
    cut = bool(tail) and size > tail
    with open(path, "rb") as f:
        if cut:
            f.seek(size - tail)
            f.readline()
        data = f.read()
    return data.decode("utf-8", errors="replace").splitlines(), cut


def _turn(prompt: str, human: bool) -> dict:
    return {"prompt": prompt, "human": human, "calls": [], "results": {}, "texts": []}


def parse(lines) -> list:
    """記録の行を、ターン（人の発言から次の人の発言の前まで）の列にする。
    1 ターンは prompt（人の発言）、calls（ツール呼び出し）、results（呼び出しの ID ごとの結果）、
    texts（(その文より前の呼び出しの数, 返答の文) の列）。"""
    turns = [_turn("", False)]
    for line in lines:
        if '"user"' not in line and '"assistant"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("isSidechain"):
            continue  # subagent の中のやり取りは、親のターンに数えない
        c = (e.get("message") or {}).get("content", [])
        cur = turns[-1]
        if e.get("type") == "user":
            if is_human(e):
                turns.append(_turn(text_of(c), True))
                continue
            for x in blocks(c):
                if x.get("type") == "tool_result":
                    cur["results"][x.get("tool_use_id")] = {"error": bool(x.get("is_error")), "text": result_text(x)[:20000]}
        elif e.get("type") == "assistant":
            t = text_of(c)
            if t.strip():
                cur["texts"].append((len(cur["calls"]), t))
            for x in blocks(c):
                if x.get("type") == "tool_use":
                    inp = x.get("input")
                    cur["calls"].append({"id": x.get("id"), "name": str(x.get("name", "")), "input": inp if isinstance(inp, dict) else {}})
    return [t for t in turns if t["human"] or t["calls"] or t["texts"] or t["results"]]


def last_turns(path: str, n: int = 1) -> list:
    """最後の n ターンを古い順に返す。後ろの 1MB から読み、人の発言が n 個そろわなければ 8MB、全部と広げる。
    長い会話の記録は数十 MB になるので、毎回全部は読まない。全部読んでも足りなければ、あるだけ返す。"""
    turns = []
    for tail in (1 << 20, 8 << 20, None):
        lines, cut = _lines(path, tail)
        turns = parse(lines)
        if not cut or sum(1 for t in turns if t["human"]) >= n:
            break
    return turns[-n:]


def call_text(call: dict) -> str:
    """呼び出しを 1 行の文にする。Bash はコマンド、ほかは名前と入力。表の正規表現を当てる相手。"""
    inp = call.get("input") or {}
    if call.get("name") == "Bash":
        return str(inp.get("command", ""))
    return f"{call.get('name', '')} {json.dumps(inp, ensure_ascii=False, sort_keys=True)}"


# ---- 調べる呼び出し --------------------------------------------------------
WEB = re.compile(r"^(WebSearch|WebFetch)$|^mcp__(Context7|Exa)__")
READ_TOOLS = ("Read", "Grep", "Glob", "NotebookRead", "LS")
MCP_READ = re.compile(r"^mcp__.*?(get|list|read|search|query|fetch|view|status|check)", re.I)
# 入っている版と、外の事実を見に行くコマンド。記憶ではなく一次情報に当たった印
BASH_EXTERNAL = re.compile(
    r"--help\b|\s-h\s*(?:$|[|;&)])|--version\b|\s-V\s*(?:$|[|;&)])|\bman\s+\w"
    r"|\bpip3?\s+(?:show|index|list|freeze|download)\b|\buv\s+pip\s+(?:show|list)\b"
    r"|\bnpm\s+(?:view|info|show|ls|list|outdated)\b|\bpnpm\s+(?:info|view|list|outdated)\b|\byarn\s+(?:info|list)\b"
    r"|\bcargo\s+(?:search|info|tree)\b|\bgo\s+(?:list|doc|version)\b"
    r"|\bgh\s+(?:api|release|search)\b|\bgh\s+repo\s+view\b|\bgit\s+(?:ls-remote|clone)\b|\bcurl\b|\bwget\b"
    r"|\bapt(?:-cache)?\s+(?:show|policy|search)\b|\bbrew\s+info\b|\bdocker\s+(?:manifest|search|inspect)\b"
    r"|\bwhich\b|\bcommand\s+-v\b|importlib\.metadata|__version__|\bpydoc\b")
# 手元のコードと出力を見るコマンド、動かして確かめるコマンド。書き換えるだけのコマンド（add、commit、push、mkdir）は入れない
BASH_INSPECT = re.compile(
    r"(?:^|[;&|(]\s*|\bxargs\s+)(?:cat|head|tail|less|grep|egrep|rg|ag|ls|find|fd|tree|wc|stat|file|diff|cmp|jq|yq|awk|sort|uniq|cut|strings|env|printenv|type|ps|df|du|date|uname|id|pwd)\b"
    r"|\bsed\s+-n\b"
    r"|\bgit\s+(?:-C\s+\S+\s+)?(?:log|show|diff|status|blame|ls-files|check-ignore|rev-parse|branch|remote|describe|grep|cat-file|shortlog|reflog|tag|worktree\s+list)\b"
    r"|\bgh\s+(?:pr|run|issue|workflow|repo)\s+(?:view|list|checks|status|diff)\b"
    r"|\b(?:pytest|unittest|ruff|mypy|tsc|eslint|shellcheck|bandit|semgrep)\b|\bnpm\s+(?:test|run)\b|\bcargo\s+(?:test|check|build)\b|\bgo\s+(?:test|build|vet)\b|\bmake\b"
    r"|validate\.sh|precheck\.py|hooks-smoke\.py|test_\w+\.py|_check\.py|\bscan\.py|\baudit\.py|proofread\.py|knowledge\.py|--check\b|--dry-run\b|--find\b|\bpython3?\s+-c\b")
# 入っている版が書いてあるファイルと、入っている物そのもの
MANIFEST = re.compile(
    r"package(?:-lock)?\.json|pnpm-lock\.yaml|yarn\.lock|requirements[\w.-]*\.txt|constraints[\w.-]*\.txt|pyproject\.toml|uv\.lock|poetry\.lock"
    r"|Pipfile|Cargo\.(?:toml|lock)|go\.(?:mod|sum)|Gemfile|CHANGELOG|site-packages|dist-packages|node_modules|\.venv", re.I)


def is_external_lookup(name: str, inp: dict) -> bool:
    """外の事実（版、API、フラグ、仕様）を一次情報で見に行った呼び出しか。"""
    if WEB.search(name):
        return True
    if name == "Bash":
        return bool(BASH_EXTERNAL.search(str(inp.get("command", ""))))
    if name in READ_TOOLS:
        where = " ".join(str(inp.get(k, "")) for k in ("file_path", "path", "glob", "pattern"))
        return bool(MANIFEST.search(where))
    if name == "Skill":
        return "claude-api" in str(inp.get("skill", ""))  # モデルの ID と値段は、このスキルが今の一覧を持つ
    return bool(re.search(r"^mcp__.*?(docs|release|tag|get_file_contents|search_code|search_repositories)", name, re.I))


def is_lookup(name: str, inp: dict) -> bool:
    """調べた呼び出しか（手元のコードと出力を見た、動かして確かめた、外の文書を見た）。"""
    if name in READ_TOOLS or MCP_READ.search(name) or is_external_lookup(name, inp):
        return True
    return name == "Bash" and bool(BASH_INSPECT.search(str(inp.get("command", ""))))
