#!/usr/bin/env python3
"""focus.py の動きを、一時ディレクトリの表で確かめる。"""

import json
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

    def test_prompt_turns_are_spaced_out(self):
        table = "| 2 | 設定画面 | 設定を保存して再読み込み後も表示される | 進行中 | 保存の API | 2026-10-24 |\n"
        run(self.tmp.name, table)
        state = tempfile.mkdtemp()

        def call(payload):
            env = dict(os.environ, FINISH_FIRST_STATE=state, FINISH_FIRST_EVERY="3")
            env.pop("FINISH_FIRST_OFF", None)
            return subprocess.run([sys.executable, "-I", str(HOOK)], cwd=self.tmp.name, input=json.dumps(payload), capture_output=True, text=True, env=env, check=True).stdout

        self.assertIn("今の成果物 2", call({"session_id": "s1", "hook_event_name": "SessionStart"}))  # 始まりは出す
        self.assertEqual("", call({"session_id": "s1", "prompt": "続けて"}))  # 同じ行は出し直さない
        self.assertEqual("", call({"session_id": "s1", "prompt": "次"}))
        self.assertIn("今の成果物 2", call({"session_id": "s1", "prompt": "3 ターン目"}))  # N ターンごとに出す
        (Path(self.tmp.name) / "docs/reports/deliverables.md").write_text("# 成果物\n\n" + HEAD + table.replace("保存の API", "画面の文言"), encoding="utf-8")
        self.assertIn("残り: 画面の文言", call({"session_id": "s1", "prompt": "進んだ"}))  # 行が変わったら出す
        self.assertIn("今の成果物 2", call({"session_id": "s2", "prompt": "別のセッションの最初"}))

    def test_long_cells_are_cut(self):
        out = run(self.tmp.name, "| 1 | " + "名" * 100 + " | " + "条" * 200 + "が通る | 進行中 | " + "残" * 200 + " | |\n")
        self.assertLess(len(out), 330)

    def test_no_table_is_silent(self):
        self.assertEqual("", run(self.tmp.name, None))


if __name__ == "__main__":
    unittest.main()
