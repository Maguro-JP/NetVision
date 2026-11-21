#!/usr/bin/env python3
"""removed.py を、前と今の中身の組と、一時の git リポジトリで確かめる。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import removed  # noqa: E402

SCRIPT = HERE / "removed.py"
OLD_PY = "__all__ = ['load', 'save']\n\n\ndef load(p):\n    return open(p).read()\n\n\ndef save(p, s):\n    open(p, 'w').write(s)\n\n\nclass Store:\n    def get(self, k):\n        return self.d[k]\n\n    def put(self, k, v):\n        self.d[k] = v\n"


def gone(changes):
    return sorted((i["kind"], i["path"], i["name"]) for i in removed.removed_items(changes))


class Names(unittest.TestCase):
    def test_python_functions_classes_and_exports(self):
        new = "__all__ = ['load']\n\n\ndef load(p):\n    return open(p).read()\n\n\nclass Store:\n    def get(self, k):\n        return self.d[k]\n"
        self.assertEqual(gone([("app/store.py", "M", OLD_PY, new)]),
                         [("export", "app/store.py", "save"), ("関数", "app/store.py", "Store.put"), ("関数", "app/store.py", "save")])
        self.assertEqual(gone([("app/store.py", "M", OLD_PY, OLD_PY.replace("class Store:", "class Store:\n    size = 0\n"))]), [])

    def test_javascript_functions_exports_and_tests(self):
        old = "export function load(p) {\n  return read(p);\n}\nexport const save = (p, s) => write(p, s);\nclass Store {}\nexport { Store };\n"
        new = "export function load(p) {\n  return read(p);\n}\n"
        self.assertEqual(gone([("src/store.ts", "M", old, new)]),
                         [("export", "src/store.ts", "Store"), ("export", "src/store.ts", "save"), ("クラス", "src/store.ts", "Store"), ("関数", "src/store.ts", "save")])
        old = "it('adds', () => {\n  expect(add(1, 2)).toBe(3);\n});\nit('subtracts', () => {\n  expect(sub(3, 1)).toBe(2);\n});\n"
        self.assertEqual(gone([("src/calc.test.js", "M", old, old.split("it('subtracts'")[0])]), [("テスト", "src/calc.test.js", "subtracts")])

    def test_names_inside_strings_are_not_definitions(self):
        old = "SAMPLE = '''\ndef ghost():\n    pass\n'''\n\n\ndef real():\n    return 1\n"
        self.assertEqual(gone([("app/a.py", "M", old, "def real():\n    return 1\n")]), [])
        old = "const s = 'function ghost() {}';\nfunction real() { return 1; }\n"
        self.assertEqual(gone([("src/a.js", "M", old, "function real() { return 1; }\n")]), [])

    def test_moved_things_are_not_removed(self):
        self.assertEqual(gone([("app/store.py", "M", OLD_PY, OLD_PY.split("\n\n\ndef save")[0] + "\n"),
                               ("app/io.py", "A", None, "def save(p, s):\n    open(p, 'w').write(s)\n\n\nclass Store:\n    def get(self, k):\n        return self.d[k]\n\n    def put(self, k, v):\n        self.d[k] = v\n")]),
                         [])

    def test_deleted_files(self):
        self.assertEqual(gone([("app/store.py", "D", OLD_PY, None)]), [("ファイル", "app/store.py", "app/store.py")])
        self.assertEqual(gone([("tests/test_store.py", "D", "def test_a():\n    assert load('x') == 'y'\n", None)]), [("テスト", "tests/test_store.py", "tests/test_store.py")])
        # 中身ごと別のファイルに移したら、消したことにしない
        self.assertEqual(gone([("app/store.py", "D", OLD_PY, None), ("app/storage.py", "A", None, OLD_PY)]), [])

    def test_settings_ui_and_routes(self):
        self.assertEqual(gone([("config.json", "M", '{\n  "timeout": 30,\n  "retries": 3\n}\n', '{\n  "timeout": 30\n}\n')]), [("設定", "config.json", "retries")])
        old = "export const A = () => (\n  <form>\n    <input name=\"email\" />\n    <button id=\"save\" onClick={save}>保存</button>\n  </form>\n);\n"
        new = "export const A = () => (\n  <form>\n    <input name=\"email\" />\n  </form>\n);\n"
        self.assertEqual(gone([("src/a.tsx", "M", old, new)]), [("UI", "src/a.tsx", "id=save")])
        users = ["@app.route('/users')", "def users():", "    return q()"]  # デコレータの行は、文字列の先頭に置く（前の行とつなげると、メールアドレスの形に見える）
        health = ["", "", "@app.route('/health')", "def health():", "    return 'ok'"]
        self.assertEqual(gone([("app/web.py", "M", "\n".join(users + health) + "\n", "\n".join(users) + "\n")]),
                         [("ルート", "app/web.py", "/health"), ("関数", "app/web.py", "health")])

    def test_broken_old_text_falls_back_to_lines(self):
        self.assertEqual(gone([("app/a.py", "M", "def keep(:\n    pass\n\ndef lost():\n    pass\n", "def keep():\n    return 1\n")]), [("関数", "app/a.py", "lost")])


def sh(cwd, *args):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@users.noreply.github.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@users.noreply.github.com"))
    if r.returncode != 0:
        raise AssertionError(f"{args}: {r.stdout}{r.stderr}")
    return r.stdout


class Cli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        root = Path(self.dir)
        sh(self.dir, "git", "init", "-q", "-b", "main")
        (root / "app").mkdir()
        (root / "app" / "store.py").write_text(OLD_PY, encoding="utf-8")
        (root / "app" / "old.py").write_text("def unused_helper(x):\n    return x * 2 + 1\n", encoding="utf-8")
        sh(self.dir, "git", "add", "-A")
        sh(self.dir, "git", "commit", "-q", "-m", "base")
        sh(self.dir, "git", "checkout", "-q", "-b", "work")

    def listing(self, *args):
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), self.dir, *args], capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout

    def test_uncommitted_and_committed_removals(self):
        root = Path(self.dir)
        (root / "app" / "store.py").write_text(OLD_PY.split("\n\n\nclass Store")[0] + "\n", encoding="utf-8")
        code, out = self.listing()
        self.assertEqual(code, 0)
        self.assertIn("クラス  app/store.py  Store", out)
        self.assertEqual(out.strip().splitlines()[-1], "消えた物: 関数 2 / クラス 1 / export 0 / テスト 0 / ファイル 0 / 設定 0 / UI 0 / ルート 0")
        sh(self.dir, "git", "rm", "-q", "app/old.py")
        sh(self.dir, "git", "commit", "-q", "-am", "片付け")
        code, out = self.listing("--diff", "main", "--json")
        data = json.loads(out)
        self.assertEqual(data["counts"]["ファイル"], 1)
        self.assertEqual(self.listing()[1].strip().splitlines()[-1], "消えた物: 関数 0 / クラス 0 / export 0 / テスト 0 / ファイル 0 / 設定 0 / UI 0 / ルート 0")

    def test_reason_in_the_commit_message_takes_it_off_the_list(self):
        sh(self.dir, "git", "rm", "-q", "app/old.py")
        sh(self.dir, "git", "commit", "-q", "-m", "old を外す\n\nno-fake: ignore app/old.py（使う所が無くなった）")
        self.assertEqual(json.loads(self.listing("--diff", "main", "--json")[1])["removed"], [])

    def test_no_repository_is_quiet(self):
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), tempfile.mkdtemp()], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
