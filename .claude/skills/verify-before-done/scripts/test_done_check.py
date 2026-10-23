#!/usr/bin/env python3
"""done_check.py を、作った transcript で確かめる。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "done_check.py"


def transcript(path: Path, user: str, assistant: str) -> None:
    rows = [
        {"type": "user", "message": {"content": "前の発言"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "前の返答。直しました"}]}},
        {"type": "user", "message": {"content": user}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": assistant}]}},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


class DoneCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, assistant: str, active: bool = False):
        t = self.dir / "t.jsonl"
        transcript(t, "直して", assistant)
        env = dict(os.environ, DONE_CHECK_STATE=str(self.dir / "state"))
        env.pop("DONE_CHECK_OFF", None)
        r = subprocess.run([sys.executable, "-I", str(HOOK)],
                           input=json.dumps({"transcript_path": str(t), "stop_hook_active": active, "session_id": "s1"}),
                           capture_output=True, text=True, env=env)
        return r.returncode, r.stderr

    def test_claim_without_evidence_is_blocked(self):
        code, err = self.run_hook("↩ 修正: 言われた箇所を直しました。")
        self.assertEqual(code, 2)
        self.assertIn("根拠", err)

    def test_claim_with_url_passes(self):
        code, _ = self.run_hook("★ 修正: https://github.com/o/r/pull/1 をマージしました。")
        self.assertEqual(code, 0)

    def test_claim_with_hash_or_path_passes(self):
        self.assertEqual(self.run_hook("❕ 直しました。main は b727f51 です。")[0], 0)
        self.assertEqual(self.run_hook("❕ 直しました。scripts/validate.sh が通りました。")[0], 0)

    def test_honest_unverified_passes(self):
        self.assertEqual(self.run_hook("❕ 設定を足しました。動かしては確かめていない。")[0], 0)

    def test_two_questions_are_blocked(self):
        code, err = self.run_hook("❔ A か B か。\n❔ いつまでに。")
        self.assertEqual(code, 2)
        self.assertIn("1 ターンに 1 つまで", err)

    def test_stop_hook_active_passes(self):
        self.assertEqual(self.run_hook("直しました。", active=True)[0], 0)

    def test_metrics_written(self):
        self.run_hook("そうです。")
        rows = (self.dir / "state" / "metrics.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertFalse(json.loads(rows[0])["claim"])


if __name__ == "__main__":
    unittest.main()
