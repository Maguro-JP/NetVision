#!/usr/bin/env python3
"""repo_check.py を、作ったリポジトリで確かめる。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "repo_check.py"
CIEL = HERE.parents[3]

README = """# sample

ファイルを 1 本のコマンドで整えるツールです。

![License](https://img.shields.io/github/license/o/r)

![画面](docs/images/screenshot.png)

## 特長

- 1 本のコマンドで整えます

## クイックスタート

1. 動かす
   ```bash
   python3 app.py
   ```

## インストール

```bash
pip install -e .
```

## 使い方

```bash
python3 app.py docs/
```

## ライセンス

MIT License。[LICENSE](LICENSE) を見てください。
"""


def make_repo(root: Path, files: dict):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@users.noreply.github.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@users.noreply.github.com")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "a"], cwd=root, check=True, env=env)


def run_check(root: Path, ciel: Path, state: Path):
    env = dict(os.environ, CIEL_ROOT=str(ciel), PR_GATE_STATE=str(state))
    r = subprocess.run([sys.executable, str(SCRIPT), str(root), "--json", "--md", str(root / "out.md")], capture_output=True, text=True, env=env, timeout=600)
    return r.returncode, json.loads(r.stdout) if r.stdout.strip().startswith("{") else r.stdout


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.empty = Path(self.tmp.name) / "empty"
        self.empty.mkdir()
        self.state = Path(self.tmp.name) / "state"

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, data):
        return {r["段"].split("（")[0]: r for r in data["rows"]}

    def test_all_missing_does_not_crash(self):
        make_repo(self.root, {"app.py": "print(1)\n"})
        code, data = run_check(self.root, self.empty, self.state)
        self.assertEqual(code, 0, data)
        rows = self.rows(data)
        self.assertEqual(rows["土台"]["状態"], "NG")
        self.assertEqual(rows["方針"]["状態"], "注意")
        self.assertEqual(rows["README"]["状態"], "NG")
        for k in ("文書", "鍵", "公開の精査", "依存の脆弱性"):
            self.assertEqual(rows[k]["状態"], "注意", k)
            self.assertIn("無い", rows[k]["根拠"])
        self.assertEqual(rows["整理整頓"]["状態"], "注意")
        self.assertIn("__pycache__/", rows[" 整理整頓".strip()]["根拠"])
        self.assertEqual(rows["出し方"]["状態"], "注意")
        self.assertTrue((self.root / "out.md").is_file())
        self.assertIn("リポジトリ管理: OK", data["summary"])

    def test_good_repo(self):
        if not (CIEL / ".claude" / "skills" / "secret-leak-check").is_dir():
            self.skipTest("Ciel のスキルが手元に無い")
        make_repo(self.root, {
            "CLAUDE.md": "# 決まり\n",
            ".claude/settings.json": json.dumps({"hooks": {"Stop": []}}),
            ".claude/policy/u.json": "{}",
            "LICENSE": "MIT\n",
            "README.md": README,
            "docs/images/screenshot.png": b"\x89PNG",
            ".gitignore": "__pycache__/\n.env\n",
            "app.py": "print(1)\n",
        })
        self.state.mkdir()
        (self.state / "repo.json").write_text(json.dumps({"sha": "abc1234", "ok": True, "ts": 1700000000}), encoding="utf-8")
        code, data = run_check(self.root, CIEL, self.state)
        self.assertEqual(code, 0, data)
        rows = self.rows(data)
        for k in ("土台", "方針", "README", "鍵", "出し方", "整理整頓"):
            self.assertEqual(rows[k]["状態"], "OK", rows[k])
        self.assertIn(rows["文書"]["状態"], ("OK", "注意"), rows["文書"])
        self.assertIn(rows["公開の精査"]["状態"], ("OK", "注意"), rows["公開の精査"])
        self.assertIn("棚卸し", rows["依存の脆弱性"]["根拠"])

    def test_secret_is_ng_and_exit_1(self):
        if not (CIEL / ".claude" / "skills" / "secret-leak-check").is_dir():
            self.skipTest("Ciel のスキルが手元に無い")
        make_repo(self.root, {"app.py": "KEY = '-----BEGIN RSA PRIVATE KEY-----'\n"})  # secret-leak-check: ignore（検出を確かめるための作り物）
        code, data = run_check(self.root, CIEL, self.state)
        self.assertEqual(code, 1)
        self.assertEqual(self.rows(data)["鍵"]["状態"], "NG")


if __name__ == "__main__":
    unittest.main()
