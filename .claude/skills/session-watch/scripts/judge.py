#!/usr/bin/env python3
"""セッションの見張り。list_sessions と list_triggers の結果から、AI が管理するセッションとルーティンの、サボり・止まり・無駄を判定し、処置を並べる。

AI が作ったセッションは AI が責任を持って管理する。催促も待機もクローズも AI がして、人には聞かない・知らせない。
人が開いたセッションは人のもので、見ない（判定にも出さない）。ルーティンに紐づいたセッションは、人が開いたものでも、
ルーティンの動き（サボり・落ちた）だけは AI が起こす。ルーティンは AI が立てたもので、起こすのはルーティンが毎回していることだから。
消すのは AI が作ったものだけ。
MCP の一覧は Python から呼べないので、セッションが `list_sessions` と `list_triggers` の結果を JSON のファイルに書き、ここに渡す。

使い方: judge.py --sessions sessions.json --triggers triggers.json [--clones /home/user] [--no-fetch] [--days 3] [--json actions.json] [--now ISO]
  sessions.json: list_sessions の結果（{"ccr":{"data":[...]}} でも {"data":[...]} でも [...] でもよい）。複数ページは配列をつなげる
  triggers.json: list_triggers の結果（同じ形）。include_completed を付けたものでもよい
  --clones: リポジトリの clone の親。ルーティンのプロンプトの owner/repo から、成果（コミット）が増えているかを見る
出力: Markdown の表（標準出力）と、--json に処置の一覧
  {"archive": [...], "disable": [...], "nudge": [{"id","title","trigger","text","interrupt","why"}], "human_skipped": N}
  nudge の trigger があれば fire_trigger(text)。無ければ create_trigger（紐づけ先 = そのセッション、スケジュール無し）→ fire_trigger → 消す。
  interrupt が真なら、先に interrupt_session で止める
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

LIMIT = re.compile(r"weekly limit|rate limit|rejected|上限", re.I)
# PR を作る・マージする・main に push する確認は、ユーザーの ❔ への返事でしか進まない（pr_gate）。催促しても動かないので待つ
PR_WAIT = re.compile(r"\bPRs?\b|マージ|\bmerge\b|\bpush\b|\bmain\b|リリース|\brelease\b|タグ", re.I)
REPO = re.compile(r"\b([\w.-]+)/([\w.-]+)\b")
AI_TAGS = ("feedback-session:", "ciel:", "config:meta-mcp-own-entry")
TEXT = {
    "サボり": "前回から成果が無い（{days} 日間コミット 0）。今日の分を進める。進めない理由があれば docs/reports/pending.md に書く",
    "落ちた": "前回は落ちた（{detail}）。続きを進める",
    "答え待ち": "判断待ちで止まっている。戻せる判断はデフォルトで進め（finish-first）、戻せないものは docs/reports/pending.md に書いて次へ進む",
    "動いたまま止まっている": "{idle} 日動いたままだったので止めた。続きを進める",
}


def load(path: str):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(d, dict):
        d = d.get("ccr", d)
        d = d.get("data", d)
    return d if isinstance(d, list) else []


def when(s: str):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def ai_made(s: dict) -> bool:
    tags = s.get("tags") or []
    return bool(s.get("parent_session_id")) or s.get("origin") == "claude_code_mcp_seed" or any(t.startswith(AI_TAGS) or t in AI_TAGS for t in tags)


def repo_of(prompt: str):
    """ルーティンのプロンプトに出てくる最初の owner/repo。無ければ None。"""
    for m in REPO.finditer(prompt or ""):
        owner, name = m.group(1), m.group(2)
        if owner.lower() in ("claude", "docs", "scripts", "tests", "src", "app", ".claude", "home", "tmp") or name.endswith((".md", ".py", ".sh", ".json", ".yml")):
            continue
        if "/" in m.group(0) and not m.group(0).startswith("."):
            return name
    return None


def commits_since(clones: Path, repo: str, days: int, fetch: bool):
    """clone の中のデフォルトブランチで、days 日以内のコミット数。clone が無ければ None。"""
    if not clones or not repo:
        return None
    d = None
    for cand in clones.iterdir() if clones.exists() else []:
        if cand.name.lower() == repo.lower() and (cand / ".git").exists():
            d = cand
            break
    if d is None:
        return None
    ref = subprocess.run(["git", "-C", str(d), "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"], capture_output=True, text=True).stdout.strip() or "origin/main"
    if fetch:
        subprocess.run(["git", "-C", str(d), "fetch", "-q", "origin", ref.split("/", 1)[1]], capture_output=True, text=True)
    r = subprocess.run(["git", "-C", str(d), "log", f"--since={days} days ago", "--oneline", ref], capture_output=True, text=True)
    return len([l for l in r.stdout.splitlines() if l.strip()]) if r.returncode == 0 else None


def judge(sessions, triggers, now, clones=None, days=3, fetch=False):
    bound = {}
    for t in triggers:
        sid = t.get("persistent_session_id")
        if sid:
            bound.setdefault(sid, []).append(t)
    rows, actions = [], {"archive": [], "disable": [], "nudge": [], "human_skipped": 0}
    for s in sessions:
        sid, title = s.get("id", ""), (s.get("title") or "")[:40]
        status, bucket = s.get("session_status", ""), s.get("status_bucket", "")
        if "ARCHIVED" in status:
            continue
        enabled = [t for t in bound.get(sid, []) if t.get("enabled")]
        ai = ai_made(s)
        if not ai and not enabled:
            actions["human_skipped"] += 1  # 人が開いたセッションは人のもの。見ない・聞かない・知らせない
            continue
        upd = when(s.get("updated_at") or "")
        idle = (now - upd).days if upd else 999
        summ = s.get("post_turn_summary") or {}
        detail = " ".join(str(summ.get(k, "")) for k in ("status_detail", "needs_action", "recent_action"))[:80]
        keep = "feedback-session:keep" in (s.get("tags") or [])

        def nudge(verdict, interrupt=False):
            text = TEXT[verdict].format(days=days, detail=detail, idle=idle)
            actions["nudge"].append({"id": sid, "title": title, "trigger": enabled[0].get("id") if enabled else None,
                                     "text": text, "interrupt": interrupt, "why": verdict})
            return "催促（" + ("止めてから、" if interrupt else "") + ("ルーティンを起こす" if enabled else "1 回きりのルーティンで起こす") + "）"

        if "BLOCKED" in bucket or "REQUIRES_ACTION" in status:
            need = str(summ.get("needs_action") or detail)
            if PR_WAIT.search(need):
                verdict, why, act = "答え待ち", f"{idle} 日。PR の確認（{need[:60]}）", "待機（PR はユーザーの ❔ の返事でしか進まない。催促しない）"
            elif ai:
                verdict, why, act = "答え待ち", f"{idle} 日。{need[:60]}", nudge("答え待ち")
            else:
                verdict, why, act = "答え待ち", f"{idle} 日。{need[:60]}", "待機（人のセッション。触らない）"
        elif "FAILED" in bucket:
            if LIMIT.search(detail):
                verdict, why, act = "上限で止まった", detail, "待機（戻ったら次の実行で動く）"
            elif enabled:
                verdict, why, act = "落ちた", detail, nudge("落ちた")
            elif idle >= days and not keep:
                verdict, why, act = "無駄", f"落ちたまま {idle} 日、ルーティン無し", "消す（アーカイブ）"
                actions["archive"].append({"id": sid, "title": title, "why": why})
            else:
                verdict, why, act = "落ちた", detail, nudge("落ちた")
        elif "WORKING" in bucket or "RUNNING" in status:
            if idle >= days and ai:
                verdict, why, act = "動いたまま止まっている", f"{idle} 日 RUNNING", nudge("動いたまま止まっている", interrupt=True)
            elif idle >= 1:
                verdict, why, act = "動いたまま止まっている", f"{idle} 日 RUNNING", f"待機（{days} 日で止めて起こす）" if ai else "待機（人のセッション。止めない）"
            else:
                verdict, why, act = "動いている", detail, "無し"
        elif enabled:
            repo = next((repo_of(t.get("derived_state", {}).get("prompt") or t.get("prompt") or "") for t in enabled), None)
            n = commits_since(clones, repo, days, fetch)
            last = (enabled[0].get("last_run") or {}).get("status", "")
            if n is None:
                verdict, why, act = "分からない", f"clone 無し（{repo}）", "待機（clone を置けば次から判定できる）"
            elif n == 0 and "SUCCEEDED" in last:
                verdict, why, act = "サボり", f"ルーティンは動くが {days} 日間コミット 0（{repo}）。{detail}", nudge("サボり")
            else:
                verdict, why, act = "回っている", f"{days} 日でコミット {n}", "無し"
        elif not keep and idle >= days:
            verdict, why, act = "無駄", f"AI が立てて {idle} 日、ルーティン無し", "消す（アーカイブ）"
            actions["archive"].append({"id": sid, "title": title, "why": why})
        else:
            verdict, why, act = "待機", f"{idle} 日" + ("（keep）" if keep else ""), "無し"
        rows.append((title, status.replace("SESSION_STATUS_", ""), verdict, why, act))
    alive = {s.get("id") for s in sessions if "ARCHIVED" not in s.get("session_status", "")}
    gone_ai = {s.get("id") for s in sessions if "ARCHIVED" in s.get("session_status", "") and ai_made(s)}
    for t in triggers:
        sid = t.get("persistent_session_id")
        if t.get("enabled") and sid and sid not in alive:
            name = (t.get("name") or "")[:40]
            if sid in gone_ai:
                why = "紐づけ先は AI が立てて消したセッション"
                rows.append((name, "ルーティン", "宙に浮いたルーティン", why, "無効にする（update_trigger enabled=false）"))
                actions["disable"].append({"id": t.get("id"), "title": name, "why": why})
            else:
                rows.append((name, "ルーティン", "宙に浮いたルーティン", "紐づけ先が一覧に無い（人のセッションか、一覧の外）", "待機（触らない）"))
    order = {"サボり": 0, "宙に浮いたルーティン": 0, "落ちた": 1, "動いたまま止まっている": 2, "答え待ち": 3, "分からない": 4, "無駄": 5, "上限で止まった": 7, "回っている": 8, "動いている": 9, "待機": 10}
    rows.sort(key=lambda r: order.get(r[2], 99))
    return rows, actions


def render(rows, actions, now) -> str:
    L = [f"# セッションの見張り {now.astimezone(dt.timezone(dt.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M')}（日本時間）", "",
         "| セッション | 状態 | 判定 | 根拠 | 処置 |", "|---|---|---|---|---|"]
    for r in rows:
        L.append("| " + " | ".join(str(x).replace("|", "／") for x in r) + " |")
    waits = sum(1 for r in rows if r[4].startswith("待機"))
    stops = sum(1 for a in actions["nudge"] if a.get("interrupt"))
    L += ["", f"処置: 消す {len(actions['archive'])}、無効にする {len(actions['disable'])}、催促 {len(actions['nudge'])}（うち止めてから {stops}）、待機 {waits}。"
          f"人が開いたセッション {actions['human_skipped']} 本は見ない"]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", required=True)
    ap.add_argument("--triggers", required=True)
    ap.add_argument("--clones")
    ap.add_argument("--no-fetch", action="store_true", help="clone を取り直さない（テスト用。取り直さないと古い clone で動いているものがサボりに見える）")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--json")
    ap.add_argument("--now")
    a = ap.parse_args()
    now = when(a.now) if a.now else dt.datetime.now(dt.timezone.utc)
    rows, actions = judge(load(a.sessions), load(a.triggers), now, Path(a.clones) if a.clones else None, a.days, not a.no_fetch)
    print(render(rows, actions, now))
    if a.json:
        Path(a.json).write_text(json.dumps(actions, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
