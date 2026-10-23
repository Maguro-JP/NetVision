#!/usr/bin/env python3
"""audit.py の自己テスト。仕込んだ値を拾い、拾ってはいけない例を拾わないことを見る。

  python3 -I .claude/skills/content-audit/scripts/test_audit.py
"""
import os, subprocess, sys, tempfile, textwrap, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUDIT = HERE / "audit.py"

# 仕込む値は組み立てて作る。このテストファイル自身が validate.sh や audit.py に拾われないため
EMAIL = "taro" + "@" + "example-corp" + ".jp"
SESS = "session_01" + "A" * 22
OK1 = "noreply" + "@" + "example.com"                 # 返信不可は拾わない
OK2 = "bot" + "@" + "users.noreply.github.com"         # GitHub の匿名アドレスは拾わない
URL = "https://claude" + ".ai/code/" + SESS


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@users.noreply.github.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@users.noreply.github.com"})


def audit(cwd, *args):
    r = subprocess.run([sys.executable, "-I", str(AUDIT), *args], cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.d = d
        git(d, "init", "-q", "-b", "main")
        (d / "ok.md").write_text(textwrap.dedent(f"""\
            連絡は {OK1} か {OK2} へ
            設定は settings.local.json、作業は /home/user/work/ と /home/runner/x/
            引数は "$1" と ${{2}}、版は 1.2.3、例示用のアドレスは 192.0.2.10 と 203.0.113.5
            時刻 0 15 * * 5、日付 2026-10-18、ループバック 127.0.0.1
            接続の例 postgres://user:password@host:5432/dbname
            バカンスの予定、カスタム設定、「まとめて」と言われたら、コードのせいだと疑った
            """))
        (d / "bad.md").write_text(textwrap.dedent(f"""\
            担当: {EMAIL}
            {URL}
            起動するのは trig_01{'B' * 22} の方
            端末の /Users/hanako.sato/src/ と C:\\Users\\Hanako\\Documents\\
            〒100-0001 東京都、電話 03-1234-5678
            社内は 10.20.30.40 と build.corp.internal、外は 8.8.4.4
            資料 https://docs.google.com/document/d/abc/edit と https://x.slack.com/archives/C01/p1
            今月は $12.50、先月は 1,200円、合計 $3,400
            ignored: {EMAIL} content-audit: ignore
            「お前の作ったものは全部やり直しだ」と言われた。ユーザーが怒ったので直した
            レビューで無能と言われた。遅れたのはユーザーのせい。初歩的なミスをする素人
            """))
        (d / "svc.pem").write_text("x")
        (d / ".env").write_text("A=1")
        (d / ".env.example").write_text("A=")
        (d / "shot.png").write_bytes(b"\x89PNG")
        (d / "dump.sql").write_text("select 1")
        git(d, "add", "-A")
        git(d, "commit", "-q", "-m", "first\n\n" + URL)
        (d / "secret.key").write_text("k")
        git(d, "add", "-A")
        git(d, "commit", "-q", "-m", "add key")
        git(d, "rm", "-q", "secret.key")
        git(d, "commit", "-q", "-m", "drop key")
        (d / "names.txt").write_text("owner/SecretProject\nshortx\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_tree_finds_each_rule(self):
        code, out = audit(self.d, "--tree")
        self.assertEqual(code, 1, out)
        self.assertNotIn("names.txt（", out)   # 一覧のファイル自身は拾わない
        for name in ["メールアドレス", "セッションの URL", "セッション・Routine の ID", "ユーザー名入りのパス",
                     "郵便番号・住所", "電話番号", "私有アドレス", "内部ホスト名", "グローバルアドレス",
                     "非公開サービスへのリンク", "費用の数字", "認証情報のファイル", ".env の実体",
                     "データの持ち出し", "画像・動画・PDF",
                     "人への罵倒・侮蔑", "人の発言の引用", "感情・叱責の描写", "責任を人に向ける表現", "見下し・皮肉"]:
            self.assertIn(name, out, f"{name} を拾っていない\n{out}")
        self.assertIn("まだ止められる", out)

    def test_tree_skips_examples(self):
        code, out = audit(self.d, "--tree")
        for bad in ["ok.md", "settings.local", "/home/user/", "192.0.2.10", "127.0.0.1",
                    ".env.example", "password@host"]:
            self.assertNotIn(bad, out, f"{bad} を拾った\n{out}")

    def test_values_are_redacted(self):
        _, out = audit(self.d, "--tree")
        self.assertNotIn(EMAIL, out)
        self.assertNotIn("hanako.sato", out)
        _, shown = audit(self.d, "--tree", "--show")
        self.assertIn(EMAIL, shown)

    def test_ignore_mark_and_allowlist(self):
        _, out = audit(self.d, "--tree")
        self.assertEqual(out.count("bad.md:1（"), 1)      # 1行目は拾う
        self.assertNotIn("bad.md:9（", out)                # ignore の行は拾わない
        (self.d / ".contentallow").write_text("path:dump.sql\n8\\.8\\.4\\.4\n")
        try:
            _, out2 = audit(self.d, "--tree")
            self.assertNotIn("dump.sql", out2)
            self.assertNotIn("グローバルアドレス", out2)
        finally:
            (self.d / ".contentallow").unlink()

    def test_names_list(self):
        _, out = audit(self.d, "--tree")
        self.assertIn("非公開の名前は検査していない", out)
        (self.d / "note.md").write_text("see github.com/owner/SecretProject and secretproject and shortx\n")
        try:
            _, out2 = audit(self.d, "--tree", "--names", "names.txt")
            self.assertEqual(out2.count("非公開リポジトリの名前"), 3, out2)
            self.assertNotIn("非公開の名前は検査していない", out2)
        finally:
            (self.d / "note.md").unlink()

    def test_diff(self):
        (self.d / "new.md").write_text(f"追加 {EMAIL}\n")
        git(self.d, "add", "new.md")
        try:
            code, out = audit(self.d, "--diff")
            self.assertEqual(code, 1)
            self.assertIn("new.md:1", out)
            self.assertNotIn("bad.md", out)
        finally:
            git(self.d, "rm", "-q", "-f", "--cached", "new.md")
            (self.d / "new.md").unlink()

    def test_history(self):
        code, out = audit(self.d, "--history")
        self.assertEqual(code, 1)
        self.assertIn("コミットメッセージ", out)                # 同じ値でも場所は並ぶ
        self.assertIn("secret.key（消したが履歴に残る）", out)
        self.assertIn("すでに共有", out)          # origin が無いので public か分からない
        self.assertIn("著者 t", out)
        self.assertNotIn("著者のメールアドレス", out)     # noreply は個人を指さない

    def test_clean_tree_exits_zero(self):
        with tempfile.TemporaryDirectory() as t:
            git(t, "init", "-q", "-b", "main")
            Path(t, "a.md").write_text("きれい\n")
            git(t, "add", "-A")
            code, out = audit(t, "--tree")
            self.assertEqual(code, 0, out)
            self.assertIn("検出されなかった", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
