#!/usr/bin/env python3
"""README と docs とスキルの文を、今のリポジトリと突き合わせる。

誤り（exit 1）: 壊れた相対リンク、無いファイルへの言及、スキル一覧の抜けと余り、README か USAGE から辿れない docs。
注意（exit 0）: 古くなる数（「（N 件）」）、予定の語、2 つの文書にある同じ段落、強調の記号（**）。

使い方: docs_check.py [root]   root の既定はカレント
"""

import glob
import os
import re
import sys
from pathlib import Path

DOC_GLOBS = ["README.md", "CONTRIBUTING.md", "docs/*.md", ".claude/skills/*/SKILL.md", ".claude/skills/*/references/*.md"]
LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)\)")
BACKTICK_PATH = re.compile(r"`((?:\.{1,2}/)?[\w.-]+(?:/[\w.-]+)+\.(?:py|sh|md|json|ya?ml|toml|txt|csv|bat))`")
COUNT = re.compile(r"[（(]\s*\d+\s*件\s*[）)]")
PLAN = re.compile(r"する予定|の予定だ|予定。|\bTBD\b|あとで直す|後で直す|仮置き")
EMPH = re.compile(r"\*\*[^*\n]+\*\*")
TABLE_SKILL = re.compile(r"^\|\s*\[?`([a-z0-9][a-z0-9-]*)`")


def mentioned_exists(root: Path, doc: Path, pth: str) -> bool:
    """言及先が実在するか。スキルの文は、自分のディレクトリと隣のスキルからの相対で書く。
    スキルの文に出る対象リポジトリ側の場所（docs/reports/pending.md、.claude/hooks/check.sh）は、ここには無いので見ない。"""
    skills_root = root / ".claude" / "skills"
    try:
        in_skill = doc.resolve().relative_to(skills_root.resolve())
        skill_dir = skills_root / in_skill.parts[0]
    except ValueError:
        skill_dir = None
    siblings = {d.name for d in skills_root.iterdir() if d.is_dir()} if skills_root.is_dir() else set()
    if skill_dir is None:
        return (root / pth).exists() or (doc.parent / pth).exists() or (pth.split("/")[0] in siblings and (skills_root / pth).exists())
    if pth.startswith(".claude/skills/"):
        return (root / pth).exists()
    first = pth.split("/")[0]
    if first in ("scripts", "hooks", "references", "templates", "assets") or first in siblings:
        return (skill_dir / pth).exists() or (skills_root / pth).exists() or (doc.parent / pth).exists()
    return True  # 対象リポジトリ側の場所。ここでは確かめられない


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    errors, notes = [], []
    docs = []
    for g in DOC_GLOBS:
        docs += [Path(p) for p in sorted(glob.glob(str(root / g)))]
    skills = sorted(d.name for d in (root / ".claude" / "skills").iterdir() if d.is_dir()) if (root / ".claude" / "skills").is_dir() else []
    paragraphs = {}
    for doc in docs:
        rel = doc.relative_to(root)
        text = doc.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            for target in LINK.findall(re.sub(r"`[^`]*`", "", line)):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                t = target.split("#")[0].split("?")[0]
                if not t or any(c in t for c in "<>*$"):
                    continue
                if not (doc.parent / t).exists() and not (root / t).exists():
                    errors.append(f"{rel}:{n} 壊れたリンク: {target}")
            for pth in BACKTICK_PATH.findall(line) if rel.name != "SOURCES.md" else []:  # 出典の表は相手のリポジトリの場所を書く
                if pth.startswith("~") or any(c in pth for c in "<>*$") or re.search(r"[A-Z]{3,}", pth):
                    continue  # ひな形（YYYY など）と置き換えの印は見ない
                if not mentioned_exists(root, doc, pth):
                    errors.append(f"{rel}:{n} 無いファイルへの言及: {pth}")
            if COUNT.search(line) and "reports/" not in str(rel):
                notes.append(f"{rel}:{n} 古くなる数: {COUNT.search(line).group(0)}（数は本文に書かない）")
            bare = re.sub(r"「[^」]*」|`[^`]*`", "", line)  # 引用とコードの中は見ない
            if PLAN.search(bare):
                notes.append(f"{rel}:{n} 予定の語: {PLAN.search(bare).group(0)}（成果物の表に書くか、消す）")
            if EMPH.search(line) and rel.name in ("README.md", "USAGE.md", "CONTRIBUTING.md"):
                notes.append(f"{rel}:{n} 強調の記号: {EMPH.search(line).group(0)[:40]}")
        for para in re.split(r"\n\s*\n", text):
            p = re.sub(r"\s+", " ", para).strip()
            if len(p) >= 80 and not p.startswith(("|", "```", "#", "-", "<")):
                if p in paragraphs and paragraphs[p] != rel:
                    notes.append(f"{rel} と {paragraphs[p]} に同じ段落: {p[:40]}…")
                paragraphs.setdefault(p, rel)
    # スキル一覧の抜けと余り
    for name in ("README.md", "docs/USAGE.md"):
        f = root / name
        if not f.exists() or not skills:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        listed = {m.group(1) for line in text.splitlines() for m in [TABLE_SKILL.match(line)] if m}
        if not listed:
            continue
        for s in skills:
            if s not in listed:
                errors.append(f"{name} の一覧に無いスキル: {s}")
        for s in sorted(listed - set(skills)):
            errors.append(f"{name} の一覧にあるが無いスキル: {s}")
    # 辿れない docs
    entry = ""
    for name in ("README.md", "docs/USAGE.md"):
        if (root / name).exists():
            entry += (root / name).read_text(encoding="utf-8", errors="replace")
    for d in sorted((root / "docs").glob("*.md")) if (root / "docs").is_dir() else []:
        if d.name == "USAGE.md":
            continue
        if f"docs/{d.name}" not in entry and d.name not in entry:
            errors.append(f"docs/{d.name} は README からも USAGE からも辿れない")
    print(f"== docs-upkeep: {root.name} ==")
    for e in errors:
        print(f"  !! [誤り] {e}")
    for n in notes:
        print(f"  ? [注意] {n}")
    print(f"  誤り {len(errors)} / 注意 {len(notes)}（文書 {len(docs)}、スキル {len(skills)}）")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
