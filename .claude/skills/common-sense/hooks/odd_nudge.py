#!/usr/bin/env python3
"""PostToolUse と PostToolUseFailure のフック（Bash と MCP）: 同じ失敗が並んだとき・繰り返したときに、原因を先に調べるよう 1 行で知らせる。

ユーザーの指摘: おかしいことをおかしいと思わない。
2 つを見る。止めはしない。
  1. 1 回の出力に、同じ失敗の行が 3 回以上ある（配った先の全部で同じテストが落ちた、など）。個別に直す前に、共通の原因を調べる
  2. 同じ呼び出しが、同じ失敗で 2 回目（同じフックに同じ理由で 2 回止められた、も含む）。やり方を変える前に、原因を調べる

PostToolUse は成功したツールでしか動かない（公式の文書と、実際の記録で確かめた）。失敗を見る道は 2 つ。
  - PostToolUseFailure に登録されていれば、失敗したその場で見る（入力の error）
  - 登録が無いリポジトリと、フックや権限に止められた呼び出し（どちらのフックも動かない）は、次に成功した呼び出しのときに、
    会話の記録の後ろから失敗した結果を拾う。同じ結果を 2 回数えないよう、見た呼び出しの ID を覚える

token-efficiency の trajectory_guard.py は「通った検査の再実行」（無駄）を見る。こちらは失敗だけを見るので、重ならない。

環境変数:
  COMMON_SENSE_OFF=1          止める
  COMMON_SENSE_MAX_NUDGE=N    1 セッションで知らせる回数の上限（既定 8）
  COMMON_SENSE_STATE=dir      数えた失敗の置き場
"""

import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cs  # noqa: E402

SAME = 3          # 1 回の出力で、同じ失敗の行が何回あれば知らせるか
TAIL = 256 << 10  # 失敗した結果を拾うときに読む、記録の後ろの大きさ
FAIL = re.compile(
    r"\b(?:error|errors|failed|failure|fatal|exception|denied|refused|rejected|timed out|not found|no such file|cannot|unable to|panic)\b"
    r"|(?-i:\b\w+(?:Error|Exception)\b)|\bFAIL\b|\bNG\b|✗|✘|❌|失敗|エラー|落ちた|落ちました|誤り|見つかりません|できません", re.I)
# ソースコードの行（ファイルの中身や差分を出したとき）。失敗の語を含んでいても、失敗の出力ではない
CODE = re.compile(r"^\s*(?:\d+[:\t|]\s*)?[+-]?\s*(?:except\b|raise\b|catch\b|throw\b|def\b|class\b|if\b|elif\b|else\b|return\b|import\b|from\b|try\b|with\b"
                  r"|assert\b|print\(|#|//|/\*|\*\s|\"\"\"|\'\'\')")
# 失敗が 0 件という行は、失敗ではない
ZERO = re.compile(r"\b(?:0|no)\s+(?:errors?|failures?|failed|warnings?)\b|(?:errors?|failures?|failed|誤り|失敗|重大|注意|エラー)\s*[:=]?\s*0\b|\b0\s*件", re.I)
# 数えた結果（"うち失敗": 2、失敗 2 件、failed: 2、failures=1）。何が失敗したかを言わないので、同じ失敗かを比べられない。
# この部分を除いても失敗の語が残る行だけを、失敗の行に数える。単数の Error: 500 は、数ではなく失敗の中身
COUNTED = re.compile(r"(?:失敗|エラー|誤り)[\"'」]?\s*[:：=]?\s*\d+\s*(?:件|個|本|回)?(?![\w.])|\b(?:errors|failures|failed)[\"']?\s*[:=]\s*\d+(?![\w.])", re.I)
ERR_KIND = re.compile(r"\b\w*(?:error|exception)\b\s*[:：]\s*.{5,}$", re.I)
# 探した結果の行は、失敗の語を含んでいても失敗ではない。探すコマンドかどうかは is_search で決める
SEARCH_CMD = re.compile(r"^(?:grep|egrep|fgrep|rg|ag|ack|git\s+grep)\b")
# 出力を出さないコマンド（場所や変数を決めるだけ）。探すコマンドの前に並んでいても、出力は探した結果のまま
QUIET_CMD = re.compile(r"^(?:cd|pushd|popd|export|set|unset|shopt|source|\.|:|true)\b|^\w+=\S*$")
# 中身や一覧を次に渡すだけのコマンド。パイプの中で探すコマンドの前後にあっても、全体は探すコマンドのまま
FEED_CMD = re.compile(r"^(?:cat|tac|nl|head|tail|sed|find|fd|ls|sort|uniq|cut|tr|wc|echo|printf|git\s+(?:ls-files|ls-tree|show|log|diff|blame))\b")
XARGS = re.compile(r"^xargs\b(?:\s+-\S+(?:\s+(?:\d+|\{\}))?)*\s*")  # xargs は、後ろのコマンドで決める（xargs grep は探す、xargs python3 は実行）
# ソースコードのファイルの中身を出すコマンド（sed -n 1,80p x.py）。出力はファイルの中身で、実行の結果ではない。
# ログと記録（.log、.txt、.json、.md、拡張子なし）は実行の結果を書いた物なので入れない
SHOW_CMD = re.compile(r"^(?:cat|nl|head|tail|sed\s+-n)\b")
SOURCE_FILE = re.compile(r"\.(?:py|pyi|js|mjs|cjs|ts|tsx|jsx|sh|bash|html|css|rs|go|java|rb|c|h|cpp)$", re.I)
QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
BLOCKED = re.compile(r"hook error|PreToolUse[^\n]{0,60}(?:blocked|denied|returned)|blocking hook", re.I)
# 人が止めた、権限で断られた、は調べる失敗ではない（権限は follow-instructions の手順で扱う）
SKIP = re.compile(r"^\s*(?:Permission |\[Request interrupted|The user doesn't want|<tool_use_error>)", re.I)


