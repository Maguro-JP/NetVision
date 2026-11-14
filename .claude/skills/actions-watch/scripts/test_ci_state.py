#!/usr/bin/env python3
"""ci_state.py と 3 つのフックを、作った会話の記録で確かめる。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ci_state  # noqa: E402

HOOKS = HERE.parent / "hooks"
PR = "Owner/Repo#12"
SHA = "3083043ba9006abe971f2d5b8a1d29218abb5d1d"


def user(text):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def result(text):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": text}]}}


def assistant(text="", tools=()):
    c = [{"type": "text", "text": text}] if text else []
    for n, (name, inp) in enumerate(tools):
        c.append({"type": "tool_use", "id": f"toolu_{name[-12:]}_{n}", "name": name, "input": inp})
    return {"type": "assistant", "message": {"role": "assistant", "content": c}}


def tool_result(tool_use_id, text, is_error=False):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "is_error": is_error, "content": text}]}}


def event(kind, conclusion=None, sha=SHA, pr=PR):
    body = {"head_sha": sha, "pr": pr}
    if conclusion:
        body["conclusion"] = conclusion
    return (f'<wake reason="external-event"><event source="github" kind="{kind}" from="system" trust="relay">\n'
            f"<!-- No third-party check suite is still running or failed. -->\n{json.dumps(body)}\n</event></wake>")


def write(lines):
    d = tempfile.mkdtemp()
    p = Path(d) / "t.jsonl"
    p.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n", encoding="utf-8")
    return str(p)


def run_hook(name, payload):
    r = subprocess.run([sys.executable, str(HOOKS / name)], input=json.dumps(payload), capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, ACTIONS_WATCH_OFF="0"))
    return r.returncode, r.stdout + r.stderr


class State(unittest.TestCase):
    def test_reads_events_from_user_and_tool_result(self):
        p = write([user("hi"), user(event("check_suite.completed")), result(event("workflow_run.completed", "failure", pr="Owner/Repo#13"))])
        st = ci_state.read(p)
        self.assertEqual([(e["pr"], e["status"]) for e in st["events"]], [(PR, "通った"), ("Owner/Repo#13", "落ちた")])
        self.assertEqual(ci_state.latest(st, 12)["status"], "通った")
        self.assertEqual(ci_state.latest(st, 13)["status"], "落ちた")
        self.assertEqual(ci_state.latest(st, 99), {})

    def test_push_after_event_is_stale(self):
        p = write([user("go"), user(event("check_suite.completed")), assistant("", [("Bash", {"command": "git push origin x"})])])
        self.assertTrue(ci_state.latest(ci_state.read(p), 12)["stale"])

    def test_in_progress(self):
        p = write([user(event("check_suite.requested"))])
        self.assertEqual(ci_state.read(p)["events"][0]["status"], "動いている")


class Gate(unittest.TestCase):
    def stop(self, lines):
        return run_hook("ci_gate.py", {"transcript_path": write(lines), "stop_hook_active": False})

    def test_created_without_subscribe(self):
        code, out = self.stop([user("PR を作って"), assistant("★ PR #12 を作りました", [("mcp__github__create_pull_request", {"owner": "Owner", "repo": "Repo"})])])
        self.assertEqual(code, 2)
        self.assertIn("購読", out)
        code, out = self.stop([user("PR を作って"), assistant("★ PR #12", [("mcp__github__create_pull_request", {}), ("mcp__Claude_Code_Remote__subscribe_pr_activity", {})])])
        self.assertEqual(code, 0, out)

    def test_blocked_create_is_not_counted(self):
        tool = ("mcp__github__create_pull_request", {"owner": "Owner", "repo": "Repo"})
        tid = assistant("", [tool])["message"]["content"][0]["id"]
        # フックに止められた作成（エラー）。PR は無いので差し戻さない
        code, out = self.stop([user("PR を作って"), assistant("", [tool]), tool_result(tid, "PreToolUse:mcp__github__create_pull_request hook error: blocked", True),
                               assistant("❕ 作成は止められた。")])
        self.assertEqual(code, 0, out)
        # is_error が付かずに文だけが hook error の形でも同じ
        code, out = self.stop([user("PR を作って"), assistant("", [tool]), tool_result(tid, "hook error: pr_gate blocked", False), assistant("❕ 止められた。")])
        self.assertEqual(code, 0, out)
        # 成功した作成で購読が無ければ差し戻す
        code, out = self.stop([user("PR を作って"), assistant("", [tool]), tool_result(tid, '{"number": 12}'), assistant("★ PR #12")])
        self.assertEqual(code, 2)
        self.assertIn("購読", out)
        # 成功した作成でも購読があれば通る
        sub = ("mcp__github__subscribe_pr_activity", {})
        code, out = self.stop([user("PR を作って"), assistant("", [tool, sub]), tool_result(tid, '{"number": 12}'), assistant("★ PR #12")])
        self.assertEqual(code, 0, out)

    def test_acts_skip_failed_calls(self):
        push = ("Bash", {"command": "git push origin x"})
        tid = assistant("", [push])["message"]["content"][0]["id"]
        p = write([user("go"), user(event("check_suite.completed")), assistant("", [push]), tool_result(tid, "rejected", True)])
        st = ci_state.read(p)
        self.assertEqual(st["acts"], [])
        self.assertFalse(ci_state.latest(st, 12)["stale"])

    def test_merge_question_before_actions(self):
        code, out = self.stop([user("進めて"), assistant("❔ Repo: PR #12 をマージしてよいですか。")])
        self.assertEqual(code, 2)
        self.assertIn("終わっていない", out)
        code, out = self.stop([user("進めて"), assistant("❔ Repo: PR にして main にマージしてよいですか。")])
        self.assertEqual(code, 2)
        self.assertIn("番号", out)

    def test_merge_question_after_actions_ok(self):
        code, out = self.stop([user(event("check_suite.completed")), assistant("❕ Actions: PR #12 は通りました。\n❔ Repo: PR #12 をマージしてよいですか。")])
        self.assertEqual(code, 0, out)

    def test_merge_question_after_push_is_blocked(self):
        code, out = self.stop([user(event("check_suite.completed")), user("直して"), assistant("直しました", [("Bash", {"command": "git push origin fix"})]),
                               assistant("❕ Actions: 通った\n❔ Repo: PR #12 をマージしてよいですか。")])
        self.assertEqual(code, 2)
        self.assertIn("push", out)

    def test_mentioning_merge_later_is_not_a_merge_question(self):
        code, out = self.stop([user("進めて"), assistant("❔ Repo: ブランチを PR にしてよいですか。作ったら購読し、Actions の結果が来てからマージの ❔ を出します。")])
        self.assertEqual(code, 0, out)

    def test_no_actions_escape(self):
        code, out = self.stop([user("進めて"), assistant("❔ Repo: PR #12 をマージしてよいですか（Actions は無い）。")])
        self.assertEqual(code, 0, out)

    def test_result_not_written(self):
        code, out = self.stop([user(event("check_suite.completed")), assistant("❕ 通知を読みました。")])
        self.assertEqual(code, 2)
        self.assertIn("結果", out)
        code, out = self.stop([user(event("workflow_run.completed", "failure")), assistant("❕ Actions: PR #12 は落ちました。ci-triage で見ます。")])
        self.assertEqual(code, 0, out)

    def test_stop_hook_active_passes(self):
        code, _ = run_hook("ci_gate.py", {"transcript_path": write([user("x"), assistant("❔ PR #12 をマージ")]), "stop_hook_active": True})
        self.assertEqual(code, 0)


class MergeGate(unittest.TestCase):
    def pre(self, lines, n=12):
        return run_hook("ci_merge_gate.py", {"tool_name": "mcp__github__merge_pull_request", "tool_input": {"owner": "Owner", "repo": "Repo", "pullNumber": n},
                                             "transcript_path": write(lines)})

    def test_blocks_without_event_and_passes_with(self):
        code, out = self.pre([user("マージして")])
        self.assertEqual(code, 2)
        self.assertIn("通知がまだ無い", out)
        code, out = self.pre([user(event("check_suite.completed")), user("お願い")])
        self.assertEqual(code, 0, out)
        code, out = self.pre([user(event("check_suite.completed", "failure")), user("お願い")])
        self.assertEqual(code, 2)
        code, out = self.pre([assistant("❔ Repo: PR #12 をマージしてよいですか（Actions は無い）"), user("お願い")])
        self.assertEqual(code, 0, out)


class Prompt(unittest.TestCase):
    def ask(self, prompt):
        code, out = run_hook("ci_prompt.py", {"prompt": prompt})
        return out

    def test_messages(self):
        self.assertIn("ReadNotifications", self.ask("<task-notification><task-type>queued-remote-notifications</task-type>"))
        self.assertIn("通った", self.ask(event("check_suite.completed")))
        self.assertIn("ci-triage", self.ask(event("workflow_run.completed", "failure")))
        self.assertIn("待つ", self.ask(event("check_suite.requested")))
        self.assertEqual(self.ask("普通の一言"), "")


if __name__ == "__main__":
    unittest.main()
