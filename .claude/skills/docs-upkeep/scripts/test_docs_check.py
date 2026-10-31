#!/usr/bin/env python3
"""docs_check.py を、作ったリポジトリで確かめる。"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECK = Path(__file__).resolve().parent / "docs_check.py"


def repo(tmp: Path, readme: str, usage: str = "", extra=None):
    (tmp / "docs").mkdir(exist_ok=True)
    for s in ("alpha", "beta"):
        (tmp / ".claude" / "skills" / s).mkdir(parents=True, exist_ok=True)
        (tmp / ".claude" / "skills" / s / "SKILL.md").write_text(f"---\nname: {s}\n---\n# {s}\n", encoding="utf-8")
    (tmp / "README.md").write_text(readme, encoding="utf-8")
    (tmp / "docs" / "USAGE.md").write_text(usage or "# 使い方\n", encoding="utf-8")
    for name, body in (extra or {}).items():
        p = tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def run(tmp: Path):
    r = subprocess.run([sys.executable, "-I", str(CHECK), str(tmp)], capture_output=True, text=True)
    return r.returncode, r.stdout


GOOD = "# X\n\n| スキル | 用途 |\n|---|---|\n| [`alpha`](.claude/skills/alpha/) | a |\n| [`beta`](.claude/skills/beta/) | b |\n\n[使い方](docs/USAGE.md)\n"


class DocsCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_repo_passes(self):
        repo(self.dir, GOOD)
        code, out = run(self.dir)
        self.assertEqual(code, 0, out)
        self.assertIn("誤り 0", out)

    def test_broken_link_and_missing_path(self):
        repo(self.dir, GOOD + "\n[無い](docs/NOPE.md) と `scripts/nope.sh` を見る\n")
        code, out = run(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("壊れたリンク: docs/NOPE.md", out)
        self.assertIn("無いファイルへの言及: scripts/nope.sh", out)

    def test_skill_table_mismatch(self):
        repo(self.dir, "# X\n\n| スキル | 用途 |\n|---|---|\n| [`alpha`](.claude/skills/alpha/) | a |\n| `gamma` | g |\n\n[使い方](docs/USAGE.md)\n")
        code, out = run(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("一覧に無いスキル: beta", out)
        self.assertIn("あるが無いスキル: gamma", out)

    def test_orphan_doc(self):
        repo(self.dir, GOOD, extra={"docs/LOST.md": "# 迷子\n"})
        code, out = run(self.dir)
        self.assertEqual(code, 1)
        self.assertIn("docs/LOST.md は README からも USAGE からも辿れない", out)

    def test_notes_do_not_fail(self):
        repo(self.dir, GOOD + "\nテストは 3 本（7 件）ある。**強調**。あとで直す予定。\n")
        code, out = run(self.dir)
        self.assertEqual(code, 0)
        self.assertIn("古くなる数", out)
        self.assertIn("強調の記号", out)
        self.assertIn("予定の語", out)


if __name__ == "__main__":
    unittest.main()
