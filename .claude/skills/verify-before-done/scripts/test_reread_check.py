#!/usr/bin/env python3
"""reread_check.py を、作った transcript で確かめる。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "reread_check.py"


def user(t):
    return {"type": "user", "message": {"content": t}}


def use(name, **inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def result(t="ok"):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "content": t}]}}


def text(t):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": t}]}}


class Reread(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.t = self.dir / "t.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, rows, active=False, off=False):
        self.t.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        env = dict(os.environ, DONE_CHECK_STATE=str(self.dir))
        env.pop("REREAD_CHECK_OFF", None)
        if off:
            env["REREAD_CHECK_OFF"] = "1"
        r = subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps({"transcript_path": str(self.t), "session_id": "s", "stop_hook_active": active}),
                           capture_output=True, text=True, env=env)
        return r.returncode, r.stderr

    def test_edit_without_reread_is_blocked(self):
        rows = [user("直して"), use("Edit", file_path="a.md", old_string="x", new_string="y"), result(), text("★ 直しました。")]
        code, err = self.run_hook(rows)
        self.assertEqual(code, 2)
        self.assertIn("読み直していない", err)

    def test_edit_then_read_passes(self):
        rows = [user("直して"), use("Edit", file_path="a.md", old_string="x", new_string="y"), result(), use("Read", file_path="a.md"), result("y"), text("★ 直しました。\n❕ 疑い: 動かしていない。")]
        self.assertEqual(self.run_hook(rows)[0], 0)
        rows = [user("直して"), use("Edit", file_path="a.md", old_string="x", new_string="y"), result(), use("Bash", command="git diff -- a.md"), result("+y"), text("★\n❕ 疑い: 1 行だけ。")]
        self.assertEqual(self.run_hook(rows)[0], 0)

    def test_work_without_doubt_line_is_blocked(self):
        rows = [user("直して"), use("Edit", file_path="a.py", old_string="x", new_string="y"), result(), use("Bash", command="python3 -I scripts/test_a.py"), result("OK"), text("★ 直しました。")]
        code, err = self.run_hook(rows)
        self.assertEqual(code, 2)
        self.assertIn("疑い", err)
        rows = [user("見て"), use("Bash", command="ls"), result("a"), text("★ 直しました。")]  # 編集は無いが完了の主張がある
        self.assertEqual(self.run_hook(rows)[0], 2)
        rows = [user("見て"), use("Bash", command="ls"), result("a"), text("★ 1 件です。")]  # 主張も作業も無い
        self.assertEqual(self.run_hook(rows)[0], 0)

    def test_markdown_edit_needs_text_not_only_tests(self):
        rows = [user("直して"), use("Edit", file_path="a.md", old_string="x", new_string="y"), result(), use("Bash", command="./scripts/validate.sh"), result("誤り 0"), text("★")]
        code, err = self.run_hook(rows)
        self.assertEqual(code, 2)
        self.assertIn("文を読んでいない", err)
        rows = [user("直して"), use("Edit", file_path="a.py", old_string="x", new_string="y"), result(), use("Bash", command="python3 -I scripts/test_a.py"), result("OK"), text("★\n❕ 疑い: 境界は試していない。")]
        self.assertEqual(self.run_hook(rows)[0], 0)

    def test_bash_edit_with_read_in_same_command_passes(self):
        rows = [user("直して"), use("Bash", command="sed -i 's/a/b/' a.md && sed -n 3p a.md"), result("b"), text("★\n❕ 疑い: 他の行は見ていない。")]
        self.assertEqual(self.run_hook(rows)[0], 0)
        rows = [user("直して"), use("Bash", command="sed -i 's/a/b/' a.md"), result(), text("★")]
        self.assertEqual(self.run_hook(rows)[0], 2)

    def test_commit_without_diff_is_blocked(self):
        rows = [user("直して"), use("Bash", command="git add -A && git commit -q -m x && git show --stat HEAD"), result("1 file changed"), text("★")]
        code, err = self.run_hook(rows)
        self.assertEqual(code, 2)
        self.assertIn("差分を読まずにコミット", err)
        rows = [user("直して"), use("Bash", command="git diff --cached -- a.md"), result("+y"), use("Bash", command="git commit -q -m x"), result(), text("★\n❕ 疑い: push はまだ。")]
        self.assertEqual(self.run_hook(rows)[0], 0)

    def test_no_edit_honest_and_off_pass(self):
        rows = [user("見て"), use("Bash", command="ls"), result("a"), text("★ 1 件です。")]
        self.assertEqual(self.run_hook(rows)[0], 0)
        rows = [user("直して"), use("Edit", file_path="a.md", old_string="x", new_string="y"), result(), text("❕ 直しましたが、読み直していない。")]
        self.assertEqual(self.run_hook(rows)[0], 0)
        rows = [user("直して"), use("Edit", file_path="a.md", old_string="x", new_string="y"), result(), text("★")]
        self.assertEqual(self.run_hook(rows, active=True)[0], 0)
        self.assertEqual(self.run_hook(rows, off=True)[0], 0)
        st = (self.dir / "reread.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertTrue(st and json.loads(st[-1])["edits"] >= 1)


if __name__ == "__main__":
    unittest.main()
