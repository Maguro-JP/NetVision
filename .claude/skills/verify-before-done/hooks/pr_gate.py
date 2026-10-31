#!/usr/bin/env python3
"""PreToolUse フック: PR の作成・マージ・main への push の前に、検査の記録とユーザーの確認が無ければ止める。

出したあとに間違いが分かると、直すのはユーザーになる。そこで、PR を作る前と、マージする前と、main に直接 push する前に、
1) precheck.py が今の HEAD で通った記録（~/.claude/state/pr-gate/<repo>.json）があること、
2) 出す内容を ❔ で見せた直後に、ユーザーの返事があること（最後の一言が、直前の返答の ❔ への返事。「違う」「やめ」「まだ」なら無し）、
を transcript と記録から確かめる。ユーザーが「確認なし」「聞かずに」と言ったときだけ、2) を要らない。
止めるもの: mcp__github__create_pull_request、mcp__github__merge_pull_request、Bash の gh pr create / gh pr merge /
git push で main か master を指すもの、gh release create / git tag / git push --tags（Release とタグは頼まれたときだけ）。

環境変数:
  PR_GATE_OFF=1        止める（テスト用）
  PR_GATE_STATE=dir    precheck の記録の場所（既定は ~/.claude/state/pr-gate）
  PR_GATE_MAX_AGE=sec  記録の有効時間（既定 7200）
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

CREATE = re.compile(r"\bgh\s+pr\s+create\b")
MERGE = re.compile(r"\bgh\s+pr\s+merge\b")
PUSH_MAIN = re.compile(r"\bgit\s+push\b(?![^\n;|&]*--delete)[^\n;|&]*\b(?:main|master)\b")
# Release とタグは頼まれたときだけ。消す・一覧は止めない
RELEASE = re.compile(r"\bgh\s+release\s+create\b|\bgit\s+tag\s+(?!-[dl]\b)\S|\bgit\s+push\b[^\n;|&]*--tags\b|\bgh\s+api\b[^\n;|&]*/releases\b(?![^\n;|&]*(?:-X\s+GET|--method\s+GET))")
ASK = re.compile(r"PR|マージ|push|出し")
REFUSE = re.compile(r"違う|ちがう|やめ|だめ|ダメ|駄目|待て|待って|まだ|しないで|するな|中止|止め|直して|直せ|戻せ|作らないで|マージしないで")
WAIVE = re.compile(r"確認なし|確認は(?:要|い)らない|聞かずに|聞かないで|そのまま(?:作って|マージ|出して)|毎回聞くな|聞くな")
SYSTEMISH = re.compile(r"^\s*<|wake reason|task-notification|SYSTEM NOTIFICATION|system-reminder|This session is being continued")
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
QMARK = "❔❓"


def text_of(c) -> str:
    if isinstance(c, str):
        return c
    return "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")


HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?\n.*?\n\1(?=\n|$)", re.S)
QUOTED = re.compile(r"\"[^\"\n]*\"|'[^'\n]*'")


def command_part(cmd: str) -> str:
    """heredoc の中身と引用の中は、コマンドではなく文章なので外す。コミットメッセージに書いた語で止めないため。"""
    return QUOTED.sub("", HEREDOC.sub("", cmd))


def action(tool: str, inp: dict):
    """(何をするか, 確認の行に出ているはずの語)。語の先頭が #番号 なら、その番号のときだけ数える。止めないなら (None, [])。"""
    if tool.endswith("create_pull_request"):
        return "PR の作成", [str(inp.get("repo", "")), str(inp.get("head", ""))]
    if tool.endswith("merge_pull_request"):
        return "マージ", [str(inp.get("repo", "")), f"#{inp.get('pullNumber', '')}"]
    if tool == "Bash":
        cmd = command_part(str(inp.get("command", "")))
        if CREATE.search(cmd):
            return "PR の作成", []
        if MERGE.search(cmd):
            return "マージ", []
        if PUSH_MAIN.search(cmd):
            return "main への push", []
        if RELEASE.search(cmd):
            return "Release やタグの作成", []
    return None, []


