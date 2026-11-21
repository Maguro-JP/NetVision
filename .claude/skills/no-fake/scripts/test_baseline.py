#!/usr/bin/env python3
"""baseline.py を、実際に流して取った出力のコピーと、一時のリポジトリで確かめる。

出力の見本は、pytest 9.1、Python 3.11 の unittest、jest 29.7、vitest 2.1、mocha 10.8、Node 22 の node:test を流して取った物を短くした。
記憶で書いた形は、本物と違っていた（unittest の subTest の行、jest の ● の行、mocha の落ちた物の番号）。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import baseline  # noqa: E402

SCRIPT = HERE / "baseline.py"

PYTEST_RA = """\
=========================== short test summary info ============================
PASSED tests/test_calc.py::Calc::test_add
PASSED tests/test_calc.py::Calc::test_many
PASSED tests/test_calc.py::test_plain
SUBFAILED(n=2) tests/test_calc.py::Calc::test_many - AssertionError: 2 != 1
FAILED tests/test_calc.py::Calc::test_sub - AssertionError: 3 != 2
2 failed, 3 passed, 1 subtests passed in 0.02s
"""
PYTEST_V = """\
tests/test_calc.py::Calc::test_add PASSED                                [ 25%]
tests/test_calc.py::Calc::test_sub FAILED                                [ 75%]
tests/test_calc.py::test_plain PASSED                                    [100%]
"""
UNITTEST_V = """\
test_add (test_calc.Calc.test_add) ... ok
test_many (test_calc.Calc.test_many) ...
  test_many (test_calc.Calc.test_many) (n=2) ... FAIL
test_sub (test_calc.Calc.test_sub)
引き算を確かめる。 ... FAIL
test_doc (test_calc.Calc.test_doc)
足し算を確かめる。 ... ok

======================================================================
FAIL: test_many (test_calc.Calc.test_many) (n=2)
----------------------------------------------------------------------
Ran 4 tests in 0.001s

FAILED (failures=2)
"""
UNITTEST_QUIET = """\
.FF
======================================================================
FAIL: test_sub (test_calc.Calc.test_sub)
引き算を確かめる。
----------------------------------------------------------------------
Ran 3 tests in 0.001s
"""
JEST = """\
PASS src/other.test.js
FAIL src/calc.test.js
  ● calc › subtracts two numbers

    expect(received).toBe(expected) // Object.is equality

Test Suites: 1 failed, 1 passed, 2 total
Tests:       1 failed, 2 passed, 3 total
"""
JEST_VERBOSE = """\
FAIL src/calc.test.js
  calc
    ✓ adds two numbers (1 ms)
    ✕ subtracts two numbers (1 ms)

PASS src/other.test.js
  ✓ adds zero
"""
VITEST = """\
 ✓ vt/other.test.mjs (2 tests) 3ms
 ❯ vt/calc.test.mjs (2 tests | 1 failed) 6ms
   × calc > subtracts two numbers 4ms
     → expected 3 to be 2 // Object.is equality

 FAIL  vt/calc.test.mjs > calc > subtracts two numbers
AssertionError: expected 3 to be 2 // Object.is equality
"""
VITEST_VERBOSE = """\
 ✓ vt/calc.test.mjs > calc > adds two numbers
 × vt/calc.test.mjs > calc > subtracts two numbers
   → expected 3 to be 2 // Object.is equality
"""
MOCHA = """\
  calc
    ✔ adds two numbers
    1) subtracts two numbers


  1 passing (3ms)
  1 failing

  1) calc
       subtracts two numbers:

      AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:
"""
NODE_TAP = """\
TAP version 13
# Subtest: adds two numbers
ok 1 - adds two numbers
  ---
  duration_ms: 0.4
  ...
# Subtest: subtracts two numbers
not ok 2 - subtracts two numbers
  ---
  duration_ms: 0.6
  ...
# Subtest: later
ok 3 - later # SKIP
1..3
# tests 3
"""
NODE_SPEC = """\
✔ adds two numbers (0.467257ms)
✖ subtracts two numbers (0.65196ms)
ℹ tests 2

✖ failing tests:

