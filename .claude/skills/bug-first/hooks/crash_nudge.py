#!/usr/bin/env python3
"""PostToolUse フック（Bash）: 実行の出力にクラッシュのマークがあるとき、バグとして帳簿に書いて先に直すよう 1 行で知らせる。

ユーザーの決まり: 何よりもバグ修正を優先させる。特に致命的なバグ、クラッシュや操作不能、破損や漏洩等は即修正版を出す。
クラッシュの出力を見ても、やり方を変えて先へ進み、落ちた物をそのまま残してしまう。止めはしない。

マークは 2 段。
  強いマーク: Segmentation fault、core dumped、SIGSEGV、SIGABRT、FATAL、メモリ不足。テストの中でも、わざと出す物ではない
  弱いマーク: Traceback、panic、Unhandled、Uncaught。テストはわざと落として出すので、テストの出力では知らせない
知らせないもの（偽の知らせを減らす）:
  - テストの出力と、テストを動かすコマンドの弱いマーク
  - 探すコマンド（grep）と、ソースや文書を見るコマンド（cat、sed、git diff）の出力（マークの文字が書いてあるだけ）
  - その場で書いた 1 行のスクリプトだけで起きた Traceback（File "<string>"、"<stdin>"）と、入れてある物の中だけの Traceback
  - 人が止めた（KeyboardInterrupt）、出力の先が閉じた（BrokenPipeError）
  - 同じクラッシュの 2 回目から（同じ出力には 1 回だけ）

PostToolUse は成功したツールでしか動かない（公式の文書と、docs/knowledge/tech/ の common-sense の知識）。クラッシュは終了コードが 0 でないことが多く、
その呼び出しではこのフックは動かない。そこで、次に成功した Bash のときに、会話の記録の後ろから失敗した Bash の結果を拾う（common-sense の _cs があるとき）。
拾った呼び出しの ID を覚え、同じ結果を 2 回見ない。

重なりの整理:
  - common-sense の odd_nudge.py は「同じ失敗の行が 3 回以上」と「同じ失敗の 2 回目」を知らせる。同じ失敗が 3 件以上並ぶ出力では、こちらは黙る。
    2 回目は、こちらが「同じ出力には 1 回だけ」なので黙り、odd_nudge.py だけが言う
  - code-check の code_nudge.py は、書き込みのたびに構文の誤りを見る（Edit、Write）。こちらは Bash だけを見て、SyntaxError はマークにしない

環境変数:
  BUG_FIRST_OFF=1          止める
  BUG_FIRST_MAX_NUDGE=N    1 セッションで知らせる回数の上限（既定 6）
  BUG_FIRST_STATE=dir      見た呼び出しと、知らせたクラッシュの置き場
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bf  # noqa: E402

TAIL = 256 << 10  # 失敗した結果を拾うときに読む、記録の後ろの大きさ
HARD = [
    ("Segmentation fault", re.compile(r"Segmentation fault|\bSIGSEGV\b|\bsegfault\b")),
    ("core dumped", re.compile(r"\(core dumped\)|\bSIGABRT\b|^Bus error|double free or corruption|stack smashing detected", re.M)),
    ("FATAL", re.compile(r"^[ \t]*(?:\[[^\]\n]{0,30}\]\s*)?\[?FATAL\]?(?: ERROR)?[:\s]\s*\S|JavaScript heap out of memory|java\.lang\.OutOfMemoryError|^fatal error: \S", re.M)),
]
SOFT = [
    ("Traceback", re.compile(r"^[ \t]*Traceback \(most recent call last\):\s*$", re.M)),
    ("panic", re.compile(r"^[ \t]*thread '[^'\n]*' panicked at |^panic: \S|^goroutine \d+ \[running\]:", re.M)),
    ("Unhandled", re.compile(r"Unhandled(?:PromiseRejection(?:Warning)?| [Pp]romise [Rr]ejection| [Ee]xception| [Rr]ejection)\b"
                             r"|^[ \t]*Uncaught (?:\w+\.)*\w*(?:Error|Exception)\b|^[ \t]*(?:\[[^\]\n]{0,30}\]\s*)?uncaughtException\b|^Exception in thread \"", re.M)),
]
# テストを動かすコマンドと、テストの出力の形。弱いマークは、テストがわざと落として出す
TEST_CMD = re.compile(r"\bpytest\b|\bunittest\b|\btest_\w+\.py\b|\w+_test\.(?:py|go)\b|\b(?:npm|pnpm|yarn)\s+(?:run\s+)?test\b|\b(?:vitest|jest|mocha|tox|nox)\b"
                      r"|\bcargo\s+test\b|\bgo\s+test\b|validate\.sh|precheck\.py|hooks-smoke\.py")
TEST_OUT = re.compile(r"^Ran \d+ tests? in |^(?:FAIL|ERROR): \w+ \(|^=+ (?:FAILURES|ERRORS|test session starts|short test summary info) =+|^test result: (?:ok|FAILED)"
                      r"|^running \d+ tests?$|^Tests:\s+\d+ |^--- (?:FAIL|PASS): |^(?:ok|FAIL)\s+\S+\s+[\d.]+s$", re.M)
# 探すコマンドと、ソースや文書を見るコマンド。出力にマークの文字があっても、クラッシュではない
SEARCH_CMD = re.compile(r"(?:^|[;&|(]\s*)(?:grep|egrep|fgrep|rg|ag|ack)\b|\bgit\s+(?:-C\s+\S+\s+)?(?:grep|diff|show|log|blame)\b")
VIEW_SRC = re.compile(r"(?:^|[;&|(]\s*)(?:cat|head|tail|less|more|nl|bat|sed|awk)\b[^;&|]*\.(?:py|pyi|js|mjs|cjs|ts|tsx|jsx|rs|go|java|kt|c|cc|cpp|h|hpp|cs|rb|php|sh|bash|md|json|ya?ml|toml|html|css)\b")
FRAME = re.compile(r'^\s*File "([^"\n]+)", line \d+', re.M)
OWN_SCRIPT = re.compile(r"^<(?:string|stdin)>$")
INSTALLED = re.compile(r"site-packages|dist-packages|/lib/python\d|node_modules")
NOT_BUG = re.compile(r"^(?:KeyboardInterrupt|BrokenPipeError|SystemExit)\b")


def flat(x, limit: int = 200_000) -> str:
    """Bash の結果から文を取る（stdout と stderr。公式の文書の tool_response の欄）。"""
    if isinstance(x, dict):
        return f"{x.get('stdout') or ''}\n{x.get('stderr') or ''}"[:limit]
    return str(x or "")[:limit]


def norm(line: str) -> str:
    """件ごとに違う所（数、ハッシュ、パス）を落とす。同じクラッシュかを比べる形。"""
    s = re.sub(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-f]{7,40}\b", "H", line.strip())
    s = re.sub(r"\d+", "N", s)
    s = re.sub(r"(?:~|\.{1,2})?/[\w./@+-]+", "P", s)
    return re.sub(r"\s+", " ", s)[:160]


def traceback_sig(text: str, start: int):
    """Traceback の最後の行（例外の種類と文）。知らせない Traceback なら None。"""
    block = text[start:start + 20000].splitlines()
    head = len(block[0]) - len(block[0].lstrip())
    body, last = [], ""
    for ln in block[1:]:
        if ln.strip() and len(ln) - len(ln.lstrip()) <= head:
            last = ln.strip()  # 見出しと同じ深さに戻った最初の行が、例外の行
            break
        body.append(ln)
    frames = FRAME.findall("\n".join(body))
    if frames and all(OWN_SCRIPT.match(f) for f in frames):
        return None  # その場で書いた 1 行のスクリプトの誤り。作っている物のクラッシュではない
    if frames and all(INSTALLED.search(f) for f in frames):
        return None  # 入れてある物の中だけで起きた。自分のコードを通っていない
    if NOT_BUG.match(last):
        return None
    return last or "例外の行は出力に無い"


def crash(cmd: str, text: str):
    """(マークの名前, 同じクラッシュかを比べる形)。クラッシュの出力でなければ None。"""
    if not text.strip() or SEARCH_CMD.search(cmd) or VIEW_SRC.search(cmd):
        return None
    for name, pat in HARD:
        m = pat.search(text)
        if m:
            line = text[text.rfind("\n", 0, m.start()) + 1:].splitlines()[0]
            return name, norm(line)
    if TEST_CMD.search(cmd) or TEST_OUT.search(text):
        return None
    for name, pat in SOFT:
        for m in pat.finditer(text):
            if name == "Traceback":
                last = traceback_sig(text, m.start())
                if last is None:
                    continue
                return name, norm(last)
            line = text[text.rfind("\n", 0, m.start()) + 1:].splitlines()[0]
            return name, norm(line)
    return None


def same_failures_many(text: str) -> bool:
    """common-sense の odd_nudge.py が「同じ失敗が 3 件以上」と言う出力か。言うなら、こちらは黙る。"""
    if _bf.cs() is None:
        return False
    try:
        import odd_nudge  # common-sense の hooks は _bf.cs() が sys.path に足している
        return odd_nudge.same_failures(text)[1] >= odd_nudge.SAME
    except Exception:  # noqa: BLE001 相手のフックが無い・壊れているときは、重なりを見ずに進める
        return False


def catch_up(seen: list, path: str) -> list:
    """記録の後ろから、まだ見ていない失敗した Bash の結果を拾う。[(コマンド, 出力, 呼び出しの ID)]"""
    cs = _bf.cs()
    if cs is None:
        return []
    try:
        lines, _ = cs._lines(path, TAIL)
    except OSError:
        return []
    turns = cs.parse(lines)
    out = []
    for c in (turns[-1]["calls"] if turns else []):  # 今のターンだけ。前のターンの失敗を、あとから蒸し返さない
        r = turns[-1]["results"].get(c["id"])
        if r and c["id"] and c["id"] not in seen and c["name"] == "Bash" and r["error"]:
            out.append((str(c["input"].get("command", "")), r["text"], c["id"]))
    return out


def main() -> int:
    if _bf.off():
        return 0
    d = _bf.read_event()
    if d.get("tool_name") != "Bash":
        return 0
    inp = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    sid = d.get("session_id")
    st = _bf.load(sid, "crash")
    st = {"seen": st.get("seen") or [], "said": st.get("said") or [], "n": int(st.get("n") or 0)}
    found = []
    path = d.get("transcript_path")
    if path and os.path.isfile(path):
        for cmd, text, uid in catch_up(st["seen"], path):
            st["seen"] = (st["seen"] + [uid])[-300:]
            found.append((cmd, text))
    if d.get("tool_use_id"):
        st["seen"] = (st["seen"] + [d["tool_use_id"]])[-300:]
    found.append((str(inp.get("command", "")), flat(d.get("tool_response"))))

    try:
        cap = max(0, int(os.environ.get("BUG_FIRST_MAX_NUDGE") or 6))
    except ValueError:
        cap = 6
    say = ""
    for cmd, text in found:
        hit = crash(cmd, text)
        key = f"{hit[0]}\x00{hit[1]}" if hit else ""
        if not hit or key in st["said"] or st["n"] >= cap or same_failures_many(text):
            continue
        # 知らせた 1 件だけを覚える。同じターンの別のクラッシュは、もう一度出たときに知らせる
        st["said"] = (st["said"] + [key])[-100:]
        st["n"] += 1
        say = f"クラッシュの出力（{hit[0]}: {hit[1][:60]}）。バグとして帳簿に書き、先に直す（bug-first の bugs.py --add）"
        break
    _bf.save(sid, "crash", st)
    if say:
        _bf.emit(str(d.get("hook_event_name") or "PostToolUse"), say)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 知らせるだけのフックなので、壊れてもツールの結果を止めない
        sys.exit(0)