def flat(x, limit: int = 200_000) -> str:
    """ツールの結果から文だけを集める。Bash は stdout と stderr（差分などほかの欄は見ない）、MCP は text の列で、形はツールごとに違う。"""
    if isinstance(x, dict) and "stdout" in x:
        return f"{x.get('stdout') or ''}\n{x.get('stderr') or ''}"[:limit]
    out, n = [], 0
    stack = [x]
    while stack and n < limit:
        v = stack.pop()
        if isinstance(v, str):
            out.append(v)
            n += len(v)
        elif isinstance(v, dict):
            stack.extend(v.get(k) for k in reversed(list(v)) if k not in ("originalFile", "structuredPatch", "base64"))
        elif isinstance(v, list):
            stack.extend(reversed(v))
    return "\n".join(out)[:limit]


def norm(line: str) -> str:
    """失敗の行から、件ごとに違う所（数、ハッシュ、パス、引用された名前）を落とす。"""
    s = line.strip().lower()
    s = re.sub(r"\b[0-9a-f]{7,40}\b", "H", s)
    s = re.sub(r"\d+", "N", s)
    s = re.sub(r"(?:~|\.{1,2})?/[\w./@+-]+", "P", s)
    s = re.sub(r"'[^']*'|\"[^\"]*\"|`[^`]*`|「[^」]*」", "Q", s)
    return re.sub(r"\s+", " ", s)[:160]


def signatures(line: str) -> set:
    """1 行から、同じ失敗かを比べる形をいくつか作る。先頭の名前（リポジトリ、ファイル）が違っても同じ失敗と数えるため。"""
    s = norm(line)
    out = {s}
    m = re.match(r"^(?:[\[(][^\])]*[\])]\s*|\S+[:：]\s+)(.+)$", s)
    if m and FAIL.search(m.group(1)):
        out.add(m.group(1))
    k = ERR_KIND.search(s)
    if k:
        out.add(k.group(0))
    return {x for x in out if len(x) >= 8}


def is_failure_line(line: str) -> bool:
    """失敗の行か。失敗が 0 件の行、ソースコードの行、数えた結果だけの行は、失敗の行ではない。"""
    if len(line) > 400 or ZERO.search(line) or CODE.match(line):
        return False
    return bool(FAIL.search(COUNTED.sub(" ", line)))


def is_search(command: str) -> bool:
    """出力が、探した結果かファイルの中身だけのコマンドか。
    cd や変数の代入を前に並べた形（cd x && grep …）、中身や一覧をパイプで渡す形（cat x | grep …、git ls-files | xargs grep …）、
    ソースコードのファイルの中身を出す形（sed -n 1,80p x.py）も、探すコマンドと見る。
    実行の出力を grep で絞った形（pytest | grep FAILED）、ログを読む形（tail x.log）、実行が 1 つでも混ざる形は、実行の結果なので、探すコマンドと見ない。"""
    bare = QUOTED.sub("Q", command)  # 引用の中の区切り（grep -E "a|b"）で切らない
    if re.search(r"<<|\$\(|`|\b(?:for|while|if|do|then)\b", bare):
        return False  # heredoc、埋め込み、繰り返しの中は読み切れない。実行の結果として見る
    found = False
    for seg in re.split(r"&&|\|\||;|\n", bare):
        stages = [re.sub(r"^(?:\w+=\S*\s+)+", "", s.strip()) for s in seg.split("|")]
        stages = [s for s in stages if s]
        if not stages or QUIET_CMD.match(stages[0]):
            continue
        kinds = []
        for s in stages:
            s = XARGS.sub("", s) or "echo"
            kinds.append("search" if SEARCH_CMD.match(s) else "feed" if FEED_CMD.match(s) else "run")
        if "run" in kinds:
            return False  # 実行が 1 つでも混ざれば、出力は実行の結果
        if "search" in kinds:
            found = True
            continue
        files = [t for t in stages[0].split()[1:] if not t.startswith("-") and "." in t.rsplit("/", 1)[-1]]
        if SHOW_CMD.match(stages[0]) and files and all(SOURCE_FILE.search(t) for t in files):
            found = True
            continue
        return False  # ログや一覧を読むだけの形は、実行の結果を読んでいる
    return found


