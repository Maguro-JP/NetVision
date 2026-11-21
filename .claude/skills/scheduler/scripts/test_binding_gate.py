#!/usr/bin/env python3
"""binding_gate.py を、作った会話の記録で確かめる。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "binding_gate.py"
NEW = "session_01NewSessionAbcdef123"
OLD = "session_01UsersOwnSession456"


def user(text):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def result(tool_use_id, text, is_error=False):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": text, "is_error": is_error}]}}


def call(name, inp, tid="t1", text=""):
    c = ([{"type": "text", "text": text}] if text else []) + [{"type": "tool_use", "id": tid, "name": name, "input": inp}]
    return {"type": "assistant", "message": {"role": "assistant", "content": c}}


def say(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def gate(lines, active=False):
    d = tempfile.mkdtemp()
    p = Path(d) / "t.jsonl"
    p.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"transcript_path": str(p), "stop_hook_active": active}),
                       capture_output=True, text=True, timeout=60, env=dict(os.environ, SCHEDULER_BINDING_OFF="0"))
    return r.returncode, r.stderr


CREATE = call("mcp__Claude_Code_Remote__create_session", {"title": "DEV repo", "source_url": "https://github.com/o/r"}, "s1")
CREATED = result("s1", json.dumps({"ccr": {"id": NEW, "parent_session_id": "session_01ParentParent99999"}}))


def trigger(**kw):
    inp = {"name": "long-dev（repo）", "prompt": "o/r の long-dev。auto-dev を 1 周", "cron_expression": "0 0 * * *"}
    inp.update(kw)
    return call("mcp__Claude_Code_Remote__create_trigger", inp, "g1")


class Gate(unittest.TestCase):
    def test_bound_to_a_session_made_in_this_conversation_is_blocked(self):
        code, out = gate([user("long-dev を回して"), CREATE, CREATED, trigger(persistent_session_id=NEW), say("★ 作りました")])
        self.assertEqual(code, 2)
        self.assertIn("今作った新しいセッション", out)
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_session_made_in_an_earlier_turn_still_counts(self):
        code, _ = gate([user("準備して"), CREATE, CREATED, say("★"), user("long-dev を回して"), trigger(persistent_session_id=NEW), say("★")])
        self.assertEqual(code, 2)

    def test_new_session_on_each_fire_is_blocked(self):
        code, out = gate([user("毎日回して"), trigger(create_new_session_on_fire=True), say("★")])
        self.assertEqual(code, 2)
        self.assertIn("実行のたびに新しいセッション", out)

    def test_current_session_and_users_existing_session_pass(self):
        code, out = gate([user("long-dev を回して"), trigger(), say("★")])
        self.assertEqual(code, 0, out)
        code, out = gate([user("long-dev を回して"), trigger(persistent_session_id=OLD), say("★")])
        self.assertEqual(code, 0, out)

    def test_other_routines_are_not_checked(self):
        watch = call("mcp__Claude_Code_Remote__create_trigger", {"name": "週 1 回の見回り", "prompt": "vuln-watch", "create_new_session_on_fire": True}, "g2")
        code, out = gate([user("見回りを作って"), watch, say("★")])
        self.assertEqual(code, 0, out)
        # 本文の後ろで long-dev に触れるだけの見回りは、継続開発と見ない
        body = "週 1 回の見回り。やらないこと: PR を作らない、マージしない。" + "あ" * 120 + " long-dev のルーティンの状態も表にする"
        watch = call("mcp__Claude_Code_Remote__create_trigger", {"name": "週 1 回の見回り", "prompt": body, "create_new_session_on_fire": True}, "g3")
        code, out = gate([user("見回りを作って"), watch, say("★")])
        self.assertEqual(code, 0, out)

    def test_failed_create_session_is_not_counted(self):
        failed = result("s1", "error " + NEW, is_error=True)
        code, out = gate([user("回して"), CREATE, failed, trigger(persistent_session_id=NEW), say("★")])
        self.assertEqual(code, 0, out)

    def test_failed_create_trigger_is_not_counted(self):
        code, out = gate([user("毎日回して"), trigger(create_new_session_on_fire=True), result("g1", "hook error: blocked", is_error=True), say("❕ 作成は止められました。")])
        self.assertEqual(code, 0, out)

    def test_users_instruction_escape_and_active_flag(self):
        lines = [user("新しいセッションで long-dev を回して"), CREATE, CREATED, trigger(persistent_session_id=NEW),
                 say("★ 作りました\n❕ 紐づけ: 新しいセッション（ユーザーの指示）")]
        code, out = gate(lines)
        self.assertEqual(code, 0, out)
        code, _ = gate([user("回して"), CREATE, CREATED, trigger(persistent_session_id=NEW), say("★")], active=True)
        self.assertEqual(code, 0)
        lines[-1] = say("★ 作りました\n❕ 紐づけ: 新しいセッション（ユーザーのセッションが無い）")
        code, out = gate(lines)
        self.assertEqual(code, 0, out)
        lines[-1] = say("★ 作りました\n❕ 紐づけ: 新しいセッションにしました")  # 理由の無い行では通さない
        self.assertEqual(gate(lines)[0], 2)

    def test_child_session_must_not_make_a_dev_routine(self):
        first = user("o/r の作業を、親のセッションから分けた 1 本です（題: x。難易度: 普通）。")
        code, out = gate([first, trigger(), say("★")])  # 子が自分に紐づける形（persistent_session_id 無し）
        self.assertEqual(code, 2)
        self.assertIn("子のセッション", out)
        self.assertEqual(len(out.strip().splitlines()), 1)
        code, out = gate([first, say("★ FB x: branch（abc1234）")])
        self.assertEqual(code, 0, out)
        code, _ = gate([first, trigger(), say("★\n❕ 紐づけ: 新しいセッション（ユーザーの指示）")])  # 子は理由の行でも通さない
        self.assertEqual(code, 2)
        # 人が開いたセッションで、あとの発言にその語が出ても子とは見ない
        code, out = gate([user("long-dev を回して"), say("★"), user("親のセッションから分けた子は?"), trigger(), say("★")])
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
