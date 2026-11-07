#!/usr/bin/env python3
"""skill-upkeep の観察を 1 本で集める。手元にある各リポジトリの、この 1 週間のコミット・PR・lessons・判断待ちを表にする。

毎週の観察を手で組むと、コマンドが 10 本を超えて抜けが出る。clone が手元にあるリポジトリ（Routine の紐づけ先）を順に見て、
デフォルトブランチを fetch し、期間内のコミット数、PR のマージ数、DEV と SYNC と FB の数、Ciel に無い lessons の題、
答えの無い判断待ちの数を 1 行ずつ出す。Ciel 自身の LESSONS.md に無い題は、教訓の収穫の候補になる。
報告に書くときは、非公開のリポジトリの名前を伏せる（この出力は手元で読むだけ）。

使い方: observe.py [--root <clone の親>] [--ciel <Ciel の場所>] [--since <YYYY-MM-DD>] [--days N]
  既定: root は Ciel の親、since は 7 日前（JST の 0 時）。
"""

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

JST = dt.timezone(dt.timedelta(hours=9))


def git(repo: Path, *args, check=False):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.stdout


def default_branch(repo: Path) -> str:
    ref = git(repo, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD").strip()
    if ref:
        return ref.split("/", 1)[1]
    for b in ("main", "master", "develop"):
        if git(repo, "rev-parse", "-q", "--verify", f"origin/{b}").strip():
            return b
    return "main"


def heads(text: str):
    return re.findall(r"^## (.+)$", text, re.M)


def observe(repo: Path, since: str, ciel_heads: set, fetch: bool = True) -> dict:
    branch = default_branch(repo)
    if fetch:
        subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin", branch], capture_output=True, text=True)
    ref = f"origin/{branch}"
    subjects = [s for s in git(repo, "log", f"--since={since}", "--format=%s", ref).splitlines() if s.strip()]
    kinds = {k: sum(1 for s in subjects if s.startswith(k)) for k in ("DEV", "SYNC", "FB", "FIX")}
    merges = sum(1 for s in subjects if re.search(r"\(#\d+\)$|^Merge pull request", s))
    lessons = git(repo, "show", f"{ref}:.claude/skills/lessons/LESSONS.md")
    extra = [h for h in heads(lessons) if h not in ciel_heads] if lessons else []
    pending = git(repo, "show", f"{ref}:docs/reports/pending.md")
    requests = git(repo, "show", f"{ref}:docs/reports/skill-requests.md")
    requests_open = sum(1 for s in re.split(r"^(?=## )", requests, flags=re.M) if s.startswith("## ") and not re.search(r"^- 状態: (?:対応済み|見送り)", s, re.M))
    sections = [s for s in re.split(r"^(?=## )", pending, flags=re.M) if s.startswith("## ")]
    open_items = [s for s in sections if not re.search(r"^- 答え|答え[:：（(]|反映済み", s, re.M)]
    return {"repo": repo.name, "branch": branch, "commits": len(subjects), "merges": merges, **kinds,
            "lessons_extra": extra, "pending_open": len(open_items), "requests_open": requests_open}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    ap.add_argument("--ciel", default=str(Path(__file__).resolve().parents[4]))
    ap.add_argument("--since")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    ciel = Path(a.ciel)
    root = Path(a.root) if a.root else ciel.parent
    if not a.since:
        a.since = (dt.datetime.now(JST) - dt.timedelta(days=a.days)).strftime("%Y-%m-%dT00:00:00+09:00")
    ciel_heads = set(heads((ciel / ".claude/skills/lessons/LESSONS.md").read_text(encoding="utf-8"))) if (ciel / ".claude/skills/lessons/LESSONS.md").exists() else set()
    repos = sorted(p for p in root.iterdir() if (p / ".git").exists() and p.resolve() != ciel.resolve())
    print(f"since {a.since}  リポジトリ {len(repos)}")
    print("| リポジトリ | ブランチ | コミット | PR マージ | DEV | SYNC | FB | Ciel に無い lessons | 判断待ち |")
    print("|---|---|---|---|---|---|---|---|---|")
    rows = []
    for r in repos:
        try:
            o = observe(r, a.since, ciel_heads, fetch=not a.no_fetch)
        except Exception as e:  # 1 つの clone が壊れていても残りは見る
            print(f"| {r.name} | (読めない: {str(e)[:40]}) |")
            continue
        rows.append(o)
        print(f"| {o['repo']} | {o['branch']} | {o['commits']} | {o['merges']} | {o['DEV']} | {o['SYNC']} | {o['FB']} | {len(o['lessons_extra'])} | {o['pending_open']} |")
    print()
    print("Ciel に無い lessons の題（教訓の収穫の候補。同じ題は 1 回だけ）:")
    seen = set()
    for o in rows:
        for h in o["lessons_extra"]:
            if h not in seen:
                seen.add(h)
                print(f"  - [{o['repo']}] {h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
