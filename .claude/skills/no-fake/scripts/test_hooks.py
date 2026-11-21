#!/usr/bin/env python3
"""fake_nudge.py（PostToolUse）と fake_gate.py（Stop）を、作った会話の記録と一時のリポジトリで確かめる。

記録の形は、実際の記録に合わせる。人の発言は origin.kind が human、ツールの結果は type が user で tool_result を持ち、
Edit と Write の結果の行には toolUseResult.originalFile（書く前の中身。新しく作ったときは null）がある。
差し戻す場面と、差し戻してはいけない場面（途中で直した、前からある、動かしただけ、頼まれた、返答に行がある）を組で置く。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
HOOKS = SKILL / "hooks"
MISSING = object()
SWALLOW = "def load(p):\n    try:\n        return open(p).read()\n    except Exception:\n        pass\n"
CLEAN = "def load(p):\n    return open(p).read()\n"
TESTS = "def test_add():\n    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n\n\ndef test_sub():\n    assert sub(3, 1) == 2\n    assert sub(0, 0) == 0\n"


def src(*lines):
    return "\n".join(lines) + "\n"


def user(text, kind="human"):
    return {"type": "user", "origin": {"kind": kind}, "message": {"role": "user", "content": text}}


def say(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def call(name, inp, tid):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def result(tid, text="ok", is_error=False, original=MISSING):
    e = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": is_error}]}}
    if original is not MISSING:
        e["toolUseResult"] = {"originalFile": original}
    return e


def bash(cmd, tid, out="ok", is_error=False):
    return [call("Bash", {"command": cmd}, tid), result(tid, out, is_error)]


class Case(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.state = tempfile.mkdtemp()
        self.n = 0
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True, capture_output=True)

    def put(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return str(p)

    def commit(self):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@users.noreply.github.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@users.noreply.github.com")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=self.root, check=True, capture_output=True, env=env)

    def write(self, rel, old, new, original=True):
        """ファイルを new にし、その編集の呼び出しと結果の行を返す。old が None なら新しく作る。"""
        self.n += 1
        tid = f"e{self.n}"
        path = self.put(rel, new)
        return [call("Write", {"file_path": path, "content": new}, tid), result(tid, "ok", original=old if original else MISSING)]

    def edit(self, rel, old, new, old_string, new_string, original=True):
        self.n += 1
        tid = f"e{self.n}"
        path = self.put(rel, new)
        return [call("Edit", {"file_path": path, "old_string": old_string, "new_string": new_string}, tid), result(tid, "ok", original=old if original else MISSING)]

    def gate(self, lines, last=None, active=False, hooks=HOOKS, **env):
        t = Path(tempfile.mkdtemp()) / "t.jsonl"
        t.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
        payload = {"session_id": "s1", "transcript_path": str(t), "stop_hook_active": active, "hook_event_name": "Stop", "cwd": str(self.root)}
        if last is not None:
            payload["last_assistant_message"] = last
        e = dict(os.environ, NO_FAKE_STATE=self.state, **env)
        if "NO_FAKE_OFF" not in env:
            e.pop("NO_FAKE_OFF", None)
        r = subprocess.run([sys.executable, "-I", str(hooks / "fake_gate.py")], input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True, timeout=60, env=e)
        return r.returncode, r.stderr

    def nudge(self, tool, inp, response=None, **env):
        payload = {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": inp, "tool_response": response or {}, "cwd": str(self.root)}
        e = dict(os.environ, NO_FAKE_STATE=self.state, **env)
        if "NO_FAKE_OFF" not in env:
            e.pop("NO_FAKE_OFF", None)
        r = subprocess.run([sys.executable, "-I", str(HOOKS / "fake_nudge.py")], input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True, timeout=60, env=e)
        text = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout.strip() else ""
        return r.returncode, text

    def metrics(self):
        f = Path(self.state) / "metrics.jsonl"
        return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


class Sham(Case):
    def test_swallow_left_in_the_turn_is_sent_back_with_one_line(self):
        lines = [user("load を作って")] + self.write("app/a.py", None, SWALLOW) + bash("python3 app/a.py", "b1") + [say("★ load: 作りました。")]
        code, out = self.gate(lines)
        self.assertEqual(code, 2)
        self.assertIn("見せかけの形が残っている（app/a.py:4", out)
        self.assertIn("❕ 見せかけ:", out)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn(("gate", "握りつぶし", 1, True), [(r["src"], r["kind"], r["n"], r["blocked"]) for r in self.metrics()])

    def test_the_line_in_the_reply_lets_it_through(self):
        lines = [user("load を作って")] + self.write("app/a.py", None, SWALLOW) + bash("python3 app/a.py", "b1")
        code, _ = self.gate(lines + [say("★ load: 作りました。\n❕ 見せかけ: load の例外を捨てている（読めないファイルは空として扱う仕様のため）")])
        self.assertEqual(code, 0)
        code, _ = self.gate(lines, last="★ load: 作りました。\n❕ 見せかけ: load の例外を捨てている（仕様）")
        self.assertEqual(code, 0)

    def test_shape_fixed_later_in_the_turn_is_not_counted(self):
        lines = [user("load を作って")] + self.write("app/a.py", None, SWALLOW) + self.write("app/a.py", SWALLOW, CLEAN) + bash("python3 -m pytest -q", "b1") + [say("★ load: 作りました。")]
        self.assertEqual(self.gate(lines)[0], 0)

    def test_shape_that_was_already_there_is_not_mine(self):
        new = SWALLOW + "\n\ndef save(p, s):\n    open(p, 'w').write(s)\n"
        lines = [user("save を足して")] + self.write("app/a.py", SWALLOW, new) + bash("python3 -m pytest -q", "b1") + [say("★ save: 足しました。")]
        self.assertEqual(self.gate(lines)[0], 0)
        moved = "def load(p):\n    if p:\n        try:\n            return open(p).read()\n        except Exception:\n            pass\n"
        lines = [user("p が空のときを足して")] + self.write("app/a.py", SWALLOW, moved) + bash("python3 -m pytest -q", "b1") + [say("★ load: 直しました。")]
        self.assertEqual(self.gate(lines)[0], 0)

    def test_fake_checker_unless_a_skeleton_was_asked(self):
        code = "def validate_token(token):\n    return True\n"
        tail = self.write("app/auth.py", None, code) + bash("python3 app/auth.py", "b1") + [say("★ auth: 作りました。")]
        self.assertEqual(self.gate([user("認証を作って")] + tail)[0], 2)
        self.assertEqual(self.gate([user("認証の骨組みだけ作って。中身はあとで実装する")] + tail)[0], 0)

    def test_old_text_missing_from_the_record_still_works(self):
        new = CLEAN + "\n\ndef save(p, s):\n    try:\n        open(p, 'w').write(s)\n    except Exception:\n        pass\n"
        lines = [user("save を足して")] + self.edit("app/a.py", CLEAN, new, "    return open(p).read()\n", new[len("def load(p):\n"):], original=False) + bash("python3 app/a.py", "b1") + [say("★ save: 足しました。")]
        code, out = self.gate(lines)
        self.assertEqual(code, 2)
        self.assertIn("握りつぶし", out)


class Weakened(Case):
    def test_skip_added_to_a_test(self):
        new = src("import pytest", "", "", "@pytest.mark.skip(reason='あとで')", "def test_add():", "    assert add(1, 2) == 3")
        lines = [user("テストを通して")] + self.write("tests/test_calc.py", "def test_add():\n    assert add(1, 2) == 3\n", new) + bash("python3 -m pytest -q", "b1")
        code, out = self.gate(lines + [say("★ テスト: 通りました。")])
        self.assertEqual(code, 2)
        self.assertIn("テストか検査を弱めた", out)
        self.assertEqual(self.gate(lines + [say("★ テスト: 通りました。\n❕ 弱めた: test_add を skip にした（add が外の API を呼ぶので、この環境では動かない）")])[0], 0)

    def test_assertions_and_tests_removed(self):
        tail = bash("python3 -m pytest -q", "b1") + [say("★ テスト: 通りました。")]
        lines = [user("テストを通して")] + self.write("tests/test_calc.py", TESTS, "def test_add():\n    add(1, 2)\n\n\ndef test_sub():\n    sub(3, 1)\n") + tail
        self.assertEqual(self.gate(lines)[0], 2)
        lines = [user("テストを通して")] + self.write("tests/test_calc.py", TESTS, TESTS.split("\n\n\n")[0] + "\n") + bash("python3 -m pytest -q", "b1")
        code, out = self.gate(lines + [say("★ テスト: 通りました。")])
        self.assertEqual(code, 2)
        self.assertIn("test_sub", out)
        self.assertEqual(self.gate(lines + [say("★ テスト: 通りました。\n❕ 消した: test_sub（sub の機能を外すと決まった）")])[0], 0)

    def test_expected_value_rewritten(self):
        lines = [user("テストを通して")] + self.write("tests/test_calc.py", TESTS, TESTS.replace("add(1, 2) == 3", "add(1, 2) == 4")) + bash("python3 -m pytest -q", "b1")
        code, out = self.gate(lines + [say("★ テスト: 通りました。")])
        self.assertEqual(code, 2)
        self.assertIn("期待値を書き換えた", out)
        self.assertEqual(self.gate(lines + [say("★ テスト: 通りました。\n❕ 弱めた: test_add の期待値を 3 から 4 に変えた（add の仕様が変わった）")])[0], 0)

    def test_test_that_only_grew_is_fine(self):
        lines = [user("テストを足して")] + self.write("tests/test_calc.py", TESTS, TESTS + "\n\ndef test_mul():\n    assert mul(2, 3) == 6\n") + bash("python3 -m pytest -q", "b1") + [say("★ テスト: 足しました。")]
        self.assertEqual(self.gate(lines)[0], 0)

    def test_checks_switched_off_from_the_shell(self):
        edit = self.write("app/a.py", CLEAN, CLEAN + "\n\nX = 1\n")
        lines = [user("直して")] + edit + bash("python3 app/a.py", "b1") + bash("git commit -q -m fix --no-verify", "b2") + [say("★ a: 直しました。")]
        code, out = self.gate(lines)
        self.assertEqual(code, 2)
        self.assertIn("--no-verify", out)
        lines = [user("直して")] + edit + bash("npx jest -u", "b1") + [say("★ a: 直しました。")]
        self.assertIn("スナップショット", self.gate(lines)[1])
        lines = [user("直して")] + edit + bash("python3 app/a.py && git commit -q -m 'コミットに --no-verify を使わない'", "b1") + [say("★ a: 直しました。")]
        self.assertEqual(self.gate(lines)[0], 0)

    def test_hook_registration_removed(self):
        hook = '          {\n            "command": "run.sh .claude/skills/x/hooks/%s.py"\n          }'
        settings = '{\n  "hooks": {\n    "Stop": [\n      {\n        "hooks": [\n%s\n        ]\n      }\n    ]\n  }\n}\n'
        lines = [user("フックを直して")] + self.write(".claude/settings.json", settings % (hook % "a" + ",\n" + hook % "b"), settings % (hook % "a")) + [say("★ 設定: 直しました。")]
        code, out = self.gate(lines)
        self.assertEqual(code, 2)
        self.assertIn("フックの登録を消した", out)


class Removed(Case):
    OLD = "def load(p):\n    return open(p).read()\n\n\ndef save(p, s):\n    open(p, 'w').write(s)\n"

    def tail(self, text="★ a: 直しました。"):
        return bash("python3 -m pytest -q", "b1") + [say(text)]

    def test_function_removed_without_a_word(self):
        code, out = self.gate([user("load を速くして")] + self.write("app/a.py", self.OLD, CLEAN) + self.tail())
        self.assertEqual(code, 2)
        self.assertIn("消した物がある（関数 app/a.py save", out)
        self.assertIn("❕ 消した:", out)

    def test_removal_that_was_asked_or_reported_is_fine(self):
        edit = self.write("app/a.py", self.OLD, CLEAN)
        self.assertEqual(self.gate([user("save を消して")] + edit + self.tail())[0], 0)
        self.assertEqual(self.gate([user("load を速くして")] + edit + self.tail("★ a: 直しました。\n❕ 消した: save（どこからも呼ばれていない。頼まれていないので、要るなら戻す）"))[0], 0)

    def test_long_instructions_do_not_count_as_asking(self):
        prompt = "load を速くして。" + "決まり: 頼まれていない削除は戻す。消した物は報告に書く。" * 30
        self.assertEqual(self.gate([user(prompt)] + self.write("app/a.py", self.OLD, CLEAN) + self.tail())[0], 2)

    def test_moved_function_is_not_removed(self):
        lines = [user("load を速くして")] + self.write("app/a.py", self.OLD, CLEAN + "\n\nfrom app.io import save  # 再輸出\n") \
            + self.write("app/io.py", None, "def save(p, s):\n    open(p, 'w').write(s)\n") + self.tail()
        self.assertEqual(self.gate(lines)[0], 0)

    def test_file_removed_from_the_shell(self):
        self.put("app/old.py", "def helper(x):\n    return x + 1\n")
        self.put("app/a.py", CLEAN)
        self.commit()
        (self.root / "app" / "old.py").unlink()
        lines = [user("load を速くして")] + bash("rm -f app/old.py", "b1") + [say("★ a: 直しました。")]
        code, out = self.gate(lines)
        self.assertEqual(code, 2)
        self.assertIn("ファイル app/old.py", out)
        lines = [user("load を速くして")] + bash("rm -f /tmp/scratch-xyz.py build/out.js", "b1") + [say("★ a: 直しました。")]
        self.assertEqual(self.gate(lines)[0], 0)


class NotRun(Case):
    def test_code_changed_and_never_started(self):
        code, out = self.gate([user("load を足して")] + self.write("app/a.py", None, CLEAN) + [say("★ load: 足しました。")])
        self.assertEqual(code, 2)
        self.assertIn("コードを変えたあとに 1 回も動かしていない（app/a.py）", out)
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_what_counts_as_running_it(self):
        edit = self.write("app/a.py", None, CLEAN)
        for cmd in ("python3 -m pytest -q tests/", "npm test", "python3 app/a.py --help", "./scripts/validate.sh", "cd app && node server.js", "bash scripts/check.sh", "cargo test"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.gate([user("load を足して")] + edit + bash(cmd, "b1") + [say("★ load: 足しました。")])[0], 0)
        for cmd in ("git commit -q -m 'pytest を足す'", "cat ./app/a.py", "ruff check app", "git diff -- app/a.py", "python3 -m py_compile app/a.py"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.gate([user("load を足して")] + edit + bash(cmd, "b1") + [say("★ load: 足しました。")])[0], 2)

    def test_run_before_the_change_does_not_count(self):
        lines = [user("load を足して")] + bash("python3 -m pytest -q", "b1") + self.write("app/a.py", None, CLEAN) + [say("★ load: 足しました。")]
        self.assertEqual(self.gate(lines)[0], 2)

    def test_run_stopped_by_a_hook_does_not_count(self):
        lines = [user("load を足して")] + self.write("app/a.py", None, CLEAN) + bash("python3 -m pytest -q", "b1", "PreToolUse:Bash hook error: 長いコマンドを前面で動かしている", True) + [say("★ load: 足しました。")]
        self.assertEqual(self.gate(lines)[0], 2)

    def test_saying_so_lets_it_through(self):
        lines = [user("load を足して")] + self.write("app/a.py", None, CLEAN)
        self.assertEqual(self.gate(lines + [say("★ load: 足しました。\n❕ 疑い: 動かしていない。この環境に Python の依存が入っていない")])[0], 0)
        self.assertEqual(self.gate(lines + [say("★ load: 足しました。")], last="★ load: 足しました。\n❕ 疑い: 起動は試していない")[0], 0)

    def test_things_that_need_no_run(self):
        self.assertEqual(self.gate([user("README を直して")] + self.write("README.md", "# a\n", "# a\n\n説明。\n") + [say("★ README: 直しました。")])[0], 0)
        outside = Path(tempfile.mkdtemp()) / "patch.py"
        outside.write_text(CLEAN, encoding="utf-8")
        lines = [user("直して"), call("Write", {"file_path": str(outside), "content": CLEAN}, "w1"), result("w1", "ok", original=None), say("★ 直しました。")]
        self.assertEqual(self.gate(lines)[0], 0)
        lines = [user("load を足して")] + self.write("app/a.py", None, CLEAN) + [call("Agent", {"prompt": "テストを流して"}, "a1"), result("a1", "通った"), say("★ load: 足しました。")]
        self.assertEqual(self.gate(lines)[0], 0)
        lines = [user("load を足して"), call("Write", {"file_path": str(self.root / "app" / "b.py"), "content": CLEAN}, "w1"), result("w1", "PreToolUse:Write hook error: 止めた", True), say("★ 止められました。")]
        self.assertEqual(self.gate(lines)[0], 0)


class Turns(Case):
    def test_notices_and_feedback_do_not_split_the_turn(self):
        notice = user("<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n</task-notification>", kind="task-notification")
        feedback = {"type": "user", "isMeta": True, "message": {"role": "user", "content": "Stop hook feedback:\n足りない行だけを足す"}}
        lines = [user("load を足して")] + self.write("app/a.py", None, SWALLOW) + bash("python3 app/a.py", "b1") + [say("❕ 検査を裏で動かしています。"), notice, feedback, say("★ load: 足しました。")]
        self.assertEqual(self.gate(lines)[0], 2)

    def test_edits_of_an_earlier_turn_are_not_judged_again(self):
        lines = [user("load を足して")] + self.write("app/a.py", None, SWALLOW) + [say("★ load: 足しました。\n❕ 見せかけ: 例外を捨てている（仕様）。動かしていない"), user("ありがとう。README はどこ?"), say("↩ README: 根にあります。")]
        self.assertEqual(self.gate(lines)[0], 0)

    def test_switches(self):
        lines = [user("load を足して")] + self.write("app/a.py", None, SWALLOW) + [say("★ load: 足しました。")]
        self.assertEqual(self.gate(lines)[0], 2)
        self.assertEqual(self.gate(lines, active=True)[0], 0)
        self.assertEqual(self.gate(lines, NO_FAKE_OFF="1")[0], 0)
        r = subprocess.run([sys.executable, "-I", str(HOOKS / "fake_gate.py")], input="{broken", capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        r = subprocess.run([sys.executable, "-I", str(HOOKS / "fake_gate.py")], input=json.dumps({"transcript_path": "/nonexistent/t.jsonl"}), capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_without_common_sense_it_stays_quiet(self):
        alone = Path(tempfile.mkdtemp()) / "skills" / "no-fake"
        shutil.copytree(SKILL, alone, ignore=shutil.ignore_patterns("__pycache__"))
        lines = [user("load を足して")] + self.write("app/a.py", None, SWALLOW) + [say("★ load: 足しました。")]
        self.assertEqual(self.gate(lines, hooks=alone / "hooks"), (0, ""))

    def test_all_four_reasons_fit_in_one_short_line(self):
        skipped = src("import pytest", "", "", "@pytest.mark.skip", "def test_add():", "    assert add(1, 2) == 3")
        lines = [user("load を直して")] + self.write("app/a.py", Removed.OLD, SWALLOW) + self.write("tests/test_calc.py", TESTS, skipped) + [say("★ load: 直しました。")]
        code, out = self.gate(lines)
        self.assertEqual(code, 2)
        self.assertEqual(len(out.strip().splitlines()), 1)
        for part in ("見せかけの形が残っている", "テストか検査を弱めた", "消した物がある", "1 回も動かしていない"):
            self.assertIn(part, out)
        self.assertLess(len(out), 700)


class Nudge(Case):
    def test_new_swallow_is_told_with_its_line(self):
        path = self.put("app/a.py", SWALLOW)
        code, text = self.nudge("Write", {"file_path": path, "content": SWALLOW}, {"type": "create", "originalFile": None})
        self.assertEqual(code, 0)
        self.assertIn("a.py に重大 1 件。4 行 握りつぶし", text)
        self.assertIn("❕ 見せかけ:", text)
        self.assertNotIn("\n", text)
        self.assertEqual([(r["src"], r["kind"], r["n"]) for r in self.metrics()], [("nudge", "握りつぶし", 1)])

    def test_only_lines_added_by_this_edit(self):
        new = SWALLOW + "\n\ndef save(p, s):\n    open(p, 'w').write(s)\n"
        path = self.put("app/a.py", new)
        self.assertEqual(self.nudge("Edit", {"file_path": path, "old_string": "", "new_string": "def save"}, {"originalFile": SWALLOW})[1], "")
        new = CLEAN + "\n\ndef save(p, s):\n    try:\n        open(p, 'w').write(s)\n    except Exception:\n        pass\n"
        path = self.put("app/a.py", new)
        self.assertIn("8 行 握りつぶし", self.nudge("Edit", {"file_path": path, "old_string": "", "new_string": ""}, {"originalFile": CLEAN})[1])
        # 書く前の中身が無い版の結果でも、置き換えの文から足した行を見つける
        self.assertIn("8 行 握りつぶし", self.nudge("Edit", {"file_path": path, "old_string": "X = 1\n", "new_string": new[len(CLEAN):]})[1])

    def test_tests_get_the_matching_line(self):
        path = self.put("tests/test_a.py", "def test_a():\n    assert True\n")
        self.assertIn("❕ 見せかけ:", self.nudge("Write", {"file_path": path}, {"originalFile": None})[1])
        new = src("import pytest", "", "", "@pytest.mark.skip", "def test_a():", "    assert f() == 1")
        path = self.put("tests/test_a.py", new)
        text = self.nudge("Write", {"file_path": path}, {"originalFile": "def test_a():\n    assert f() == 1\n"})[1]
        self.assertIn("テスト（テストを飛ばしている", text)
        self.assertIn("❕ 弱めた:", text)

    def test_quiet_cases(self):
        todo = self.put("app/a.py", "x = 1  # TODO: あとで直す\n")
        self.assertEqual(self.nudge("Write", {"file_path": todo}, {"originalFile": None}), (0, ""))  # 注意だけなら黙る
        self.assertEqual(self.nudge("Write", {"file_path": self.put("tests/fixtures/bad.py", SWALLOW)}, {"originalFile": None}), (0, ""))
        self.assertEqual(self.nudge("Write", {"file_path": self.put("docs/a.md", "except Exception: pass\n")}, {"originalFile": None}), (0, ""))
        self.assertEqual(self.nudge("Write", {"file_path": self.put(".claude/skills/no-fake/references/patterns.md", "catch (e) {}\n")}, {"originalFile": None}), (0, ""))
        self.assertEqual(self.nudge("Write", {"file_path": self.put("app/s.py", "SAMPLE = 'try:\\n    go()\\nexcept Exception:\\n    pass\\n'\n")}, {"originalFile": None}), (0, ""))
        outside = Path(tempfile.mkdtemp()) / "a.py"
        outside.write_text(SWALLOW, encoding="utf-8")
        self.assertEqual(self.nudge("Write", {"file_path": str(outside)}, {"originalFile": None}), (0, ""))
        path = self.put("app/b.py", SWALLOW)
        self.assertEqual(self.nudge("Read", {"file_path": path}), (0, ""))
        self.assertEqual(self.nudge("Write", {"file_path": path}, {"originalFile": None}, NO_FAKE_OFF="1"), (0, ""))
        self.assertEqual(self.nudge("Write", {"file_path": str(self.root / "app" / "nothing.py")}, {"originalFile": None}), (0, ""))
        r = subprocess.run([sys.executable, "-I", str(HOOKS / "fake_nudge.py")], input="{broken", capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_copies_of_skills_are_told_only_at_their_home(self):
        path = self.put(".claude/skills/x/hooks/a.py", SWALLOW)
        self.assertEqual(self.nudge("Write", {"file_path": path}, {"originalFile": None}), (0, ""))
        lines = [user("フックを足して")] + self.write(".claude/skills/x/hooks/a.py", None, SWALLOW) + [say("★ フック: 足しました。")]
        self.assertEqual(self.gate(lines)[0], 0)
        self.put("scripts/validate.sh", "#!/usr/bin/env bash\n# skill validator\n")
        self.assertIn("握りつぶし", self.nudge("Write", {"file_path": path}, {"originalFile": None})[1])
        self.assertEqual(self.gate(lines)[0], 2)

    def test_javascript_and_shell(self):
        path = self.put("src/a.ts", "export async function load() {\n  try {\n    return await get();\n  } catch (e) {}\n}\n")
        self.assertIn("4 行 握りつぶし", self.nudge("Write", {"file_path": path}, {"originalFile": None})[1])
        path = self.put(".github/workflows/ci.yml", "jobs:\n  t:\n    steps:\n      - run: pytest -q || true\n")
        self.assertIn("弱めた（検査の失敗を握りつぶしている", self.nudge("Write", {"file_path": path}, {"originalFile": "jobs:\n  t:\n    steps:\n      - run: pytest -q\n"})[1])


if __name__ == "__main__":
    unittest.main()
