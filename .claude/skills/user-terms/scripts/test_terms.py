#!/usr/bin/env python3
"""terms.py、terms_check.py、terms_learn.py を、作った文章と transcript と一時リポジトリで確かめる。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from terms import check_table, find, load_table  # noqa: E402

CHECK = HERE.parent / "hooks" / "terms_check.py"
LEARN = HERE.parent / "hooks" / "terms_learn.py"
TERMS = HERE / "terms.py"


def user(t):
    return {"type": "user", "message": {"content": t}}


def assistant(t):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": t}]}}


def result(t):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "content": t}]}}


class Table(unittest.TestCase):
    def test_table_loads_without_conflict(self):
        rows = load_table()
        self.assertGreater(len(rows), 50)
        self.assertEqual(check_table(), 0)

    def test_variants_found_with_user_word(self):
        hits = {(v, u) for _, v, u in find("プルリクをマージして、リポのフォルダを片付けます。Routine は 9:00 JST です。e.g. これ。")}
        self.assertEqual(hits, {("プルリク", "PR"), ("リポ", "リポジトリ"), ("フォルダ", "ディレクトリ"), ("片付け", "整理整頓"),
                                ("Routine", "ルーティン"), ("JST", "日本時間"), ("e.g.", "例えば")})

    def test_user_words_and_boundaries_pass(self):
        self.assertEqual(find("PR をマージし、リポジトリのディレクトリを整理整頓しました。後片付けも済みです。ユーザーの指示です。"), [])
        self.assertEqual(find("secret-leak-check と content-audit と update_pull_request を呼びます。"), [])

    def test_code_quotes_urls_paths_are_skipped(self):
        text = "`repo` と「プルリク」と https://example.com/repo と docs/USAGE.md と <owner>/<repo> は見ない。\n```\nRoutine\n```\n"
        self.assertEqual(find(text), [])
        self.assertEqual(find("---\ndescription: プルリク\n---\n本文"), [])
        self.assertEqual(find("プルリク <!-- user-terms: ignore -->"), [])

    def test_line_numbers_and_only(self):
        hits = find("1 行目\nプルリク\nRoutine\n", only={3})
        self.assertEqual([(n, u) for n, _, u in hits], [(3, "ルーティン")])


class Hooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.t = self.dir / "t.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, rows, active=False, off=False):
        self.t.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        env = dict(os.environ, USER_TERMS_STATE=str(self.dir))
        env.pop("USER_TERMS_OFF", None)
        if off:
            env["USER_TERMS_OFF"] = "1"
        r = subprocess.run([sys.executable, str(CHECK)], input=json.dumps({"transcript_path": str(self.t), "session_id": "s", "stop_hook_active": active}),
                           capture_output=True, text=True, env=env)
        return r.returncode, r.stderr

    def test_reply_with_variant_is_blocked(self):
        code, err = self.run_check([user("直して"), assistant("★ 済み: プルリクを出し、Routine を消しました。")])
        self.assertEqual(code, 2)
        self.assertIn("「プルリク」→「PR」", err)
        self.assertIn("「Routine」→「ルーティン」", err)
        rows = [json.loads(l) for l in (self.dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[-1]["words"], ["プルリク", "Routine"])

    def test_clean_reply_passes(self):
        self.assertEqual(self.run_check([user("直して"), assistant("★ 済み: PR を出し、ルーティンを消しました。")])[0], 0)

    def test_only_this_turn_is_checked(self):
        rows = [user("x"), assistant("プルリク"), user("直して"), assistant("★ 済み: PR を出しました。")]
        self.assertEqual(self.run_check(rows)[0], 0)
        rows = [user("直して"), assistant("呼びます"), result("プルリク"), assistant("★ 済み: PR を出しました。")]
        self.assertEqual(self.run_check(rows)[0], 0)

    def test_retry_and_off_pass(self):
        rows = [user("直して"), assistant("プルリク")]
        self.assertEqual(self.run_check(rows, active=True)[0], 0)
        self.assertEqual(self.run_check(rows, off=True)[0], 0)

    def run_learn(self, args=(), prompt=None):
        env = dict(os.environ, USER_TERMS_STATE=str(self.dir))
        inp = json.dumps({"prompt": prompt}) if prompt is not None else ""
        r = subprocess.run([sys.executable, str(LEARN), *args], input=inp, capture_output=True, text=True, env=env)
        return r.returncode, r.stdout

    def test_learn_counts_user_words_and_skips_notices(self):
        self.assertEqual(self.run_learn(prompt="スキルの更新と統合をやれ"), (0, ""))
        self.assertEqual(self.run_learn(prompt='<wake reason="external-event">スキル</wake>'), (0, ""))
        self.assertEqual(self.run_learn(prompt="Stop hook feedback: スキル"), (0, ""))
        st = json.loads((self.dir / "terms.json").read_text(encoding="utf-8"))
        self.assertEqual(st["n"], 1)
        self.assertEqual(st["words"]["スキル"], 1)

    def test_candidates_exclude_table_words(self):
        for _ in range(2):
            self.run_learn(prompt="プルリクとリポジトリとウィジェットを見て")
        code, out = self.run_learn(["--candidates", "5"])
        self.assertEqual(code, 0)
        self.assertIn("ウィジェット 2", out)
        self.assertNotIn("リポジトリ", out)
        self.assertNotIn("プルリク", out)

    def test_scan_reads_transcript(self):
        self.t.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [user("ルーティンを整理整頓して"), result("プルリク"), assistant("x")]) + "\n", encoding="utf-8")
        code, out = self.run_learn(["--scan", str(self.t)])
        self.assertEqual(code, 0)
        st = json.loads((self.dir / "terms.json").read_text(encoding="utf-8"))
        self.assertEqual(st["words"].get("ルーティン"), 1)
        self.assertNotIn("プルリク", st["words"])

    def test_diff_reports_added_lines_only(self):
        repo = self.dir / "repo"
        repo.mkdir()
        g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True)
        g("init", "-q", "-b", "main")
        (repo / "a.md").write_text("古い行に Routine があっても見ない\n", encoding="utf-8")
        g("add", "."), g("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "a")
        (repo / "a.md").write_text("古い行に Routine があっても見ない\nプルリクを足した行\n", encoding="utf-8")
        (repo / "b.py").write_text("# プルリク\n", encoding="utf-8")
        r = subprocess.run([sys.executable, str(TERMS), "--diff", "HEAD"], cwd=str(repo), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("a.md:2 「プルリク」→「PR」", r.stdout)
        self.assertNotIn("Routine", r.stdout)
        self.assertNotIn("b.py", r.stdout)
        self.assertIn("用語 注意 1 件", r.stdout)


if __name__ == "__main__":
    unittest.main()
