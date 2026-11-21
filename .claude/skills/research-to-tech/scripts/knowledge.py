#!/usr/bin/env python3
"""調べ物を、情報 → 知識 → アルゴリズム → 技術 → 実装 の 5 段で残し、どの段まで進んだかを機械で見る。

ユーザーの決まり: 開発で調べ物をしたら、ただの情報で終わらせない。知識として蓄え、アルゴリズムを作り、
技術として使えるところまで考えて、実装する。会話の中だけで分かった気になると次の日には消え、同じことをまた調べる。
1 件 1 ファイルで docs/knowledge/tech/ に置き、段は節の中身から数える（自分で「できた」と書いた欄は信じない）。

使い方:
  knowledge.py --add "<題>" [--words 語,語]            1 件のファイルを作る（docs/knowledge/tech/<日付>-<slug>.md）
  knowledge.py --find <語> [<語> ...]                  調べる前に、既にある知識を探す
  knowledge.py --check [--strict] [--json]             全件の段と欠けを出す。--strict は欠けがあれば終了コード 1
  knowledge.py --diff <base>                           precheck 用。--check と同じ物を出し、落とさない
  knowledge.py --index                                 一覧（docs/knowledge/tech/README.md）を書き直す
  どれも --root DIR でリポジトリの根を指せる（既定は git の根）
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
DIR = Path("docs") / "knowledge" / "tech"
STAGES = ["情報", "知識", "アルゴリズム", "技術", "実装"]
STALE_DAYS = 7
TEMPLATE = """# {title}

- 日付: {date}
- 語: {words}

## 情報
<!-- 調べた事実と出所。1 行 1 件で、URL と見た日を書く。2 つ以上の出所で確かめたか、動かして確かめたかも書く -->

## 知識
<!-- 一般化した決まり。いつ使えるか、いつ使えないか（前提と限界）、なぜそうなるか。この件を知らない人が半年後に読んで使える形で -->

## アルゴリズム
<!-- 入力、出力、手順（番号つき）、端の場合、費用（時間、回数、トークン）。頭の中の手順ではなく、機械が実行できる細かさで -->

## 技術
<!-- 再利用できる形にした物の場所とテスト。関数、スクリプト、フック、スキルの決まりのどれか -->
<!-- - 場所: `path/to/file.py` の `function`  -->
<!-- - テスト: `path/to/test_file.py`  -->

