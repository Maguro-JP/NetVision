#!/usr/bin/env python3
"""observe.py を、一時的に作った 2 つのリポジトリで確かめる。"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from observe import heads, observe  # noqa: E402


def g(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout


def make_repo(root: Path, name: str, lessons: str, pending: str, subjects):
    bare = root / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    repo = root / name
    subprocess.run(["git", "clone", "-q", str(bare), str(repo)], check=True, capture_output=True)
    g(repo, "config", "user.email", "t@example.invalid"), g(repo, "config", "user.name", "t")
    (repo / ".claude/skills/lessons").mkdir(parents=True)
    (repo / ".claude/skills/lessons/LESSONS.md").write_text(lessons, encoding="utf-8")
    (repo / "docs/reports").mkdir(parents=True)
    (repo / "docs/reports/pending.md").write_text(pending, encoding="utf-8")
    for s in subjects:
        g(repo, "add", "-A"), g(repo, "commit", "-q", "--allow-empty", "-m", s)
    g(repo, "push", "-q", "origin", "main")
    g(repo, "remote", "set-head", "origin", "main")
    return repo


class Observe(unittest.TestCase):
    def test_counts_and_extra_lessons(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = make_repo(root, "a", "# 失敗の記録\n\n## 2026-10-01 共通の題\n場面: x\n\n## 2026-10-02 この repo だけの題\n場面: y\n",
                             "# 判断待ち\n\n## 2026-10-01 色\n- 決めてほしいこと: 赤か青か\n\n## 2026-10-02 形\n- 決めてほしいこと: 丸か四角か\n- 答え（2026-10-02）: 丸\n",
                             ["DEV 20261025: a (#3)", "SYNC: skills", "FB 身体: 回1 (#4)", "plain"])
            o = observe(repo, "2000-01-01", {"2026-10-01 共通の題"})
            self.assertEqual((o["commits"], o["merges"], o["DEV"], o["SYNC"], o["FB"]), (4, 2, 1, 1, 1))
            self.assertEqual(o["lessons_extra"], ["2026-10-02 この repo だけの題"])
            self.assertEqual(o["pending_open"], 1)
            self.assertEqual(heads("## a\n## b\n"), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
