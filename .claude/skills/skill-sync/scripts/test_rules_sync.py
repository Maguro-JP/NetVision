#!/usr/bin/env python3
"""rules_sync.py を、作った CLAUDE.md で確かめる。"""

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from rules_sync import apply, key, plan, token  # noqa: E402

CIEL = "# Ciel\n\n## ユーザーの決めたこと\n\n前書き。\n\n- 時刻は日本時間で書く\n- 脆弱性は週に 1 回見る（vuln-watch）。毎日は見ない\n- 新しい決まり。中身\n\n## 応答の言語\n\n- 日本語で書く\n"


class RulesSync(unittest.TestCase):
    def test_key(self):
        self.assertEqual(key("- 脆弱性は週に 1 回見る（vuln-watch）。毎日は見ない"), "脆弱性は週に 1 回見る")
        self.assertEqual(key("- 時刻は日本時間で書く"), "時刻は日本時間で書く")
        self.assertEqual(token("- 脆弱性は毎日見る（vuln-watch）。全部"), "vuln-watch")
        self.assertIsNone(token("- 時刻は日本時間で書く"))

    def test_plan_and_apply_with_following_section(self):
        target = "# App\n\n## ユーザーの決めたこと\n\n- 時刻は日本時間で書く\n- 脆弱性は毎日見る（vuln-watch）。全部\n- このアプリだけの決まり\n\n## 次の節\n\n本文\n"
        add, replace = plan(CIEL, target)
        self.assertEqual([key(l) for l in add], ["新しい決まり"])
        self.assertEqual([(key(o), key(n)) for o, n in replace], [("脆弱性は毎日見る", "脆弱性は週に 1 回見る")])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "CLAUDE.md"
            p.write_text(target, encoding="utf-8")
            s = apply(p, add, replace)
            self.assertIn("- 脆弱性は週に 1 回見る（vuln-watch）。毎日は見ない\n- このアプリだけの決まり\n- 新しい決まり。中身\n\n## 次の節\n", s)
            self.assertNotIn("\n\n\n", s)
            self.assertEqual(plan(CIEL, s), ([], []))

    def test_apply_when_section_is_last(self):
        target = "# App\n\n## ユーザーの決めたこと\n\n- 時刻は日本時間で書く\n"
        add, replace = plan(CIEL, target)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "CLAUDE.md"
            p.write_text(target, encoding="utf-8")
            s = apply(p, add, replace)
            self.assertTrue(s.endswith("- 新しい決まり。中身\n"))  # 末尾に余分な空行を作らない（precheck の diff --check で落ちる）
            self.assertEqual(plan(CIEL, s), ([], []))

    def test_no_section(self):
        self.assertEqual(plan(CIEL, "# App\n\n本文だけ\n"), (None, None))


if __name__ == "__main__":
    unittest.main()
