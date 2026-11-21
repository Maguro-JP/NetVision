#!/usr/bin/env python3
"""vuln_scan.py を、作った中身で確かめる。悪い例は文字列を分けて書き、このファイル自身が検査に引っかからないようにする。"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import vuln_scan  # noqa: E402
from common import changed, is_test, tracked  # noqa: E402


def ids(findings):
    return sorted({f.id for f in findings})


class Patterns(unittest.TestCase):
    def test_python_bad(self):
        src = "\n".join([
            "import subprocess, pickle, yaml, hashlib, random, tempfile",
            "subprocess.run(f'ls {d}', sh" + "ell=True)",
            "pickle." + "loads(data)",
            "yaml." + "load(s)",
            "requests.get(u, ver" + "ify=False)",
            "p = tempfile.mk" + "temp()",
            "cur.execute(f'SELECT * FROM t WHERE id = {uid}')",
            "app.run(de" + "bug=True)",
            "os.chmod(p, 0o" + "777)",
            "try:", "    x()", "except:", "    pass",
        ])
        f = vuln_scan.scan_text("a.py", src)
        got = ids(f)
        for want in ("V02", "V03", "V04", "V05", "V07", "V10", "V11", "V16", "V17"):
            self.assertIn(want, got, want)
        self.assertTrue(all(x.sev in ("重大", "注意") for x in f))

    def test_python_ok(self):
        src = "\n".join(["import subprocess, yaml, hashlib, secrets", "subprocess.run(['ls', d])", "yaml.safe_load(s)", "h = hashlib.sha256(b).hexdigest()",
                         "t = secrets.token_hex(16)", "print('Enter password:')", "cur.execute('SELECT * FROM t WHERE id = ?', (uid,))"])
        self.assertEqual(vuln_scan.scan_text("a.py", src), [])

    def test_js_bad(self):
        src = "\n".join(["el.inner" + "HTML = user", "ev" + "al(code)", "exec(`rm ${p}`)", "const h = { rejectUnauthorized: fal" + "se }"])
        got = ids(vuln_scan.scan_text("a.js", src))
        for want in ("V14", "V01", "V02", "V05"):
            self.assertIn(want, got, want)

    def test_js_ok(self):
        src = "\n".join(["el.textContent = user", "el.innerHTML = ''", "JSON.parse(s)", "execFile('ls', [p])"])
        self.assertEqual(vuln_scan.scan_text("a.js", src), [])

    def test_ignore_marker(self):
        src = "subprocess.run(cmd, sh" + "ell=True)  # code-check: ignore"
        self.assertEqual(vuln_scan.scan_text("a.py", src), [])
        self.assertEqual(vuln_scan.scan_text("test_a.py", "ev" + "al(x)", test=True), [])

    def test_hardcoded_secret(self):
        src = "api_key = 'sk-live-" + "0123456789abcdef'"  # secret-leak-check: ignore
        self.assertIn("V20", ids(vuln_scan.scan_text("c.py", src)))
        self.assertEqual(vuln_scan.scan_text("c.py", "api_key = os.environ['API_KEY']"), [])


class Repo(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.root = Path(self.d)
        subprocess.run(["git", "init", "-q"], cwd=self.d, check=True)
        (self.root / "ok.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "bad.js").write_text("ev" + "al(x)\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.d, check=True)
        subprocess.run(["git", "-c", "user.email=t@users.noreply.github.com", "-c", "user.name=t", "commit", "-q", "-m", "init"], cwd=self.d, check=True)

    def test_tracked_skips_node_modules(self):
        self.assertEqual(tracked(self.root), ["ok.py"])

    def test_changed_sees_new_file(self):
        (self.root / "new.py").write_text("y = 2\n", encoding="utf-8")
        self.assertIn("new.py", changed(self.root, "HEAD"))

    def test_is_test(self):
        self.assertTrue(is_test("tests/test_x.py"))
        self.assertTrue(is_test("a/b.test.ts"))
        self.assertFalse(is_test("src/app.py"))

    def test_cli_runs(self):
        (self.root / "bad.py").write_text("import os\nos.sys" + "tem('rm -rf ' + p)\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.d, check=True)  # 追跡している物だけ見るので足す
        env = dict(os.environ, CODE_CHECK_NO_TOOLS="1")  # ツールを隠し、正規表現の補いで動くことを見る
        r = subprocess.run([sys.executable, str(HERE / "vuln_scan.py"), self.d, "--no-ruff"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("V02", r.stdout)
        self.assertIn("無し bandit", r.stdout)
        self.assertIn("不具合や脆弱性: 重大 1", r.stdout)

    def test_diff_mode_only_blocks_on_changed_lines(self):
        bad = "import os\nos.sys" + "tem('rm -rf ' + p)\nx = 1\n"
        (self.root / "old.py").write_text(bad, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.d, check=True)
        subprocess.run(["git", "-c", "user.email=t@users.noreply.github.com", "-c", "user.name=t", "commit", "-q", "-m", "old"], cwd=self.d, check=True)
        env = dict(os.environ, CODE_CHECK_NO_TOOLS="1")
        (self.root / "old.py").write_text(bad.replace("x = 1", "x = 2"), encoding="utf-8")  # 悪い行には触らない
        r = subprocess.run([sys.executable, str(HERE / "vuln_scan.py"), self.d, "--diff", "HEAD"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("前からある行", r.stdout)
        (self.root / "old.py").write_text(bad + "os.sys" + "tem('ls ' + q)\n", encoding="utf-8")  # 新しい悪い行
        r = subprocess.run([sys.executable, str(HERE / "vuln_scan.py"), self.d, "--diff", "HEAD"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("old.py:4", r.stdout)

    def test_cli_with_tools_merges_same_line(self):
        import tools
        if not (tools.find("bandit") and tools.find("ruff")):
            self.skipTest("bandit と ruff が無い")
        (self.root / "bad.py").write_text("import subprocess\nsubprocess.run('ls ' + p, sh" + "ell=True)\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.d, check=True)
        r = subprocess.run([sys.executable, str(HERE / "vuln_scan.py"), self.d, "--regex"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("bad.py:2"), 1, r.stdout)  # 3 つのツールと正規表現が同じ行を 1 行に畳む
        self.assertIn("使った", r.stdout)


if __name__ == "__main__":
    unittest.main()
