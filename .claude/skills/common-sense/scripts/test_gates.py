#!/usr/bin/env python3
"""guess_gate.py と followup_gate.py（Stop）を、作った会話の記録で確かめる。

記録の形は、実際の記録に合わせる。人の発言、ツールの結果、Stop フックの差し戻し、裏の処理の通知は、どれも type が user の行。
通知が 1 つの文に複数並ぶ形と、差し戻しが user の行として入る形で、偽の差し戻しをしないことを見る。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import followup_gate  # noqa: E402
import guess_gate  # noqa: E402

MERGE = "mcp__github__merge_pull_request"
MERGED = json.dumps({"merged": True, "message": "Pull Request successfully merged", "sha": "0123abc"})


def user(text, origin=True):
    e = {"type": "user", "message": {"role": "user", "content": text}}
    if origin:
        e["origin"] = {"kind": "human"}
    return e


def result(tool_use_id, text="ok", is_error=False):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": text, "is_error": is_error}]}}


def call(name, inp=None, tid="t1", text=""):
    c = ([{"type": "text", "text": text}] if text else []) + [{"type": "tool_use", "id": tid, "name": name, "input": inp or {}}]
    return {"type": "assistant", "message": {"role": "assistant", "content": c}}


def say(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def feedback(text, meta=True):
    """Stop フックの差し戻し。type が user の行として入る。"""
    e = {"type": "user", "message": {"role": "user", "content": "Stop hook feedback:\n" + text}}
    if meta:
        e["isMeta"] = True
    return e


def notices(n, origin=True, body="completed"):
    """裏の処理の通知。1 つの文に n 件並ぶ。"""
    text = "\n".join(f"<task-notification>\n<task-id>b{i}</task-id>\n<status>{body}</status>\n<summary>たぶん {i} 件のはずです。マージしました</summary>\n</task-notification>" for i in range(n))
    e = {"type": "user", "message": {"role": "user", "content": text}}
    if origin:
        e["origin"] = {"kind": "task-notification"}
    return e


def gate(hook, lines, active=False, last=None, **env):
    d = tempfile.mkdtemp()
    p = Path(d) / "t.jsonl"
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
    payload = {"session_id": "s", "transcript_path": str(p), "stop_hook_active": active, "hook_event_name": "Stop"}
    if last is not None:
        payload["last_assistant_message"] = last
    e = dict(os.environ, **env)
    if "COMMON_SENSE_OFF" not in env:
        e.pop("COMMON_SENSE_OFF", None)
    r = subprocess.run([sys.executable, "-I", str(HOOKS / hook)], input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True, timeout=60, env=e)
    return r.returncode, r.stderr


def guess(lines, **kw):
    return gate("guess_gate.py", lines, **kw)


def follow(lines, **kw):
    return gate("followup_gate.py", lines, **kw)


class Words(unittest.TestCase):
    def test_guess_words_are_found_in_assertions_only(self):
        for text in ("たぶん通ります。", "テストは通るはずです。", "原因は設定だと思います。", "そうなるだろうと思います。", "確か 3 件でした。",
                     "記憶では v4 が最新です。", "原因は A と思われます。", "おそらく権限です。"):
            with self.subTest(text=text):
                self.assertTrue(guess_gate.guesses(text, ""))
        for text in ("通るはずがありません。", "3 ファイルのはずが 100 ファイルでした。", "この形で進めたいと思います。", "直そうと思います。",
                     "確かめました。確かに通りました。", "`たぶん` という語を見ます。", "「たぶん」と書かない。", "❕ 疑い: たぶん設定が原因。",
                     "❕ おかしい: 3 件のはずです。実際は 100 件。", "↩ 読み取り: たぶん動くはずです、を受けて直します。", "手はずです。",
                     "```\necho たぶん\n```", "> おそらく、と書いてあった"):
            with self.subTest(text=text):
                self.assertEqual(guess_gate.guesses(text, ""), [])

    def test_words_copied_from_the_user_are_not_counted(self):
        self.assertEqual(guess_gate.guesses("たぶん壊れている、とのことなので見ます。", "たぶん壊れているから見て"), [])
        self.assertTrue(guess_gate.guesses("たぶん設定の誤りです。", "たぶん壊れているから見て"))


class GuessGate(unittest.TestCase):
    def test_guess_without_any_lookup_is_blocked_with_one_line(self):
        code, out = guess([user("これは動く?"), say("★ たぶん動きます。")])
        self.assertEqual(code, 2)
        self.assertIn("たぶん", out)
        self.assertIn("❕ 疑い", out)
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_any_lookup_in_this_turn_passes(self):
        for name, inp in (("Read", {"file_path": "a.py"}), ("Grep", {"pattern": "x"}), ("Glob", {"pattern": "*.py"}), ("WebSearch", {"query": "x"}),
                          ("WebFetch", {"url": "https://example.org"}), ("mcp__Context7__query-docs", {}), ("mcp__Exa__web_search_exa", {}),
                          ("Bash", {"command": "gh pr merge --help"}), ("Bash", {"command": "ruff --version"}), ("Bash", {"command": "pip show requests"}),
                          ("Bash", {"command": "npm view react version"}), ("Bash", {"command": "cd x && git log --oneline | head -3"}),
                          ("mcp__github__pull_request_read", {"method": "get"})):
            with self.subTest(name=name, inp=inp):
                code, out = guess([user("これは動く?"), call(name, inp), result("t1"), say("★ 動くはずです。")])
                self.assertEqual(code, 0, out)

    def test_commands_that_only_change_things_are_not_a_lookup(self):
        for cmd in ("git add -A && git commit -m x", "git push origin HEAD", "mkdir -p out", "rm -f a.txt"):
            with self.subTest(cmd=cmd):
                code, _ = guess([user("push して"), call("Bash", {"command": cmd}), result("t1"), say("★ push しました。通るはずです。")])
                self.assertEqual(code, 2)

    def test_lookup_in_an_earlier_turn_does_not_count(self):
        code, _ = guess([user("読んで"), call("Read", {"file_path": "a.py"}), result("t1"), say("★ 読みました。"), user("動く?"), say("★ おそらく動きます。")])
        self.assertEqual(code, 2)

    def test_doubt_line_with_a_reason_passes_and_without_a_reason_does_not(self):
        code, out = guess([user("動く?"), say("★ たぶん動きます。\n❕ 疑い: 配った先の環境は、権限が無く調べられない。")])
        self.assertEqual(code, 0, out)
        code, out = guess([user("動く?"), say("★ 設定は直してあります。\n❕ 疑い: たぶん古い版では動かない。")])  # 推測の語が疑いの行の中だけ
        self.assertEqual(code, 0, out)
        code, _ = guess([user("動く?"), say("★ たぶん動きます。\n❕ 疑い: 動かしていない。")])  # 調べられない理由が無い
        self.assertEqual(code, 2)

    def test_asked_for_a_guess_passes(self):
        code, out = guess([user("原因は何だと思う?"), say("★ 設定の誤りだと思います。")])
        self.assertEqual(code, 0, out)
        code, _ = guess([user("推測で書くな。原因は?"), say("★ 設定の誤りだと思います。")])  # 推測を禁じた一言は、聞いたことにならない
        self.assertEqual(code, 2)

    def test_no_guess_no_block(self):
        code, out = guess([user("直して"), call("Edit", {"file_path": "a.py"}), result("t1"), say("★ 直しました。\n❕ 疑い: 動かしていない。")])
        self.assertEqual(code, 0, out)

    def test_active_flag_and_off(self):
        lines = [user("動く?"), say("★ たぶん動きます。")]
        self.assertEqual(guess(lines, active=True)[0], 0)
        self.assertEqual(guess(lines, COMMON_SENSE_OFF="1")[0], 0)
        self.assertEqual(guess(lines)[0], 2)

    def test_last_assistant_message_is_read_before_the_transcript(self):
        lines = [user("動く?"), say("★ 見ています。")]
        self.assertEqual(guess(lines, last="★ たぶん動きます。")[0], 2)  # 記録が遅れていても、入力の最後の返答を見る
        lines = [user("動く?"), say("★ たぶん動きます。")]
        code, out = guess(lines, last="★ `a.py` の 3 行目で止まります。")
        self.assertEqual(code, 0, out)

    def test_several_notifications_in_one_line_do_not_split_the_turn(self):
        for origin in (True, False):
            with self.subTest(origin=origin):
                lines = [user("動く?", origin), call("Read", {"file_path": "a.py"}), result("t1"), notices(3, origin), say("★ この形なら、おそらく動きます。")]
                code, out = guess(lines)
                self.assertEqual(code, 0, out)  # 通知を人の発言と数えると、Read が前のターンに回って偽の差し戻しになる

    def test_meta_and_summary_lines_do_not_split_the_turn(self):
        # 人の発言の形をしていても、isMeta（ほかのセッションからの連絡など）と要約は、人の発言ではない
        for mark in ("isMeta", "isCompactSummary"):
            with self.subTest(mark=mark):
                extra = {"type": "user", mark: True, "message": {"role": "user", "content": "別のセッションからの連絡です"}}
                lines = [user("動く?"), call("Read", {"file_path": "a.py"}), result("t1"), extra, say("★ この形なら、おそらく動きます。")]
                code, out = guess(lines)
                self.assertEqual(code, 0, out)

    def test_stop_feedback_line_does_not_split_the_turn(self):
        for meta in (True, False):
            with self.subTest(meta=meta):
                lines = [user("動く?", meta), call("Grep", {"pattern": "x"}), result("t1"), say("★ 動きます。"),
                         feedback("疑いの 1 行を足す", meta), say("★ 古い版でも、この形で動くはずです。")]
                code, out = guess(lines)
                self.assertEqual(code, 0, out)

    def test_words_inside_tool_results_and_notifications_are_not_the_reply(self):
        lines = [user("動かして"), call("Bash", {"command": "mkdir -p out"}), result("t1", "たぶん失敗するはずです"), notices(2), say("★ 作りました。")]
        code, out = guess(lines)
        self.assertEqual(code, 0, out)


class Table(unittest.TestCase):
    def test_rows_are_read_from_the_reference_table(self):
        rows = followup_gate.rows(followup_gate.TABLE.read_text(encoding="utf-8"))
        self.assertTrue(rows)
        row = next(r for r in rows if r["name"] == "マージ")
        for text in (MERGE + ' {"pullNumber": 3}', "gh pr merge 12 --squash", "cd repo && gh pr merge 12 --squash --delete-branch"):
            self.assertTrue(row["did"].search(text), text)
        for text in ("gh pr merge 12 --auto --squash", 'grep -n "gh pr merge" docs/USAGE.md', 'ToolSearch {"query": "select:mcp__github__merge_pull_request"}', "gh pr view 12"):
            self.assertFalse(row["did"].search(text), text)
        self.assertTrue(row["next"].search("python3 .claude/skills/skill-sync/scripts/sync.py ../app --apply"))
        self.assertFalse(row["next"].search("git pull origin main"))

    def test_broken_rows_are_skipped(self):
        text = "## 機械で見る組\n\n| 組 | したこと（正規表現） | 次の手順（正規表現） | 差し戻しの文 |\n|---|---|---|---|\n| 壊れ | `(` | `x` | 文 |\n| 良い | `a\\|b` | `c` | 文 |\n\n## 別の節\n| x | `y` | `z` | 文 |\n"
        rows = followup_gate.rows(text)
        self.assertEqual([r["name"] for r in rows], ["良い"])
        self.assertTrue(rows[0]["did"].search("b"))


class FollowupGate(unittest.TestCase):
    def test_merge_then_question_without_next_step_is_blocked_with_one_line(self):
        code, out = follow([user("マージして"), call(MERGE, {"pullNumber": 3}), result("t1", MERGED), say("★ マージしました #3\n❔ 配布: 配ってよいですか")])
        self.assertEqual(code, 2)
        self.assertIn("❕ 次", out)
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_next_step_started_or_next_line_passes(self):
        sync = call("Bash", {"command": "python3 .claude/skills/skill-sync/scripts/sync.py ../app > /tmp/s.log 2>&1"}, "t2")
        code, out = follow([user("マージして"), call(MERGE, {}), result("t1", MERGED), sync, result("t2"), say("★ マージしました\n❔ 配布: 10 本の PR を作ってよいですか")])
        self.assertEqual(code, 0, out)
        code, out = follow([user("マージして"), call(MERGE, {}), result("t1", MERGED), say("★ マージしました\n❕ 次: 後片付け（配る物は無い）\n❔ ブランチ: 残りの 1 本を消してよいですか")])
        self.assertEqual(code, 0, out)

    def test_cleanup_alone_is_not_the_next_step(self):
        pull = call("Bash", {"command": "git pull origin main"}, "t2")
        code, _ = follow([user("マージして"), call(MERGE, {}), result("t1", MERGED), pull, result("t2"), say("★ マージしました\n❔ 配布: 配ってよいですか")])
        self.assertEqual(code, 2)

    def test_without_a_question_it_passes(self):
        code, out = follow([user("マージして"), call(MERGE, {}), result("t1", MERGED), say("★ マージしました #3\n❕ 疑い: 配った先は見ていない。")])
        self.assertEqual(code, 0, out)

    def test_failed_blocked_or_unfinished_merge_is_not_counted(self):
        q = say("❔ マージ: #3 をマージしてよいですか")
        blocked = result("t1", "PreToolUse:mcp__github__merge_pull_request hook error: [command hook 1]: 確認が無い", is_error=True)
        for res in ([blocked], [result("t1", json.dumps({"merged": False, "message": "not mergeable"}))], []):
            with self.subTest(res=res):
                code, out = follow([user("マージして"), call(MERGE, {})] + res + [q])
                self.assertEqual(code, 0, out)

    def test_gh_pr_merge_in_bash(self):
        q = say("★ マージしました\n❔ 配布: 配ってよいですか")
        code, _ = follow([user("マージして"), call("Bash", {"command": "gh pr merge 12 --squash"}), result("t1", "✓ Squashed and merged pull request #12"), q])
        self.assertEqual(code, 2)
        for cmd in ("gh pr merge 12 --auto --squash", 'grep -n "gh pr merge" docs/USAGE.md'):
            with self.subTest(cmd=cmd):
                code, out = follow([user("見て"), call("Bash", {"command": cmd}), result("t1"), q])
                self.assertEqual(code, 0, out)

    def test_merge_in_an_earlier_turn_is_not_counted(self):
        code, out = follow([user("マージして"), call(MERGE, {}), result("t1", MERGED), say("★ マージしました"), user("次は?"), say("❔ 配布: 配ってよいですか")])
        self.assertEqual(code, 0, out)

    def test_notifications_that_mention_a_merge_are_not_a_merge(self):
        # 通知の文に「マージしました」があっても、マージの呼び出しではない。1 つの文に複数並ぶ形
        code, out = follow([user("様子を見て"), call("Read", {"file_path": "a.md"}), result("t1"), notices(3), say("❔ 配布: 配ってよいですか")])
        self.assertEqual(code, 0, out)
        # マージのあとに通知が並んでも、同じターンの続き。配布を始めていれば通る
        sync = call("Bash", {"command": "python3 .claude/skills/skill-sync/scripts/sync.py ../app"}, "t2")
        code, out = follow([user("マージして"), call(MERGE, {}), result("t1", MERGED), notices(2), sync, result("t2"), say("❔ 配布: PR を作ってよいですか")])
        self.assertEqual(code, 0, out)
        # 通知で配布が始まったことにはならない
        code, _ = follow([user("マージして"), call(MERGE, {}), result("t1", MERGED), notices(2, body="sync.py 配布"), say("❔ 配布: 配ってよいですか")])
        self.assertEqual(code, 2)

    def test_stop_feedback_line_is_part_of_the_same_turn(self):
        for meta in (True, False):
            with self.subTest(meta=meta):
                lines = [user("マージして", meta), call(MERGE, {}), result("t1", MERGED), say("★ マージしました #3"),
                         feedback("Actions の結果を 1 行書く", meta), say("❕ Actions: 通りました。")]
                code, out = follow(lines)
                self.assertEqual(code, 0, out)  # ❔ が無い
                lines = lines[:4] + [say("❔ 配布: 配ってよいですか"), feedback("疑いの 1 行を足す", meta), say("❕ 次: 配布の用意（sync.py）")]
                code, out = follow(lines)
                self.assertEqual(code, 0, out)  # 差し戻しのあとに足した 1 行で通る

    def test_last_assistant_message_decides_the_question(self):
        lines = [user("マージして"), call(MERGE, {}), result("t1", MERGED), say("★ マージしました")]
        self.assertEqual(follow(lines, last="★ マージしました\n❔ 配布: 配ってよいですか")[0], 2)
        code, out = follow(lines, last="★ マージしました\n❕ 次: 配布の用意（sync.py）")
        self.assertEqual(code, 0, out)

    def test_active_flag_and_off(self):
        lines = [user("マージして"), call(MERGE, {}), result("t1", MERGED), say("❔ 配布: 配ってよいですか")]
        self.assertEqual(follow(lines, active=True)[0], 0)
        self.assertEqual(follow(lines, COMMON_SENSE_OFF="1")[0], 0)
        self.assertEqual(follow(lines)[0], 2)


if __name__ == "__main__":
    unittest.main()