def approval(path: str, keys):
    """transcript を見て、最後の人の一言が、確認の ❔ への返事かを見る。
    ❔ は、その一言の直前の自分の返答にあるものだけを数える。古い ❔ への返事は、別の話のあとでは使わない。
    返り: approved / refused / no_question / waived"""
    number = next((k for k in keys if re.fullmatch(r"#\d+", k)), None)
    names = [k.lower() for k in keys if k and k != number]

    def matches(l: str) -> bool:
        nums = re.findall(r"#\d+", l)
        if number and nums:
            return number in nums
        return (not names) or any(k in l.lower() for k in names)

    blocks = []  # ("user", 文) と ("assistant", 文) を順に。ツールの結果と通知は入れない
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        c = e.get("message", {}).get("content", [])
        if e.get("type") == "user":
            if not isinstance(c, str) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                continue
            t = REMINDER.sub("", text_of(c)).strip()
            if t and not SYSTEMISH.search(t):
                blocks.append(("user", t))
        elif e.get("type") == "assistant":
            t = text_of(c)
            if t.strip():
                blocks.append(("assistant", t))
    users = [i for i, (k, _) in enumerate(blocks) if k == "user"]
    if not users:
        return "no_question"
    last = users[-1]
    last_user = blocks[last][1]
    if WAIVE.search(last_user):
        return "waived"
    prev = users[-2] if len(users) >= 2 else -1
    asked = any(l.strip() and l.strip()[0] in QMARK and ASK.search(l) and matches(l.strip())
                for k, t in blocks[prev + 1:last] if k == "assistant" for l in t.splitlines())
    if not asked:
        return "no_question"
    if REFUSE.search(last_user):
        return "refused"
    return "approved"


def repo_name(cwd: str) -> str:
    try:
        r = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10)
        url = r.stdout.strip()
        if r.returncode == 0 and url:
            return re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1]).lower()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return Path(cwd).name.lower()


def head_sha(cwd: str) -> str:
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def stamp_ok(repo: str, sha: str):
    """precheck の記録が、このリポジトリで通っているか。(真偽, 理由)"""
    root = Path(os.environ.get("PR_GATE_STATE") or Path.home() / ".claude" / "state" / "pr-gate")
    p = root / f"{repo.lower()}.json"
    if not p.exists():
        return False, "記録が無い"
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return False, "記録が読めない"
    if not st.get("ok"):
        return False, "落ちた検査がある（" + "、".join(s["name"] for s in st.get("steps", []) if not s.get("ok")) + "）"
    if time.time() - st.get("ts", 0) > int(os.environ.get("PR_GATE_MAX_AGE", "7200")):
        return False, "記録が古い"
    if sha and st.get("sha") and st["sha"] != sha:
        return False, f"記録の HEAD（{st['sha'][:7]}）が今の HEAD（{sha[:7]}）と違う"
    return True, ""


def main() -> int:
    if os.environ.get("PR_GATE_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    tool = str(d.get("tool_name", ""))
    inp = d.get("tool_input") or {}
    what, keys = action(tool, inp)
    if not what:
        return 0
    reasons = []
    path = d.get("transcript_path")
    state = approval(path, keys) if path and os.path.isfile(path) else "no_question"
    if state == "no_question":
        reasons.append(f"{what}の前にユーザーの確認が無い。出す内容（リポジトリ、ブランチ、変えたもの、HEAD）と precheck の結果を ❔ で 1 行出し、その直後の返事を待つ。返事が無いまま作らない・マージしない")
    elif state == "refused":
        reasons.append(f"{what}の確認に、ユーザーが止める返事をしている。直してから、もう一度 ❔ で確認する")
    if what not in ("マージ", "Release やタグの作成"):
        cwd = str(d.get("cwd") or os.getcwd())
        repo = str(inp.get("repo") or repo_name(cwd))
        sha = "" if inp.get("repo") else head_sha(cwd)
        ok, why = stamp_ok(repo, sha)
        if not ok:
            reasons.append(f"precheck が今の HEAD で通っていない（{why}）。`python3 .claude/skills/verify-before-done/scripts/precheck.py` を動かし、出力を ❔ に添える")
    if reasons:
        print("\n".join(reasons) + "（ユーザーの決めたこと: PR やマージの前に必ず確認。solo-pr-flow「出す前の確認」）", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
