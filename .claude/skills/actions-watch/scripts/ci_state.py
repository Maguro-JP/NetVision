#!/usr/bin/env python3
"""GitHub Actions の通知を、会話の記録（transcript）から読み取る部品。

ユーザーの決まり: GitHub Actions は必ず通知させ、通知を受け取る。終わっていないのに確認することも、終わっているのに
確認しないことも無くす。そのために、フックが「どの PR の、どの head の Actions が、終わったか・落ちたか」を
会話の記録から機械で読む。通知は webhook の文（kind="check_suite.completed" と "head_sha"、"pr"）として
ユーザーの発言か ReadNotifications の結果に入るので、両方を同じ形で拾う。

使い方: ci_state.py <transcript.jsonl> [--pr Owner/Repo#N]   読み取った通知の一覧と、PR ごとの最後の状態を出す
"""

import json
import re
import sys

# 通知の中の目印。kind のあとに head_sha と pr が続く（間に説明の文が挟まる）
# 記録の中では引用符が \" に化け、json.dumps の形で : のあとに空白が入ることもあるので、どちらも受ける
Q = r'\\?"'
EVENT = re.compile(r'kind=' + Q + r'(?P<kind>[a-z_]+\.[a-z_]+)' + Q + r'(?P<body>.{0,2500}?)' + Q + r'head_sha' + Q + r':\s*' + Q + r'(?P<sha>[0-9a-f]{7,40})' + Q
                   + r'(?P<tail>.{0,300}?)' + Q + r'pr' + Q + r':\s*' + Q + r'(?P<pr>[\w.-]+/[\w.-]+#\d+)' + Q, re.S)
CONCLUSION = re.compile(Q + r'conclusion' + Q + r':\s*' + Q + r'(?P<c>[a-z_]+)' + Q)
FAILED = {"failure", "timed_out", "action_required", "startup_failure", "stale"}
DONE_KINDS = {"check_suite.completed", "workflow_run.completed"}
PUSH = re.compile(r"\bgit\s+push\b|\bgh\s+pr\s+create\b")
# PreToolUse のフックに止められた呼び出しの結果。is_error が付かない形でも文に出る
BLOCKED_RESULT = re.compile(r"hook error|PreToolUse[^\n]{0,40}(?:blocked|denied|returned)|blocking hook", re.I)
PUSH_TOOLS = ("push_files", "create_or_update_file", "create_pull_request", "update_pull_request_branch", "merge_pull_request")


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for x in content or []:
        if isinstance(x, dict):
            if x.get("type") == "text":
                parts.append(str(x.get("text", "")))
            elif x.get("type") == "tool_result":
                inner = x.get("content")
                parts.append(inner if isinstance(inner, str) else text_of(inner))
    return "\n".join(parts)


def failed_result(block: dict) -> bool:
    """tool_result が失敗か。エラーの印か、フックに止められた文なら失敗。"""
    return bool(block.get("is_error")) or bool(BLOCKED_RESULT.search(text_of([block])[:2000]))


def status_of(kind: str, block: str) -> str:
    """通知の 1 件を、通った・落ちた・動いている のどれかにする。"""
    m = CONCLUSION.search(block)
    c = m.group("c") if m else ""
    if c in FAILED:
        return "落ちた"
    if kind in DONE_KINDS:
        return "落ちた" if c in FAILED else "通った"
    if kind.endswith(".completed") and c in FAILED:
        return "落ちた"
    return "動いている"


def read(path: str) -> dict:
    """記録を 1 回だけ読み、(通知の列, 行ごとの出来事の列) を返す。

    出来事は、PR を作る・push する呼び出し（その後の Actions は古い）と、人の発言の位置（このターンの判定に使う）。
    止められた呼び出し（結果がエラー、フックの差し戻し）は、PR も push も起きていないので出来事に入れない。
    結果がまだ無い呼び出しは、起きたものとして残す。
    """
    events, acts, users = [], [], []
    failed_ids = set()
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if "head_sha" in line:
                for m in EVENT.finditer(line):
                    block = line[m.start():m.end() + 400]  # conclusion は pr の前にも後にも来る
                    events.append({"line": i, "kind": m.group("kind"), "sha": m.group("sha"), "pr": m.group("pr"), "status": status_of(m.group("kind"), block)})
            if '"type":"assistant"' in line or '"type": "assistant"' in line or '"tool_use"' in line:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") != "assistant":
                    continue
                for x in e.get("message", {}).get("content", []) or []:
                    if isinstance(x, dict) and x.get("type") == "tool_use":
                        name = str(x.get("name", ""))
                        inp = x.get("input") or {}
                        cmd = str(inp.get("command", ""))
                        if any(t in name for t in PUSH_TOOLS) or (name == "Bash" and PUSH.search(cmd)):
                            acts.append({"id": x.get("id"), "line": i, "tool": name, "cmd": cmd[:200], "pr": f"{inp.get('owner', '')}/{inp.get('repo', '')}#{inp.get('pullNumber') or inp.get('pull_number') or ''}"})
            elif '"type":"user"' in line or '"type": "user"' in line:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") != "user":
                    continue
                c = e.get("message", {}).get("content", [])
                is_result = not isinstance(c, str) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c)
                if is_result:
                    failed_ids.update(x.get("tool_use_id") for x in c if isinstance(x, dict) and x.get("type") == "tool_result" and failed_result(x))
                users.append({"line": i, "result": is_result, "text": text_of(c)[:4000]})
    acts = [a for a in acts if not (a["id"] and a["id"] in failed_ids)]
    return {"events": events, "acts": acts, "users": users}


def latest(state: dict, pr_number: int, repo: str = "") -> dict:
    """その PR の最後の通知と、その後に push や作成があったか。無ければ空。"""
    evs = [e for e in state["events"] if e["pr"].endswith(f"#{pr_number}") and (not repo or e["pr"].lower().startswith(repo.lower()))]
    if not evs:
        return {}
    last = evs[-1]
    stale = any(a["line"] > last["line"] and (a["pr"].endswith(f"#{pr_number}") or a["tool"] == "Bash") for a in state["acts"])
    return {**last, "stale": stale}


def current_turn(state: dict) -> dict:
    """最後の人の発言（ツールの結果ではない物）から後の範囲。"""
    starts = [u for u in state["users"] if not u["result"]]
    if not starts:
        return {"line": -1, "text": ""}
    return starts[-1]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    st = read(sys.argv[1])
    for e in st["events"]:
        print(f"  行 {e['line']}  {e['pr']}  {e['sha'][:7]}  {e['kind']}  {e['status']}")
    if "--pr" in sys.argv:
        pr = sys.argv[sys.argv.index("--pr") + 1]
        n = int(pr.rsplit("#", 1)[-1])
        print(latest(st, n, pr.rsplit("#", 1)[0]) or "通知なし")
    print(f"通知 {len(st['events'])} 件 / push と作成 {len(st['acts'])} 回")
    return 0


if __name__ == "__main__":
    sys.exit(main())
