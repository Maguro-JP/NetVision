#!/usr/bin/env python3
"""crash_check.py を、作ったリポジトリで確かめる。"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import crash_check  # noqa: E402


def make_repo(files: dict) -> Path:
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    for rel, text in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "-c", "user.email=t@users.noreply.github.com", "-c", "user.name=t", "commit", "-q", "-m", "init"], cwd=d, check=True)
    return d


class Crash(unittest.TestCase):
    def test_syntax_error(self):
        root = make_repo({"a.py": "def f(:\n    pass\n"})
        used, missing = [], []
        f = crash_check.syntax(root, ["a.py"], used, missing)
        self.assertEqual([x.id for x in f], ["C01"])
        self.assertEqual(f[0].sev, "重大")

    def test_clean(self):
        root = make_repo({"a.py": "def f():\n    return 1\n"})
        f, used, missing = crash_check.scan(root, ["a.py"], no_tests=True, no_smoke=True)
        self.assertEqual([x for x in f if x.sev == "重大"], [])

    def test_smoke_crash(self):
        root = make_repo({"main.py": "import argparse\nraise RuntimeError('boom')\n"})
        f = crash_check.smoke(root)
        self.assertEqual([x.id for x in f], ["C06"])
        self.assertEqual(f[0].sev, "重大")

    def test_smoke_missing_dependency_is_notice_and_only_filter(self):
        root = make_repo({"main.py": "import argparse\nimport nonexistent_module_xyz\n"})
        f = crash_check.smoke(root)
        self.assertEqual([(x.id, x.sev) for x in f], [("C07", "注意")])  # 依存が無い環境は、不具合と決めない
        self.assertEqual(crash_check.smoke(root, only=set()), [])  # 差分の検査では、変えていない入口を動かさない

    def test_smoke_ok_and_gui(self):
        root = make_repo({"main.py": "import argparse\np = argparse.ArgumentParser()\np.parse_args()\n",
                          "app.py": "import webview\nimport argparse\n"})
        f = crash_check.smoke(root)
        self.assertEqual([(x.id, x.sev) for x in f], [("C07", "注意")])

    def test_tests_fail(self):
        root = make_repo({"tests/test_x.py": "def test_x():\n    assert 1 == 2\n"})
        used, missing = [], []
        f = crash_check.tests(root, used, missing)
        if "pytest" in missing:
            self.skipTest("pytest が無い")
        self.assertEqual([x.id for x in f], ["C05"])

    def test_cli(self):
        root = make_repo({"a.py": "x = (\n"})
        r = subprocess.run([sys.executable, str(HERE / "crash_check.py"), str(root), "--no-tests", "--no-smoke"], capture_output=True, text=True,
                           env=dict(os.environ, CODE_CHECK_NO_TOOLS="1"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("C01", r.stdout)
        self.assertIn("バグやクラッシュ: 重大 1", r.stdout)


if __name__ == "__main__":
    unittest.main()
