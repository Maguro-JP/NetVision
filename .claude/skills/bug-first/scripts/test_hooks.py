#!/usr/bin/env python3
"""bug_prompt.py、bug_gate.py、crash_nudge.py を、作った入力と会話の記録で確かめる。登録も見る。

フックの入力の形は、公式の文書と実際の記録に合わせる。PostToolUse は成功した呼び出しでしか動かず、
失敗した Bash の出力は、会話の記録の is_error の結果に入る。人の発言、通知、Stop フックの差し戻しは、どれも type が user で来る。
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
TEMPLATE = SKILL.parent / "repo-bootstrap" / "templates" / "settings.json"
HAS_CS = (SKILL.parent / "common-sense" / "hooks" / "_cs.py").is_file()
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(SKILL / "scripts"))
import bug_prompt  # noqa: E402
import bugs  # noqa: E402
import crash_nudge  # noqa: E402

TRACEBACK = ('Traceback (most recent call last):\n  File "/repo/app/main.py", line 12, in <module>\n    run()\n'
             '  File "/repo/app/store.py", line 40, in save\n    data = json.loads(text)\nValueError: invalid literal for int() with base 10: \'x\'\n')


def user(text, kind="human"):
    return {"type": "user", "origin": {"kind": kind}, "message": {"role": "user", "content": text}}


def result(tool_use_id, text="ok", is_error=False):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": text, "is_error": is_error}]}}


def call(name, inp, tid):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def say(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def feedback(text):
    return {"type": "user", "isMeta": True, "message": {"role": "user", "content": "Stop hook feedback:\n" + text}}


class Base(unittest.TestCase):
    hooks = HOOKS

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.state = str(Path(self.tmp.name) / "state")

    def run_hook(self, hook, payload, **env):
        e = dict(os.environ, BUG_FIRST_STATE=self.state, HOME=self.tmp.name, **env)
        if "BUG_FIRST_OFF" not in env:
            e.pop("BUG_FIRST_OFF", None)
        return subprocess.run([sys.executable, "-I", str(self.hooks / hook)], input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True,
                              timeout=60, env=e, cwd=self.root)

    def transcript(self, lines):
        p = Path(self.tmp.name) / "t.jsonl"
        p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
        return str(p)

    def ledger(self, *rows):
        """rows は (症状, 重さ, 状態)。番号は B-001 から。"""
        for i, (title, severity, state) in enumerate(rows, 1):
            bugs.add(self.root, title, severity=severity)
            if state != "未修正":
                bugs.set_fields(self.root, f"B-{i:03d}", {"状態": state})


# ---- bug_prompt.py -----------------------------------------------------------
class Report(unittest.TestCase):
    YES = ["保存したらクラッシュする", "アプリが落ちる", "起動しないんだけど", "画面が固まって操作できない", "設定が消えた", "ファイルが壊れた", "文字化けしてる",
           "エラーが出る", "なんで落ちるの?", "バグってる", "バグがある。直して", "検索のバグを直して", "鍵がログに漏れてる", "落ちるときがある", "保存したら落ちた",
           'Traceback (most recent call last):\n  File "app.py", line 3', "ウィンドウが真っ白", "ボタンが反応しない", "落ちたり固まったりする", "動かなくなった",
           "操作不能になった", "保存されない", "書いたメモが消えてる", "入力の途中で固まる"]
    NO = ["README の表を直して", "バグは無い?", "バグある?", "バグがある?", "バグが無いか見て", "バグがあるか確認して", "クラッシュしない?", "漏れてない?",
          "何よりもバグ修正を優先させて\n特に致命的なバグ、クラッシュや操作不能、破損や漏洩等は即修正版を出せ", "バグも放置",
          "落ちたら直して", "クラッシュしたときは修正版を出せ", "固まっても再起動できるようにして", "動かないなら言って", "エラーが出たら教えて",
          "「落ちる」という語をフックに足して", "バグの語の検出を直して", "テストが落ちた", "CI が落ちてる", "Actions が落ちました", "マージして", "続けて",
          "エラーが消えた", "警告は消えてる?", "古いブランチは消えてる?", "方針が固まった", "仕様は固まってる"]

    def test_reports(self):
        for s in self.YES:
            self.assertTrue(bug_prompt.report(s), s)

    def test_not_reports(self):
        for s in self.NO:
            self.assertFalse(bug_prompt.report(s), s)


class Prompt(Base):
    def ask(self, prompt, **env):
        r = self.run_hook("bug_prompt.py", {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": prompt}, **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r.stdout.strip()

    def test_report_gets_one_line(self):
        out = self.ask("保存したらクラッシュする")
        self.assertEqual(out, bug_prompt.LINE)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertIn("bug-first", out)

    def test_plain_request_is_silent(self):
        self.assertEqual(self.ask("README の表を直して"), "")

    def test_notification_feedback_and_child_prompt_are_silent(self):
        self.assertEqual(self.ask("<task-notification>\n<summary>アプリが落ちる</summary>\n</task-notification>"), "")
        self.assertEqual(self.ask("Stop hook feedback:\nアプリが落ちる、と書いた行に根拠が無い"), "")
        self.assertEqual(self.ask("親のセッションから分けた 1 本です。アプリが落ちるのを直してください"), "")
        self.assertEqual(self.ask("アプリが落ちる。\nセッションやルーティンを作らない。"), "")

    def test_long_paste_is_silent(self):
        self.assertEqual(self.ask("仕様です。" * 900 + "アプリが落ちる"), "")

    def test_off(self):
        self.assertEqual(self.ask("保存したらクラッシュする", BUG_FIRST_OFF="1"), "")

    def test_broken_input_passes(self):
        r = subprocess.run([sys.executable, "-I", str(self.hooks / "bug_prompt.py")], input="{", capture_output=True, text=True, timeout=60, cwd=self.root)
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class Start(Base):
    def start(self):
        r = self.run_hook("bug_prompt.py", {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r.stdout.strip()

    def test_no_ledger_is_silent(self):
        self.assertEqual(self.start(), "")

    def test_open_fatal(self):
        self.ledger(("保存のあとに起動しない", "致命的", "未修正"))
        out = self.start()
        self.assertEqual(len(out.splitlines()), 1)
        self.assertIn("直していない致命的なバグが 1 件（B-001 保存のあとに起動しない）", out)
        self.assertLess(len(out), 200)

    def test_waiting_and_heavy(self):
        self.ledger(("保存のあとに起動しない", "致命的", "確認待ち"), ("検索が効かない", "重い", "未修正"))
        out = self.start()
        self.assertIn("修正版の確認待ちが 1 件", out)
        self.assertIn("重いバグが 1 件（B-002 検索が効かない）", out)
        self.assertNotIn("直していない致命的", out)

    def test_fixed_and_light_are_silent(self):
        self.ledger(("保存のあとに起動しない", "致命的", "修正済み"), ("文言が古い", "軽い", "未修正"), ("誤報", "致命的", "対象外"))
        self.assertEqual(self.start(), "")


# ---- bug_gate.py -------------------------------------------------------------
@unittest.skipUnless(HAS_CS, "common-sense（会話の記録の読み手）が無い")
class Gate(Base):
    def setUp(self):
        super().setUp()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True, capture_output=True)
        (self.root / "src").mkdir()

    def stop(self, lines, last="★ 検索の並べ替えを足しました。", **payload):
        d = {"hook_event_name": "Stop", "session_id": "s1", "transcript_path": self.transcript(lines), "stop_hook_active": False, "last_assistant_message": last}
        d.update(payload)
        env = {k: d.pop(k) for k in list(d) if k.startswith("BUG_FIRST_")}
        r = self.run_hook("bug_gate.py", d, **env)
        self.assertNotIn("Traceback", r.stderr)
        return r.returncode, r.stderr.strip()

    def edit(self, rel="src/app.py", tid="e1", ok=True, tool="Edit"):
        path = rel if os.path.isabs(rel) else str(self.root / rel)
        return [call(tool, {"file_path": path, "old_string": "a", "new_string": "b"}, tid), result(tid, "updated" if ok else "String to replace not found", not ok)]

    def test_blocks_other_edit_while_fatal_is_open(self):
        self.ledger(("保存のあとに起動しない", "致命的", "未修正"))
        code, err = self.stop([user("検索に並べ替えを足して")] + self.edit() + [say("★ 足しました。")])
        self.assertEqual(code, 2)
        self.assertEqual(len(err.splitlines()), 1)
        self.assertIn("B-001 保存のあとに起動しない", err)
        self.assertIn("src/app.py", err)
        self.assertIn("❕ バグ:", err)

    def test_state_in_progress_still_blocks(self):
        self.ledger(("保存のあとに起動しない", "致命的", "修正中"))
        self.assertEqual(self.stop([user("並べ替えを足して")] + self.edit())[0], 2)

    def test_notification_does_not_split_the_turn(self):
        self.ledger(("保存のあとに起動しない", "致命的", "未修正"))
        lines = [user("並べ替えを足して")] + self.edit() + [user("<task-notification>\n<summary>終わった</summary>\n</task-notification>", "task-notification"), say("❕ 終わりました。")]
        self.assertEqual(self.stop(lines)[0], 2)

    def test_no_ledger_passes(self):
        self.assertEqual(self.stop([user("並べ替えを足して")] + self.edit()), (0, ""))

    def test_no_open_fatal_passes(self):
        self.ledger(("検索が効かない", "重い", "未修正"), ("起動しない", "致命的", "修正済み"), ("落ちる", "致命的", "確認待ち"), ("誤報", "致命的", "対象外"))
        self.assertEqual(self.stop([user("並べ替えを足して")] + self.edit()), (0, ""))

    def test_no_edit_passes(self):
        self.ledger(("起動しない", "致命的", "未修正"))
        lines = [user("状況は?"), call("Bash", {"command": "git status"}, "b1"), result("b1"), call("Read", {"file_path": str(self.root / "src/app.py")}, "r1"), result("r1")]
        self.assertEqual(self.stop(lines), (0, ""))

    def test_failed_edit_passes(self):
        self.ledger(("起動しない", "致命的", "未修正"))
        self.assertEqual(self.stop([user("並べ替えを足して")] + self.edit(ok=False)), (0, ""))

    def test_edit_in_earlier_turn_passes(self):
        self.ledger(("起動しない", "致命的", "未修正"))
        lines = [user("並べ替えを足して")] + self.edit() + [say("★ 足しました。"), user("ありがとう"), say("↩ はい。")]
        self.assertEqual(self.stop(lines), (0, ""))

    def test_records_and_outside_files_are_not_development(self):
        self.ledger(("起動しない", "致命的", "未修正"))
        lines = ([user("記録を書いて")] + self.edit("docs/reports/pending.md", "e1") + self.edit("docs/knowledge/tech/a.md", "e2")
                 + self.edit("LESSONS.md", "e3") + self.edit(str(Path(self.tmp.name) / "scratch" / "note.py"), "e4", tool="Write"))
        self.assertEqual(self.stop(lines), (0, ""))

    def test_touching_the_ledger_passes(self):
        self.ledger(("起動しない", "致命的", "未修正"))
        self.assertEqual(self.stop([user("直して")] + self.edit() + self.edit("docs/reports/bugs.md", "e2")), (0, ""))
        cmd = "python3 .claude/skills/bug-first/scripts/bugs.py --set B-001 --state 修正中"
        self.assertEqual(self.stop([user("直して")] + self.edit() + [call("Bash", {"command": cmd}, "b1"), result("b1")]), (0, ""))

    def test_blocked_ledger_command_does_not_count(self):
        self.ledger(("起動しない", "致命的", "未修正"))
        cmd = "python3 .claude/skills/bug-first/scripts/bugs.py --add 別の件"
        lines = [user("足して")] + self.edit() + [call("Bash", {"command": cmd}, "b1"), result("b1", "PreToolUse:Bash hook error: 止めた", True)]
        self.assertEqual(self.stop(lines)[0], 2)

    def test_call_with_the_bug_id_passes(self):
        self.ledger(("起動しない", "致命的", "未修正"))
        lines = [user("直して")] + self.edit() + [call("Bash", {"command": "git commit -m 'FIX B-001: 起動しないのを直す'"}, "b1"), result("b1")]
        self.assertEqual(self.stop(lines), (0, ""))
        # 番号の一部が同じだけの別の番号では通さない
        lines = [user("直して")] + self.edit() + [call("Bash", {"command": "git commit -m 'B-0012 の件'"}, "b1"), result("b1")]
        self.assertEqual(self.stop(lines)[0], 2)

    def test_fix_branch_passes(self):
        self.ledger(("起動しない", "致命的", "未修正"))
        subprocess.run(["git", "-C", str(self.root), "checkout", "-q", "-b", "claude/FIX-startup-20261121"], check=True, capture_output=True)
        self.assertEqual(self.stop([user("直して")] + self.edit()), (0, ""))

    def test_other_branch_names_do_not_pass(self):
        self.ledger(("起動しない", "致命的", "未修正"))
        subprocess.run(["git", "-C", str(self.root), "checkout", "-q", "-b", "claude/DEV-prefix-20261121"], check=True, capture_output=True)
        self.assertEqual(self.stop([user("足して")] + self.edit())[0], 2)

    def test_reason_line_passes(self):
        self.ledger(("起動しない", "致命的", "未修正"))
        lines = [user("先に並べ替えを足して")] + self.edit()
        self.assertEqual(self.stop(lines, last="★ 足しました。\n❕ バグ: B-001 は未修正。ユーザーの指示で並べ替えを先にした。"), (0, ""))
        self.assertEqual(self.stop(lines, last="★ 足しました。バグ: あとで")[0], 2)

    def test_stop_hook_active_and_off_pass(self):
        self.ledger(("起動しない", "致命的", "未修正"))
        lines = [user("足して")] + self.edit()
        self.assertEqual(self.stop(lines, stop_hook_active=True), (0, ""))
        self.assertEqual(self.stop(lines, BUG_FIRST_OFF="1"), (0, ""))

    def test_feedback_keeps_the_turn(self):
        # Stop フックの差し戻しは人の発言ではない。差し戻しのあとも、同じターンの編集を見る
        self.ledger(("起動しない", "致命的", "未修正"))
        lines = [user("足して")] + self.edit() + [say("★ 足しました。"), feedback("疑いの行が無い"), say("❕ 疑い: テストは動かしていない。")]
        self.assertEqual(self.stop(lines)[0], 2)


class GateWithoutCommonSense(Base):
    """common-sense が無いリポジトリでは、黙って通る。"""

    def test_passes_silently(self):
        skills = Path(self.tmp.name) / "skills"
        shutil.copytree(SKILL, skills / "bug-first", ignore=shutil.ignore_patterns("__pycache__"))
        self.hooks = skills / "bug-first" / "hooks"
        bugs.add(self.root, "起動しない", severity="致命的")
        path = str(self.root / "src" / "app.py")
        lines = [user("足して"), call("Edit", {"file_path": path, "old_string": "a", "new_string": "b"}, "e1"), result("e1")]
        d = {"hook_event_name": "Stop", "transcript_path": self.transcript(lines), "stop_hook_active": False, "last_assistant_message": "★ 足しました。"}
        r = self.run_hook("bug_gate.py", d)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        # クラッシュの知らせは、今の出力だけを見て動く
        r = self.run_hook("crash_nudge.py", {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Bash", "tool_use_id": "t1",
                                             "transcript_path": d["transcript_path"], "tool_input": {"command": "python3 app/main.py || true"},
                                             "tool_response": {"stdout": "", "stderr": TRACEBACK, "interrupted": False, "isImage": False}})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("クラッシュの出力（Traceback: ValueError", r.stdout)


# ---- crash_nudge.py ----------------------------------------------------------
class Crash(unittest.TestCase):
    def test_marks(self):
        cases = [
            ("python3 app/main.py", TRACEBACK, "Traceback"),
            ("cargo run", "thread 'main' panicked at src/main.rs:4:5:\nindex out of bounds: the len is 3 but the index is 7\n", "panic"),
            ("go run .", "panic: runtime error: invalid memory address or nil pointer dereference\n\ngoroutine 1 [running]:\nmain.main()\n", "panic"),
            ("node app.js", "node:internal/process/promises:288\n[UnhandledPromiseRejection: This error originated either by throwing inside of an async function]\n", "Unhandled"),
            ("node app.js", "Uncaught TypeError: Cannot read properties of undefined (reading 'x')\n    at main (app.js:3:9)\n", "Unhandled"),
            ("./app", "Segmentation fault (core dumped)\n", "Segmentation fault"),
            ("node big.js", "FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory\n", "FATAL"),
            ("tail -50 /tmp/app.log", "[12:00:01] FATAL: could not open the settings file\n", "FATAL"),
            ("tail -50 /tmp/run.log", "starting\n" + TRACEBACK, "Traceback"),
        ]
        for cmd, text, want in cases:
            hit = crash_nudge.crash(cmd, text)
            self.assertIsNotNone(hit, cmd)
            self.assertEqual(hit[0], want, cmd)

    def test_not_crashes(self):
        own = 'Traceback (most recent call last):\n  File "<string>", line 1, in <module>\nNameError: name \'x\' is not defined\n'
        installed = ('Traceback (most recent call last):\n  File "/usr/lib/python3/dist-packages/pip/_internal/cli.py", line 9, in main\n    run()\n'
                     'pip._internal.exceptions.InstallationError: no such package\n')
        cases = [
            ("git status", "fatal: not a git repository (or any of the parent directories): .git\n"),
            ("python3 -m pytest -q", TRACEBACK),
            ("python3 scripts/test_store.py", TRACEBACK + "\nRan 3 tests in 0.1s\n\nFAILED (errors=1)\n"),
            ("tail -30 /tmp/precheck.log", "ERROR: test_save (__main__.Store.test_save)\n" + TRACEBACK + "\nRan 3 tests in 0.1s\n"),
            ("cargo test", "running 2 tests\nthread 'tests::it_works' panicked at src/lib.rs:9:9:\nassertion failed\ntest result: FAILED. 1 passed; 1 failed\n"),
            ("grep -rn Traceback .", TRACEBACK),
            ("cd /repo && rg 'panicked at' src", "thread 'main' panicked at src/main.rs:4:5:\n"),
            ("cat scripts/test_hooks.py", TRACEBACK),
            ("sed -n 1,80p docs/notes.md", TRACEBACK),
            ("git diff HEAD~1", TRACEBACK),
            ("python3 -c 'print(x)'", own),
            ("pip install nothing", installed),
            ("python3 app/main.py", TRACEBACK.replace("ValueError: invalid literal for int() with base 10: 'x'", "KeyboardInterrupt")),
            ("python3 app/main.py | head -1", TRACEBACK.replace("ValueError: invalid literal for int() with base 10: 'x'", "BrokenPipeError: [Errno 32] Broken pipe")),
            ("python3 app/main.py", "12:  Traceback (most recent call last):\n"),
            ("python3 app/main.py", "完了しました\n"),
            ("python3 app/main.py", ""),
        ]
        for cmd, text in cases:
            self.assertIsNone(crash_nudge.crash(cmd, text), cmd)

    def test_hard_marks_count_even_in_tests(self):
        self.assertEqual(crash_nudge.crash("python3 -m pytest -q", "tests/test_native.py Segmentation fault (core dumped)\n")[0], "Segmentation fault")

    def test_same_crash_has_the_same_signature(self):
        a = crash_nudge.crash("python3 app/main.py", TRACEBACK)
        b = crash_nudge.crash("python3 app/main.py --again", TRACEBACK.replace("line 12", "line 13").replace("base 10", "base 16"))
        self.assertEqual(a, b)

    def test_cut_traceback_still_counts(self):
        self.assertEqual(crash_nudge.crash("python3 app/main.py 2>&1 | head -3", "\n".join(TRACEBACK.splitlines()[:3]) + "\n")[0], "Traceback")


class Nudge(Base):
    def post(self, command="python3 app/main.py || true", stdout="", stderr="", sid="s1", tid="t1", lines=None, tool="Bash", **env):
        d = {"hook_event_name": "PostToolUse", "session_id": sid, "tool_name": tool, "tool_use_id": tid, "tool_input": {"command": command},
             "tool_response": {"stdout": stdout, "stderr": stderr, "interrupted": False, "isImage": False}}
        if lines is not None:
            d["transcript_path"] = self.transcript(lines)
        r = self.run_hook("crash_nudge.py", d, **env)
        self.assertEqual(r.returncode, 0, r.stderr)  # 知らせるだけのフックは、止めない
        self.assertNotIn("Traceback", r.stderr)
        if not r.stdout.strip():
            return ""
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PostToolUse")
        self.assertEqual(len(out["additionalContext"].splitlines()), 1)
        return out["additionalContext"]

    def test_crash_is_told_once(self):
        out = self.post(stderr=TRACEBACK)
        self.assertIn("クラッシュの出力（Traceback: ValueError: invalid literal", out)
        self.assertIn("帳簿に書き、先に直す", out)
        self.assertEqual(self.post(stderr=TRACEBACK, tid="t2"), "")  # 同じ出力には 1 回だけ
        self.assertTrue(self.post(stderr=TRACEBACK, sid="s2"))        # 別のセッションでは、もう一度

    def test_other_tools_and_clean_output_are_silent(self):
        self.assertEqual(self.post(stderr=TRACEBACK, tool="Read"), "")
        self.assertEqual(self.post(stdout="完了\n"), "")

    def test_test_output_is_silent(self):
        self.assertEqual(self.post(command="python3 -m pytest -q", stdout=TRACEBACK), "")

    def test_off_and_cap(self):
        self.assertEqual(self.post(stderr=TRACEBACK, BUG_FIRST_OFF="1"), "")
        self.assertTrue(self.post(stderr=TRACEBACK, BUG_FIRST_MAX_NUDGE="1"))
        self.assertEqual(self.post(stderr="Segmentation fault (core dumped)\n", tid="t2", BUG_FIRST_MAX_NUDGE="1"), "")

    @unittest.skipUnless(HAS_CS, "common-sense（会話の記録の読み手）が無い")
    def test_failed_command_is_caught_at_the_next_success(self):
        # 失敗した Bash では PostToolUse が動かない。次に成功した Bash のときに、記録から拾う
        lines = [user("動かして"), call("Bash", {"command": "python3 app/main.py"}, "f1"), result("f1", "Exit code 1\n" + TRACEBACK, True),
                 call("Bash", {"command": "git status"}, "t1")]
        out = self.post(command="git status", stdout="On branch main\n", lines=lines)
        self.assertIn("クラッシュの出力（Traceback: ValueError", out)
        self.assertEqual(self.post(command="git status", stdout="On branch main\n", tid="t2", lines=lines), "")  # 同じ結果を 2 回見ない

    @unittest.skipUnless(HAS_CS, "common-sense（会話の記録の読み手）が無い")
    def test_failure_of_an_earlier_turn_is_not_brought_back(self):
        lines = [user("動かして"), call("Bash", {"command": "python3 app/main.py"}, "f1"), result("f1", "Exit code 1\n" + TRACEBACK, True), say("❕ 落ちました。"),
                 user("状況は?"), call("Bash", {"command": "git status"}, "t1")]
        self.assertEqual(self.post(command="git status", stdout="On branch main\n", lines=lines), "")

    @unittest.skipUnless(HAS_CS, "common-sense（会話の記録の読み手）が無い")
    def test_silent_when_the_same_failure_repeats_in_one_output(self):
        # 同じ失敗が 3 件以上並ぶ出力は、common-sense の odd_nudge.py が言う。重ねて言わない
        many = "".join(f"repo-{c}: 実行に失敗した（ValueError: invalid literal）\n" for c in "abc") + TRACEBACK
        self.assertEqual(self.post(stderr=many), "")


# ---- 登録 --------------------------------------------------------------------
class Registered(unittest.TestCase):
    WANT = {"SessionStart": ["bug_prompt.py"], "UserPromptSubmit": ["bug_prompt.py"], "Stop": ["bug_gate.py"], "PostToolUse": ["crash_nudge.py"]}

    def test_hooks_are_registered_in_the_template_through_the_launcher(self):
        if not TEMPLATE.is_file():
            self.skipTest("ひな形が無い")
        hooks = json.loads(TEMPLATE.read_text(encoding="utf-8")).get("hooks", {})
        for event, names in self.WANT.items():
            cmds = [h.get("command", "") for e in hooks.get(event, []) for h in e.get("hooks", [])]
            for n in names:
                want = f'"${{CLAUDE_PROJECT_DIR:-.}}/.claude/hooks/run.sh" .claude/skills/bug-first/hooks/{n}'
                self.assertIn(want, cmds, f"{event} に {n} の登録が無い")
        # 配られるのは SessionStart・UserPromptSubmit・Stop・PostToolUse だけ。ほかのイベントに頼らない
        for event, groups in hooks.items():
            if event not in self.WANT:
                self.assertFalse([h for g in groups for h in g.get("hooks", []) if "bug-first" in h.get("command", "")], event)

    def test_crash_nudge_is_matched_to_bash(self):
        if not TEMPLATE.is_file():
            self.skipTest("ひな形が無い")
        hooks = json.loads(TEMPLATE.read_text(encoding="utf-8")).get("hooks", {})
        rows = [g for g in hooks.get("PostToolUse", []) if any("crash_nudge.py" in h.get("command", "") for h in g.get("hooks", []))]
        self.assertEqual([g.get("matcher") for g in rows], ["Bash"])

    def test_hook_files_exist(self):
        for names in self.WANT.values():
            for n in names:
                self.assertTrue((HOOKS / n).is_file(), n)


if __name__ == "__main__":
    unittest.main()