def same_failures(text: str):
    """同じ失敗の行がいちばん多い形と、その数。"""
    count = Counter()
    for line in text.splitlines()[:3000]:
        if not is_failure_line(line):
            continue
        count.update(signatures(line))
    if not count:
        return "", 0
    sig, n = max(count.items(), key=lambda kv: (kv[1], len(kv[0])))
    return sig, n


def failure_sig(text: str) -> str:
    """失敗の中身を 1 行にする。最初の失敗の行、無ければ最後の行。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines and re.match(r"\s*Exit code \d+", lines[0]):
        lines = lines[1:]
    for ln in lines[:200]:
        if is_failure_line(ln):
            return norm(ln)
    return norm(lines[-1]) if lines else ""


def call_key(name: str, inp: dict) -> str:
    return re.sub(r"\s+", " ", _cs.call_text({"name": name, "input": inp})).strip()[:300]


def record(st: dict, name: str, inp: dict, text: str, uid) -> str:
    """失敗を 1 件数える。2 回目に達したら知らせる文を返す。"""
    if uid:
        if uid in st["seen"]:
            return ""
        st["seen"] = (st["seen"] + [uid])[-300:]
    if SKIP.match(text):
        return ""
    sig = failure_sig(text)
    if not sig:
        return ""
    blocked = bool(BLOCKED.search(text[:300]))
    # 止められたときは、入力が少し違っても同じ理由なら同じ失敗（言い方を変えて同じフックに当たり続ける形）
    key = f"{name}\x00{sig}" if blocked else f"{call_key(name, inp)}\x00{sig}"
    n = st["fails"].get(key, 0) + 1
    st["fails"][key] = n
    if len(st["fails"]) > 200:
        st["fails"] = dict(list(st["fails"].items())[-150:])
    if n != 2:
        return ""
    if blocked:
        return f"同じ理由で止められた 2 回目（{sig[:60]}）。もう一度試す前に、何が止めたか（フックの名前、読み取った文、権限の名前）を調べる（common-sense）"
    return f"同じ失敗の 2 回目（{sig[:60]}）。やり方を変える前に原因を調べる。エラーの文を最後まで読み、手元で再現し、直前に変えた所を見る（common-sense）"


def catch_up(st: dict, path: str) -> list:
    """記録の後ろから、まだ数えていない失敗した結果（Bash と MCP）を拾う。"""
    try:
        lines, _ = _cs._lines(path, TAIL)
    except OSError:
        return []
    out = []
    for turn in _cs.parse(lines):
        for c in turn["calls"]:
            r = turn["results"].get(c["id"])
            if not r or not c["id"] or c["id"] in st["seen"]:
                continue
            if not (c["name"] == "Bash" or c["name"].startswith("mcp__")):
                continue
            if r["error"]:  # 止められた呼び出しも is_error が付く（実際の記録で確かめた）。文だけでは決めない
                out.append((c["name"], c["input"], r["text"], c["id"]))
    return out


def main() -> int:
    if _cs.off():
        return 0
    d = _cs.read_event()
    name = str(d.get("tool_name") or "")
    if not (name == "Bash" or name.startswith("mcp__")):
        return 0
    event = str(d.get("hook_event_name") or "PostToolUse")
    inp = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    sid = d.get("session_id")
    st = _cs.load(sid, "odd")
    st = {"fails": st.get("fails") or {}, "seen": st.get("seen") or [], "said": st.get("said") or [], "n": int(st.get("n") or 0)}
    messages = []

    if event == "PostToolUseFailure":
        text = str(d.get("error") or "")
        if not d.get("is_interrupt"):
            messages.append(record(st, name, inp, text, d.get("tool_use_id")))
    else:
        text = flat(d.get("tool_response"))
        if d.get("tool_use_id"):
            st["seen"] = (st["seen"] + [d["tool_use_id"]])[-300:]  # 成功した呼び出しは、あとで失敗として拾わない
        path = d.get("transcript_path")
        if path and os.path.isfile(path):
            for n_, i_, t_, u_ in catch_up(st, path):
                messages.append(record(st, n_, i_, t_, u_))
                sig, n = same_failures(t_)
                if n >= SAME and sig not in st["said"]:
                    st["said"] = (st["said"] + [sig])[-100:]
                    messages.append(f"同じ失敗が {n} 件（{sig[:60]}）。個別に直さず、共通の原因を先に調べる（common-sense）")

    if not (name == "Bash" and is_search(str(inp.get("command", "")))):
        sig, n = same_failures(text)
        if n >= SAME and sig not in st["said"]:
            st["said"] = (st["said"] + [sig])[-100:]
            messages.insert(0, f"同じ失敗が {n} 件（{sig[:60]}）。個別に直さず、共通の原因を先に調べる（common-sense）")

    messages = [m for m in messages if m]
    try:
        cap = max(0, int(os.environ.get("COMMON_SENSE_MAX_NUDGE") or 8))
    except ValueError:
        cap = 8
    say = messages[0] if messages and st["n"] < cap else ""
    if say:
        st["n"] += 1
    _cs.save(sid, "odd", st)
    if say:
        _cs.emit(event, say)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 知らせるだけのフックなので、壊れてもツールの結果を止めない
        sys.exit(0)
