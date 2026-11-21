#!/usr/bin/env python3
"""knowledge.py と 2 つのフックを、作ったリポジトリと会話の記録で確かめる。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import knowledge  # noqa: E402

HOOKS = HERE.parent / "hooks"
FULL = """# 再試行の待ち方

- 日付: 2026-11-01
- 語: retry, backoff

## 情報
- 待ち時間は倍々に延ばし、ばらつきを足す（https://example.com/backoff 、2026-11-01）

## 知識
同じ先に何度も当てるときは、待ち時間を倍にして上限を決める。一斉に再試行すると相手が落ちる。

## アルゴリズム
1. 待ち時間を 1 秒から始める
2. 失敗するたびに倍にし、上限 30 秒で止める

## 技術
- 場所: `lib/retry.py` の `backoff`
- テスト: `lib/test_retry.py`

## 実装
- `sync.py` の push に当てた。出力: `retry 1 after 1.0s`
"""


def repo(files: dict) -> Path:
    d = Path(tempfile.mkdtemp())
    for rel, text in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


def run(root: Path, *args):
    r = subprocess.run([sys.executable, str(HERE / "knowledge.py"), "--root", str(root), *args], capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr


class Ladder(unittest.TestCase):
    def test_rung_counts_only_filled_sections_in_order(self):
        sec = knowledge.sections(FULL)
        self.assertEqual(knowledge.rung_of(sec), 5)
        only_info = FULL.split("## 知識")[0] + "## 知識\n<!-- まだ -->\n\n## アルゴリズム\n\n## 技術\n\n## 実装\n"
        self.assertEqual(knowledge.rung_of(knowledge.sections(only_info)), 1)
        skipped = FULL.replace("1. 待ち時間を 1 秒から始める\n2. 失敗するたびに倍にし、上限 30 秒で止める\n", "")
        self.assertEqual(knowledge.rung_of(knowledge.sections(skipped)), 2)  # アルゴリズムが空なら、技術が書いてあっても知識まで

    def test_add_then_check_and_find(self):
        root = repo({"README.md": "x\n"})
        code, out = run(root, "--add", "Retry backoff", "--words", "retry,backoff")
        self.assertEqual(code, 0)
        self.assertIn("作った", out)
        files = list((root / "docs/knowledge/tech").glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].name.endswith("-retry-backoff.md"))
        code, out = run(root, "--check")
        self.assertEqual(code, 0)
        self.assertIn("情報のまま 1", out)
        code, out = run(root, "--find", "backoff")
        self.assertIn("retry-backoff.md", out)
        code, out = run(root, "--find", "kubernetes")
        self.assertIn("無し", out)

    def test_japanese_title_gets_stable_name(self):
        self.assertEqual(knowledge.slug_of("通知の読み方"), knowledge.slug_of("通知の読み方"))
        self.assertTrue(knowledge.slug_of("通知の読み方").startswith("note-"))

    def test_full_entry_and_missing_paths(self):
        root = repo({"docs/knowledge/tech/2026-11-01-retry.md": FULL, "lib/retry.py": "x\n", "lib/test_retry.py": "x\n"})
        code, out = run(root, "--check", "--strict")
        self.assertEqual(code, 0, out)
        self.assertIn("実装 1", out)
        (root / "lib/retry.py").unlink()
        code, out = run(root, "--check", "--strict")
        self.assertEqual(code, 1)
        self.assertIn("技術の場所が無い: lib/retry.py", out)
        code, out = run(root, "--diff", "origin/main")
        self.assertEqual(code, 0)  # precheck からは落とさない

    def test_notes(self):
        no_source = FULL.replace("（https://example.com/backoff 、2026-11-01）", "")
        no_test = FULL.replace("- テスト: `lib/test_retry.py`\n", "")
        root = repo({"docs/knowledge/tech/2026-11-01-a.md": no_source, "docs/knowledge/tech/2026-11-01-b.md": no_test,
                     "lib/retry.py": "x\n", "lib/test_retry.py": "x\n"})
        code, out = run(root, "--check")
        self.assertIn("出所", out)
        self.assertIn("テストが書いていない", out)

    def test_word_containing_test_is_not_a_test_line(self):
        got = knowledge.paths_in(["- 場所: `lib/state.py` の `read` と `latest`", "- テスト: `lib/check_state.py`", "- 場所: `lib/test_x.py`"])
        self.assertEqual(got, [("lib/state.py", False), ("lib/check_state.py", True), ("lib/test_x.py", True)])

    def test_stale_info_only(self):
        old = "# 古い\n\n## 情報\n- 何か（https://example.com 、2020-01-01）\n\n## 知識\n\n## アルゴリズム\n\n## 技術\n\n## 実装\n"
        root = repo({"docs/knowledge/tech/2020-01-01-old.md": old})
        code, out = run(root, "--check")
        self.assertIn("情報のまま", out)
        self.assertIn("日。知識にするか", out)

    def test_index(self):
        root = repo({"docs/knowledge/tech/2026-11-01-retry.md": FULL, "lib/retry.py": "x\n", "lib/test_retry.py": "x\n"})
        code, out = run(root, "--index")
        self.assertEqual(code, 0)
        idx = (root / "docs/knowledge/tech/README.md").read_text(encoding="utf-8")
        self.assertIn("[再試行の待ち方](2026-11-01-retry.md)", idx)
        self.assertIn("`lib/retry.py`", idx)
        code, out = run(root, "--check")
        self.assertIn("全 1 件", out)  # 一覧そのものは数えない


def user(text):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def assistant(text="", tools=()):
    c = [{"type": "text", "text": text}] if text else []
    for name, inp in tools:
        c.append({"type": "tool_use", "name": name, "input": inp})
    return {"type": "assistant", "message": {"role": "assistant", "content": c}}


def gate(lines, active=False):
    d = tempfile.mkdtemp()
    p = Path(d) / "t.jsonl"
    p.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(HOOKS / "research_gate.py")], input=json.dumps({"transcript_path": str(p), "stop_hook_active": active}),
                       capture_output=True, text=True, timeout=60, env=dict(os.environ, RESEARCH_TO_TECH_OFF="0"))
    return r.returncode, r.stderr


SEARCH = ("WebSearch", {"query": "exponential backoff jitter"})
EDIT = ("Edit", {"file_path": "/repo/lib/retry.py", "old_string": "a", "new_string": "b"})
NOTE = ("Write", {"file_path": "/repo/docs/knowledge/tech/2026-11-01-retry.md", "content": "x"})


class Gate(unittest.TestCase):
    def test_research_then_code_without_note_is_blocked(self):
        code, out = gate([user("再試行を直して"), assistant("", [SEARCH]), assistant("★ 直しました", [EDIT])])
        self.assertEqual(code, 2)
        self.assertIn("知識に残していない", out)
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_research_in_previous_turn_counts(self):
        code, _ = gate([user("調べて"), assistant("↩ 調べました", [SEARCH]), user("では実装して"), assistant("★", [EDIT])])
        self.assertEqual(code, 2)

    def test_note_written_passes(self):
        code, out = gate([user("直して"), assistant("", [SEARCH]), assistant("", [NOTE]), assistant("★", [EDIT])])
        self.assertEqual(code, 0, out)
        code, out = gate([user("直して"), assistant("", [SEARCH]), assistant("", [("Bash", {"command": "python3 knowledge.py --add 'retry'"})]),
                          assistant("★", [("Bash", {"command": "git commit -m x"})])])
        self.assertEqual(code, 0, out)

    def test_research_after_note_needs_another_note(self):
        code, _ = gate([user("直して"), assistant("", [NOTE]), assistant("", [SEARCH]), assistant("★", [EDIT])])
        self.assertEqual(code, 2)

    def test_question_only_is_not_blocked(self):
        code, out = gate([user("これは何?"), assistant("↩ こうです", [SEARCH])])
        self.assertEqual(code, 0, out)

    def test_escape_line(self):
        code, out = gate([user("直して"), assistant("", [SEARCH]), assistant("★ 直しました\n❕ 調べ物: 知識にしない（版の番号を見ただけ）", [EDIT])])
        self.assertEqual(code, 0, out)

    def test_old_research_is_outside_window(self):
        lines = [user("調べて"), assistant("↩", [SEARCH]), user("a"), assistant("↩ a"), user("b"), assistant("↩ b"), user("c"), assistant("★", [EDIT])]
        code, out = gate(lines)
        self.assertEqual(code, 0, out)

    def test_hook_feedback_is_same_turn(self):
        lines = [user("直して"), assistant("", [SEARCH]), assistant("★", [EDIT]), user("Stop hook feedback: x"), assistant("❕ 足しました")]
        code, _ = gate(lines)
        self.assertEqual(code, 2)
        code, _ = gate(lines, active=True)
        self.assertEqual(code, 0)


class Nudge(unittest.TestCase):
    def call(self, tool, state):
        r = subprocess.run([sys.executable, str(HOOKS / "research_nudge.py")], input=json.dumps({"tool_name": tool, "session_id": "s1"}),
                           capture_output=True, text=True, timeout=30, env=dict(os.environ, RESEARCH_TO_TECH_STATE=state, RESEARCH_TO_TECH_OFF="0"))
        return r.stdout.strip()

    def test_once_per_window_and_only_research_tools(self):
        state = tempfile.mkdtemp()
        first = self.call("WebSearch", state)
        self.assertIn("knowledge.py --find", first)
        self.assertLessEqual(len(json.loads(first)["hookSpecificOutput"]["additionalContext"]), 220)
        self.assertEqual(self.call("mcp__Context7__query-docs", state), "")  # 15 分以内は出さない
        self.assertEqual(self.call("Bash", tempfile.mkdtemp()), "")


if __name__ == "__main__":
    unittest.main()
