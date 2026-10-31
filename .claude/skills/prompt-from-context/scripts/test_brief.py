#!/usr/bin/env python3
"""brief.py を、作った transcript で確かめる。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "brief.py"


def transcript(path: Path, rows) -> None:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def user(text):
    return {"type": "user", "message": {"content": text}}


def tool_result():
    return {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}


def assistant(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


ROWS = [
    user("絵のアプリ用のスキルを作って"),
    assistant("★ スキル: 作りました。PR は https://example.invalid/pull/1 です。"),
    user("<wake reason=\"external-event\">通知</wake>"),
    tool_result(),
    user("週報も書いて"),
    user("This session is being continued from a previous conversation that ran out of context. Summary: 1. Primary Request"),
    assistant("❕ 週報: 書きました。\n❔ 配布: 8 本に今配りますか。"),
]


class Brief(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.t = self.dir / "t.jsonl"
        transcript(self.t, ROWS)

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, prompt: str, off: bool = False):
        env = dict(os.environ, PROMPT_FROM_CONTEXT_STATE=str(self.dir / "state"))
        env.pop("PROMPT_FROM_CONTEXT_OFF", None)
        if off:
            env["PROMPT_FROM_CONTEXT_OFF"] = "1"
        r = subprocess.run([sys.executable, "-I", str(HOOK)],
                           input=json.dumps({"prompt": prompt, "transcript_path": str(self.t), "session_id": "s1"}),
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_short_prompt_gets_context(self):
        out = self.run_hook("やれ")
        self.assertIn("読み取り", out)
        self.assertIn("前の一言: 週報も書いて", out)
        self.assertIn("自分の行: ★ スキル", out)
        self.assertIn("答え待ちの ❔: ❔ 配布", out)

    def test_notice_and_tool_result_are_not_context(self):
        out = self.run_hook("やれ")
        self.assertNotIn("wake", out)
        self.assertNotIn("continued", out)
        self.assertNotIn("ok", out.split("直前の文脈:")[1])

    def test_long_prompt_is_silent(self):
        self.assertEqual(self.run_hook("絵のアプリの app/launch.py で、ウィンドウが開かないときにサーバーだけを残す流れを、tests を足してから直してください。"), "")

    def test_hint_in_longer_prompt_counts(self):
        out = self.run_hook("さっきのスキルと同じようにテストも足して、validate が通るまでやって")
        self.assertIn("読み取り", out)

    def test_slash_command_is_silent(self):
        self.assertEqual(self.run_hook("/auto-dev"), "")

    def test_off_switch(self):
        self.assertEqual(self.run_hook("やれ", off=True), "")

    def test_metrics_written(self):
        self.run_hook("直して")
        rows = (self.dir / "state" / "metrics.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertTrue(json.loads(rows[0])["pending"])


if __name__ == "__main__":
    unittest.main()
