#!/usr/bin/env python3
"""ユーザーの語の対応表（references/user-terms.md）を読み、文章に残った略語・訳語・造語を見つける。

ユーザーが使った語と違う言い方（プルリク、リポ、Routine、片付け、JST）は、読む側に言い換えの手間を残す。
対応表の「直す言い方」を文章から探し、ユーザーの語を添えて出す。
コード、引用（「…」）、URL、パス、HTML のコメント、<...> の中、frontmatter は文章ではないので見ない。
対応表そのものと ja-proofread の glossary.md は、直す言い方を並べる場所なので見ない。

使い方:
  terms.py --diff <base>        base からの差分で足した行（.md と .txt）を見る。注意として出し、終了コードは 0
  terms.py --files <path>...    ファイル全体を見る。見つかれば終了コード 1
  terms.py --text               標準入力の文章を見る。見つかれば終了コード 1
  terms.py --table              対応表を読み、行数と、ユーザーの語が別の行の直す言い方に当たる矛盾を出す
"""

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TABLE = HERE.parent / "references" / "user-terms.md"
EXEMPT = ("user-terms/references/user-terms.md", "ja-proofread/references/glossary.md")
IGNORE = re.compile(r"<!--\s*(?:user-terms|ja-proofread):\s*ignore\s*-->")
HEADER = re.compile(r"^ユーザーの語")
# 文章ではない部分。順に消す
INLINE = [
    re.compile(r"`[^`\n]*`"),
    re.compile(r"https?://\S+"),
    re.compile(r"「[^」\n]*」|『[^』\n]*』"),
    re.compile(r"<!--.*?-->"),
    re.compile(r"<[^<>\n]*>"),
    re.compile(r"\]\([^)\n]*\)"),
    re.compile(r"[\w.~-]*/[\w./~-]+"),
    re.compile(r"[\w-]+\.(?:md|py|sh|json|ya?ml|txt|js|ts|toml|csv|html|css|exe)\b"),
]
ALNUM = re.compile(r"^[A-Za-z0-9 ._'/-]+$")
KATA = re.compile(r"^[ァ-ヴー]+$")
META = re.compile(r"[\\\[\]()?*+^$|]")


def compile_variant(v: str):
    """英数字は単語の境界で（大文字小文字は区別しない）、カタカナはカタカナの境界で当てる。正規表現はそのまま。"""
    if META.search(v):
        return re.compile(v)
    if ALNUM.match(v):
        return re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(v) + r"(?![A-Za-z0-9_-])", re.I)
    if KATA.match(v):
        return re.compile(r"(?<![ァ-ヴー])" + v + r"(?![ァ-ヴー])")
    return re.compile(v)


def load_table(path: Path = TABLE):
    """[(ユーザーの語, 直す言い方, 正規表現)]。表の行だけを読む。"""
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or HEADER.match(cells[0]) or re.fullmatch(r"[-: ]*", cells[0]):
            continue
        for v in cells[1].split("、"):
            v = v.strip().strip("`")
            if v:
                rows.append((cells[0], v, compile_variant(v)))
    return rows


def clean_lines(text: str):
    """行数を変えずに、文章ではない部分を消す。"""
    out, fence, front = [], False, False
    for i, l in enumerate(text.splitlines()):
        s = l.strip()
        if i == 0 and s == "---":
            front = True
            out.append("")
            continue
        if front:
            out.append("")
            if s == "---":
                front = False
            continue
        if s.startswith("```") or s.startswith("~~~"):
            fence = not fence
            out.append("")
            continue
        if fence or IGNORE.search(l):
            out.append("")
            continue
        for p in INLINE:
            l = p.sub(" ", l)
        out.append(l)
    return out


def find(text: str, rows=None, only=None):
    """[(行番号, 見つけた言い方, ユーザーの語)]。only は見る行番号の集合（1 始まり）。"""
    rows = load_table() if rows is None else rows
    hits = []
    for n, l in enumerate(clean_lines(text), 1):
        if (only is not None and n not in only) or not l.strip():
            continue
        seen = set()
        for user, _, pat in rows:
            m = pat.search(l)
            if m and (m.group(0).lower(), user) not in seen:
                seen.add((m.group(0).lower(), user))
                hits.append((n, m.group(0), user))
    return hits


def added_lines(base: str, path: str):
    r = subprocess.run(["git", "diff", "-U0", base, "--", path], capture_output=True, text=True)
    nums = set()
    for m in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", r.stdout, re.M):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        nums.update(range(start, start + count))
    return nums


def is_exempt(path: str) -> bool:
    return path.replace("\\", "/").endswith(EXEMPT)


def check_diff(base: str) -> int:
    rows = load_table()
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=AM", base, "--", "*.md", "*.txt"], capture_output=True, text=True)
    total = 0
    for f in r.stdout.split():
        if is_exempt(f) or not Path(f).is_file():
            continue
        nums = added_lines(base, f)
        if not nums:
            continue
        for n, v, u in find(Path(f).read_text(encoding="utf-8", errors="replace"), rows, only=nums):
            print(f"  ? {f}:{n} 「{v}」→「{u}」")
            total += 1
    print(f"用語 注意 {total} 件（ユーザーの語に直す目安。表は .claude/skills/user-terms/references/user-terms.md）")
    return 0


def check_files(paths) -> int:
    rows = load_table()
    total = 0
    for f in paths:
        if is_exempt(f):
            continue
        for n, v, u in find(Path(f).read_text(encoding="utf-8", errors="replace"), rows):
            print(f"  ? {f}:{n} 「{v}」→「{u}」")
            total += 1
    print(f"用語 {total} 件")
    return 1 if total else 0


def check_table() -> int:
    rows = load_table()
    users = {u for u, _, _ in rows}
    bad = [(u, v) for u, v, pat in rows for w in users if w != u and pat.search(w)]
    for u, v in bad:
        print(f"  !! 直す言い方「{v}」（{u} の行）が、ユーザーの語に当たる")
    print(f"表 {len(users)} 語、直す言い方 {len(rows)} 個、矛盾 {len(bad)}")
    return 1 if bad else 0


def main() -> int:
    a = sys.argv[1:]
    if a[:1] == ["--diff"] and len(a) >= 2:
        return check_diff(a[1])
    if a[:1] == ["--files"] and len(a) >= 2:
        return check_files(a[1:])
    if a[:1] == ["--text"]:
        hits = find(sys.stdin.read())
        for n, v, u in hits:
            print(f"  ? {n} 「{v}」→「{u}」")
        print(f"用語 {len(hits)} 件")
        return 1 if hits else 0
    if a[:1] == ["--table"]:
        return check_table()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
