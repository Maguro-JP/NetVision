#!/usr/bin/env python3
"""tidy_report.py を、作ったリポジトリで確かめる。"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "tidy_report.py"


class TidyReport(unittest.TestCase):
    def test_lists_junk_and_empty_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True)
            g("init", "-q", "-b", "main")
            (repo / "a.py").write_text("x", encoding="utf-8")
            (repo / "cache" / "__pycache__").mkdir(parents=True)
            (repo / "cache" / "__pycache__" / "a.pyc").write_text("x", encoding="utf-8")
            (repo / "empty").mkdir()
            g("add", "-A")
            g("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "a")
            env = dict(os.environ, PATH="/nonexistent")  # gh と git は絶対パスで呼ばない設計ではないので、gh が無い状態を作る
            r = subprocess.run([sys.executable, "-I", str(SCRIPT)], cwd=str(repo), capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("余計なファイル", r.stdout)
            self.assertIn("a.pyc", r.stdout)
            self.assertIn("`empty`", r.stdout)
            self.assertIn("リモートブランチ", r.stdout)


if __name__ == "__main__":
    unittest.main()
