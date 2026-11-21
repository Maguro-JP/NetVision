#!/usr/bin/env python3
"""fake_scan.py を、形ごとの小さな見本と、一時の git リポジトリで確かめる。

見本は文字列で持つ（このファイル自身が、見本の形で拾われないように）。
拾うべき形と、拾ってはいけない形（abstract、条件つきの skip、理由のあるコメント、動かしただけの行）を組で置く。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fake_scan  # noqa: E402

SCAN = HERE / "fake_scan.py"


def found(rel, text):
    """(ID, 重さ) の一覧。"""
    return [(f.id, f.sev) for f in fake_scan.scan_text(rel, text)]


def ids(rel, text):
    return [i for i, _ in found(rel, text)]


def src(*lines):
    """行を並べて 1 つの見本にする。デコレータの行を、文字列の先頭に置くため（前の行とつなげて書くと、メールアドレスの形に見える）。"""
    return "\n".join(lines) + "\n"


class PythonShapes(unittest.TestCase):
    def test_hollow_function_is_a_placeholder(self):
        self.assertEqual(found("app/a.py", "def load(path):\n    pass\n"), [("P01", "注意")])
        self.assertEqual(found("app/a.py", "def load(path):\n    \"\"\"読む。\"\"\"\n    ...\n"), [("P01", "注意")])
        self.assertEqual(found("app/a.py", "def load(path):\n    raise NotImplementedError\n"), [("P02", "注意")])
        self.assertEqual(found("app/a.py", "def load(path):\n    raise NotImplementedError('あとで')\n"), [("P02", "注意")])

    def test_checker_that_checks_nothing_is_severe(self):
        self.assertEqual(found("app/a.py", "def validate_token(token):\n    return True\n"), [("P04", "重大")])
        self.assertEqual(found("app/a.py", "def check_input(x):\n    pass\n"), [("P04", "重大")])
        self.assertEqual(found("app/a.py", "def is_valid(x):\n    return True\n"), [("P04", "重大")])
        self.assertEqual(found("app/a.py", "class A:\n    def has_access(self, user):\n        return True\n"), [("P04", "重大")])
        # 中身のある検査は拾わない
        self.assertEqual(found("app/a.py", "def is_valid(x):\n    return len(x) > 3\n"), [])

    def test_constant_return_needs_an_unused_argument(self):
        self.assertEqual(found("app/a.py", "def fetch_users(db):\n    return [{'id': 1, 'name': 'a'}]\n"), [("P03", "注意")])
        self.assertEqual(found("app/a.py", "def version():\n    return '1.0'\n"), [])
        self.assertEqual(found("app/a.py", "class A:\n    @property\n    def name(self):\n        return 'a'\n"), [])

    def test_hollow_bodies_that_are_fine(self):
        for text in ("import abc\nclass A(abc.ABC):\n    @abc.abstractmethod\n    def load(self, p):\n        pass\n",
                     "from typing import Protocol\nclass P(Protocol):\n    def load(self, p: str) -> str: ...\n",
                     src("from typing import overload", "@overload", "def f(x: int) -> int: ..."),
                     "class A:\n    def __init__(self):\n        pass\n",
                     "class Error(Exception):\n    pass\n"):
            with self.subTest(text=text):
                self.assertEqual(found("app/a.py", text), [])

    def test_pretend_work(self):
        self.assertEqual(found("app/a.py", "import time\ndef upload(f):\n    time.sleep(2)\n    return {'ok': True}\n"), [("D01", "注意")])
        self.assertEqual(found("app/a.py", "def save(data):\n    print('保存しました')\n    return True\n"), [("D02", "注意")])
        self.assertEqual(found("app/a.py", "import time\ndef wait(n):\n    time.sleep(n)\n    return compute(n)\n"), [])

    def test_branch_only_for_tests(self):
        self.assertEqual(found("app/a.py", "import os\ndef price():\n    if os.environ.get('PYTEST_CURRENT_TEST'):\n        return 100\n    return query()\n"), [("D03", "重大")])
        self.assertEqual(found("app/a.py", "import sys\nFAST = 'pytest' in sys.modules\n"), [("D03", "重大")])
        # 正規表現や説明の文字列の中の語は拾わない
        self.assertEqual(found("app/a.py", "import re\nRX = re.compile(r'PYTEST_CURRENT_TEST')\nMSG = 'TODO や pass を見る'\n"), [])

    def test_swallowed_exceptions(self):
        self.assertEqual(found("app/a.py", "try:\n    go()\nexcept Exception:\n    pass\n"), [("S01", "重大")])
        self.assertEqual(found("app/a.py", "try:\n    go()\nexcept:\n    pass\n"), [("S01", "重大")])
        self.assertEqual(found("app/a.py", "try:\n    go()\nexcept (OSError, Exception) as e:\n    ...\n"), [("S01", "重大")])
        self.assertEqual(found("app/a.py", "from contextlib import suppress\nwith suppress(Exception):\n    go()\n"), [("S01", "重大")])
        self.assertEqual(found("app/a.py", "try:\n    go()\nexcept OSError:\n    pass\n"), [("S02", "注意")])
        # 理由のあるコメントが付いた、型を絞った捨て方と、中身のある扱いは拾わない
        self.assertEqual(found("app/a.py", "try:\n    go()\nexcept OSError:  # 書けなくても続ける\n    pass\n"), [])
        self.assertEqual(found("app/a.py", "try:\n    go()\nexcept Exception as e:\n    log(e)\n    raise\n"), [])

    def test_words_in_comments(self):
        self.assertEqual(found("app/a.py", "x = 1  # TODO: あとで直す\n"), [("P05", "注意")])
        self.assertEqual(found("app/a.py", "# とりあえず固定の値\nx = 1\n"), [("P05", "注意")])
        self.assertEqual(found("app/a.py", "def f():\n    # ... rest of the code unchanged\n    return g()\n"), [("P06", "重大")])
        self.assertEqual(found("app/a.py", "class A:\n    # （中略）\n    x = 1\n"), [("P06", "重大")])
        self.assertEqual(found("app/a.py", "# 既存のコードの呼び出しを壊さないために残す\nx = 1\n"), [])
        # 実際の差分で偽の当たりになった形。小文字の todo は、印ではなく普通の語として出てくる（TAP の「# todo」の説明など）
        self.assertEqual(found("app/a.py", "# skip と todo の印が付いた物は数えない\nx = 1\n"), [])
        self.assertEqual(found("app/a.py", "x = 1  # todo: 直す\n"), [("P05", "注意")])
        # テストのファイルでは、stub や dummy は普通の語
        self.assertEqual(found("tests/test_a.py", "# dummy の値を渡す\ndef test_a():\n    assert f(1) == 2\n"), [])

    def test_silencing_the_checkers(self):
        self.assertEqual(found("app/a.py", "x = f()  # type: ignore\n"), [("W03", "重大")])
        self.assertEqual(found("app/a.py", "import os  # noqa\n"), [("W03", "重大")])
        self.assertEqual(found("app/a.py", "import os  # noqa: F401 再輸出\n"), [("W13", "注意")])
        self.assertEqual(found("app/a.py", "x = f()  # type: ignore[attr-defined]\n"), [("W13", "注意")])
        self.assertEqual(found("app/a.py", "MSG = '# noqa と書かない'\n"), [])

    def test_ignore_marks(self):
        self.assertEqual(found("app/a.py", "try:\n    go()\nexcept Exception:  # no-fake: ignore（終了の途中なので捨てる）\n    pass\n"), [])
        self.assertEqual(found("app/a.py", "def check(x):  # no-fake: ignore（下の組が中身を足す）\n    pass\n"), [])
        self.assertEqual(found("app/a.py", "# no-fake: ignore-file（悪い見本を置く）\ndef check(x):\n    pass\n"), [])

    def test_broken_syntax_does_not_crash(self):
        self.assertEqual(found("app/a.py", "def f(:\n    pass  # TODO\n"), [("P05", "注意")])


class PythonTests(unittest.TestCase):
    def test_tests_that_cannot_break(self):
        self.assertEqual(found("tests/test_a.py", "def test_a():\n    pass\n"), [("T02", "重大")])
        self.assertEqual(found("tests/test_a.py", "def test_a():\n    assert True\n"), [("T02", "重大")])
        self.assertEqual(found("tests/test_a.py", "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(True)\n"), [("T02", "重大")])
        self.assertEqual(found("tests/test_a.py", "def test_a():\n    x = f()\n    assert x == x\n"), [("T02", "重大")])
        self.assertEqual(found("tests/test_a.py", "def test_a():\n    f(1)\n"), [("T01", "注意")])

    def test_real_tests_are_quiet(self):
        for text in ("def test_a():\n    assert f(1) == 2\n",
                     "import pytest\ndef test_a():\n    with pytest.raises(ValueError):\n        f(-1)\n",
                     "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(f(1), 2)\n",
                     "def test_a(mock):\n    f(mock)\n    mock.assert_called_once_with(1)\n",
                     "def test_a():\n    check_output(f(1))\n"):
            with self.subTest(text=text):
                self.assertEqual(found("tests/test_a.py", text), [])

    def test_skips(self):
        self.assertEqual(ids("tests/test_a.py", src("import pytest", "@pytest.mark.skip(reason='x')", "def test_a():", "    assert f() == 1")), ["T03"])
        self.assertEqual(ids("tests/test_a.py", src("import pytest", "@pytest.mark.xfail", "def test_a():", "    assert f() == 1")), ["T03"])
        self.assertEqual(ids("tests/test_a.py", "import unittest\nclass T(unittest.TestCase):\n    @unittest.skip('x')\n    def test_a(self):\n        self.assertEqual(f(), 1)\n"), ["T03"])
        self.assertEqual(ids("tests/test_a.py", "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.skipTest('x')\n        self.assertEqual(f(), 1)\n"), ["T03"])
        self.assertEqual(ids("tests/test_a.py", "import pytest\npytestmark = pytest.mark.skip\ndef test_a():\n    assert f() == 1\n"), ["T03"])

    def test_skips_with_a_condition_are_fine(self):
        for text in (src("import pytest, sys", "@pytest.mark.skipif(sys.platform == 'win32', reason='x')", "def test_a():", "    assert f() == 1"),
                     "import unittest, shutil\nclass T(unittest.TestCase):\n    def test_a(self):\n        if not shutil.which('ruff'):\n            self.skipTest('ruff が無い')\n        self.assertEqual(f(), 1)\n",
                     "import unittest, sys\nclass T(unittest.TestCase):\n    @unittest.skipUnless(sys.platform == 'linux', 'x')\n    def test_a(self):\n        self.assertEqual(f(), 1)\n",
                     "import pytest\nnp = pytest.importorskip('numpy')\ndef test_a():\n    assert f() == 1\n"):
            with self.subTest(text=text):
                self.assertEqual(found("tests/test_a.py", text), [])

    def test_mocking_the_thing_under_test(self):
        text = src("from unittest.mock import patch", "@patch('app.config.load_config')", "def test_load_config(m):", "    m.return_value = 1", "    assert load_config() == 1")
        self.assertEqual(ids("tests/test_config.py", text), ["T06"])
        text = src("from unittest.mock import patch", "@patch('app.config.requests.get')", "def test_load_config(m):", "    assert load_config() == 1")
        self.assertEqual(ids("tests/test_config.py", text), [])


class JavaScriptShapes(unittest.TestCase):
    def test_hollow_functions(self):
        self.assertEqual(found("src/a.js", "function load(path) {\n}\n"), [("P01", "注意")])
        self.assertEqual(found("src/a.ts", "export const load = async (path: string) => {};\n"), [("P01", "注意")])
        self.assertEqual(found("src/a.js", "function load(path) {\n  throw new Error('not implemented');\n}\n"), [("P02", "注意")])
        self.assertEqual(found("src/a.ts", "export function validateEmail(email: string): boolean {\n  return true;\n}\n"), [("P04", "重大")])
        self.assertEqual(found("src/a.js", "const isAdmin = (user) => true;\n"), [("P04", "重大")])
        self.assertEqual(found("src/a.js", "function getUsers(db) {\n  return [{ id: 1, name: 'a' }];\n}\n"), [("P03", "注意")])
        self.assertEqual(found("src/a.js", "async function upload(file) {\n  await sleep(1000);\n  return { ok: true };\n}\n"), [("D01", "注意")])

    def test_real_functions_are_quiet(self):
        for text in ("function load(path) {\n  return fs.readFileSync(path, 'utf8');\n}\n",
                     "const isAdmin = (user) => user.role === 'admin';\n",
                     "export function validateEmail(email) {\n  return /@/.test(email);\n}\n",
                     "if (x) {\n}\nfor (const a of b) {\n}\n",
                     "const noop = () => {};\n",
                     "const msg = 'function load(path) {}';\n// function old(path) {}\n"):
            with self.subTest(text=text):
                self.assertEqual(found("src/a.js", text), [])

    def test_swallowed_failures(self):
        self.assertEqual(found("src/a.js", "try { go(); } catch (e) {}\n"), [("S03", "重大")])
        self.assertEqual(found("src/a.js", "try {\n  go();\n} catch {\n}\n"), [("S03", "重大")])
        self.assertEqual(found("src/a.js", "go().catch(() => {});\n"), [("S03", "重大")])
        self.assertEqual(found("src/a.js", "go().catch(() => null);\n"), [("S03", "重大")])
        self.assertEqual(found("src/a.js", "try {\n  go();\n} catch (e) {\n  // 無くても動く\n}\n"), [])
        self.assertEqual(found("src/a.js", "go().catch((e) => report(e));\n"), [])
        # 同じ行に理由のコメントがある物は、捨てると決めた物として拾わない
        self.assertEqual(found("src/a.js", "try { go(); } catch (e) { /* 無くても動く */ }\n"), [])
        self.assertEqual(found("src/a.js", "audio.play().catch(() => {}); // 自動再生が断られても、画面は進める\n"), [])

    def test_branch_only_for_tests(self):
        self.assertEqual(found("src/a.js", "if (process.env.JEST_WORKER_ID) return fake;\n"), [("D03", "重大")])
        self.assertEqual(found("src/a.js", "const fast = process.env.NODE_ENV === 'test';\n"), [("D04", "注意")])
        self.assertEqual(found("src/a.js", "// process.env.JEST_WORKER_ID は見ない\nconst s = 'process.env.JEST_WORKER_ID';\n"), [])

    def test_tests_that_cannot_break(self):
        self.assertEqual(found("src/a.test.js", "it.skip('adds', () => {\n  expect(add(1, 2)).toBe(3);\n});\n"), [("T03", "重大")])
        self.assertEqual(found("src/a.test.js", "describe.only('x', () => {\n  it('adds', () => {\n    expect(add(1, 2)).toBe(3);\n  });\n});\n"), [("T03", "重大")])
        self.assertEqual(found("src/a.test.js", "xit('adds', () => {\n  expect(add(1, 2)).toBe(3);\n});\n"), [("T03", "重大")])
        self.assertEqual(found("src/a.test.js", "it('adds', () => {\n  expect(true).toBe(true);\n});\n"), [("T02", "重大")])
        self.assertEqual(found("src/a.test.js", "it('adds', () => {\n});\n"), [("T02", "重大")])
        self.assertEqual(found("src/a.test.js", "it('adds', () => {\n  add(1, 2);\n});\n"), [("T01", "注意")])
        self.assertEqual(found("src/calc.test.ts", "jest.mock('./calc');\nit('adds', () => {\n  expect(add(1, 2)).toBe(3);\n});\n"), [("T06", "注意")])

    def test_real_tests_are_quiet(self):
        for text in ("it('adds', () => {\n  expect(add(1, 2)).toBe(3);\n});\n",
                     "test.skip(browserName === 'webkit', 'この環境には無い');\ntest('adds', async () => {\n  expect(add(1, 2)).toBe(3);\n});\n",
                     "jest.mock('axios');\nit('loads', async () => {\n  await expect(load()).resolves.toEqual([]);\n});\n"):
            with self.subTest(text=text):
                self.assertEqual(found("src/a.test.js", text), [])

    def test_silencing_the_checkers(self):
        self.assertEqual(found("src/a.ts", "// @ts-ignore\nconst x: number = f();\n"), [("W03", "重大")])
        self.assertEqual(found("src/a.js", "/* eslint-disable */\nconst x = 1;\n"), [("W03", "重大")])
        self.assertEqual(found("src/a.js", "// eslint-disable-next-line no-console\nconsole.log(1);\n"), [("W13", "注意")])
        self.assertEqual(found("src/a.js", "const s = '@ts-ignore と書かない';\n"), [])

    def test_buttons(self):
        self.assertEqual(found("src/a.tsx", "export const A = () => (\n  <div>\n    <button className=\"x\">保存</button>\n  </div>\n);\n"), [("U02", "注意")])
        self.assertEqual(found("src/a.tsx", "export const A = () => <button onClick={() => {}}>保存</button>;\n"), [("U03", "注意")])
        for text in ("export const A = () => <button onClick={() => save(x)}>保存</button>;\n",
                     "export const A = () => <button className={cx(a => a)} onClick={save}>保存</button>;\n",
                     "export const A = () => (\n  <form onSubmit={save}>\n    <button>保存</button>\n  </form>\n);\n",
                     "export const A = () => (\n  <DialogTrigger asChild>\n    <Button>開く</Button>\n  </DialogTrigger>\n);\n",
                     "export const A = (props) => <button {...props}>保存</button>;\n"):
            with self.subTest(text=text):
                self.assertEqual(found("src/a.tsx", text), [])
        self.assertEqual(found("web/a.html", "<button>保存</button>\n"), [("U02", "注意")])
        self.assertEqual(found("web/a.html", "<button id=\"save\">保存</button>\n"), [])


class ShellAndConfig(unittest.TestCase):
    def test_checks_that_cannot_break(self):
        self.assertEqual(found("scripts/ci.sh", "pytest -q || true\n"), [("W06", "重大")])
        self.assertEqual(found(".github/workflows/ci.yml", "      - run: npm test || echo skipped\n"), [("W06", "重大")])
        self.assertEqual(found(".github/workflows/ci.yml", "      - run: npm test\n        continue-on-error: true\n"), [("W02", "重大")])
        self.assertEqual(found("scripts/ci.sh", "git commit -m x --no-verify\n"), [("W01", "重大")])
        self.assertEqual(found("package.json", "{\n  \"scripts\": {\n    \"test\": \"echo no tests\"\n  }\n}\n"), [("W06", "重大")])
        self.assertEqual(found("tsconfig.json", "{\n  \"compilerOptions\": {\n    \"strict\": false\n  }\n}\n"), [("W07", "重大")])

    def test_plain_shell_noise_is_a_note_only_when_new(self):
        hits = fake_scan.scan_text("scripts/a.sh", "grep -q x file || true\nls /nope 2>/dev/null\n")
        self.assertEqual([(f.id, f.sev, f.new_only) for f in hits], [("S04", "注意", True), ("S05", "注意", True)])
        self.assertEqual(found("scripts/a.sh", "# pytest || true と書かない\npytest -q\n"), [])
        self.assertEqual(found("scripts/a.sh", "pytest -q || { echo 落ちた; exit 1; }\n"), [])

    def test_skipped_places(self):
        self.assertEqual(found("node_modules/x/a.js", "try { go(); } catch (e) {}\n"), [])
        self.assertEqual(found("tests/fixtures/bad.py", "def check(x):\n    pass\n"), [])
        self.assertEqual(found(".claude/skills/no-fake/references/patterns.md", "except Exception: pass\n"), [])


def sh(cwd, *args):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@users.noreply.github.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@users.noreply.github.com"))
    if r.returncode != 0:
        raise AssertionError(f"{args}: {r.stdout}{r.stderr}")
    return r.stdout


class Repo:
    """一時の git リポジトリ。main に土台を置き、work で変える。"""

    def __init__(self, files: dict):
        self.dir = tempfile.mkdtemp()
        self.root = Path(self.dir)
        sh(self.dir, "git", "init", "-q", "-b", "main")
        self.write(files)
        self.commit("base")
        sh(self.dir, "git", "checkout", "-q", "-b", "work")

    def write(self, files: dict):
        for rel, text in files.items():
            p = self.root / rel
            if text is None:
                p.unlink()
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

    def commit(self, msg: str):
        sh(self.dir, "git", "add", "-A")
        sh(self.dir, "git", "commit", "-q", "-m", msg, "--allow-empty")

    def scan(self, *args, state=None):
        env = dict(os.environ, NO_FAKE_STATE=state or tempfile.mkdtemp())
        r = subprocess.run([sys.executable, "-I", str(SCAN), self.dir, *args], capture_output=True, text=True, timeout=120, env=env)
        return r.returncode, r.stdout + r.stderr

    def diff_ids(self):
        code, out = self.scan("--diff", "main", "--json")
        return sorted((f["id"], f["sev"]) for f in json.loads(out)["findings"])


OLD_TEST = "def test_add():\n    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n\n\ndef test_sub():\n    assert sub(3, 1) == 2\n    assert sub(0, 0) == 0\n"


class Diff(unittest.TestCase):
    def test_only_added_lines_are_judged(self):
        r = Repo({"app/a.py": "def old():\n    try:\n        go()\n    except Exception:\n        pass\n"})
        r.write({"app/a.py": "def old():\n    try:\n        go()\n    except Exception:\n        pass\n\n\ndef new():\n    return old()\n\n\nnew()\n"})
        self.assertEqual(r.diff_ids(), [])
        r.write({"app/main.py": "import a\n\n\ndef more():\n    try:\n        a.new()\n    except Exception:\n        pass\n\n\nmore()\n"})
        self.assertEqual(r.diff_ids(), [("S01", "重大")])
        code, out = r.scan("--diff", "main")
        self.assertEqual(code, 1)
        self.assertEqual(out.strip().splitlines()[-1], "見せかけの確認: 重大 1 / 注意 0")

    def test_lines_that_only_moved_are_not_new(self):
        r = Repo({"app/a.py": "def one():\n    try:\n        go()\n    except Exception:\n        pass\n\n\none()\n"})
        r.write({"app/a.py": "def one():\n    if ready():\n        try:\n            go()\n        except Exception:\n            pass\n\n\none()\n"})
        self.assertEqual(r.diff_ids(), [])

    def test_changes_on_the_base_branch_are_not_mine(self):
        # base の先頭と比べると、base が足したテストは「私が消した」に、base が消した握りつぶしは「私が足した」に見える。分かれ目と比べる
        r = Repo({"app/a.py": "x = 1\n", "app/old.py": "try:\n    go()\nexcept Exception:\n    pass\n"})
        sh(r.dir, "git", "checkout", "-q", "main")
        r.write({"tests/test_theirs.py": OLD_TEST, "app/old.py": None})
        r.commit("base が進んだ")
        sh(r.dir, "git", "checkout", "-q", "work")
        r.write({"app/a.py": "x = 2\n"})
        r.commit("mine")
        self.assertEqual(r.diff_ids(), [])

    def test_deleted_and_weakened_tests(self):
        r = Repo({"tests/test_calc.py": OLD_TEST, "tests/test_other.py": OLD_TEST.replace("add", "mul").replace("sub", "div")})
        r.write({"tests/test_other.py": None})
        self.assertEqual(r.diff_ids(), [("T04", "重大")])
        r = Repo({"tests/test_calc.py": OLD_TEST})
        r.write({"tests/test_calc.py": OLD_TEST.split("\n\n\n")[0] + "\n"})
        self.assertEqual(r.diff_ids(), [("T04", "重大")])
        r = Repo({"tests/test_calc.py": OLD_TEST})
        r.write({"tests/test_calc.py": "def test_add():\n    add(1, 2)\n\n\ndef test_sub():\n    sub(3, 1)\n"})
        self.assertEqual(r.diff_ids(), [("T01", "注意"), ("T01", "注意"), ("T05", "重大")])
        r = Repo({"tests/test_calc.py": OLD_TEST})
        r.write({"tests/test_calc.py": OLD_TEST.replace("add(1, 2) == 3", "add(1, 2) == 4")})
        self.assertEqual(r.diff_ids(), [("T08", "注意")])

    def test_moved_and_renamed_tests_are_fine(self):
        r = Repo({"tests/test_calc.py": OLD_TEST})
        r.write({"tests/test_calc.py": None, "tests/test_math.py": OLD_TEST})
        self.assertEqual(r.diff_ids(), [])
        r = Repo({"tests/test_calc.py": OLD_TEST})
        r.write({"tests/test_calc.py": OLD_TEST.replace("def test_sub", "def test_subtract")})
        self.assertEqual(r.diff_ids(), [])

    def test_weakened_gates(self):
        hook = '          {\n            "type": "command",\n            "command": "run.sh .claude/skills/x/hooks/%s.py"\n          }'
        settings = '{\n  "hooks": {\n    "Stop": [\n      {\n        "hooks": [\n%s\n        ]\n      }\n    ]\n  }\n}\n'
        r = Repo({".claude/settings.json": settings % (hook % "a" + ",\n" + hook % "b"),
                  ".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - run: pytest -q\n      - run: ruff check .\n",
                  "pyproject.toml": "[tool.coverage.report]\nfail_under = 90\n",
                  "tests/__snapshots__/a.snap": "exports[`a 1`] = `1`;\n"})
        r.write({".claude/settings.json": settings % (hook % "a"),
                 ".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - run: ruff check .\n",
                 "pyproject.toml": "[tool.coverage.report]\nfail_under = 60\n",
                 "tests/__snapshots__/a.snap": "exports[`a 1`] = `2`;\n"})
        self.assertEqual(r.diff_ids(), [("T07", "注意"), ("W04", "重大"), ("W05", "重大"), ("W05", "重大")])
        r.write({".github/workflows/ci.yml": None})
        self.assertIn(("W05", "重大"), r.diff_ids())

    def test_reordered_hook_registrations_are_not_removed(self):
        hook = '          {\n            "command": "run.sh .claude/skills/x/hooks/%s.py"\n          }'
        settings = '{\n  "hooks": {\n    "Stop": [\n      {\n        "hooks": [\n%s\n        ]\n      }\n    ]\n  }\n}\n'
        r = Repo({".claude/settings.json": settings % (hook % "a" + ",\n" + hook % "b")})
        r.write({".claude/settings.json": settings % (hook % "b" + ",\n" + hook % "a" + ",\n" + hook % "c")})
        self.assertEqual(r.diff_ids(), [])

    def test_rewritten_hook_registrations_are_not_removed(self):
        # 実際の履歴で偽の当たりになった形。登録の書き方（起動役を通す、cd を前に付ける）を変えただけで、動かす物は同じ
        settings = '{\n  "hooks": {\n    "Stop": [\n      {\n        "hooks": [\n          {\n            "command": "%s"\n          },\n          {\n            "command": "%s"\n          }\n        ]\n      }\n    ]\n  }\n}\n'
        r = Repo({".claude/settings.json": settings % (".claude/hooks/lang-rule.sh", "awk '{print}' .claude/skills/work-style/SKILL.md || true")})
        r.write({".claude/settings.json": settings % ('\\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/run.sh\\" .claude/hooks/lang-rule.sh',
                                                      'cd \\"${CLAUDE_PROJECT_DIR:-.}\\" && awk \'{print}\' .claude/skills/work-style/SKILL.md || true')})
        self.assertEqual(r.diff_ids(), [])
        r.write({".claude/settings.json": settings % ('\\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/run.sh\\" .claude/hooks/other.sh', "true")})
        self.assertEqual(r.diff_ids(), [("W05", "重大")])

    def test_ignore_in_the_commit_message(self):
        r = Repo({"tests/test_calc.py": OLD_TEST, "app/a.py": "x = 1\n"})
        r.write({"tests/test_calc.py": None})
        r.commit("calc を外した\n\nno-fake: ignore tests/test_calc.py（calc の機能ごと外すと決まった）")
        self.assertEqual(r.diff_ids(), [])

    def test_unwired_things(self):
        r = Repo({"app/main.py": "from app import util\n\n\ndef main():\n    return util.used(1)\n", "app/util.py": "def used(x):\n    return x + 1\n"})
        r.write({"app/util.py": "def used(x):\n    return x + 1\n\n\ndef orphan(x):\n    return x * 2\n",
                 "app/lonely.py": "def helper(x):\n    return x - 1\n\n\ndef other(x):\n    return helper(x)\n"})
        got = r.diff_ids()
        self.assertEqual(got, [("U01", "注意"), ("U01", "注意"), ("U04", "注意")])
        r.write({"app/main.py": "from app import util, lonely\n\n\ndef main():\n    return util.used(1) + util.orphan(2) + lonely.other(3)\n"})
        self.assertEqual(r.diff_ids(), [])

    def test_copies_of_skills_are_judged_only_at_their_home(self):
        swallow = "try:\n    go()\nexcept Exception:\n    pass\n"
        # 配った先（スキルの検査の台本が無い）: .claude/skills/ の中はコピーなので、差分でも全体でも見ない
        r = Repo({"README.md": "# x\n"})
        r.write({".claude/skills/x/hooks/a.py": swallow})
        self.assertEqual(r.diff_ids(), [])
        self.assertEqual(r.scan()[0], 0)
        # スキルの元のリポジトリ: 同じ場所を見る
        r = Repo({"scripts/validate.sh": "#!/usr/bin/env bash\n# skill validator\n"})
        r.write({".claude/skills/x/hooks/a.py": swallow})
        self.assertEqual(r.diff_ids(), [("S01", "重大")])
        self.assertEqual(r.scan()[0], 1)

    def test_added_setting_that_nothing_reads(self):
        r = Repo({"config.json": '{\n  "timeout": 30\n}\n', "app/main.py": "import json\n\n\ndef main():\n    return json.load(open('config.json'))['timeout']\n"})
        r.write({"config.json": '{\n  "timeout": 30,\n  "retry_limit": 3,\n  "cache_size": 10\n}\n',
                 "app/main.py": "import json\n\n\ndef main():\n    c = json.load(open('config.json'))\n    return c['timeout'] * c['retry_limit']\n"})
        code, out = r.scan("--diff", "main")
        self.assertEqual(r.diff_ids(), [("U06", "注意")])
        self.assertIn("config.json:4  U06 配線: 足した設定 cache_size を、コードのどこも読んでいない疑い", out)

    def test_new_shell_noise_is_counted_only_in_a_diff(self):
        r = Repo({"scripts/a.sh": "ls /nope 2>/dev/null\n"})
        r.write({"scripts/a.sh": "ls /nope 2>/dev/null\ngrep -q x f || true\n"})
        self.assertEqual(r.diff_ids(), [("S04", "注意")])
        code, out = r.scan()
        self.assertEqual((code, out.strip().splitlines()[-1]), (0, "見せかけの確認: 重大 0 / 注意 0"))


class Cli(unittest.TestCase):
    def test_tree_scan_and_exit_code(self):
        r = Repo({"app/a.py": "def check(x):\n    pass\n\n\ncheck(1)\n", "README.md": "# x\n"})
        code, out = r.scan()
        self.assertEqual(code, 1)
        self.assertIn("app/a.py:1  P04 偽の検査", out)
        self.assertEqual(out.strip().splitlines()[-1], "見せかけの確認: 重大 1 / 注意 0")

    def test_not_a_repository_is_quiet(self):
        d = tempfile.mkdtemp()
        r = subprocess.run([sys.executable, "-I", str(SCAN), d, "--diff", "main"], capture_output=True, text=True, timeout=60, env=dict(os.environ, NO_FAKE_STATE=tempfile.mkdtemp()))
        self.assertEqual(r.returncode, 0)
        r = subprocess.run([sys.executable, "-I", str(SCAN), d], capture_output=True, text=True, timeout=60, env=dict(os.environ, NO_FAKE_STATE=tempfile.mkdtemp()))
        self.assertEqual((r.returncode, r.stdout.strip().splitlines()[-1]), (0, "見せかけの確認: 重大 0 / 注意 0"))

    def test_counts_are_recorded_one_row_per_kind(self):
        state = tempfile.mkdtemp()
        r = Repo({"app/a.py": "def check(x):\n    pass\n\n\ntry:\n    check(1)\nexcept Exception:\n    pass\n"})
        r.scan(state=state)
        rows = [json.loads(l) for l in (Path(state) / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sorted((x["src"], x["kind"], x["sev"], x["n"]) for x in rows), [("scan", "偽の検査", "重大", 1), ("scan", "握りつぶし", "重大", 1)])
        out = subprocess.run([sys.executable, "-I", str(SCAN), "--stats", "--days", "1"], capture_output=True, text=True, timeout=60, env=dict(os.environ, NO_FAKE_STATE=state)).stdout
        self.assertIn("握りつぶし（重大） 1", out)
        self.assertEqual(out.strip().splitlines()[-1], "拾った数: 合計 2 / 差し戻し 0")


class DiffTexts(unittest.TestCase):
    def test_added_and_removed_lines_from_two_texts(self):
        fd = fake_scan.diff_texts("app/a.py", "a = 1\nb = 2\n", "a = 1\nc = 3\n")
        self.assertEqual(([t for _, t, _ in fd.added], [t for _, t, _ in fd.removed], fd.status), (["c = 3"], ["b = 2"], "M"))
        self.assertEqual(fake_scan.diff_texts("a.py", None, "x = 1\n").status, "A")
        hits = fake_scan.scan_diffs([fake_scan.diff_texts("app/a.py", "x = 1\n", "x = 1\ntry:\n    go()\nexcept Exception:\n    pass\n")],
                                    lambda rel: "x = 1\ntry:\n    go()\nexcept Exception:\n    pass\n")
        self.assertEqual([(f.id, f.line) for f in hits], [("S01", 4)])


if __name__ == "__main__":
    unittest.main()
