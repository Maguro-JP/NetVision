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

    def run_hook(self, assistant: str, active: bool = False, user: str = "直して"):
        t = self.dir / "t.jsonl"
        transcript(t, user, assistant)
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

    def test_pushback_after_correction_is_blocked(self):
        code, err = self.run_hook("↩ 形: しかし、その形だと壊れます。https://example.invalid/x", user="違う。言った通りにして")
        self.assertEqual(code, 2)
        self.assertIn("反論", err)

    def test_acknowledgement_after_correction_passes(self):
        self.assertEqual(self.run_hook("↩ 形: 言われた形に直しました。README.md:3 を見てください。", user="違う。言った通りにして")[0], 0)

    def test_quoted_marker_after_correction_passes(self):
        # 検出する語を説明のために引用しただけの返事は差し戻さない
        self.assertEqual(self.run_hook("★ フック: 訂正への返事に「しかし」「ただし」があれば差し戻します。docs/x.md に書きました。", user="反抗するな。指示通り動け")[0], 0)

    def test_pushback_without_correction_passes(self):
        self.assertEqual(self.run_hook("❕ 比較: A は速い。ただし B は小さい。docs/x.md に表があります。", user="A と B を比べて")[0], 0)

    def test_two_directives_without_marks_are_blocked(self):
        code, err = self.run_hook("❕ 1つ目は見ました。", user="テストを足して スキルも配って")
        self.assertEqual(code, 2)
        self.assertIn("指示が 2 個", err)

    def test_two_directives_with_marks_pass(self):
        self.assertEqual(self.run_hook("★ テスト: 足しました。tests/a.py\n↩ 配布: 配りました。https://example.invalid/pull/2", user="テストを足して スキルも配って")[0], 0)
        self.assertEqual(self.run_hook("❕ テストは足しました（tests/a.py）。配布はやっていない。", user="テストを足して スキルも配って")[0], 0)

    def test_plan_lines_are_counted(self):
        self.run_hook("❕ 直して validate を通します。")
        rows = (self.dir / "state" / "metrics.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(json.loads(rows[-1])["plan_lines"], 1)

    def test_stop_hook_active_passes(self):
        self.assertEqual(self.run_hook("直しました。", active=True)[0], 0)

    def test_metrics_written(self):
        self.run_hook("そうです。")
        rows = (self.dir / "state" / "metrics.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertFalse(json.loads(rows[0])["claim"])


if __name__ == "__main__":
    unittest.main()
