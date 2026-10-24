#!/usr/bin/env python3
"""directives.py の動きを、一時ディレクトリの保持先で確かめる。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "directives.py"


def run(prompt: str, state: str, sid: str = "t1") -> str:
    env = dict(os.environ, FOLLOW_INSTRUCTIONS_STATE=state)
    env.pop("FOLLOW_INSTRUCTIONS_OFF", None)
    p = subprocess.run(
        [sys.executable, "-I", str(HOOK)],
        input=json.dumps({"session_id": sid, "prompt": prompt}),
        capture_output=True, text=True, env=env, check=True,
    )
    return p.stdout


class Directives(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_directive_is_kept_and_reinjected(self):
        self.assertIn("ブランチは claude/ で始めて", run("ブランチは claude/ で始めて", self.state))
        out = run("README を読んで", self.state)
        self.assertIn("ブランチは claude/ で始めて", out)

    def test_question_and_paste_are_not_kept(self):
        self.assertEqual("", run("どのブランチで作業してる?", self.state))
        self.assertEqual("", run("必ず " + "あ" * 500, self.state))

    def test_correction_injects_protocol_and_counts(self):
        out = run("違う。言った通りに JST で書いて", self.state)
        self.assertIn("訂正を受けた", out)
        self.assertIn("（訂正）違う。言った通りに JST で書いて", out)
        self.assertNotIn("回目", out)
        out = run("勝手に description を縮めるな", self.state)
        self.assertIn("訂正は 2 回目", out)
        self.assertIn("ユーザーの決めたこと", out)

    def test_system_prompt_is_ignored(self):
        self.assertEqual("", run("<wake reason=\"external-event\">必ず見ろ</wake>", self.state))

    def test_several_directives_are_counted(self):
        out = run("テストを足して スキルも配って CLAUDE.md にも書け", self.state)
        self.assertIn("指示が 3 個ある", out)
        self.assertNotIn("個ある", run("README を直して", self.state))

    def test_off_switch(self):
        env = dict(os.environ, FOLLOW_INSTRUCTIONS_STATE=self.state, FOLLOW_INSTRUCTIONS_OFF="1")
        p = subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps({"session_id": "x", "prompt": "違う"}),
                           capture_output=True, text=True, env=env, check=True)
        self.assertEqual("", p.stdout)


if __name__ == "__main__":
    unittest.main()
