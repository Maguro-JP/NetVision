#!/usr/bin/env python3
# 出典: kojikojikojikoji/natural-japanese-writing の scripts/check.py（MIT License, Copyright (c) 2025 Siqi Chen）。
# Ciel の ja-proofread では、AI っぽさの目印を数える層として proofread.py から呼ぶ。中身は元のまま。
# 元のスキルは 6 層（表記・語彙・文末とリズム・構文・構成・中身）の点検表を持つ。要点は references/translationese.md。
"""natural-japanese-writing の機械チェック。

文章を数えて、AIっぽさの目印を出す。判定はしない（数値は読み直す場所の目印）。

使い方:
    python3 check.py 文章.md                  # 人向けの一覧
    python3 check.py 文章.md --json           # 機械向け（回帰テスト・フック用）
    python3 check.py 文章.md --type business  # 文書の種類でしきい値を変える（business / article / manual）
    pbpaste | python3 check.py

依存: 標準ライブラリのみ。fugashi（+ unidic-lite）が入っていれば語尾と体言止めの判定に使う。
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter

try:  # 形態素解析は任意
    import fugashi  # type: ignore

    _TAGGER = fugashi.Tagger()
except Exception:  # pragma: no cover
    _TAGGER = None

# ---------------------------------------------------------------- パターン
# 語彙・構文・構成のうち、文字列で拾えるもの。★は1つで直す癖。
PATTERNS = {
    "★否定対比（〜ではなく 等）": r"ではなく|ではない[。、]|というより|だけでなく|にとどまらず|単なる",
    "★重要なのは／ポイントは": r"重要なのは|大事なのは|ポイントは|押さえておきたいのは|肝(?:は|なのは)",
    "★AI頻出語（硬い万能語）": r"適切な|さまざまな|様々な|多岐にわたる|包括的な|多面的な|画期的な|効果的な|円滑な|不可欠",
    "★AI頻出語（翻訳調）": r"において|これにより|を実現する|を促進|を強化|を活用|を可能に|が求められ|していきます",
    "★AI頻出語（意義づけ）": r"重要な役割|浮き彫り|を示唆|を物語って|の証である|計り知れない",
    "★重要です": r"重要です|重要となります|重要である|重要な(?:ポイント|要素)",
    "★口語の呼称（提案書・社外文書は貴社）": r"御社",
    "流行語（本質・解像度 等）": r"本質|解像度|言語化|粒度|刺さる|筋がいい|一段上が|腹落ち|効きます",
    "謎の比喩": r"羅針盤|車の両輪|潤滑油|パズルのピース|DNA|土台|設計図|スパイス|レシピ",
    "翻訳調の述語": r"の役割を果たし|として位置づけ|と言えるでしょう|ではないでしょうか|できたかなと",
    "回りくどい表現（弱い）": r"することができ|に関して",
    "主体のない受け身": r"が求められて|と考えられて|とされて(?:い|お)",
    "だからこそ": r"だからこそ",
    "持ち上げ": r"素晴らしい(?:ご)?質問|良い視点|お気持ち.{0,4}わかります",
    "★定型の冒頭": r"本記事では|本稿では|結論から(?:言う|いう)と|ご質問ありがとうございます|について解説します|が注目を集めて",
    "★定型の締め": r"今後の展開が注目|が期待されます|参考になれば幸い|いかがでしたか|ぜひ試してみて|お役に立てれば",
    "両論併記・保険": r"一概には言えません|メリットもあればデメリット|状況によって異なり|場合によっては|ケースバイケース",
    "見出しの括弧補足": r"^#+ .+（.+）\s*$",
}

MARKS = {
    "★太字（**）": r"\*\*[^*\n]+\*\*",
    "★太字ラベル＋コロン": r"^\s*[-*・]?\s*\*\*[^*\n]+\*\*\s*[:：]",
    "★全角ダッシュ": r"——|―|—",
    "コロン＋半角スペース": r"[：:] (?=\S)",
    "スラッシュ並列": r"[^\s/]／[^\s/]",
    "かぎ括弧の入れ子": r"「[^」]*『",
    "絵文字": "[\U0001F300-\U0001FAFF✅✨]",
    "英語見出し": r"TL;DR|Next Steps|^#+\s*(?:Why|Summary)\b|Step\s*\d+\s*[:：]",
}

# 文書の種類ごとのしきい値。数値は評価セット（人40本・AI40本）で決めた。eval/README.md を参照。
THRESHOLDS = {
    # 人 p10 の CV は 0.39、AI の中央値は 0.50。読点は人 p90 が 0.93/文、AI 中央値 0.70/文。
    # 同じ語尾の連続は、業務の Slack では人でも p90 が 7 と長く、弱い指標。
    "business": {"cv_min": 0.40, "same_ending_run": 6, "comma_per_sentence": 1.0, "para_hits": 3},
    "article": {"cv_min": 0.45, "same_ending_run": 4, "comma_per_sentence": 1.0, "para_hits": 3},
    "manual": {"cv_min": 0.35, "same_ending_run": 6, "comma_per_sentence": 1.2, "para_hits": 4},
}


# ---------------------------------------------------------------- 前処理
def strip_code(text: str) -> str:
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)  # YAML の冒頭
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", text)


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def sentences(text: str) -> list[str]:
    body = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "|", ">")):
            continue
        s = re.sub(r"^[-*・\d.]+\s*", "", s)
        body.extend(p.strip() for p in re.split(r"(?<=[。！？])", s) if p.strip())
    return body


# ---------------------------------------------------------------- 個別の指標
def period_drop(text: str) -> list[str]:
    """1-4: 行の途中に「。」があるのに行末に句点が無い行。"""
    hits = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "|", "```")):
            continue
        core = re.sub(r"[（(][^（）()]*[）)]\s*$", "", s).rstrip("」』 ")  # 末尾の丸括弧の注記は無視
        if "。" in core[:-1] and not core.endswith(("。", "！", "？", ":", "：")):
            hits.append(s)
    return hits


_ENDING_RE = re.compile(r"(でした|ました|ません|です|ます|である|だ|ない|た|る|う|か)$")


def ending_of(sentence: str) -> str:
    t = sentence.rstrip("。！？」）) ")
    if not t:
        return "その他"
    if _TAGGER is not None:
        words = list(_TAGGER(t))
        if words:
            last = words[-1]
            pos = last.feature.pos1
            if pos == "名詞":
                return "体言止め"
            if pos in ("助動詞", "動詞", "形容詞"):
                m = _ENDING_RE.search(t)
                return m.group(1) if m else pos
            return pos
    m = _ENDING_RE.search(t)
    return m.group(1) if m else "体言止め"


def endings(sents: list[str]) -> Counter:
    return Counter(ending_of(s) for s in sents)


def longest_run(sents: list[str]) -> int:
    run, prev, worst = 0, None, 0
    for s in sents:
        e = ending_of(s)
        run = run + 1 if e == prev else 1
        worst, prev = max(worst, run), e
    return worst


def paragraph_scores(text: str) -> list[dict]:
    """段落ごとに、★の癖が何種類重なっているかを数える。"""
    out = []
    star = {k: v for k, v in {**PATTERNS, **MARKS}.items() if k.startswith("★")}
    for i, p in enumerate(paragraphs(text), 1):
        kinds = [k for k, pat in star.items() if re.search(pat, p, flags=re.M)]
        if period_drop(p):
            kinds.append("★行末だけ句点が落ちた行")
        if kinds:
            out.append({"paragraph": i, "hits": len(kinds), "kinds": kinds, "head": p[:40]})
    return out


# ---------------------------------------------------------------- 本体
def analyze(raw: str, doc_type: str = "business") -> dict:
    text = strip_code(raw)
    sents = sentences(text)
    th = THRESHOLDS[doc_type]

    marks = {k: len(re.findall(v, text, flags=re.M)) for k, v in MARKS.items()}
    marks = {k: v for k, v in marks.items() if v}
    drops = period_drop(text)

    words = {}
    for name, pat in PATTERNS.items():
        found = re.findall(pat, text, flags=re.M)
        if found:
            words[name] = {"count": len(found), "top": Counter(found).most_common(4)}

    rhythm: dict = {"sentences": len(sents)}
    flags: list[str] = []
    if len(sents) >= 5:
        lens = [len(s) for s in sents]
        mean = statistics.mean(lens)
        cv = statistics.pstdev(lens) / mean if mean else 0.0
        commas = sum(s.count("、") for s in sents) / len(sents)
        ec = endings(sents)
        run = longest_run(sents)
        rhythm.update(
            {
                "mean_len": round(mean, 1),
                "cv": round(cv, 3),
                "commas_per_sentence": round(commas, 2),
                "endings": ec.most_common(),
                "taigen": ec.get("体言止め", 0),
                "same_ending_run": run,
            }
        )
        if cv < th["cv_min"]:
            flags.append(f"★文の長さが揃いすぎ（変動係数 {cv:.2f} < {th['cv_min']}）")
        if run >= th["same_ending_run"]:
            flags.append(f"★同じ語尾が {run} 文連続（{th['same_ending_run']} 以上）")
        if commas > th["comma_per_sentence"]:
            flags.append(f"読点が多い（1文あたり {commas:.1f}）")
        if doc_type == "article" and ec.get("体言止め", 0) == 0:
            flags.append("体言止めが 0（読み物なら1〜2か所入れる候補）")

    paras = paragraph_scores(text)
    dense = [p for p in paras if p["hits"] >= th["para_hits"]]
    star_total = sum(v for k, v in marks.items() if k.startswith("★")) + sum(
        v["count"] for k, v in words.items() if k.startswith("★")
    ) + len(drops)

    return {
        "type": doc_type,
        "marks": marks,
        "period_drop": drops[:10],
        "period_drop_count": len(drops),
        "words": words,
        "rhythm": rhythm,
        "flags": flags,
        "dense_paragraphs": dense,
        "star_total": star_total,
    }


def print_report(r: dict) -> None:
    print(f"== 文書の種類: {r['type']}")
    print("== 記号・表記")
    for k, v in r["marks"].items():
        print(f"  {k}: {v}")
    if r["period_drop_count"]:
        print(f"  ★行末だけ句点が落ちた行: {r['period_drop_count']}")
        for d in r["period_drop"][:5]:
            print(f"    - {d[:60]}")
    print("== 語彙・構文・構成")
    for k, v in r["words"].items():
        top = ", ".join(f"{w}×{n}" for w, n in v["top"])
        print(f"  {k}: {v['count']}（{top}）")
    print("== 文末とリズム")
    rh = r["rhythm"]
    if "cv" in rh:
        print(
            f"  文の数 {rh['sentences']} / 平均 {rh['mean_len']:.0f}字 / ばらつき(変動係数) {rh['cv']:.2f}"
            f" / 読点 {rh['commas_per_sentence']}/文 / 同じ語尾の最大連続 {rh['same_ending_run']}"
        )
        print("  語尾: " + ", ".join(f"{k} {v}" for k, v in rh["endings"]))
    else:
        print("  文が5文未満のため省略")
    for f in r["flags"]:
        print(f"  {f}")
    if r["dense_paragraphs"]:
        print("== ★が重なった段落（直す優先）")
        for p in r["dense_paragraphs"]:
            print(f"  段落{p['paragraph']}（{p['hits']}種）: {p['head']}…")
            print("    " + " / ".join(k.lstrip("★") for k in p["kinds"]))
    print(f"== ★の合計: {r['star_total']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--type", choices=sorted(THRESHOLDS), default="business")
    a = ap.parse_args()
    raw = open(a.file, encoding="utf-8").read() if a.file else sys.stdin.read()
    r = analyze(raw, a.type)
    if a.json:
        json.dump(r, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        print_report(r)


if __name__ == "__main__":
    main()
