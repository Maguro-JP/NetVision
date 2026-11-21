#!/usr/bin/env python3
"""info_scan.py と code_nudge.py を、作った中身で確かめる。悪い例は文字列を分けて書く。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import info_scan  # noqa: E402

HOOK = HERE.parent / "hooks" / "code_nudge.py"


def ids(findings):
    return sorted({f.id for f in findings})


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


class Text(unittest.TestCase):
    def test_log_secret(self):
        f = info_scan.scan_text("a.py", "print('tok', " + "api" + "_key)")
        self.assertIn("I04", ids(f))
        self.assertEqual(info_scan.scan_text("a.py", "print('Enter pass" + "word:')"), [])
        self.assertEqual(info_scan.scan_text("a.py", "logger.info('token length %d', len(" + "token))"), [])
        self.assertEqual(info_scan.scan_text("a.py", "print(f'約 {tokens(text)} トークン')"), [])
        self.assertEqual([f.sev for f in info_scan.scan_text("notes.md", "https://deploy:p" + "w@artifacts.example.net")], ["注意"])  # 文書の中は注意
        self.assertEqual(info_scan.scan_text("notes.md", "https://deploy:p" + "w@host.example.net  # secret-leak-check: ignore"), [])

    def test_url_secret(self):
        f = info_scan.scan_text("a.js", "fetch('https://api.example.net/v1?api" + "_key=abc123')")
        self.assertIn("I05", ids(f))
        self.assertIn("I08", ids(f))
        self.assertEqual(ids(info_scan.scan_text("a.js", "fetch(`https://api.example.net/v1?api" + "_key=${k}`)")), ["I08"])

    def test_chmod_key(self):
        f = info_scan.scan_text("a.py", "key = p / 'id_rsa'\nos.chmod(key, 0o" + "644)")
        self.assertIn("I07", ids(f))

    def test_pii_and_telemetry(self):
        f = info_scan.scan_text("m.py", "class User:\n    email = Column()\n    phone = Column()\nimport sentry_sdk\n")
        self.assertIn("I09", ids(f))
        self.assertIn("I10", ids(f))

    def test_test_files_only_output_rules(self):
        f = info_scan.scan_text("test_a.py", "os.chmod(key, 0o" + "644)\nclass U:\n    email = 1\n", test=True)
        self.assertEqual(f, [])


class Repo(unittest.TestCase):
    def test_tracked_env(self):
        root = make_repo({".env": "A=1\n", "a.py": "x = 1\n", ".env.example": "A=\n", "notes.db": "", ".gitignore": "*.pyc\n"})
        f = info_scan.tracked_files(root, ["a.py"])
        got = {(x.id, x.path) for x in f}
        self.assertIn(("I01", ".env"), got)
        self.assertNotIn(("I01", ".env.example"), got)
        self.assertIn(("I02", "notes.db"), got)
        self.assertIn(("I03", ".gitignore"), got)

    def test_diff_mode_does_not_scan_the_whole_tree(self):
        root = make_repo({".env": "A=1\n", "a.py": "x = 1\n", ".gitignore": "*.pyc\n"})
        env = dict(os.environ, CODE_CHECK_NO_TOOLS="1", CIEL_ROOT="/nonexistent")
        (root / "a.py").write_text("x = 2\n", encoding="utf-8")
        r = subprocess.run([sys.executable, str(HERE / "info_scan.py"), str(root), "--diff", "HEAD"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # 前からある .env では止めない
        self.assertNotIn("I01", r.stdout)
        r = subprocess.run([sys.executable, str(HERE / "info_scan.py"), str(root)], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1)  # 木全体の検査では見つける
        self.assertIn("I01", r.stdout)

    def test_cli_without_tools(self):
        root = make_repo({"a.py": "print('x', pass" + "word)\n"})
        env = dict(os.environ, CODE_CHECK_NO_TOOLS="1", CIEL_ROOT="/nonexistent")
        r = subprocess.run([sys.executable, str(HERE / "info_scan.py"), str(root)], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("I04", r.stdout)
        self.assertIn("無し", r.stdout)


class Hook(unittest.TestCase):
    def run_hook(self, path):
        payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(path), "content": "x"}})
        r = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, timeout=60)
        return r.stdout.strip()

    def test_hook_flags_shell_and_syntax(self):
        d = Path(tempfile.mkdtemp())
        bad = d / "b.py"
        bad.write_text("import subprocess\nsubprocess.run(f'ls {d}', sh" + "ell=True)\n", encoding="utf-8")
        out = self.run_hook(bad)
        self.assertIn("code-check:", out)
        self.assertIn("重大", out)
        self.assertLessEqual(len(out), 300)
        broken = d / "c.py"
        broken.write_text("def f(:\n", encoding="utf-8")
        self.assertIn("C01", self.run_hook(broken))

    def test_hook_silent_on_clean(self):
        d = Path(tempfile.mkdtemp())
        ok = d / "ok.py"
        ok.write_text("import subprocess\nsubprocess.run(['ls', '-l'])\n", encoding="utf-8")
        self.assertEqual(self.run_hook(ok), "")
        self.assertEqual(self.run_hook(d / "README.md"), "")


if __name__ == "__main__":
    unittest.main()
