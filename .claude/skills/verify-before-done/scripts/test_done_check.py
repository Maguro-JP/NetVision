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


def transcript(path: Path, user: str, assistant: str, tools, results) -> None:
    rows = [
        {"type": "user", "message": {"content": "前の発言"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "前のターンの出力 merged true"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "前の返答。直しました"}]}},
        {"type": "user", "message": {"content": user}},
    ]
    for t in tools:
        rows.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": t, "input": {}}]}})
    for r in results:
        rows.append({"type": "user", "message": {"content": [{"type": "tool_result", "content": r}]}})
    rows.append({"type": "assistant", "message": {"content": [{"type": "text", "text": assistant}]}})
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


class DoneCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, assistant: str, active: bool = False, user: str = "直して", tools=("Bash",), results=("ok",)):
        t = self.dir / "t.jsonl"
        transcript(t, user, assistant, tools, results)
        env = dict(os.environ, DONE_CHECK_STATE=str(self.dir / "state"))
        env.pop("DONE_CHECK_OFF", None)
        r = subprocess.run([sys.executable, "-I", str(HOOK)],
                           input=json.dumps({"transcript_path": str(t), "stop_hook_active": active, "session_id": "s1"}),
                           capture_output=True, text=True, env=env)
        return r.returncode, r.stderr

    def last_metric(self):
        rows = (self.dir / "state" / "metrics.jsonl").read_text(encoding="utf-8").strip().splitlines()
        return json.loads(rows[-1])

    # 一般の完了の語
    def test_claim_without_evidence_is_blocked(self):
        code, err = self.run_hook("↩ 修正: 言われた箇所を直しました。")
        self.assertEqual(code, 2)
        self.assertIn("根拠", err)

    def test_honest_unverified_passes(self):
        self.assertEqual(self.run_hook("❕ 設定を足しました。動かしては確かめていない。", tools=())[0], 0)

    def test_two_questions_are_blocked(self):
        code, err = self.run_hook("❔ A か B か。\n❔ いつまでに。")
        self.assertEqual(code, 2)
        self.assertIn("1 ターンに 1 つまで", err)

    # ツールの出力との照合
    def test_merge_claim_needs_merge_output(self):
        code, err = self.run_hook("★ 修正: https://example.invalid/pull/1 をマージしました。")
        self.assertEqual(code, 2)
        self.assertIn("マージの結果が無い", err)
        self.assertEqual(self.run_hook("★ 修正: https://example.invalid/pull/1 をマージしました。",
                                       tools=("mcp__github__merge_pull_request",), results=('{"sha":"abc","merged":true}',))[0], 0)

    def test_push_claim_needs_push_output(self):
        self.assertEqual(self.run_hook("❕ 配布: 8 本に配りました。", results=("ok",))[0], 2)
        self.assertEqual(self.run_hook("❕ 配布: 8 本に配りました。", results=("   1a2b3c4..5d6e7f8  main -> main",))[0], 0)

    def test_commit_claim_needs_commit_output(self):
        self.assertEqual(self.run_hook("❕ コミットしました。", results=("ok",))[0], 2)
        self.assertEqual(self.run_hook("❕ コミットしました。", results=(" 2 files changed, 9 insertions(+)",))[0], 0)

    def test_pass_claim_needs_pass_output_and_no_failure(self):
        self.assertEqual(self.run_hook("❕ テストが通りました。scripts/x.py", results=("ok",))[0], 2)
        self.assertEqual(self.run_hook("❕ テストが通りました。scripts/x.py", results=("Ran 7 tests\n\nOK",))[0], 0)
        code, err = self.run_hook("❕ テストが通りました。scripts/x.py", results=("Ran 7 tests\n\nFAILED (failures=1)",))
        self.assertEqual(code, 2)
        self.assertIn("FAILED", err)

    def test_look_claim_needs_a_tool_call(self):
        code, err = self.run_hook("❕ 通知: 11 件を読みました。", tools=(), results=())
        self.assertEqual(code, 2)
        self.assertIn("ツールを 1 つも呼んでいない", err)
        self.assertEqual(self.run_hook("❕ 通知: 11 件を読みました。", tools=("ReadNotifications",), results=("x",))[0], 0)
        # 「〜と読みました」は読み取りの言い方で、見たという主張ではない
        self.assertEqual(self.run_hook("↩ 読み取り: 「やれ」を直前の ❔ への答えと読みました。", tools=(), results=())[0], 0)

    def test_edit_claim_needs_edit_tool(self):
        code, err = self.run_hook("❕ 直しました。docs/a.md に書きました。", tools=("Read",), results=("x",))
        self.assertEqual(code, 2)
        self.assertIn("書き込むツール", err)
        self.assertEqual(self.run_hook("❕ 直しました。docs/a.md に書きました。", tools=("Edit",), results=("x",))[0], 0)

    def test_retraction_is_counted_not_blocked(self):
        code, _ = self.run_hook("❕ 前の返答は間違えていました。マージはまだです。", tools=(), results=())
        self.assertEqual(code, 0)
        self.assertTrue(self.last_metric()["retraction"])

    # 訂正への返事
    def test_pushback_after_correction_is_blocked(self):
        code, err = self.run_hook("↩ 形: しかし、その形だと壊れます。https://example.invalid/x", user="違う。言った通りにして")
        self.assertEqual(code, 2)
        self.assertIn("反論", err)

    def test_acknowledgement_after_correction_passes(self):
        self.assertEqual(self.run_hook("↩ 形: 言われた形に直しました。README.md:3 を見てください。", user="違う。言った通りにして", tools=("Edit",))[0], 0)

    def test_quoted_marker_after_correction_passes(self):
        self.assertEqual(self.run_hook("★ フック: 訂正への返事に「しかし」「ただし」があれば差し戻します。docs/x.md に書きました。", user="反抗するな。指示通り動け")[0], 0)

    def test_pushback_without_correction_passes(self):
        self.assertEqual(self.run_hook("❕ 比較: A は速い。ただし B は小さい。docs/x.md に表があります。", user="A と B を比べて")[0], 0)

    # 指示の数
    def test_two_directives_without_marks_are_blocked(self):
        code, err = self.run_hook("❕ 1つ目は見ました。", user="テストを足して スキルも配って")
        self.assertEqual(code, 2)
        self.assertIn("指示が 2 個", err)

    def test_two_directives_with_marks_pass(self):
        self.assertEqual(self.run_hook("★ テスト: 足しました。tests/a.py\n↩ 配布: 配りました。https://example.invalid/pull/2",
                                       user="テストを足して スキルも配って", tools=("Edit", "Bash"), results=(" * [new branch] x -> x",))[0], 0)
        self.assertEqual(self.run_hook("❕ テストは足しました（tests/a.py）。配布はやっていない。", user="テストを足して スキルも配って")[0], 0)

    def test_plan_lines_are_counted(self):
        self.run_hook("❕ 直して validate を通します。")
        self.assertEqual(self.last_metric()["plan_lines"], 1)

    def test_stop_hook_active_passes(self):
        self.assertEqual(self.run_hook("直しました。", active=True)[0], 0)

    def test_metrics_written(self):
        self.run_hook("そうです。")
        m = self.last_metric()
        self.assertFalse(m["claim"])
        self.assertEqual(m["unbacked"], [])


if __name__ == "__main__":
    unittest.main()
