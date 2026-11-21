#!/usr/bin/env python3
"""README を「魅せる」10 項目で見て、OK・NG・注意と点数を出す。

初めて開いた人が 10 秒で「何をする物か」「自分に要るか」「どう動かすか」を掴めるか、を機械で拾える範囲で見る。
人が見る問いは references/structure.md。根拠の無い物（無いバッジ、無いファイルへのリンク、置き場所の語）は NG にし、
目安の物（手数、目次、言語の混在、ライセンスの位置）は注意にする。

使い方:
  readme_check.py [README.md か リポジトリの root] [--strict] [--json]
  readme_check.py --diff <base>     README が base からの差分にあるときだけ見て、注意として出す（終了コード 0。precheck が呼ぶ）
終了コード: 0。--strict のときだけ、NG があれば 1
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
IMG_MD = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMG_HTML = re.compile(r"<img[^>]*\ssrc=[\"']([^\"']+)[\"']", re.I)
LINK = re.compile(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HREF = re.compile(r"<a[^>]*\shref=[\"']([^\"']+)[\"']", re.I)
STEP = re.compile(r"^\s*\d+[.)]\s+\S")
JA = re.compile(r"[ぁ-んァ-ヶ一-龠]")
PLACEHOLDER = re.compile(r"\bTODO\b|\bTBD\b|\bFIXME\b|lorem ipsum|<[^<>/=\"'\n]*[ぁ-んァ-ヶ一-龠][^<>\n]*>|<(?:owner|repo|user|name|url|version|path|package|project[-_ ]?name|your[-_][\w-]+)>", re.I)
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif")
EN_HEADS = {"install", "installation", "usage", "license", "contributing", "quick start", "quickstart", "getting started", "features",
            "configuration", "roadmap", "contact", "acknowledgments", "acknowledgements", "background", "security", "maintainers",
            "troubleshooting", "development", "table of contents", "requirements", "prerequisites", "overview", "about"}

QUICK = re.compile(r"クイックスタート|quick\s*start|はじめ方|すぐ動かす|getting\s*started|始め方", re.I)
INSTALL = re.compile(r"インストール|install|導入|入れ方", re.I)
USAGE = re.compile(r"使い方|usage|使用方法|使う", re.I)
LICENSE = re.compile(r"ライセンス|license", re.I)
TOC = re.compile(r"目次|table of contents|contents", re.I)


def is_badge(url: str) -> bool:
    u = url.lower()
    return "shields.io" in u or "badge" in u or "/badges/" in u


def parse(text: str):
    """見出しの一覧 (level, title, start_line, end_line_of_any_next_heading, end_line_of_same_or_higher) と code block の行。"""
    lines = text.splitlines()
    heads, fence, in_fence = [], [], False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("```") or s.startswith("~~~"):
            in_fence = not in_fence
            fence.append(i)
            continue
        if in_fence:
            continue
        m = HEAD.match(line)
        if m:
            heads.append([len(m.group(1)), m.group(2).strip(), i, len(lines), len(lines)])
    for k, h in enumerate(heads):
        for n in heads[k + 1:]:
            h[3] = min(h[3], n[2])
            if n[0] <= h[0]:
                h[4] = min(h[4], n[2])
                break
    return lines, heads


def body(lines, h, until_same=True):
    return lines[h[2] + 1:(h[4] if until_same else h[3])]


def has_code(seg) -> bool:
    return any(l.strip().startswith(("```", "~~~")) for l in seg)


def find(heads, pat, levels=(2, 3)):
    for h in heads:
        if h[0] in levels and pat.search(h[1]):
            return h
    return None


def check(readme: Path, root: Path):
    text = readme.read_text(encoding="utf-8", errors="replace")
    lines, heads = parse(text)
    items = []

    def add(name, status, note):
        items.append({"name": name, "status": status, "note": note})

    # 1 タイトル
    h1 = [h for h in heads if h[0] == 1]
    if h1 and heads and heads[0][0] == 1:
        add("タイトル", "OK", h1[0][1])
    elif h1:
        add("タイトル", "注意", "H1 が最初の見出しではない")
    else:
        add("タイトル", "NG", "H1（# タイトル）が無い")

    # 2 1 文
    start = h1[0][2] + 1 if h1 else 0
    cand = []
    in_fence = False
    for line in lines[start:]:
        s = line.strip()
        if s.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or not s:
            continue
        if HEAD.match(line):
            break
        if s.startswith(("<", "![", "[![", "|", "-", "*")) or IMG_MD.search(s) and not re.sub(IMG_MD, "", s).strip():
            continue
        cand.append(s)
        if len(cand) >= 5:
            break
    if not cand:
        add("1 文", "NG", "タイトルの下に、何をする物かの 1 文が無い")
    else:
        first = cand[0]
        n = len(re.sub(r"\s+", "", first))
        if n > 120:
            add("1 文", "注意", f"最初の文が {n} 文字。120 文字以内にする")
        else:
            add("1 文", "OK", first[:60] + ("…" if len(first) > 60 else ""))

    # 画像を集める
    images = [(a, u) for a, u in IMG_MD.findall(text)] + [("", u) for u in IMG_HTML.findall(text)]
    badges = [u for _, u in images if is_badge(u)]
    shots = [u for _, u in images if not is_badge(u) and u.lower().split("?")[0].endswith(IMAGE_EXT)]
    mermaid = "```mermaid" in text

    def local_missing(urls):
        return [u for u in urls if not u.startswith(("http://", "https://", "data:")) and not (readme.parent / u.split("?")[0]).exists() and not (root / u.split("?")[0]).exists()]

    # 3 バッジ
    if not badges:
        add("バッジ", "NG", "バッジが無い。根拠のある物（CI、ライセンス、版、対応 OS）を置く。references/badges.md")
    elif local_missing(badges):
        add("バッジ", "NG", "無いファイルのバッジ: " + ", ".join(local_missing(badges)[:3]))
    else:
        add("バッジ", "OK", f"{len(badges)} 個")

    # 4 スクリーンショット
    if local_missing(shots):
        add("スクリーンショット", "NG", "無いファイルの画像: " + ", ".join(local_missing(shots)[:3]))
    elif shots or mermaid:
        add("スクリーンショット", "OK", f"画像 {len(shots)} 個" + ("、図（mermaid）" if mermaid else ""))
    else:
        add("スクリーンショット", "NG", "スクリーンショット・GIF・図が無い。scripts/shot.py で撮って 1 文の下に置く")

    # 5 クイックスタート
    q = find(heads, QUICK)
    if not q:
        add("クイックスタート", "NG", "クイックスタートの節が無い（5 手以内で動かす手順）")
    else:
        seg = body(lines, q)
        steps = sum(1 for l in seg if STEP.match(l))
        if not has_code(seg):
            add("クイックスタート", "NG", "コマンドの code block が無い")
        elif steps > 5:
            add("クイックスタート", "注意", f"手順が {steps} 手。5 手以内にする")
        else:
            add("クイックスタート", "OK", f"{steps} 手、code block あり")

    # 6 インストール
    ins = find(heads, INSTALL)
    if not ins:
        if q and has_code(body(lines, q)):
            add("インストール", "注意", "インストールの節が無い（クイックスタートに含めているなら可）")
        else:
            add("インストール", "NG", "インストールの節が無い")
    elif not has_code(body(lines, ins)):
        add("インストール", "NG", "インストールに code block が無い")
    else:
        add("インストール", "OK", "code block あり")

    # 7 使い方
    us = find(heads, USAGE)
    if not us:
        add("使い方", "NG", "使い方の節が無い")
    elif not has_code(body(lines, us)):
        add("使い方", "NG", "使い方に code block が無い")
    else:
        add("使い方", "OK", "code block あり")

    # 8 ライセンス
    lic = find(heads, LICENSE, levels=(2,))
    lic_files = [p for p in root.iterdir() if p.is_file() and p.name.upper().startswith(("LICENSE", "LICENCE", "COPYING"))] if root.is_dir() else []
    h2 = [h for h in heads if h[0] == 2]
    if not lic:
        add("ライセンス", "NG", "ライセンスの節が無い")
    elif not lic_files:
        add("ライセンス", "NG", "LICENSE のファイルがリポジトリに無い")
    elif h2 and h2[-1] is not lic:
        add("ライセンス", "注意", "ライセンスが最後の節ではない")
    else:
        add("ライセンス", "OK", lic_files[0].name)

    # 9 リンク
    targets = LINK.findall(re.sub(r"`[^`\n]*`", "", text)) + HREF.findall(text)
    broken = []
    for t in targets:
        if t.startswith(("http://", "https://", "mailto:", "#", "data:")):
            continue
        p = t.split("#")[0].split("?")[0]
        if p and not (readme.parent / p).exists() and not (root / p).exists():
            broken.append(t)
    if broken:
        add("リンク", "NG", "つながらない: " + ", ".join(broken[:3]))
    else:
        add("リンク", "OK", f"{len(targets)} 個")

    # 10 仕上げ
    problems, notes = [], []
    for k, h in enumerate(heads):
        seg = [l for l in lines[h[2] + 1:h[3]] if l.strip()]
        nxt = heads[k + 1] if k + 1 < len(heads) else None
        if not seg and (nxt is None or nxt[0] <= h[0]):
            problems.append(f"空の節「{h[1]}」")
    ph = sorted({m.group(0) for m in PLACEHOLDER.finditer(re.sub(r"<!--.*?-->", "", text, flags=re.S))})
    if ph:
        problems.append("置き場所の語: " + ", ".join(ph[:3]))
    if len(lines) > 100 and not find(heads, TOC) and len(re.findall(r"\]\(#[^)]+\)", text)) < 3:
        notes.append(f"{len(lines)} 行なのに目次が無い")
    ja_heads = [h for h in h2 if JA.search(h[1])]
    en_heads = [h for h in h2 if not JA.search(h[1]) and h[1].strip().lower() in EN_HEADS]
    if ja_heads and en_heads:
        notes.append("見出しに日本語と英語が混ざっている（" + en_heads[0][1] + "）")
    if problems:
        add("仕上げ", "NG", "、".join(problems))
    elif notes:
        add("仕上げ", "注意", "、".join(notes))
    else:
        add("仕上げ", "OK", "空の節・置き場所の語なし")
    return items


def score(items):
    ok = sum(1 for i in items if i["status"] == "OK")
    ng = sum(1 for i in items if i["status"] == "NG")
    warn = sum(1 for i in items if i["status"] == "注意")
    return ok, ng, warn


def report(readme: Path, items, as_json=False):
    if as_json:
        print(json.dumps({"readme": str(readme), "items": items, "score": score(items)[0]}, ensure_ascii=False, indent=1))
        return
    print(f"== readme-showcase: {readme} ==")
    for i in items:
        print(f"  {i['status']:<2} {i['name']}: {i['note']}")
    ok, ng, warn = score(items)
    print(f"  点数 {ok}/{len(items)}（NG {ng}、注意 {warn}）")


def locate(arg: str):
    p = Path(arg).resolve()
    readme = p / "README.md" if p.is_dir() else p
    root = p if p.is_dir() else p.parent
    for d in (root, *root.parents):
        if (d / ".git").exists():
            root = d
            break
    return readme, root


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--diff", metavar="BASE")
    a = ap.parse_args()
    readme, root = locate(a.path)
    if a.diff:
        r = subprocess.run(["git", "diff", "--name-only", a.diff, "--", "README.md"], capture_output=True, text=True, cwd=root)
        if not r.stdout.strip():
            print("README: 差分なし")
            return 0
        if not readme.is_file():
            print("README: 消えている")
            return 0
        items = check(readme, root)
        ok, ng, warn = score(items)
        bad = [i["name"] for i in items if i["status"] != "OK"]
        print(f"README 注意: 点数 {ok}/{len(items)}（NG {ng}、注意 {warn}）" + ("。直す目安: " + "、".join(bad) if bad else "") + "（魅せる README の 10 項目。readme-showcase）")
        return 0
    if not readme.is_file():
        print(f"README が無い: {readme}")
        return 1 if a.strict else 0
    items = check(readme, root)
    report(readme, items, a.json)
    return 1 if a.strict and score(items)[1] else 0


if __name__ == "__main__":
    sys.exit(main())
