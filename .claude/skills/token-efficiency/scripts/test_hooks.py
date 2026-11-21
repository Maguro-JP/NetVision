#!/usr/bin/env python3
"""token-efficiency のフック 3 本（efficiency_core.py、trajectory_guard.py、session_report.py）と _state.py を確かめる。

フックは会話を止めてはいけない。状態のファイルが無い・壊れている・書けないときは、黙って続ける。
一方で、フックの中の予期しない例外まで黙って捨てると、壊れたフックが一度も動いていないことに気づけない。
終了コードは 0 のまま、何が起きたかを標準エラーと metrics.jsonl に残すことを見る。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
NAMES = ("efficiency_core", "trajectory_guard", "session_report")
EVENTS = {"efficiency_core": {"hook_event_name": "UserPromptSubmit", "prompt": "README の表を直して"},
          "trajectory_guard": {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_response": {"stdout": "a"}},
          "session_report": {"hook_event_name": "Stop"}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.homedir = Path(self.tmp.name) / "home"
        self.homedir.mkdir()
        self.state = self.homedir / ".claude" / "state" / "token-efficiency"

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, name, hooks=HOOKS, session="s1", **env):
        payload = dict(EVENTS[name], session_id=session)
        e = dict(os.environ, HOME=str(self.homedir), **env)
        for k in ("TOKEN_EFFICIENCY_OFF", "TOKEN_EFFICIENCY_STATE"):
            if k not in env:
                e.pop(k, None)
        return subprocess.run([sys.executable, "-I", str(Path(hooks) / f"{name}.py")], input=json.dumps(payload, ensure_ascii=False),
                              capture_output=True, text=True, timeout=60, env=e)

    def metrics(self):
        f = self.state / "metrics.jsonl"
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()] if f.exists() else []


class StateFile(Base):
    def test_first_run_without_a_state_file(self):
        r = self.run_hook("efficiency_core")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("additionalContext", r.stdout)
        self.assertEqual(json.loads((self.state / "s1.json").read_text(encoding="utf-8"))["turn"], 1)
        self.assertEqual(r.stderr, "")

    def test_broken_state_file_starts_over_quietly(self):
        self.state.mkdir(parents=True)
        for broken in ("{ これは JSON ではない", "[1, 2, 3]", ""):
            (self.state / "s1.json").write_text(broken, encoding="utf-8")
            r = self.run_hook("efficiency_core")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("additionalContext", r.stdout, broken)
            self.assertEqual(r.stderr, "", broken)  # 状態は目安なので、壊れていたら黙って最初から
            self.assertEqual(json.loads((self.state / "s1.json").read_text(encoding="utf-8"))["turn"], 1)

    def test_unwritable_state_directory_does_not_stop_the_hook(self):
        self.state.parent.mkdir(parents=True)
        self.state.write_text("ディレクトリの場所にファイルがある", encoding="utf-8")
        for name in NAMES:
            r = self.run_hook(name)
            self.assertEqual(r.returncode, 0, name + r.stderr)
            self.assertEqual(r.stderr, "", name)
        self.assertIn("additionalContext", self.run_hook("efficiency_core").stdout)


class Crash(Base):
    def broken_copy(self):
        """入力を読む所で必ず落ちる _state.py を持つ、フックのコピー。"""
        d = Path(self.tmp.name) / "hooks"
        shutil.copytree(HOOKS, d, ignore=shutil.ignore_patterns("__pycache__"))
        with (d / "_state.py").open("a", encoding="utf-8") as fh:
            fh.write('\n\ndef read_event() -> dict:\n    raise RuntimeError("入力が読めない")\n')
        return d

    def test_unexpected_error_is_recorded_and_the_hook_still_exits_zero(self):
        d = self.broken_copy()
        for name in NAMES:
            r = self.run_hook(name, hooks=d)
            self.assertEqual(r.returncode, 0, name)  # 会話は止めない
            self.assertIn(name, r.stderr)
            self.assertIn("RuntimeError: 入力が読めない", r.stderr, name)
            self.assertNotIn("additionalContext", r.stdout, name)
        rows = [m for m in self.metrics() if m.get("event") == "hook_error"]
        self.assertEqual([m["hook"] for m in rows], list(NAMES))
        self.assertTrue(all("RuntimeError" in m["error"] and m.get("ts") for m in rows))

    def test_ordinary_runs_leave_no_error_record(self):
        for name in NAMES:
            r = self.run_hook(name)
            self.assertEqual(r.returncode, 0, name + r.stderr)
            self.assertEqual(r.stderr, "", name)
        self.assertEqual([m for m in self.metrics() if m.get("event") == "hook_error"], [])

    def test_off_switch(self):
        for name in NAMES:
            r = self.run_hook(name, TOKEN_EFFICIENCY_OFF="1")
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), name)


if __name__ == "__main__":
    unittest.main()