test at nt/calc.test.js:4:1
✖ subtracts two numbers (0.65196ms)
"""


class Parse(unittest.TestCase):
    def test_pytest(self):
        self.assertEqual(baseline.parse_output(PYTEST_RA), {"tests/test_calc.py::Calc::test_add": "pass", "tests/test_calc.py::Calc::test_many": "fail",
                                                            "tests/test_calc.py::test_plain": "pass", "tests/test_calc.py::Calc::test_sub": "fail"})
        self.assertEqual(baseline.parse_output(PYTEST_V), {"tests/test_calc.py::Calc::test_add": "pass", "tests/test_calc.py::Calc::test_sub": "fail", "tests/test_calc.py::test_plain": "pass"})

    def test_unittest(self):
        self.assertEqual(baseline.parse_output(UNITTEST_V), {"test_calc.Calc.test_add": "pass", "test_calc.Calc.test_many": "fail", "test_calc.Calc.test_sub": "fail", "test_calc.Calc.test_doc": "pass"})
        self.assertEqual(baseline.parse_output(UNITTEST_QUIET), {"test_calc.Calc.test_sub": "fail"})
        self.assertEqual(baseline.parse_output("test_a (x.T.test_a) ... ok\n", "skill/"), {"skill/x.T.test_a": "pass"})

    def test_jest(self):
        self.assertEqual(baseline.parse_output(JEST), {"file:src/other.test.js": "pass", "file:src/calc.test.js": "fail", "src/calc.test.js > calc › subtracts two numbers": "fail"})
        self.assertEqual(baseline.parse_output(JEST_VERBOSE), {"file:src/calc.test.js": "fail", "src/calc.test.js > adds two numbers": "pass", "src/calc.test.js > subtracts two numbers": "fail",
                                                               "file:src/other.test.js": "pass", "src/other.test.js > adds zero": "pass"})

    def test_vitest(self):
        self.assertEqual(baseline.parse_output(VITEST), {"vt/other.test.mjs": "pass", "calc > subtracts two numbers": "fail", "vt/calc.test.mjs > calc > subtracts two numbers": "fail"})
        self.assertEqual(baseline.parse_output(VITEST_VERBOSE), {"vt/calc.test.mjs > calc > adds two numbers": "pass", "vt/calc.test.mjs > calc > subtracts two numbers": "fail"})

    def test_mocha_and_node_test(self):
        self.assertEqual(baseline.parse_output(MOCHA), {"adds two numbers": "pass", "subtracts two numbers": "fail"})
        self.assertEqual(baseline.parse_output(NODE_TAP), {"adds two numbers": "pass", "subtracts two numbers": "fail"})
        self.assertEqual(baseline.parse_output(NODE_SPEC), {"adds two numbers": "pass", "subtracts two numbers": "fail"})

    def test_other_text_is_not_a_test(self):
        self.assertEqual(baseline.parse_output("ok\nnot ok\n誤り 0  注意 0\n  1) 手順を読む\nPASS\n"), {})


GOOD = "import unittest\n\n\nclass Calc(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(1 + 2, 3)\n\n    def test_sub(self):\n        self.assertEqual(3 - 1, 2)\n"


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.state = tempfile.mkdtemp()
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_calc.py").write_text(GOOD, encoding="utf-8")

    def call(self, *args):
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), *args, "--root", str(self.root)], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, NO_FAKE_STATE=self.state))
        return r.returncode, r.stdout + r.stderr

    def test_a_test_that_passed_and_now_breaks_is_reported(self):
        cmd = f"{sys.executable} -m unittest discover -v -s tests"
        code, out = self.call("save", "--cmd", cmd)
        self.assertEqual((code, out.strip().splitlines()[-1]), (0, "変える前の記録: 通った 2 / 落ちた 0"))
        code, out = self.call("compare", "--cmd", cmd)
        self.assertEqual(code, 0)
        self.assertIn("通っていたのに落ちた 0 / 無くなった 0 / 新しく落ちた 0", out)
        (self.root / "tests" / "test_calc.py").write_text(GOOD.replace("3 - 1, 2", "3 - 1, 3") + "\n    def test_new(self):\n        self.assertEqual(1, 2)\n", encoding="utf-8")
        code, out = self.call("compare", "--cmd", cmd)
        self.assertEqual(code, 1)
        self.assertIn("  通っていたのに落ちた  test_calc.Calc.test_sub", out)
        self.assertIn("  新しく落ちた  test_calc.Calc.test_new", out)
        self.assertIn("通っていたのに落ちた 1 / 無くなった 0 / 新しく落ちた 1 / 直った 0", out.strip().splitlines()[-1])
        rows = [json.loads(l) for l in (Path(self.state) / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertIn(("baseline", "壊した", 1), [(r["src"], r["kind"], r["n"]) for r in rows])

    def test_a_test_that_disappeared_is_reported(self):
        cmd = f"{sys.executable} -m unittest discover -v -s tests"
        self.call("save", "--cmd", cmd)
        (self.root / "tests" / "test_calc.py").write_text(GOOD.split("\n    def test_sub")[0] + "\n", encoding="utf-8")
        code, out = self.call("compare", "--cmd", cmd, "--json")
        self.assertEqual((code, json.loads(out)["lost"]), (0, ["test_calc.Calc.test_sub"]))

    def test_a_test_that_already_failed_is_not_mine(self):
        (self.root / "tests" / "test_calc.py").write_text(GOOD.replace("3 - 1, 2", "3 - 1, 3"), encoding="utf-8")
        cmd = f"{sys.executable} -m unittest discover -v -s tests"
        code, out = self.call("save", "--cmd", cmd)
        self.assertIn("  前から落ちている  test_calc.Calc.test_sub", out)
        code, out = self.call("compare", "--cmd", cmd)
        self.assertEqual(code, 0)

    def test_tests_are_found_without_a_command(self):
        cmds = baseline.detect(self.root)
        self.assertEqual(len(cmds), 1)
        code, out = self.call("save")
        self.assertEqual(code, 0)
        self.assertRegex(out.strip().splitlines()[-1], r"^変える前の記録: 通った 2 / 落ちた 0$")
        self.assertIn("変える前の記録:", self.call("show")[1])

    def test_copies_of_skill_tests_are_not_this_repositorys_tests(self):
        dest = Path(tempfile.mkdtemp())
        skill = dest / ".claude" / "skills" / "x" / "scripts"
        skill.mkdir(parents=True)
        (skill / "test_x.py").write_text(GOOD + "\n\nif __name__ == '__main__':\n    unittest.main()\n", encoding="utf-8")
        self.assertEqual(baseline.detect(dest), [])  # 配った先: コピーのテストは数えない（テストの無いリポジトリとして黙る）
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "save", "--root", str(dest)], capture_output=True, text=True, timeout=120, env=dict(os.environ, NO_FAKE_STATE=self.state))
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        home = Path(tempfile.mkdtemp())
        shutil.copytree(dest / ".claude", home / ".claude")
        (home / "scripts").mkdir()
        (home / "scripts" / "validate.sh").write_text("#!/usr/bin/env bash\n# skill validator\n", encoding="utf-8")
        self.assertEqual([prefix for prefix, _ in baseline.detect(home)], ["x/"])
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "save", "--root", str(home)], capture_output=True, text=True, timeout=120, env=dict(os.environ, NO_FAKE_STATE=self.state))
        self.assertEqual(r.stdout.strip().splitlines()[-1], "変える前の記録: 通った 2 / 落ちた 0")

    def test_output_file_can_be_given(self):
        log = self.root / "out.log"
        log.write_text(PYTEST_V, encoding="utf-8")
        self.assertEqual(self.call("save", "--from", str(log))[1].strip().splitlines()[-1], "変える前の記録: 通った 2 / 落ちた 1")
        log.write_text(PYTEST_V.replace("test_plain PASSED", "test_plain FAILED"), encoding="utf-8")
        code, out = self.call("compare", "--from", str(log))
        self.assertEqual(code, 1)
        self.assertIn("  通っていたのに落ちた  tests/test_calc.py::test_plain", out)

    def test_repository_without_tests_is_silent(self):
        empty = Path(tempfile.mkdtemp())
        (empty / "README.md").write_text("# x\n", encoding="utf-8")
        for action in ("save", "compare"):
            r = subprocess.run([sys.executable, "-I", str(SCRIPT), action, "--root", str(empty)], capture_output=True, text=True, timeout=120, env=dict(os.environ, NO_FAKE_STATE=self.state))
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_compare_without_a_record_says_so(self):
        code, out = self.call("compare", "--cmd", f"{sys.executable} -m unittest discover -v -s tests")
        self.assertEqual(code, 0)
        self.assertIn("変える前の記録が無い", out)


if __name__ == "__main__":
    unittest.main()
