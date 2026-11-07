#!/usr/bin/env python3
"""Ciel の CLAUDE.md の「ユーザーの決めたこと」を、対象のリポジトリの CLAUDE.md に写す。

スキルを配るたびに、同じ決まりを手で足していた（配布のたびに使い捨ての台本を書き、末尾の空行で precheck が落ちた）。
行の先頭（最初の「。」か「（」まで）を鍵にして、対象に無い行は節の末尾に足し、鍵が同じで文言が変わった行は置き換える。
対象の節にだけある行（そのリポジトリ固有の決まり）は触らない。節が無いリポジトリは触らない。

使い方: rules_sync.py <対象のパス> [--ciel <Ciel>] [--apply]
  --apply が無ければ、足す行と置き換える行を出すだけ。終了コードは、差分があれば 1、無ければ 0
"""

import argparse
import re
import sys
from pathlib import Path

SEC = re.compile(r"^## ユーザーの決めたこと\n(.*?)(?=^## |\Z)", re.S | re.M)


def key(line: str) -> str:
    body = line[2:] if line.startswith("- ") else line
    return re.split(r"[。（]", body, 1)[0].strip()


def section(text: str):
    m = SEC.search(text)
    return m, ([l for l in m.group(1).splitlines() if l.startswith("- ")] if m else [])


TOKEN = re.compile(r"\b[a-z][a-z0-9]*-[a-z0-9-]+\b")  # vuln-watch、session-watch のようなスキルの名前


def token(line: str):
    m = TOKEN.search(line)
    return m.group(0) if m else None


def plan(ciel_md: str, target_md: str):
    """(足す行, (古い行, 新しい行) の一覧)。先頭の語で同じ行を探し、無ければ最初に出るスキルの名前で探す（先頭の語ごと書き換わった行のため）。"""
    _, src = section(ciel_md)
    m, dst = section(target_md)
    if m is None:
        return None, None
    have = {key(l): l for l in dst}
    by_token = {}
    for l in dst:
        t = token(l)
        if t:
            by_token.setdefault(t, []).append(l)
    used = set()
    add, replace = [], []
    for l in src:
        k = key(l)
        old = have.get(k)
        if old is None:
            t = token(l)
            cands = [c for c in by_token.get(t, []) if c not in used and key(c) not in {key(x) for x in src}] if t else []
            old = cands[0] if len(cands) == 1 else None
        if old is None:
            add.append(l)
        elif old != l:
            replace.append((old, l))
        if old is not None:
            used.add(old)
    return add, replace


def apply(target: Path, add, replace) -> str:
    s = target.read_text(encoding="utf-8")
    for old, new in replace:
        s = s.replace(old, new, 1)
    m = SEC.search(s)
    body = m.group(1).rstrip("\n")
    tail = s[m.end(1):]
    new_body = body + ("\n" + "\n".join(add) if add else "") + "\n" + ("\n" if tail.startswith("## ") or not tail else "")
    s = s[:m.start(1)] + new_body + tail
    if not s.endswith("\n"):
        s += "\n"
    s = re.sub(r"\n{3,}\Z", "\n\n", s) if tail.startswith("## ") else re.sub(r"\n{2,}\Z", "\n", s)
    target.write_text(s, encoding="utf-8")
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--ciel", default=str(Path(__file__).resolve().parents[4]))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    target = Path(a.target) / "CLAUDE.md"
    if not target.exists():
        print("CLAUDE.md: 無い")
        return 0
    add, replace = plan((Path(a.ciel) / "CLAUDE.md").read_text(encoding="utf-8"), target.read_text(encoding="utf-8"))
    if add is None:
        print("CLAUDE.md: 「ユーザーの決めたこと」の節が無いので触らない")
        return 0
    for l in add:
        print(f"  + {key(l)}")
    for old, new in replace:
        print(f"  ~ {key(new)}")
    if not add and not replace:
        print("CLAUDE.md: 既にある")
        return 0
    if a.apply:
        apply(target, add, replace)
        print(f"CLAUDE.md: {len(add)} 行足し、{len(replace)} 行置き換えた")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
