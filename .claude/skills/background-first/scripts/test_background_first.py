#!/usr/bin/env python3
"""background_first.py を、作ったツール入力と transcript で確かめる。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "hooks"))
from background_first import calls_this_turn, long_segments  # noqa: E402

HOOK = HERE.parent / "hooks" / "background_first.py"


def run(tool, inp, transcript=None, env_extra=None):
    env = dict(os.environ)
    env.pop("BACKGROUND_FIRST_OFF", None)
    env.update(env_extra or {})
    d = {"tool_name": tool, "tool_input": inp}
    if transcript:
        d["transcript_path"] = transcript
    r = subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps(d), capture_output=True, text=True, env=env)
    return r.returncode, r.stdout, r.stderr


class BackgroundFirst(unittest.TestCase):
    def test_long_segments(self):
        self.assertTrue(long_segments("python3 -I .claude/skills/verify-before-done/scripts/precheck.py > p.log 2>&1"))
        self.assertTrue(long_segments("cd /r && ./scripts/validate.sh"))
        self.assertTrue(long_segments("git add -A && git commit -q -m x && python3 -m pytest -q"))
        self.assertTrue(long_segments("npm ci && npm run build"))
        self.assertTrue(long_segments("for i in 1 2; do git clone x; done"))
        self.assertEqual(long_segments("python3 -I .claude/skills/ui-design/scripts/test_ui_check.py"), [])  # 1 本のテストは速い
        self.assertEqual(long_segments("grep -n precheck.py docs/USAGE.md; cat scripts/validate.sh"), [])  # 名前が出るだけ
        self.assertEqual(long_segments("git status -sb && git diff --stat"), [])
        self.assertEqual(long_segments("sed -n 1,5p Makefile"), [])
        self.assertEqual(long_segments("python3 -I .claude/skills/secret-leak-check/scripts/scan.py --diff"), [])  # 速い scan.py は止めない
        self.assertTrue(long_segments("python3 -I .claude/skills/vuln-watch/scripts/scan.py --inventory i.json"))

    def test_foreground_long_is_blocked_and_background_passes(self):
        code, _, err = run("Bash", {"command": "./scripts/validate.sh", "run_in_background": False})
        self.assertEqual(code, 2)
        self.assertIn("run_in_background", err)
        self.assertEqual(run("Bash", {"command": "./scripts/validate.sh > v.log 2>&1", "run_in_background": True})[0], 0)
        self.assertEqual(run("Bash", {"command": "git status"})[0], 0)
        self.assertEqual(run("Bash", {"command": "./scripts/validate.sh"}, env_extra={"BACKGROUND_FIRST_OFF": "1"})[0], 0)
        self.assertEqual(run("Read", {"file_path": "a.md"})[0], 0)

    def test_turn_count_reminder(self):
        def user(t):
            return {"type": "user", "message": {"content": t}}

        def use():
            return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}}

        def result():
            return {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}

        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "t.jsonl"
            rows = [user("前のターン")] + [use(), result()] * 5 + [user("今のターン")] + [use(), result()] * 12
            t.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
            self.assertEqual(calls_this_turn(str(t)), 12)
            code, out, _ = run("Read", {"file_path": "a.md"}, transcript=str(t))
            self.assertEqual(code, 0)
            self.assertIn("12 回", json.loads(out)["hookSpecificOutput"]["additionalContext"])
            rows.append(use())
            t.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
            self.assertEqual(run("Read", {"file_path": "a.md"}, transcript=str(t))[1], "")  # 13 回目は黙る


if __name__ == "__main__":
    unittest.main()
