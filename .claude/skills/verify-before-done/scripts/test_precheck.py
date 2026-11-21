#!/usr/bin/env python3
"""precheck.py の差分の検査が、base との分かれ目から後だけを見ることを、一時リポジトリで確かめる。

配布のブランチが base より古いと、base の先頭との差分には、base が直した行が「足した行」として出る。
それを自分の変更と数えると、触っていない行の重大（文字の混入、鍵の形、行末の空白）で、出す前の確認が落ちる。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PRECHECK = Path(__file__).resolve().parent / "precheck.py"
SKILLS = PRECHECK.parent.parent.parent
# 検査に当たらないように、悪い見本は実行のときに組み立てる
HANGUL = "한글이 섞인 줄"
KEY = "gh" + "p_" + "A1b2C3d4E5" * 4
OLD_BAD = f"# 古い文書\n\n{HANGUL}\n\n行末に空白がある行 \n\ntoken = {KEY}\n"
OLD_FIXED = "# 古い文書\n\n直した行です。\n"


class MovedBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.state = Path(self.tmp.name) / "state"
        self.repo.mkdir()
        # 差分の検査のスクリプトだけを置く（テストは置かない。置くと、precheck がそのテストまで流す）
        for rel in ("ja-proofread/scripts/proofread.py", "ja-proofread/scripts/ai_habits.py", "secret-leak-check/scripts/scan.py"):
            if not (SKILLS / rel).exists():
                self.skipTest(f"{rel} が入っていない")
            dst = self.repo / ".claude/skills" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SKILLS / rel, dst)
        self.write("docs/old.md", OLD_BAD)
        self.git("init", "-q", "-b", "main")
        self.commit("分かれ目")
        self.git("remote", "add", "origin", "https://example.invalid/o/Moved.git")
        self.git("checkout", "-q", "-b", "work")
        # main が進む: 悪い行を直し、文書を 1 つ足す
        self.git("checkout", "-q", "main")
        self.write("docs/old.md", OLD_FIXED)
        self.write("docs/theirs.md", "# ほかの人の文書\n\n足した行です。\n")
        self.commit("main が進んだ")
        # 自分のブランチは、関係の無い文書を 1 つ足しただけ
        self.git("checkout", "-q", "work")
        self.write("docs/mine.md", "# 自分の文書\n\n足した行です。\n")
        self.commit("mine")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def precheck(self, base="main"):
        env = dict(os.environ, PR_GATE_STATE=str(self.state))
        r = subprocess.run([sys.executable, "-I", str(PRECHECK), "--base", base], cwd=str(self.repo), capture_output=True, text=True, env=env, timeout=300)
        stamp = json.loads((self.state / "moved.json").read_text(encoding="utf-8"))
        return r, {s["name"]: s for s in stamp["steps"]}, stamp

    def test_what_the_base_fixed_is_not_counted_as_my_change(self):
        r, steps, stamp = self.precheck()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for name in ("git diff --check", "proofread", "secret-leak-check"):
            self.assertTrue(steps[name]["ok"], f"{name}: {steps[name]['tail']}")
        self.assertEqual(stamp["base"], "main")  # 記録の base は、渡された名前のまま
        self.assertEqual(stamp["sha"], self.git("rev-parse", "HEAD"))

    def test_my_own_bad_lines_still_stop(self):
        # 分かれ目から後に自分で足した行は、コミット済みでも、作業ツリーにあるだけでも、見る
        self.write("docs/mine.md", f"# 自分の文書\n\n{HANGUL}\n")
        self.commit("自分で混ぜた")
        self.write("docs/more.md", f"token = {KEY}\n")
        self.git("add", "-A")
        r, steps, _ = self.precheck()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(steps["proofread"]["ok"], steps["proofread"]["tail"])
        self.assertFalse(steps["secret-leak-check"]["ok"], steps["secret-leak-check"]["tail"])

    def test_fork_point(self):
        sys.path.insert(0, str(PRECHECK.parent))
        import precheck
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            fork = precheck.fork_point("main")
            self.assertEqual(fork, self.git("merge-base", "main", "HEAD"))
            self.assertNotEqual(fork, self.git("rev-parse", "main"))
            self.assertEqual(precheck.fork_point("no-such-ref"), "no-such-ref")  # 分かれ目が取れなければ base のまま
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
