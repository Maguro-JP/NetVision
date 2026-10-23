#!/usr/bin/env python3
"""focus.py の動きを、一時ディレクトリの表で確かめる。"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "focus.py"
HEAD = "| # | 成果物 | 完成条件 | 状態 | 残り | 更新 |\n|---|---|---|---|---|---|\n"


def run(cwd: str, table: str | None) -> str:
    if table is not None:
        p = Path(cwd) / "docs" / "reports"
        p.mkdir(parents=True, exist_ok=True)
        (p / "deliverables.md").write_text("# 成果物\n\n" + HEAD + table, encoding="utf-8")
    env = dict(os.environ)
    env.pop("FINISH_FIRST_OFF", None)
    r = subprocess.run([sys.executable, "-I", str(HOOK)], cwd=cwd, input="{}", capture_output=True, text=True, env=env, check=True)
    return r.stdout


class Focus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_unfinished_row_is_injected(self):
        out = run(self.tmp.name, "| 1 | ログイン | `npm test -- login` が通る | 完成 | PR #3 | 2026-10-20 |\n"
                                  "| 2 | 設定画面 | 設定を保存して再読み込み後も表示される | 進行中 | 保存の API | 2026-10-24 |\n"
                                  "| 3 | 通知 | 通知が届く | 未着手 | | |\n")
        self.assertIn("今の成果物 2: 設定画面（進行中）", out)
        self.assertIn("残り: 保存の API", out)
        self.assertNotIn("通知", out)
        self.assertNotIn("注意", out)

    def test_weak_condition_is_flagged(self):
        out = run(self.tmp.name, "| 1 | 検索 | 検索が動く | 進行中 | | |\n")
        self.assertIn("注意: 成果物 1 の完成条件", out)

    def test_all_done(self):
        out = run(self.tmp.name, "| 1 | ログイン | `npm test -- login` が通る | 完成 | PR #3 | 2026-10-20 |\n")
        self.assertIn("未完成はない", out)

    def test_no_table_is_silent(self):
        self.assertEqual("", run(self.tmp.name, None))


if __name__ == "__main__":
    unittest.main()