## 実装
<!-- 今の作業のどこに当てたかと、動かした出力。出力はそのまま貼る -->
"""


def root_of(arg) -> Path:
    if arg:
        return Path(arg).resolve()
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return Path.cwd()


def slug_of(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if len(s) >= 3:
        return s[:48]
    # 日本語だけの題は英数字が残らないので、題から決まる短い名前にする（同じ題なら同じ名前）
    return "note-" + hashlib.sha1(title.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]


def sections(text: str) -> dict:
    """節の名前 → 中身（説明のコメントと空行を除いた行）。"""
    out = {s: [] for s in STAGES}
    cur = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(\S+)\s*$", line)
        if m:
            cur = m.group(1) if m.group(1) in out else None
            continue
        if cur is None:
            continue
        s = line.strip()
        if not s or (s.startswith("<!--") and s.endswith("-->")):
            continue
        out[cur].append(line)
    return out


def rung_of(sec: dict) -> int:
    """何段まで進んだか（0〜5）。前の段が空なら、その先が書いてあっても数えない（飛ばした技術は再現できない）。"""
    n = 0
    for s in STAGES:
        if not sec[s]:
            break
        n += 1
    return n


def paths_in(lines: list) -> list:
    """技術の節の中の、ファイルらしい物（バッククォートの中で / か拡張子がある物）と、テストの行かどうか。"""
    out = []
    for line in lines:
        for tok in re.findall(r"`([^`\s]+)`", line):
            if "/" in tok or re.search(r"\.[A-Za-z0-9]{1,5}$", tok):
                if re.match(r"https?://", tok):
                    continue
                # テストかどうかは、行の頭の「テスト」かファイルの名前で決める（行の中の latest のような語に釣られない）
                is_test = bool(re.match(r"\s*-?\s*テスト", line) or re.search(r"(^|/)tests?/|(^|/)test_[^/]*$|_test\.|\.(test|spec)\.", tok))
                out.append((tok, is_test))
    return out


def read_entry(root: Path, p: Path) -> dict:
    text = p.read_text(encoding="utf-8", errors="replace")
    sec = sections(text)
    title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), p.stem)
    words = next((l.split(":", 1)[1].strip() for l in text.splitlines() if re.match(r"^- 語\s*[:：]", l)), "")
    m = re.match(r"(\d{4}-\d{2}-\d{2})", p.name)
    date = m.group(1) if m else ""
    rung = rung_of(sec)
    notes = []
    info = "\n".join(sec["情報"])
    if sec["情報"] and not re.search(r"https?://|動かして確かめ|\S+\.\w+:\d+", info):
        notes.append("情報に出所（URL か、動かして確かめた記録）が無い")
    skipped = [s for i, s in enumerate(STAGES) if sec[s] and i >= rung]
    if skipped:
        notes.append(f"{STAGES[rung]}が空のまま{skipped[0]}に進んでいる")
    tech_paths = paths_in(sec["技術"])
    if sec["技術"]:
        if not tech_paths:
            notes.append("技術に場所（ファイル）が書いていない")
        for tok, is_test in tech_paths:
            if not (root / tok).exists():
                notes.append(f"技術の場所が無い: {tok}")
        if tech_paths and not any(t for _, t in tech_paths):
            notes.append("技術にテストが書いていない")
    if rung <= 1 and date:
        try:
            age = (datetime.now(JST).date() - datetime.strptime(date, "%Y-%m-%d").date()).days
            if age >= STALE_DAYS:
                notes.append(f"情報のまま {age} 日。知識にするか、要らなければ消す")
        except ValueError:
            pass
    return {"path": str(p.relative_to(root)), "title": title, "words": words, "date": date, "rung": rung,
            "stage": STAGES[rung - 1] if rung else "空", "notes": notes, "text": text, "sec": sec,
            "tech": [t for t, is_test in tech_paths if not is_test][:1]}


def entries(root: Path) -> list:
    d = root / DIR
    if not d.is_dir():
        return []
    return [read_entry(root, p) for p in sorted(d.glob("*.md")) if p.name != "README.md"]


def summary(es: list) -> str:
    c = {s: sum(1 for e in es if e["stage"] == s) for s in STAGES}
    notes = sum(len(e["notes"]) for e in es)
    return (f"知識: 全 {len(es)} 件 / 実装 {c['実装']} / 技術 {c['技術']} / アルゴリズム {c['アルゴリズム']} / 知識 {c['知識']} / "
            f"情報のまま {c['情報'] + sum(1 for e in es if e['stage'] == '空')}（注意 {notes}）")


def cmd_add(root: Path, title: str, words: str) -> int:
    d = root / DIR
    d.mkdir(parents=True, exist_ok=True)
    date = datetime.now(JST).strftime("%Y-%m-%d")
    p = d / f"{date}-{slug_of(title)}.md"
    if p.exists():
        print(f"既にある: {p.relative_to(root)}（同じ題。追記する）")
        return 0
    p.write_text(TEMPLATE.format(title=title.strip(), date=date, words=words or ""), encoding="utf-8")
    print(f"作った: {p.relative_to(root)}（情報 → 知識 → アルゴリズム → 技術 → 実装 の順に埋める。済んだら --check）")
    return 0


def cmd_find(root: Path, terms: list) -> int:
    es = entries(root)
    hits = []
    for e in es:
        hay = (e["title"] + "\n" + e["words"] + "\n" + e["text"]).lower()
        score = sum(1 for t in terms if t.lower() in hay) * 10 + sum(3 for t in terms if t.lower() in (e["title"] + e["words"]).lower())
        if score:
            hits.append((score, e))
    if not hits:
        print(f"無し（{len(es)} 件の中に無い。調べたら --add で残す）")
        return 0
    for _, e in sorted(hits, key=lambda x: (-x[0], x[1]["path"])):
        first = next((l.strip().lstrip("- ") for l in e["sec"]["知識"]), "")
        tech = f"｜技術: {e['tech'][0]}" if e["tech"] else ""
        print(f"  {e['stage']}  {e['path']}  {e['title']}{tech}")
        if first:
            print(f"        {first[:110]}")
    print(f"{len(hits)} 件。使える物があれば、調べ直さずにそれを使う")
    return 0


def cmd_check(root: Path, strict: bool, as_json: bool) -> int:
    es = entries(root)
    if as_json:
        print(json.dumps({"entries": [{k: e[k] for k in ("path", "title", "date", "rung", "stage", "notes", "tech")} for e in es],
                          "summary": summary(es)}, ensure_ascii=False, indent=1))
    else:
        print("== research-to-tech: 知識の段 ==")
        for e in es:
            print(f"  {e['stage']}  {e['path']}  {e['title']}")
            for n in e["notes"]:
                print(f"    注意  {n}")
        print(summary(es))
    return 1 if strict and any(e["notes"] for e in es) else 0


def cmd_index(root: Path) -> int:
    es = sorted(entries(root), key=lambda e: e["date"], reverse=True)
    lines = ["# 調べ物から作った知識と技術", "",
             "調べ物を 情報 → 知識 → アルゴリズム → 技術 → 実装 の段で残した一覧。`knowledge.py --index` が書き直す。手で直さない。",
             "調べる前に `knowledge.py --find <語>` でここを探す。", "",
             "| 題 | 段 | 技術の場所 | 日付 |", "|---|---|---|---|"]
    for e in es:
        tech = f"`{e['tech'][0]}`" if e["tech"] else ""
        lines.append(f"| [{e['title']}]({Path(e['path']).name}) | {e['stage']} | {tech} | {e['date']} |")
    d = root / DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"書いた: {DIR / 'README.md'}（{len(es)} 件）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--add", metavar="TITLE")
    ap.add_argument("--words", default="")
    ap.add_argument("--find", nargs="+", metavar="WORD")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--diff", metavar="BASE")
    ap.add_argument("--index", action="store_true")
    ap.add_argument("--root")
    a = ap.parse_args()
    root = root_of(a.root)
    if a.add:
        return cmd_add(root, a.add, a.words)
    if a.find:
        return cmd_find(root, a.find)
    if a.index:
        return cmd_index(root)
    if a.diff is not None:
        cmd_check(root, False, a.json)  # precheck からは注意として出すだけで、落とさない
        return 0
    return cmd_check(root, a.strict, a.json)


if __name__ == "__main__":
    sys.exit(main())
