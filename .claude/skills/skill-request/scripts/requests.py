#!/usr/bin/env python3
"""スキルの要請を、書く・集める・状態を変える。

どのセッションも、そのリポジトリの docs/reports/skill-requests.md に 1 件 1 節で要請を書けば、Ciel の skill-upkeep が週 1 回拾って作る。
要請を会話で言っても、そのセッションが閉じれば消える。ファイルに書けば、Ciel が手元の clone から集められる。

使い方:
  requests.py --add --title <題> --what <何が欲しいか> --scene <場面> --now <今どう困るか> [--example <例>] [--done <完成条件>] [--repo <path>]
      そのリポジトリの docs/reports/skill-requests.md に節を足す（状態: 未対応）
  requests.py --collect [--root <clone の親>] [--ciel <Ciel>] [--json]
      手元の全リポジトリの skill-requests.md と、Ciel の docs/requests/*.md と、origin の claude/REQ-* ブランチから、未対応と対応中を表にする
  requests.py --mark <題> --status <状態> [--repo <path>]
      その節の「状態」を書き換える（対応中（Ciel #N）、対応済み（Ciel #N）、見送り（理由））
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

JST = dt.timezone(dt.timedelta(hours=9))
FILE = "docs/reports/skill-requests.md"
HEAD = ("# スキルの要請\n\n"
        "Ciel に作ってほしい・直してほしいスキル。1 件 1 節で、Ciel の skill-upkeep が週 1 回拾う（skill-request）。\n"
        "急ぐときは Ciel のセッションに「要請を見て」と言う。状態は 未対応・対応中（Ciel #N）・対応済み（Ciel #N）・見送り（理由）。\n")
FIELDS = [("what", "何が欲しいか"), ("scene", "場面"), ("now", "今どう困るか"), ("example", "例"), ("done", "完成条件")]


def sections(text: str):
    """[(題, 本文)]。題は「## YYYY-MM-DD 題」の題。"""
    out = []
    for s in re.split(r"^(?=## )", text, flags=re.M):
        if s.startswith("## "):
            head = s.splitlines()[0][3:].strip()
            out.append((head, s))
    return out


def field(body: str, name: str) -> str:
    m = re.search(rf"^- {re.escape(name)}: *(.*)$", body, re.M)
    return m.group(1).strip() if m else ""


def add(repo: Path, title: str, values: dict) -> Path:
    p = repo / FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    s = p.read_text(encoding="utf-8") if p.exists() else HEAD
    today = dt.datetime.now(JST).strftime("%Y-%m-%d")
    lines = [f"\n## {today} {title}"] + [f"- {label}: {values.get(key, '') or '（無し）'}" for key, label in FIELDS] + ["- 状態: 未対応", ""]
    p.write_text(s.rstrip("\n") + "\n" + "\n".join(lines), encoding="utf-8")
    return p


def mark(repo: Path, title: str, status: str) -> bool:
    p = repo / FILE
    if not p.exists():
        return False
    s = p.read_text(encoding="utf-8")
    parts = re.split(r"^(?=## )", s, flags=re.M)
    hit = False
    for i, part in enumerate(parts):
        if part.startswith("## ") and title in part.splitlines()[0]:
            parts[i] = re.sub(r"^- 状態: .*$", f"- 状態: {status}", part, count=1, flags=re.M)
            hit = True
    if hit:
        p.write_text("".join(parts), encoding="utf-8")
    return hit


def open_requests(source: str, text: str):
    rows = []
    for head, body in sections(text):
        status = field(body, "状態") or "未対応"
        if status.startswith(("対応済み", "見送り")):
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(.*)", head)
        date, title = (m.group(1), m.group(2)) if m else ("", head)
        rows.append({"date": date, "source": source, "title": title, "status": status, "what": field(body, "何が欲しいか")[:60], "scene": field(body, "場面")[:60]})
    return rows


def collect(root: Path, ciel: Path, remote: bool = True):
    rows = []
    for d in sorted(root.iterdir()) if root.exists() else []:
        f = d / FILE
        if d.is_dir() and (d / ".git").exists() and f.exists():
            rows += open_requests(d.name, f.read_text(encoding="utf-8", errors="replace"))
    for f in sorted((ciel / "docs" / "requests").glob("*.md")) if (ciel / "docs" / "requests").exists() else []:
        rows += open_requests(f"Ciel/docs/requests/{f.name}", f.read_text(encoding="utf-8", errors="replace"))
    if remote:
        r = subprocess.run(["git", "-C", str(ciel), "ls-remote", "--heads", "origin", "claude/REQ-*"], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            name = line.split("\t")[-1].replace("refs/heads/", "")
            rows.append({"date": "", "source": "Ciel のブランチ", "title": name, "status": "未対応（ブランチ）", "what": "", "scene": ""})
    rows.sort(key=lambda x: x["date"])
    return rows


def render(rows) -> str:
    L = ["| 日付 | 元 | 題 | 状態 | 何が欲しいか | 場面 |", "|---|---|---|---|---|---|"]
    for r in rows:
        L.append("| " + " | ".join(str(r[k]).replace("|", "／") for k in ("date", "source", "title", "status", "what", "scene")) + " |")
    L.append(f"\n要請 {len(rows)} 件（未対応と対応中。非公開のリポジトリの名前は報告に写さない）")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", action="store_true")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--mark")
    ap.add_argument("--status")
    ap.add_argument("--title")
    for key, _ in FIELDS:
        ap.add_argument(f"--{key}")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--root")
    ap.add_argument("--ciel", default=str(Path(__file__).resolve().parents[4]))
    ap.add_argument("--no-remote", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.add:
        if not (a.title and a.what and a.scene and a.now):
            print("--title --what --scene --now は要る", file=sys.stderr)
            return 2
        p = add(Path(a.repo), a.title, {k: getattr(a, k) for k, _ in FIELDS})
        print(f"書いた: {p}（状態: 未対応）。この作業の PR に入れる")
        return 0
    if a.mark:
        if not a.status:
            print("--status は要る", file=sys.stderr)
            return 2
        ok = mark(Path(a.repo), a.mark, a.status)
        print("書き換えた" if ok else "その題の節が無い")
        return 0 if ok else 1
    if a.collect:
        ciel = Path(a.ciel)
        rows = collect(Path(a.root) if a.root else ciel.parent, ciel, remote=not a.no_remote)
        print(json.dumps(rows, ensure_ascii=False, indent=1) if a.json else render(rows))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
