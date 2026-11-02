#!/usr/bin/env python3
"""requests.py を、一時的なリポジトリで確かめる。"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from requests import FILE, add, collect, mark, open_requests  # noqa: E402


class Requests(unittest.TestCase):
    def test_add_mark_collect(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = root / "AppA"
            repo.mkdir()
            (repo / ".git").mkdir()
            ciel = root / "Ciel"
            ciel.mkdir()
            p = add(repo, "画面の手数を数えるスクリプト", {"what": "起動から N 手を数える", "scene": "ui-design の手順 1", "now": "手で数えている"})
            self.assertEqual(p, repo / FILE)
            s = p.read_text(encoding="utf-8")
            self.assertIn("# スキルの要請", s)
            self.assertIn("- 状態: 未対応", s)
            self.assertIn("- 例: （無し）", s)
            add(repo, "二つ目", {"what": "x", "scene": "y", "now": "z"})
            rows = collect(root, ciel, remote=False)
            self.assertEqual([r["title"] for r in rows], ["画面の手数を数えるスクリプト", "二つ目"])
            self.assertEqual(rows[0]["source"], "AppA")
            self.assertTrue(mark(repo, "二つ目", "対応済み（Ciel #99）"))
            self.assertFalse(mark(repo, "無い題", "見送り"))
            rows = collect(root, ciel, remote=False)
            self.assertEqual([r["title"] for r in rows], ["画面の手数を数えるスクリプト"])
            self.assertTrue(mark(repo, "画面の手数", "対応中（Ciel #100）"))
            self.assertEqual(collect(root, ciel, remote=False)[0]["status"], "対応中（Ciel #100）")
            (ciel / "docs" / "requests").mkdir(parents=True)
            (ciel / "docs" / "requests" / "2026-11-02-x.md").write_text("## 2026-11-02 Ciel 側の要請\n- 何が欲しいか: a\n- 場面: b\n- 今どう困るか: c\n- 状態: 未対応\n", encoding="utf-8")
            self.assertEqual(len(collect(root, ciel, remote=False)), 2)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, "-I", str(HERE / "requests.py"), "--add", "--title", "t", "--what", "w", "--scene", "s", "--now", "n", "--repo", d],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("書いた", r.stdout)
            r = subprocess.run([sys.executable, "-I", str(HERE / "requests.py"), "--add", "--title", "t", "--repo", d], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(open_requests("x", "## 2026-01-01 a\n- 状態: 見送り（要らない）\n"), [])


if __name__ == "__main__":
    unittest.main()
