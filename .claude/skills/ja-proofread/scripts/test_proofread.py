#!/usr/bin/env python3
"""proofread.py の自己試験。拾うべき形を拾い、正しい文を拾わないことを見る。

  python3 -I .claude/skills/ja-proofread/scripts/test_proofread.py
"""
import subprocess, sys, tempfile, textwrap, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROOF = HERE / "proofread.py"


def proof(*args, stdin=None):
    r = subprocess.run([sys.executable, "-I", str(PROOF), *args], input=stdin, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


BAD = textwrap.dedent("""\
    这は中国語の字が混ざった文です。
    사용자が読む。
    設定のの値を見る。
    Now, 日本語で書き出していない。
    **強調**を記号で書いた。
    全角のＡＢＣと１２３、半角ｶﾅ。
    これは文です.次の文,も変。
    おっしゃられたとおりに、確認させていただきます。
    結果を見れる。食べれないものは出れる。
    設定を変更することができます。検証を行います。というものがある。
    出来るだけ早く見て下さい。既に全て更に。
    この文は読点が多くて途中で何度も立ち止まることになり、読む人は息継ぎの場所を探しながら読み進め、どこが主語でどこが述語なのかを考えているうちに、最初に何を言いたかったのかを忘れてしまい、結局は最初から読み直すことになり、それでも分からないので諦めてしまう。
    サーバを再起動する。サーバーの設定を見る。
    枝を本線にマージして、門を通す。利用者が決める。
    このスキルは並列開発を可能にします。テストを追加する必要があります。問題ないように見えます。
    指摘 → 閾値 0.8 → 通過率 70%。検査は通過。マージは完了。次は配布。
    直しました。確かめました。送りました。
    """)

OK = textwrap.dedent("""\
    国と学と会と体と点は日本語の文字。
    ものの、見れば分かる。入れる。来れば良い。枝分かれ、専門、部門、周期、印象、1周、3周続けて。
    設定は `settings.local.json` にある。URL は https://example.com/a.b.c だ。
    版は 1.2.3 で、英語の文は English sentence here. と書いてよい。
    検査が通りました。マージも終わっています。次は配布です。観察→改修の順に進める。
    """)


class Fixture(unittest.TestCase):
    def test_bad_text(self):
        code, out = proof("--stdin", stdin=BAD)
        self.assertEqual(code, 1, out)
        for name in ["ハングルの混入", "中国語の字の混入", "助詞の重複", "英語の出だし", "強調の記号", "全角の英数字",
                     "半角カナ", "和文の中の半角句読点", "二重敬語", "過剰な敬語", "ら抜き", "翻訳調",
                     "長い文", "読点が多い", "出来る→できる", "下さい→ください", "漢字を開く", "ゆれ: サーバ",
                     "勝手な訳語", "「ブランチ」に", "「ゲート」に", "「ユーザー」に",
                     "矢印でつないだ羅列", "体言止めの連発", "文末の繰り返し", "可能に"]:
            self.assertIn(name, out, f"{name} を拾っていない\n{out}")

    def test_ok_text(self):
        code, out = proof("--stdin", stdin=OK)
        self.assertEqual(code, 0, out)
        for bad in ["中国語の文字の混入", "助詞の重複", "ら抜き", "英語の出だし", "日本語の中の半角句読点", "勝手な訳語",
                    "体言止めの連発", "文末の繰り返し", "矢印でつないだ羅列"]:
            self.assertNotIn(bad, out, f"{bad} を拾った\n{out}")

    def test_code_and_tables_skipped(self):
        text = "表と囲みは見ない。\n\n| 这 | のの |\n|---|---|\n\n```\n这 のの **x**\n```\n"
        code, out = proof("--stdin", stdin=text)
        self.assertEqual(code, 0, out)

    def test_strict_and_files(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t, "a.md")
            f.write_text("設定を変更することができます。\n")
            code, _ = proof(str(f))
            self.assertEqual(code, 0)
            code, _ = proof("--strict", str(f))
            self.assertEqual(code, 1)

    def test_textlint_if_installed(self):
        import os
        d = Path(os.environ.get("CIEL_TEXTLINT_DIR") or Path.home() / ".cache/ciel-textlint")
        if not (d / "node_modules/.bin/textlint").exists():
            self.skipTest("textlint が入っていない")
        _, out = proof("--stdin", stdin="私はは思う。見れるかもしれない。\n")
        self.assertIn("textlint", out)
        _, out2 = proof("--stdin", "--no-textlint", stdin="私はは思う。\n")
        self.assertNotIn("textlint ", out2.split("\n  textlint は")[0])

    def test_tone_mix(self):
        text = "\n".join(["これは敬体です。", "これも敬体です。", "敬体で書きます。", "常体で書く。", "常体である。", "混ぜた。"]) + "\n"
        _, out = proof("--stdin", stdin=text)
        self.assertIn("文体の混在", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
