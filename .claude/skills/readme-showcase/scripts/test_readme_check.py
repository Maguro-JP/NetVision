#!/usr/bin/env python3
"""readme_check.py を、作った README で確かめる。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from readme_check import check, score  # noqa: E402

GOOD = """# sample

ファイルを 1 本のコマンドで整えるツールです。

![CI](https://img.shields.io/github/actions/workflow/status/o/r/ci.yml)
![License](https://img.shields.io/github/license/o/r)

![画面](docs/images/screenshot.png)

## 特長

- 1 本のコマンドで整えます
- 元に戻せます

## クイックスタート

前提: Python 3.11 以上。

1. 取る
   ```bash
   git clone https://example.invalid/o/r.git && cd r
   ```
2. 動かす
   ```bash
   python3 app.py
   ```
3. ウィンドウが開きます

## インストール

```bash
pip install -e .
```

## 使い方

```bash
python3 app.py docs/
```

出力の最後の行が `誤り 0` なら通っています。[設計](docs/design.md) も見てください。

## ライセンス

MIT License。[LICENSE](LICENSE) を見てください。
"""


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".git").mkdir()
        (self.root / "docs" / "images").mkdir(parents=True)
        (self.root / "docs" / "images" / "screenshot.png").write_bytes(b"\x89PNG")
        (self.root / "docs" / "design.md").write_text("# 設計\n", encoding="utf-8")
        (self.root / "LICENSE").write_text("MIT\n", encoding="utf-8")
        self.readme = self.root / "README.md"

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, text):
        self.readme.write_text(text, encoding="utf-8")
        items = check(self.readme, self.root)
        return {i["name"]: i for i in items}, score(items)

    def test_good_is_10(self):
        items, (ok, ng, warn) = self.run_check(GOOD)
        self.assertEqual((ok, ng, warn), (10, 0, 0), [i for i in items.values() if i["status"] != "OK"])

    def test_missing_quick_license_badge(self):
        text = GOOD.replace("## クイックスタート", "## 手順").replace("## ライセンス", "## 謝辞")
        text = "\n".join(l for l in text.splitlines() if "shields.io" not in l) + "\n"
        items, (ok, ng, warn) = self.run_check(text)
        self.assertEqual(items["クイックスタート"]["status"], "NG")
        self.assertEqual(items["ライセンス"]["status"], "NG")
        self.assertEqual(items["バッジ"]["status"], "NG")
        self.assertLess(ok, 10)

    def test_license_file_missing(self):
        (self.root / "LICENSE").unlink()
        items, _ = self.run_check(GOOD)
        self.assertEqual(items["ライセンス"]["status"], "NG")

    def test_broken_link_and_image(self):
        items, _ = self.run_check(GOOD.replace("docs/design.md", "docs/none.md").replace("screenshot.png", "none.png"))
        self.assertEqual(items["リンク"]["status"], "NG")
        self.assertEqual(items["スクリーンショット"]["status"], "NG")

    def test_empty_section_and_placeholder(self):
        text = GOOD.replace("## 特長\n", "## 設定\n\n## 特長\n").replace("python3 app.py docs/", "python3 app.py <パス>")
        items, _ = self.run_check(text)
        self.assertEqual(items["仕上げ"]["status"], "NG")
        self.assertIn("空の節", items["仕上げ"]["note"])
        self.assertIn("置き場所の語", items["仕上げ"]["note"])

    def test_parent_heading_with_subsections_is_not_empty(self):
        text = GOOD.replace("## インストール\n", "## インストール\n\n### 前提\n\nPython 3.11 以上。\n\n### 手順\n")
        items, _ = self.run_check(text)
        self.assertEqual(items["インストール"]["status"], "OK")
        self.assertEqual(items["仕上げ"]["status"], "OK")

    def test_notes(self):
        text = GOOD.replace("3. ウィンドウが開きます", "3. a\n4. b\n5. c\n6. d").replace("## 使い方", "## Usage")
        items, (ok, ng, warn) = self.run_check(text)
        self.assertEqual(items["クイックスタート"]["status"], "注意")
        self.assertEqual(items["仕上げ"]["status"], "注意")
        self.assertEqual(ng, 0)

    def test_one_line_missing_and_long(self):
        items, _ = self.run_check(GOOD.replace("ファイルを 1 本のコマンドで整えるツールです。\n\n", ""))
        self.assertEqual(items["1 文"]["status"], "NG")
        items, _ = self.run_check(GOOD.replace("ファイルを 1 本のコマンドで整えるツールです。", "あ" * 130))
        self.assertEqual(items["1 文"]["status"], "注意")

    def test_cli_diff_mode(self):
        import subprocess
        self.readme.write_text(GOOD, encoding="utf-8")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@users.noreply.github.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@users.noreply.github.com")
        (self.root / ".git").rmdir()
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "a"], cwd=self.root, check=True, env=env)
        r = subprocess.run([sys.executable, str(HERE / "readme_check.py"), str(self.root), "--diff", "HEAD"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("差分なし", r.stdout)
        self.readme.write_text(GOOD.replace("## 使い方", "## Usage"), encoding="utf-8")
        r = subprocess.run([sys.executable, str(HERE / "readme_check.py"), str(self.root), "--diff", "HEAD"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("README 注意: 点数", r.stdout)
        r = subprocess.run([sys.executable, str(HERE / "readme_check.py"), str(self.root), "--strict"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("点数 ", r.stdout)


if __name__ == "__main__":
    unittest.main()
