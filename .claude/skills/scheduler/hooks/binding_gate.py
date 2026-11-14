#!/usr/bin/env python3
"""Stop フック: 継続開発のルーティンの紐づけ先が、AI が新しく作ったセッションに移るのを止める。

ユーザーの決まり: long-dev のルーティンは、ユーザーが使っているセッションに紐づける。新しいセッションに移さない。
ユーザーは自分のセッションで様子を見て指示を出す。紐づけ先が別のセッションに移ると、そこで開発が進み、
元のセッションには何も起きず、一覧にはセッションが増える。
このターンの create_trigger のうち、継続開発（long-dev、auto-dev、継続開発、日次・週次の開発）の物について、次を止める。
  1. create_new_session_on_fire が真（実行のたびに新しいセッションができる）
  2. persistent_session_id が、この会話の中で create_session で作ったセッション（今作ったセッションに紐づけている）
  3. AI が親から分けて作った子のセッションの中で、継続開発のルーティンを作っている（子は終わると消されるので、紐づけが宙に浮く）
persistent_session_id を付けなければ、今のセッションに紐づく。ユーザーが使っている別のセッションに紐づけるのは止めない。

環境変数:
  SCHEDULER_BINDING_OFF=1   止める
新しいセッションに紐づけるのは、ユーザーが新しいセッションでと言ったときと、ユーザーのセッションが無いときだけ。
そのときは「❕ 紐づけ: 新しいセッション（ユーザーの指示）」か「❕ 紐づけ: 新しいセッション（ユーザーのセッションが無い）」を 1 行書けば通る。
"""

import json
import os
import re
import sys

# 名前と、プロンプトの出だしだけを見る。見回りのプロンプトが本文で long-dev に触れても、継続開発とは見ない
DEV = re.compile(r"long-dev|auto-dev|継続開発|長期開発|自動開発|自律開発|日次開発|週次の?開発|毎日の?開発", re.I)
HEAD = 120
ESCAPE = re.compile(r"^[❕!]\s*紐づけ[:：][^\n]*新しいセッション[^\n]*(?:ユーザーの指示|ユーザーのセッションが(?:無|な)い)", re.M)
# 親が子に渡すプロンプトの出だし（feedback-session の fanout.py）。最初の発言がこれなら、AI が作った子のセッション
CHILD = re.compile(r"親のセッションから分けた")
SESSION_ID = re.compile(r"session_[0-9A-Za-z]{10,}")


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for x in content or []:
        if isinstance(x, dict):
            if x.get("type") == "text":
                out.append(str(x.get("text", "")))
            elif x.get("type") == "tool_result":
                inner = x.get("content")
                out.append(inner if isinstance(inner, str) else text_of(inner))
    return "\n".join(out)


def is_real_user(content) -> bool:
    if not isinstance(content, str) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content or []):
        return False
    t = text_of(content).lstrip()
    return not (t.startswith("Stop hook feedback") or "<task-notification>" in t[:600])


def scan(path: str) -> dict:
    """会話の記録から、作ったセッションの ID（全体）と、このターンの create_trigger と自分の文を集める。"""
    made, pending, triggers, texts, first, failed = set(), {}, [], [], None, set()
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"user"' not in line and '"assistant"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            c = e.get("message", {}).get("content", [])
            if e.get("type") == "user":
                if is_real_user(c):
                    if first is None:
                        first = text_of(c)[:2000]
                    triggers, texts = [], []
                    continue
                for x in c if not isinstance(c, str) else []:
                    if isinstance(x, dict) and x.get("type") == "tool_result" and x.get("is_error"):
                        failed.add(x.get("tool_use_id"))  # 止められた・失敗した作成は、ルーティンができていないので数えない
                    if isinstance(x, dict) and x.get("type") == "tool_result" and x.get("tool_use_id") in pending and not x.get("is_error"):
                        m = SESSION_ID.search(text_of([x]))
                        if m:
                            made.add(m.group(0))  # create_session の結果の、最初の ID が作ったセッション
                continue
            if e.get("type") != "assistant":
                continue
            t = text_of(c)
            if t.strip():
                texts.append(t)
            for x in c if not isinstance(c, str) else []:
                if not (isinstance(x, dict) and x.get("type") == "tool_use"):
                    continue
                name, inp = str(x.get("name", "")), x.get("input") or {}
                if name.endswith("create_session"):
                    pending[x.get("id")] = True
                elif name.endswith("create_trigger"):
                    triggers.append((x.get("id"), inp))
    triggers = [inp for tid, inp in triggers if tid not in failed]
    return {"made": made, "triggers": triggers, "text": "\n".join(texts), "child": bool(first and CHILD.search(first))}


def problems(st: dict) -> list:
    out = []
    for inp in st["triggers"]:
        label = f"{inp.get('name', '')}\n{str(inp.get('prompt', ''))[:HEAD]}"
        if not DEV.search(label):
            continue
        name = str(inp.get("name", ""))[:30]
        if st.get("child"):
            out.append(f"子のセッションの中で、継続開発のルーティン「{name}」を作っている。子は終わると消され、紐づけが宙に浮く")
        elif inp.get("create_new_session_on_fire"):
            out.append(f"継続開発のルーティン「{name}」が、実行のたびに新しいセッションを作る形になっている")
        elif inp.get("persistent_session_id") and inp["persistent_session_id"] in st["made"]:
            out.append(f"継続開発のルーティン「{name}」を、今作った新しいセッションに紐づけている")
    return out


def main() -> int:
    if os.environ.get("SCHEDULER_BINDING_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    if d.get("stop_hook_active"):
        return 0
    path = d.get("transcript_path")
    if not path or not os.path.isfile(path):
        return 0
    try:
        st = scan(path)
    except OSError:
        return 0
    bad = problems(st)
    if not bad:
        return 0
    if st.get("child"):
        print(bad[0] + "。作ったルーティンを無効にし、親への報告に書く。ルーティンは、ユーザーが使っているセッションで作る（scheduler）", file=sys.stderr)
        return 2
    if ESCAPE.search(st["text"]):
        return 0
    print(bad[0] + "。今のセッション（persistent_session_id を付けない）か、ユーザーが使っている既存のセッションに紐づけ直し、作ったルーティンは無効にする。"
          "ユーザーが新しいセッションでと言ったなら「❕ 紐づけ: 新しいセッション（ユーザーの指示）」を 1 行書く（scheduler）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
