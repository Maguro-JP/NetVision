#!/usr/bin/env python3
"""月に 1 回の整理整頓で片付けるものを集めて、Markdown で出す。消すのは人（か、この出力を読んだ Claude）が行う。

集めるもの: デフォルトブランチに入った（消せる）リモートブランチ、開いている PR のブランチ（残す）、
リポジトリに入っている余計なファイル（キャッシュ、OS のごみ、ログ、大きなファイル）、空のディレクトリ、
docs の検査（docs-upkeep があれば）、30 日より古い判断待ち、14 日より古い開いている PR、90 日より古い開いている Issue。

使い方: tidy_report.py [--days-pr 14] [--days-pending 30] [--days-issue 90]   リポジトリの root で
"""

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JUNK = re.compile(r"(^|/)(__pycache__|\.DS_Store|Thumbs\.db|\.pytest_cache|\.mypy_cache|node_modules)(/|$)|\.(pyc|pyo|orig|rej|log|tmp|swp)$")
BIG = 5 * 1024 * 1024


def sh(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def arg(name, default):
    a = sys.argv[1:]
    return int(a[a.index(name) + 1]) if name in a and a.index(name) + 1 < len(a) else default


def main() -> int:
    now = datetime.now(timezone.utc)
    code, ref = sh(["git", "symbolic-ref", "-q", "refs/remotes/origin/HEAD"])
    default = ref.replace("refs/remotes/origin/", "") if code == 0 and ref else "main"
    sh(["git", "fetch", "-q", "--prune", "origin"])
    out = [f"## 整理整頓（{now.astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d')} JST）", ""]

    # 開いている PR（gh が使えるとき）
    open_prs = []
    code, js = sh(["gh", "pr", "list", "--state", "open", "--limit", "100", "--json", "number,headRefName,createdAt,title,author,isDraft"])
    if code == 0 and js:
        try:
            open_prs = json.loads(js)
        except ValueError:
            open_prs = []
    pr_heads = {p.get("headRefName") for p in open_prs}

    # リモートブランチ
    code, merged = sh(["git", "branch", "-r", "--merged", f"origin/{default}"])
    code2, allb = sh(["git", "branch", "-r"])
    merged_names = [b.strip() for b in merged.splitlines() if b.strip() and "->" not in b]
    all_names = [b.strip() for b in allb.splitlines() if b.strip() and "->" not in b]
    deletable = [b[len("origin/"):] for b in merged_names if b != f"origin/{default}" and b[len("origin/"):] not in pr_heads]
    keep = [b[len("origin/"):] for b in all_names if b != f"origin/{default}" and b[len("origin/"):] not in deletable]
    out.append("### リモートブランチ")
    out.append(f"- 消せる（{default} に入っている。開いている PR は無い）: " + ("、".join(f"`{b}`" for b in deletable) if deletable else "無し"))
    out.append(f"- 残す（未マージか、開いている PR がある）: " + ("、".join(f"`{b}`" for b in keep) if keep else "無し"))
    out.append("")

    # 余計なファイル、大きなファイル、空のディレクトリ
    code, files = sh(["git", "ls-files"])
    tracked = files.splitlines()
    junk = [f for f in tracked if JUNK.search(f)]
    big = []
    for f in tracked:
        try:
            if Path(f).stat().st_size > BIG:
                big.append(f)
        except OSError:
            pass
    empties = [str(p) for p in Path(".").rglob("*") if p.is_dir() and ".git" not in p.parts and not any(p.iterdir())]
    out.append("### リポジトリの中")
    out.append("- 余計なファイル（キャッシュ、OS のごみ、ログ）: " + ("、".join(f"`{f}`" for f in junk[:30]) if junk else "無し"))
    out.append("- 大きなファイル（5MB 超）: " + ("、".join(f"`{f}`" for f in big[:20]) if big else "無し"))
    out.append("- 空のディレクトリ: " + ("、".join(f"`{d}`" for d in empties[:20]) if empties else "無し"))
    out.append("")

    # 文書
    dc = Path(".claude/skills/docs-upkeep/scripts/docs_check.py")
    if dc.exists():
        code, res = sh([sys.executable, str(dc)])
        out.append("### 文書（docs-upkeep）")
        out.append("- " + (res.splitlines()[-1].strip() if res else "動かなかった"))
        out.append("")

    # 判断待ち
    pend = Path("docs/reports/pending.md")
    if pend.exists():
        old = []
        for line in pend.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"(20\d\d-\d\d-\d\d)", line)
            if m and line.lstrip().startswith(("|", "-", "##")) and "答え" not in line:
                try:
                    d = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if now - d > timedelta(days=arg("--days-pending", 30)):
                    old.append(line.strip()[:80])
        out.append("### 判断待ち")
        out.append(f"- {arg('--days-pending', 30)} 日より古いもの: " + (f"{len(old)} 件（" + " / ".join(old[:5]) + "）" if old else "無し"))
        out.append("")

    # PR と Issue
    if open_prs:
        lim = now - timedelta(days=arg("--days-pr", 14))
        stale = [p for p in open_prs if datetime.fromisoformat(p["createdAt"].replace("Z", "+00:00")) < lim]
        out.append("### 開いている PR")
        out.append(f"- {arg('--days-pr', 14)} 日より古いもの: " + ("、".join(f"#{p['number']}（{p['headRefName']}）" for p in stale) if stale else "無し"))
        out.append("")
    code, js = sh(["gh", "issue", "list", "--state", "open", "--limit", "100", "--json", "number,createdAt,title"])
    if code == 0 and js:
        try:
            issues = json.loads(js)
            lim = now - timedelta(days=arg("--days-issue", 90))
            stale = [i for i in issues if datetime.fromisoformat(i["createdAt"].replace("Z", "+00:00")) < lim]
            out.append("### 開いている Issue")
            out.append(f"- {arg('--days-issue', 90)} 日より古いもの: " + ("、".join(f"#{i['number']}" for i in stale) if stale else "無し"))
            out.append("")
        except ValueError:
            pass
    out.append("一回きりの Routine は `list_triggers`（recurring=false）で見て、実行済みのものを消す。")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
