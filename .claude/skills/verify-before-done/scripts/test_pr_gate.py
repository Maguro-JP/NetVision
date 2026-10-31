#!/usr/bin/env python3
"""pr_gate.py と precheck.py を、作った transcript と一時リポジトリで確かめる。"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "pr_gate.py"
PRECHECK = Path(__file__).resolve().parent / "precheck.py"


def user(t):
    return {"type": "user", "message": {"content": t}}


def assistant(t):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": t}]}}


class PrGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.state = self.dir / "state"
        self.state.mkdir()
        self.t = self.dir / "t.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def stamp(self, repo="ciel", ok=True, sha="", age=0):
        (self.state / f"{repo}.json").write_text(json.dumps(
            {"repo": repo, "sha": sha, "ok": ok, "ts": int(time.time()) - age, "steps": [{"name": "validate", "ok": ok}]}), encoding="utf-8")

    def run_hook(self, rows, tool, inp, off=False, cwd=None):
        self.t.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        env = dict(os.environ, PR_GATE_STATE=str(self.state))
        env.pop("PR_GATE_OFF", None)
        if off:
            env["PR_GATE_OFF"] = "1"
        r = subprocess.run([sys.executable, "-I", str(HOOK)],
                           input=json.dumps({"tool_name": tool, "tool_input": inp, "transcript_path": str(self.t), "cwd": cwd or str(self.dir)}),
                           capture_output=True, text=True, env=env)
        return r.returncode, r.stderr

    ASKED = [user("直して"), assistant("❔ 確認: Ciel の claude/x（abc1234）を PR にしてマージします。検査: precheck 通った 5/5。進めてよいですか。")]
    CREATE = ("mcp__github__create_pull_request", {"owner": "o", "repo": "Ciel", "head": "claude/x", "base": "main"})
    MERGE = ("mcp__github__merge_pull_request", {"owner": "o", "repo": "Ciel", "pullNumber": 12})

    def test_create_with_reply_and_stamp_passes(self):
        self.stamp()
        self.assertEqual(self.run_hook(self.ASKED + [user("はい")], *self.CREATE)[0], 0)

    def test_create_without_question_is_blocked(self):
        self.stamp()
        code, err = self.run_hook([user("直して"), assistant("★ 直しました。")], *self.CREATE)
        self.assertEqual(code, 2)
        self.assertIn("確認が無い", err)

    def test_create_without_reply_is_blocked(self):
        self.stamp()
        code, err = self.run_hook(self.ASKED, *self.CREATE)
        self.assertEqual(code, 2)
        self.assertIn("確認が無い", err)

    def test_old_question_does_not_count(self):
        # ❔ に答えたあとに別の話が来たら、その返事は使わない
        self.stamp()
        rows = self.ASKED + [user("はい"), assistant("★ 出しました。"), user("別の話をして")]
        self.assertEqual(self.run_hook(rows, *self.CREATE)[0], 2)

    def test_heredoc_and_quotes_are_not_commands(self):
        self.assertEqual(self.run_hook([user("x")], "Bash", {"command": "git commit -F - <<'EOF'\nPR の作成（gh pr create）と git push origin main の説明\nEOF\ngit log -1"})[0], 0)
        self.assertEqual(self.run_hook([user("x")], "Bash", {"command": 'echo "git push origin main"'})[0], 0)

    def test_refusal_is_blocked(self):
        self.stamp()
        code, err = self.run_hook(self.ASKED + [user("まだ。やめて")], *self.CREATE)
        self.assertEqual(code, 2)
        self.assertIn("止める返事", err)

    def test_create_without_stamp_is_blocked(self):
        code, err = self.run_hook(self.ASKED + [user("はい")], *self.CREATE)
        self.assertEqual(code, 2)
        self.assertIn("precheck", err)

    def test_failed_or_stale_stamp_is_blocked(self):
        self.stamp(ok=False)
        self.assertIn("落ちた検査", self.run_hook(self.ASKED + [user("はい")], *self.CREATE)[1])
        self.stamp(age=99999)
        self.assertIn("古い", self.run_hook(self.ASKED + [user("はい")], *self.CREATE)[1])

    def test_merge_needs_reply_only(self):
        rows = [user("直して"), assistant("❔ 確認: Ciel の PR #12 をマージします。進めてよいですか。")]
        self.assertEqual(self.run_hook(rows, *self.MERGE)[0], 2)
        self.assertEqual(self.run_hook(rows + [user("進めて")], *self.MERGE)[0], 0)

    def test_question_for_other_pr_does_not_count(self):
        rows = [user("直して"), assistant("❔ 確認: Ciel の PR #99 をマージします。"), user("はい")]
        self.assertEqual(self.run_hook(rows, *self.MERGE)[0], 2)

    def test_waiver_passes(self):
        rows = [user("確認なしでマージして")]
        self.assertEqual(self.run_hook(rows, *self.MERGE)[0], 0)

    def test_notice_is_not_a_reply(self):
        self.stamp()
        rows = self.ASKED + [user('<wake reason="external-event">通知</wake>')]
        self.assertEqual(self.run_hook(rows, *self.CREATE)[0], 2)

    def test_bash_other_commands_pass(self):
        self.assertEqual(self.run_hook([user("x")], "Bash", {"command": "ls -la"})[0], 0)
        self.assertEqual(self.run_hook([user("x")], "Bash", {"command": "git push -f origin claude/skill-management"})[0], 0)
        self.assertEqual(self.run_hook([user("x")], "Bash", {"command": "git push --delete origin main-old"})[0], 0)

    def test_bash_push_main_without_question_is_blocked(self):
        code, err = self.run_hook([user("x")], "Bash", {"command": "git push -u origin main"})
        self.assertEqual(code, 2)
        self.assertIn("main への push", err)
        self.assertEqual(self.run_hook([user("x")], "Bash", {"command": "gh pr create --title t"})[0], 2)

    def test_release_and_tag_need_question(self):
        self.assertEqual(self.run_hook([user("x")], "Bash", {"command": "gh release create v1.0.0 --notes x"})[0], 2)
        self.assertEqual(self.run_hook([user("x")], "Bash", {"command": "git tag v1.0.0 && git push origin --tags"})[0], 2)
        self.assertEqual(self.run_hook([user("x")], "Bash", {"command": "git tag -l"})[0], 0)
        rows = [user("直して"), assistant("❔ 確認: Ciel の v1.0.0 を Release にして出します。"), user("はい")]
        self.assertEqual(self.run_hook(rows, "Bash", {"command": "gh release create v1.0.0 --notes x"})[0], 0)

    def test_off_switch(self):
        self.assertEqual(self.run_hook([user("x")], *self.CREATE, off=True)[0], 0)

    def test_precheck_writes_stamp_for_head(self):
        repo = self.dir / "repo"
        repo.mkdir()
        g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True)
        g("init", "-q", "-b", "main")
        g("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "a")
        g("remote", "add", "origin", "https://example.invalid/o/Demo.git")
        env = dict(os.environ, PR_GATE_STATE=str(self.state))
        r = subprocess.run([sys.executable, "-I", str(PRECHECK), "--base", "HEAD"], cwd=str(repo), capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        st = json.loads((self.state / "demo.json").read_text(encoding="utf-8"))
        self.assertTrue(st["ok"])
        self.assertEqual(st["sha"], g("rev-parse", "HEAD").stdout.strip())
        # 記録があれば、その HEAD の main への push は確認のあとに通る
        rows = [user("直して"), assistant("❔ 確認: Demo の main に push します。"), user("はい")]
        self.assertEqual(self.run_hook(rows, "Bash", {"command": "git push origin main"}, cwd=str(repo))[0], 0)
        # セッションの URL が入ったコミットメッセージは落ちる
        g("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "b\n\nClaude-Session: https://claude.ai/code/" + "session_01" + "A" * 24)  # 検査に当たらないように分けて書く
        r = subprocess.run([sys.executable, "-I", str(PRECHECK), "--base", "HEAD~1"], cwd=str(repo), capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("commit messages", r.stdout)


if __name__ == "__main__":
    unittest.main()
