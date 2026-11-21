#!/usr/bin/env python3
"""today.py、odd_nudge.py、memory_nudge.py を、作った入力と会話の記録で確かめる。ひな形の登録も見る。

フックの入力の形は、公式の文書と実際の記録に合わせる。PostToolUse は成功した呼び出しでしか動かず、
失敗は PostToolUseFailure の error（Bash は「Exit code N」で始まる）か、会話の記録の is_error の結果に入る。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
HOOKS = SKILL / "hooks"
TEMPLATE = SKILL.parent / "repo-bootstrap" / "templates" / "settings.json"
TODAY = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
SAME3 = "repo-a: precheck 落ちた（test_sync.py FAILED failures=1）\nrepo-b: precheck 落ちた（test_sync.py FAILED failures=1）\nrepo-c: precheck 落ちた（test_sync.py FAILED failures=1）\n誤り 0  注意 0\n"


def user(text, kind="human"):
    return {"type": "user", "origin": {"kind": kind}, "message": {"role": "user", "content": text}}


def result(tool_use_id, text="ok", is_error=False):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": text, "is_error": is_error}]}}


def call(name, inp, tid):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def feedback(text):
    return {"type": "user", "isMeta": True, "message": {"role": "user", "content": "Stop hook feedback:\n" + text}}


def notices(n, body="FAILED"):
    text = "\n".join(f"<task-notification>\n<task-id>b{i}</task-id>\n<summary>repo: precheck 落ちた（{body}）</summary>\n</task-notification>" for i in range(n))
    return user(text, "task-notification")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.state = str(Path(self.tmp.name) / "state")

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, hook, payload, **env):
        e = dict(os.environ, COMMON_SENSE_STATE=self.state, **env)
        if "COMMON_SENSE_OFF" not in env:
            e.pop("COMMON_SENSE_OFF", None)
        r = subprocess.run([sys.executable, "-I", str(HOOKS / hook)], input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True,
                           timeout=60, env=e, cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)  # 知らせるだけのフックは、止めない
        self.assertNotIn("Traceback", r.stderr)
        return r.stdout

    def context(self, out, event="PostToolUse"):
        """差し込まれる 1 行。無ければ空。"""
        if not out.strip():
            return ""
        d = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(d["hookEventName"], event)
        self.assertEqual(len(d["additionalContext"].splitlines()), 1)
        return d["additionalContext"]

    def transcript(self, lines):
        p = Path(self.tmp.name) / "t.jsonl"
        p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
        return str(p)


class Today(Base):
    def prompt(self, text="README を直して", session="s1", **env):
        return self.run_hook("today.py", {"session_id": session, "hook_event_name": "UserPromptSubmit", "prompt": text}, **env)

    def test_session_start_prints_one_short_line_with_the_date_in_japan(self):
        out = self.run_hook("today.py", {"session_id": "s1", "hook_event_name": "SessionStart", "source": "startup"})
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertLessEqual(len(out.strip()), 120)
        self.assertIn(TODAY, out)
        self.assertIn("日本時間", out)
        self.assertIn("学習の知識は古い", out)
        self.assertIn("一次情報", out)

    def test_prompt_prints_once_in_ten_turns(self):
        outs = [self.prompt() for _ in range(20)]
        self.assertEqual([i + 1 for i, o in enumerate(outs) if o.strip()], [10, 20])

    def test_interval_can_be_changed_and_notifications_are_not_counted(self):
        outs = [self.prompt(COMMON_SENSE_EVERY="2"), self.prompt("<task-notification>x</task-notification>", COMMON_SENSE_EVERY="2"), self.prompt(COMMON_SENSE_EVERY="2")]
        self.assertEqual([bool(o.strip()) for o in outs], [False, False, True])

    def test_date_change_prints_at_the_next_prompt(self):
        self.assertEqual(self.prompt().strip(), "")
        f = Path(self.state) / "s1.today.json"
        st = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(st["day"], TODAY)
        f.write_text(json.dumps({"turn": st["turn"], "day": "2000-01-01"}), encoding="utf-8")
        self.assertIn(TODAY, self.prompt())
        self.assertEqual(self.prompt().strip(), "")

    def test_off_and_no_session(self):
        self.assertEqual(self.prompt(COMMON_SENSE_OFF="1").strip(), "")
        self.assertIn(TODAY, self.run_hook("today.py", {"hook_event_name": "UserPromptSubmit", "prompt": "x"}))  # セッションが分からなければ間引かない
        self.assertIn(TODAY, self.run_hook("today.py", {}))


class Odd(Base):
    def post(self, command="python3 sync_all.py", stdout="", tid=None, transcript=None, name="Bash", response=None, **env):
        p = {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": name, "tool_input": {"command": command} if name == "Bash" else {},
             "tool_response": response if response is not None else {"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False}}
        if tid:
            p["tool_use_id"] = tid
        if transcript:
            p["transcript_path"] = transcript
        return self.context(self.run_hook("odd_nudge.py", p, **env))

    # 名前を fail にしない。unittest の fail を上書きすると、この組の検査が 1 つも落ちなくなる
    def failed_call(self, command="git push origin HEAD", error="Exit code 1\nfatal: unable to access 'https://example.org/': 403", tid=None, name="Bash", **extra):
        p = {"session_id": "s1", "hook_event_name": "PostToolUseFailure", "tool_name": name, "tool_input": {"command": command}, "error": error, **extra}
        if tid:
            p["tool_use_id"] = tid
        return self.context(self.run_hook("odd_nudge.py", p), "PostToolUseFailure")

    def test_three_same_failure_lines_in_one_output(self):
        msg = self.post(stdout=SAME3)
        self.assertIn("同じ失敗が 3 件", msg)
        self.assertIn("共通の原因", msg)
        self.assertEqual(self.post(stdout=SAME3), "")  # 同じ失敗には 1 回だけ

    def test_same_error_kind_across_different_tests(self):
        out = "".join(f"FAILED tests/t{i}.py::test_{i} - ModuleNotFoundError: No module named 'yaml'\n" for i in range(4))
        self.assertIn("同じ失敗が 4 件", self.post("python3 -m pytest -q | tail -20", out))

    def test_error_lines_without_a_prefix_are_counted(self):
        out = "ModuleNotFoundError: No module named 'yaml'\n" * 3
        self.assertIn("同じ失敗が 3 件", self.post("sh run_all.sh | tail -30", out))

    def test_source_code_and_harmless_words_are_not_failures(self):
        # ファイルの中身を出したときの行（実際の記録を当てて見つけた偽の知らせ）
        code = "    except Exception:\n        pass\n" * 4 + "+    raise ValueError(\"x\")\n" * 3 + "12:    except OSError:\n" * 3
        self.assertEqual(self.post("sed -n 1,80p hooks/x.py", code), "")
        # 「まだ止められる」は、検査の結果の言い回しで、失敗ではない
        audit = "".join(f"docs/a{i}.md:{i}（作業ツリー、まだ止められる）\n" for i in range(5))
        self.assertEqual(self.post("python3 audit.py --diff", audit), "")
        # 失敗の中身は、ソースコードの行ではなく、エラーの行から取る
        err = "Exit code 1\nTraceback (most recent call last):\n  File \"a.py\", line 3, in <module>\n    raise ValueError(\"x\")\nValueError: x"
        self.assertEqual(self.failed_call("python3 a.py", err, tid="e1"), "")
        self.assertIn("同じ失敗の 2 回目（valueerror: x）", self.failed_call("python3 a.py", err, tid="e2"))

    def test_quiet_when_not_the_same_or_not_a_failure(self):
        self.assertEqual(self.post(stdout="repo-a: 落ちた（lint）\nrepo-b: 落ちた（test_sync.py）\nok\n"), "")
        self.assertEqual(self.post(stdout="誤り 0  注意 0\n誤り 0  注意 0\n誤り 0  注意 0\n3 passed, 0 failed\n0 errors\n0 errors\n0 errors\n"), "")
        self.assertEqual(self.post('grep -rn "error" src', "a.py:1: raise error\nb.py:2: raise error\nc.py:3: raise error\n"), "")  # 探した結果の行
        self.assertEqual(self.post(name="Read", response={"file": {"content": SAME3}}), "")  # Bash と MCP だけを見る
        self.assertEqual(self.post(stdout=SAME3, COMMON_SENSE_OFF="1"), "")
        self.assertEqual(self.post(stdout=SAME3, COMMON_SENSE_MAX_NUDGE="0"), "")

    def test_search_command_after_a_separator_is_still_a_search(self):
        # 探した結果の行は、失敗の語を含んでいても失敗ではない。cd や変数を前に並べた形、中身や一覧をパイプで渡す形も同じ（実際の記録で出た偽の知らせ）
        hits = "a.py:1: raise error\nb.py:2: raise error\nc.py:3: raise error\n"
        for cmd in ('cd /repo && grep -rn "error" src',
                    'cd /repo; grep -rn "error" src',
                    'cd /repo && git grep -n error',
                    'export LC_ALL=C && rg -n "error|failed" src | head -20',
                    "cat notes.md | grep -n 'error; failed'",
                    'git ls-files | xargs grep -n "error"',
                    'cd /repo && grep -c error a.py; grep -n error b.py'):
            self.assertEqual(self.post(cmd, hits), "", cmd)
        # ソースコードのファイルの中身を出す形。中身の行は、失敗の出力ではない（CODE の形に当たらない行もある）
        code = "".join(f'        r.write({{"app/a{i}.py": "try:\\n    go()\\nexcept Exception:\\n    pass\\n"}})\n' for i in range(3))
        self.assertEqual(self.post("cd /repo && sed -n 1,40p scripts/test_x.py && grep -n 'diff' scripts/x.py", code), "")
        self.assertEqual(self.post("sed -n 116,216p .claude/skills/x/scripts/test_x.py", code), "")
        self.assertEqual(self.post("cat hooks/a.py | head -40", code), "")

    def test_filtered_output_of_a_run_is_not_a_search(self):
        # 実行の出力を grep で絞った形は、実行の結果。探すコマンドと見ない（配った先の全部で同じテストが落ちた、を拾う）
        for cmd in ('python3 -m pytest -q 2>&1 | grep FAILED',
                    'cd /repo && python3 sync_all.py | grep -E "落ちた|OK"',
                    'for r in a b c; do (cd $r && python3 t.py 2>&1 | grep -E "落ちた|OK"); done',
                    'grep -c error a.py && python3 sync_all.py',
                    'tail -30 /tmp/precheck.log',
                    'cat /tmp/dist/result.json',
                    'sed -n 1,40p a.py && python3 a.py'):
            self.setUp()
            self.assertIn("同じ失敗が 3 件", self.post(cmd, SAME3), cmd)

    def test_counted_results_are_not_failure_lines(self):
        # 数えた結果の行（「失敗」のあとが数だけ）は、何が失敗したかを言わない。同じ失敗の行に数えない（実際の記録で出た偽の知らせ）
        counted = "".join(f'{{"種類": "k{i}", "見つけた": {i + 4}, "うち失敗": 2}}\n' for i in range(3))
        self.assertEqual(self.post("python3 stats.py --json", counted), "")
        for line in ("no-fake: 見つけた 5、うち失敗: 2", "ひな形の検査  失敗 2 件", "誤り 1  注意 30", "tests run: 9, failed: 2", "FAILED (failures=3)",
                     "found 12, errors: 12"):
            self.setUp()
            got = self.post("python3 stats.py", (line + "\n") * 3)
            if line.startswith("FAILED"):
                self.assertIn("同じ失敗が 3 件", got, line)  # 数のほかに失敗の語がある行は、数える
            else:
                self.assertEqual(got, "", line)
        # 数のほかに失敗の中身がある行は、これまでどおり数える
        self.assertIn("同じ失敗が 3 件", self.post("python3 sync_all.py", SAME3))
        self.setUp()
        self.assertIn("同じ失敗が 3 件", self.post("python3 run.py", "HTTP Error: 500\n" * 3))
        self.setUp()
        self.assertIn("同じ失敗が 3 件", self.post("python3 run.py", "エラー: 2 件のファイルが見つかりません\n" * 3))

    def test_mcp_result_as_text_blocks(self):
        msg = self.post(name="mcp__github__actions_list", response=[{"type": "text", "text": SAME3}])
        self.assertIn("同じ失敗が 3 件", msg)

    def test_same_command_failing_twice_with_the_same_error(self):
        self.assertEqual(self.failed_call(tid="f1"), "")
        msg = self.failed_call(tid="f2")
        self.assertIn("同じ失敗の 2 回目", msg)
        self.assertIn("原因を調べる", msg)
        self.assertEqual(self.failed_call(tid="f3"), "")  # 3 回目は黙る（同じ文を繰り返さない）

    def test_different_error_or_interrupt_is_not_the_same_failure(self):
        self.assertEqual(self.failed_call(tid="f1"), "")
        self.assertEqual(self.failed_call(tid="f2", error="Exit code 128\nfatal: not a git repository"), "")
        self.assertEqual(self.failed_call(tid="f3", is_interrupt=True), "")
        self.assertEqual(self.failed_call(tid="f4", error="Permission for this action has been denied"), "")
        self.assertEqual(self.failed_call(tid="f5", error="Permission for this action has been denied"), "")

    def test_failures_are_picked_up_from_the_transcript_when_the_failure_hook_is_not_registered(self):
        err = "Exit code 1\nfatal: unable to access 'https://example.org/': 403"
        t = self.transcript([user("push して"), call("Bash", {"command": "git push origin HEAD"}, "f1"), result("f1", err, True),
                             call("Bash", {"command": "git  push origin HEAD"}, "f2"), result("f2", err, True),
                             call("Bash", {"command": "git status"}, "ok1"), result("ok1", "clean")])
        msg = self.post("git status", "clean", tid="ok1", transcript=t)
        self.assertIn("同じ失敗の 2 回目", msg)
        self.assertEqual(self.post("git status", "clean", tid="ok2", transcript=t), "")  # 同じ結果を 2 回数えない

    def test_failure_seen_by_the_failure_hook_is_not_counted_again_from_the_transcript(self):
        err = "Exit code 1\nfatal: unable to access 'https://example.org/': 403"
        self.assertEqual(self.failed_call(tid="f1", error=err), "")
        t = self.transcript([user("push して"), call("Bash", {"command": "git push origin HEAD"}, "f1"), result("f1", err, True)])
        self.assertEqual(self.post("git status", "clean", tid="ok1", transcript=t), "")
        self.assertIn("同じ失敗の 2 回目", self.failed_call(tid="f2", error=err))

    def test_blocked_twice_by_the_same_hook_for_the_same_reason(self):
        why = "PreToolUse:mcp__github__merge_pull_request hook error: [command hook 1]: 確認が無い。❔ を出して返事を待つ"
        merge = "mcp__github__merge_pull_request"
        t = self.transcript([user("マージして"), call(merge, {"pullNumber": 3}, "m1"), result("m1", why, True),
                             call(merge, {"pullNumber": 3, "merge_method": "squash"}, "m2"), result("m2", why, True)])
        msg = self.post("git status", "clean", tid="ok1", transcript=t)
        self.assertIn("同じ理由で止められた 2 回目", msg)
        self.assertIn("何が止めたか", msg)

    def test_failure_words_in_notifications_and_feedback_are_not_tool_results(self):
        # 通知が 1 つの文に 3 件並び、どれも失敗の語を持つ。差し戻しの文も user の行。どちらもツールの結果ではない
        t = self.transcript([user("様子を見て"), notices(3), feedback("FAILED\nFAILED\nFAILED"), notices(3),
                             call("Bash", {"command": "git status"}, "ok1"), result("ok1", "clean")])
        self.assertEqual(self.post("git status", "clean", tid="ok1", transcript=t), "")


class Memory(Base):
    def write(self, path, content, transcript=None, original=None, name="Write", session="s1", **env):
        p = {"session_id": session, "hook_event_name": "PostToolUse", "tool_name": name,
             "tool_input": {"file_path": str(self.root / path), "content": content} if name == "Write" else
                           {"file_path": str(self.root / path), "old_string": "", "new_string": content},
             "tool_response": {"filePath": str(self.root / path), "type": "update" if original else "create", "originalFile": original}}
        if transcript:
            p["transcript_path"] = transcript
        return self.context(self.run_hook("memory_nudge.py", p, **env))

    def test_new_outside_import_without_lookup(self):
        msg = self.write("src/app.py", "import os\nimport requests\n")
        self.assertIn("記憶で書いている（import requests）", msg)
        self.assertIn("一次情報", msg)
        self.assertEqual(self.write("src/other.py", "import requests\n"), "")  # 同じ依存には 1 回だけ

    def test_standard_local_and_declared_modules_are_not_new_dependencies(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "helper.py").write_text("X = 1\n", encoding="utf-8")
        (self.root / "requirements.txt").write_text("PyYAML==6.0\nrequests>=2\n", encoding="utf-8")
        self.assertEqual(self.write("src/app.py", "import json\nfrom pathlib import Path\nimport helper\nimport _state\nfrom . import x\nimport requests\n"), "")

    def test_each_kind_of_dependency(self):
        cases = [(".github/workflows/ci.yml", "    - uses: actions/checkout@v4\n", "uses: actions/checkout@v4"),
                 ("src/llm.py", 'MODEL = "claude-opus-4-1"\n', "モデルの ID claude-opus-4-1"),
                 ("scripts/merge.sh", "gh pr merge 12 --squash\n", "gh pr merge --squash"),
                 ("requirements.txt", "ruff==0.6.9\n", "ruff の版 0.6.9"),
                 ("package.json", '{"version": "1.0.0", "dependencies": {"react": "^19.0.0"}}\n', "react の版 ^19.0.0"),
                 ("docs/SOURCES.md", "| [x](https://github.com/example-owner/example-skill) | MIT |\n", "github.com/example-owner/example-skill"),
                 ("web/app.ts", 'import { z } from "zod";\nimport fs from "node:fs";\nimport x from "./x";\n', "import zod")]
        for i, (path, content, want) in enumerate(cases):
            with self.subTest(path=path):
                msg = self.write(path, content, session=f"k{i}")
                self.assertIn(want, msg)
                self.assertNotIn("node:fs", msg)

    def test_edit_only_counts_what_was_not_in_the_file_before(self):
        before = "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n"
        self.assertEqual(self.write("ci.yml", "      - uses: actions/checkout@v4\n", original=before, name="Edit"), "")
        msg = self.write("ci.yml", "      - uses: actions/setup-python@v5\n", original=before, name="Edit")
        self.assertIn("actions/setup-python@v5", msg)

    def test_tests_and_knowledge_notes_are_skipped(self):
        for path in ("tests/test_app.py", "src/test_app.py", "web/app.test.ts", "docs/knowledge/tech/2026-01-01-x.md", "package-lock.json"):
            with self.subTest(path=path):
                self.assertEqual(self.write(path, 'import requests\nMODEL = "claude-opus-4-1"\n"react": "^19.0.0"\n'), "")

    def test_lookup_within_three_turns_keeps_quiet_and_marks_it_known(self):
        for i, (name, inp) in enumerate([("WebFetch", {"url": "https://example.org"}), ("mcp__Context7__query-docs", {}), ("Bash", {"command": "pip show requests"}),
                                         ("Bash", {"command": "gh pr merge --help"}), ("Read", {"file_path": "/repo/package-lock.json"})]):
            with self.subTest(name=name, inp=inp):
                t = self.transcript([user("調べて"), call(name, inp, "t1"), result("t1"), user("書いて"), user("続けて")])
                self.assertEqual(self.write("src/app.py", "import requests\n", transcript=t, session=f"l{i}"), "")
                self.assertEqual(self.write("src/b.py", "import requests\n", session=f"l{i}"), "")  # 確かめた依存は、あとでも知らせない

    def test_lookup_four_turns_ago_or_a_plain_read_does_not_count(self):
        t = self.transcript([user("調べて"), call("WebFetch", {"url": "https://example.org"}, "t1"), result("t1"), user("1"), user("2"), user("3")])
        self.assertIn("import requests", self.write("src/app.py", "import requests\n", transcript=t, session="a"))
        t = self.transcript([user("書いて"), call("Read", {"file_path": "/repo/src/app.py"}, "t1"), result("t1"), call("Bash", {"command": "git status"}, "t2"), result("t2")])
        self.assertIn("import requests", self.write("src/app.py", "import requests\n", transcript=t, session="b"))

    def test_notifications_and_feedback_lines_are_not_turns(self):
        # 調べたあとに、通知（1 つの文に複数）と差し戻しが 5 行続く。人の発言ではないので、まだ同じターン
        lines = [user("調べて書いて"), call("WebSearch", {"query": "x"}, "t1"), result("t1"), notices(3), feedback("1 行足す"), notices(2), feedback("1 行足す"), notices(1)]
        self.assertEqual(self.write("src/app.py", "import requests\n", transcript=self.transcript(lines)), "")

    def test_off_cap_and_other_tools(self):
        self.assertEqual(self.write("src/app.py", "import requests\n", COMMON_SENSE_OFF="1"), "")
        self.assertEqual(self.write("src/app.py", "import requests\n", COMMON_SENSE_MAX_NUDGE="0", session="c"), "")
        out = self.run_hook("memory_nudge.py", {"session_id": "s9", "tool_name": "Read", "tool_input": {"file_path": "a.py"}, "tool_response": {"ok": True}})
        self.assertEqual(out.strip(), "")


class Registered(unittest.TestCase):
    """配られるのは、ひな形の登録。配った先の settings.json にはそのリポジトリ自身の登録があるので、ひな形だけを見る。"""

    WANT = {"SessionStart": ["today.py"], "UserPromptSubmit": ["today.py"], "Stop": ["guess_gate.py", "followup_gate.py"],
            "PostToolUse": ["odd_nudge.py", "memory_nudge.py"]}

    def test_hooks_are_registered_in_the_template_through_the_launcher(self):
        if not TEMPLATE.is_file():
            self.skipTest("ひな形が無い")
        hooks = json.loads(TEMPLATE.read_text(encoding="utf-8")).get("hooks", {})
        for event, names in self.WANT.items():
            cmds = [h.get("command", "") for e in hooks.get(event, []) for h in e.get("hooks", [])]
            for n in names:
                want = f'"${{CLAUDE_PROJECT_DIR:-.}}/.claude/hooks/run.sh" .claude/skills/common-sense/hooks/{n}'
                self.assertIn(want, cmds, f"{event} に {n} の登録が無い")

    def test_hook_files_exist(self):
        for names in self.WANT.values():
            for n in names:
                self.assertTrue((HOOKS / n).is_file(), n)


if __name__ == "__main__":
    unittest.main()
