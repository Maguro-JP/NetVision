#!/usr/bin/env python3
"""変える前のテストの結果を記録し、変えたあとに比べて、通っていたのに落ちた物を出す。

ユーザーの指摘: わかっていてわざと壊す。
直したつもりの変更が、通っていた別のテストを落とす。変える前を測っていないと、前から落ちていたのか、自分が落としたのかが分からず、
「前から落ちていた」で済ませてしまう。変える前の結果を名前ごとに持ち、変えたあとに同じ物を流して比べる。
通っていたテストは変えたあとも通る、という比べ方は SWE-bench の PASS_TO_PASS と同じ（docs/SOURCES.md）。

使い方:
  baseline.py save    [--root DIR] [--cmd "<コマンド>"] [--from <出力のファイル>]   今の結果を記録する（変える前に 1 回）
  baseline.py compare [--root DIR] [--cmd "<コマンド>"] [--from <出力のファイル>] [--json]
                                                                              記録と比べ、通っていたのに落ちた物を出す
  baseline.py show    [--root DIR]                                            記録の中身（いつ、どの HEAD、通った数と落ちた数）
  --cmd      テストのコマンド。無ければ、あるものを探して流す（pytest、スキルの test_*.py、unittest、npm test）
  --from     もう流してある出力を読む（長いテストは裏で流して、出力のファイルを渡す）
  --timeout  1 本のコマンドの上限の秒（既定 900）
読める出力: pytest（-v、-rA、-q の FAILED の行）、unittest（-v と、FAIL: の行）、jest・vitest・mocha（✓ と ✕ の行、PASS と FAIL のファイルの行）、TAP（ok、not ok）
終了コード: compare で、通っていたのに落ちた物があれば 1。テストの無いリポジトリでは、何も出さずに 0
記録の場所: ~/.claude/state/no-fake/baseline/（NO_FAKE_STATE で変えられる。リポジトリには置かない）
"""

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_scan as fs  # noqa: E402

# 出力の形は、実際に流して確かめた（pytest 9.1、Python 3.11 の unittest、jest 29.7、vitest 2.1、mocha 10.8、Node 22 の node:test。2026-11-21）
PYTEST_V = re.compile(r"^(\S+::\S+)\s+(PASSED|FAILED|ERROR|XPASS)\b")
PYTEST_R = re.compile(r"^(PASSED|FAILED|ERROR|SUBFAILED(?:\([^)]*\))?)\s+(\S+::\S+)")   # SUBFAILED は subTest の中の失敗
UNITTEST = re.compile(r"^\s*(\w+) \(([\w.]+)\)(?: \([^)]*\))?(?: \[[^\]]*\])?(?: \.\.\.\s*(.*))?$")
UNITTEST_HEAD = re.compile(r"^(FAIL|ERROR): (\w+) \(([\w.]+)\)")
TIME = r"(?:\s+\(?\d+(?:\.\d+)?\s*m?s\)?)?"
MARK_PASS = re.compile(r"^\s*(?:✓|✔|√)\s+(.+?)" + TIME + r"\s*$")              # jest は ✓、mocha と node:test は ✔
MARK_FAIL = re.compile(r"^\s*(?:✕|✗|×|✖)\s+(.+?)" + TIME + r"\s*$")           # jest は ✕、vitest は ×、node:test は ✖
JEST_BULLET = re.compile(r"^\s*●\s+(.+?)\s*$")                                   # jest が落ちたテストの詳しい出力の頭に付ける
FILE_LINE = re.compile(r"^\s*(PASS|FAIL)\s+(\S+\.[cm]?[jt]sx?)(?:\s+>\s+(.+?))?" + TIME + r"\s*$")
MOCHA_FAIL = re.compile(r"^\s+\d+\)\s+(.+?)\s*$")
MOCHA_END = re.compile(r"^\s*\d+ (?:passing|failing)\b")
TAP = re.compile(r"^\s*(not ok|ok)\s+\d+\s*-?\s*(.*?)\s*(#\s*(?:skip|todo)\b.*)?$", re.I)
NOT_A_TEST = re.compile(r":$|^Console$|^(?:Deprecation|Validation) Warning|^Cannot log after tests are done")


def parse_output(text: str, prefix: str = "") -> dict:
    """テストの出力から、名前 → 'pass' か 'fail'。同じ名前が 2 回出たら、落ちた方を取る。"""
    out, pending, current = {}, "", ""
    mocha = bool(re.search(r"^\s*\d+ failing\b", text, re.M))  # mocha は、落ちた物を「1) 名前」の行で出す

    def put(name: str, ok: bool):
        name = re.sub(r"\s+\(\d+ tests?(?: \| \d+ \w+)*\)$", "", re.sub(r"\s+", " ", name.strip()))  # vitest のファイルの行の「(2 tests)」は、数が変わると名前が変わるので落とす
        if name and not NOT_A_TEST.search(name) and (not ok or prefix + name not in out):
            out[prefix + name] = "pass" if ok else "fail"

    for line in text.splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", line.rstrip())
        m = PYTEST_V.match(line)
        if m:
            put(m.group(1), m.group(2) == "PASSED")
            continue
        m = PYTEST_R.match(line)
        if m:
            put(m.group(2), m.group(1) == "PASSED")
            continue
        m = UNITTEST_HEAD.match(line)
        if m:
            put(m.group(3) if m.group(3).endswith("." + m.group(2)) else f"{m.group(3)}.{m.group(2)}", False)
            continue
        m = UNITTEST.match(line)
        if m:
            name = m.group(2) if m.group(2).endswith("." + m.group(1)) else f"{m.group(2)}.{m.group(1)}"
            status = m.group(3)
            if status is None:
                pending = name  # docstring の 1 行目が次に来て、その行の末尾に結果がある
            elif status == "ok":
                put(name, True)
            elif status in ("FAIL", "ERROR", "unexpected success"):
                put(name, False)
            continue
        if pending:
            tail = re.search(r" \.\.\. (ok|FAIL|ERROR|unexpected success|skipped.*|expected failure)$", line)
            if tail:
                if tail.group(1) in ("ok", "FAIL", "ERROR", "unexpected success"):
                    put(pending, tail.group(1) == "ok")
                pending = ""
                continue
        m = FILE_LINE.match(line)
        if m:
            current = m.group(2)
            put(f"{m.group(2)} > {m.group(3)}" if m.group(3) else f"file:{m.group(2)}", m.group(1) == "PASS")
            continue
        m = MARK_PASS.match(line) or MARK_FAIL.match(line) or JEST_BULLET.match(line)
        if m:
            name = m.group(1)
            put(name if " > " in name or not current else f"{current} > {name}", bool(MARK_PASS.match(line)))
            continue
        m = TAP.match(line)
        if m and m.group(2):
            if not m.group(3):  # skip と todo の印が付いた物は、通ったとも落ちたとも数えない
                put(m.group(2), m.group(1).lower() == "ok")
            continue
        if MOCHA_END.match(line):
            mocha = False  # ここから下は、落ちた物の詳しい出力（組の名前が「1) 組」の形で出る）
        m = MOCHA_FAIL.match(line) if mocha else None
        if m:
            put(m.group(1), False)
    return out


def detect(root: Path) -> list:
    """流すテストのコマンドの一覧 [(名前の前に付ける文, コマンド)]。無ければ空（テストの無いリポジトリでは黙る）。"""
    cmds = []
    py = sys.executable
    has_pytest = subprocess.run([py, "-c", "import pytest"], capture_output=True).returncode == 0
    tests = (root / "tests").is_dir() or (root / "test").is_dir()
    configured = (root / "pytest.ini").exists() or any("pytest" in (fs.read_text(root / n) or "") for n in ("pyproject.toml", "setup.cfg", "tox.ini"))
    loose = sorted(glob.glob(str(root / "test_*.py")) + glob.glob(str(root / "*_test.py")))
    if has_pytest and (configured or (tests and glob.glob(str(root / "test*" / "**" / "test_*.py"), recursive=True)) or loose):
        cmds.append(("", [py, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider"]))
    elif tests and glob.glob(str(root / "test*" / "**" / "test*.py"), recursive=True):
        start = "tests" if (root / "tests").is_dir() else "test"
        # 始める場所に __init__.py が無いと、根からの discover は 1 件も集めない（実際に流して確かめた）。無ければ -s で場所を渡す
        cmds.append(("", [py, "-m", "unittest", "discover", "-v"] + ([] if (root / start / "__init__.py").exists() else ["-s", start])))
    # スキルのテストは、スキルの元のリポジトリでだけ流す。配った先ではコピーなので、そのリポジトリのテストに数えない（テストの無いリポジトリでは黙る）
    for t in sorted(glob.glob(str(root / ".claude" / "skills" / "*" / "scripts" / "test_*.py"))) if fs.skills_home(root) else []:
        rel = os.path.relpath(t, root).replace(os.sep, "/")
        cmds.append((rel.split("/")[2] + "/", [py, "-I", rel, "-v"]))
    pkg = root / "package.json"
    if pkg.exists() and (root / "node_modules").is_dir():
        try:
            if "test" in (json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {}):
                cmds.append(("", ["npm", "test", "--silent"]))
        except ValueError:
            return cmds  # 壊れた package.json は読まない（npm が言う）
    return cmds


def run(root: Path, cmds: list, timeout: int) -> dict:
    results = {}
    for prefix, cmd in cmds:
        try:
            r = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=timeout, errors="replace", shell=isinstance(cmd, str),  # code-check: ignore ユーザーが渡したコマンドをそのまま流す
                               env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", CI="1"))
            text = r.stdout + "\n" + r.stderr
        except subprocess.TimeoutExpired:
            text = ""
            results[f"{prefix}（時間切れ: {' '.join(cmd) if not isinstance(cmd, str) else cmd}）"] = "fail"
        except OSError:
            continue
        for name, status in parse_output(text, prefix).items():
            if status == "fail" or name not in results:
                results[name] = status
    return results


def state_file(root: Path) -> Path:
    key = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return fs.state_dir() / "baseline" / f"{key}.json"


def collect(a, root: Path):
    """(結果, テストがあったか)。"""
    if a.source:
        try:
            text = Path(a.source).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"出力のファイルが読めない: {e}", file=sys.stderr)
            return {}, False
        return parse_output(text), True
    cmds = [("", a.cmd)] if a.cmd else detect(root)
    if not cmds:
        return {}, False
    return run(root, cmds, a.timeout), True


def split(results: dict):
    return sorted(n for n, s in results.items() if s == "pass"), sorted(n for n, s in results.items() if s == "fail")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("save", "compare", "show"))
    ap.add_argument("--root", default=".")
    ap.add_argument("--cmd")
    ap.add_argument("--from", dest="source")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    f = state_file(root)
    if a.action == "show":
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print("変える前の記録: 無い（baseline.py save で作る）")
            return 0
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(d.get("ts", 0) + 9 * 3600))
        print(f"変える前の記録: {when}（日本時間） HEAD {str(d.get('head', ''))[:7]} 通った {len(d.get('passed', []))} / 落ちた {len(d.get('failed', []))}")
        return 0
    results, has_tests = collect(a, root)
    if not has_tests:
        return 0  # テストの無いリポジトリでは黙る
    passed, failed = split(results)
    if a.action == "save":
        code, head = fs.git(root, "rev-parse", "HEAD")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"root": str(root), "head": head.strip() if code == 0 else "", "ts": int(time.time()), "passed": passed, "failed": failed}, ensure_ascii=False), encoding="utf-8")
        for name in failed[:20]:
            print(f"  前から落ちている  {name}")
        print(f"変える前の記録: 通った {len(passed)} / 落ちた {len(failed)}")
        return 0
    try:
        before = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("変える前の記録が無い。変える前に baseline.py save を流す（今回は比べていない）")
        return 0
    was_pass, was_fail = set(before.get("passed", [])), set(before.get("failed", []))
    broke = sorted(n for n in failed if n in was_pass)
    lost = sorted(n for n in was_pass if n not in results)
    new_fail = sorted(n for n in failed if n not in was_pass and n not in was_fail)
    fixed = sorted(n for n in passed if n in was_fail)
    summary = (f"変える前との比べ: 通っていたのに落ちた {len(broke)} / 無くなった {len(lost)} / 新しく落ちた {len(new_fail)} / 直った {len(fixed)}"
               f"（前 通った {len(was_pass)}・落ちた {len(was_fail)}、今 通った {len(passed)}・落ちた {len(failed)}）")
    if broke or lost or new_fail:
        fs.record("baseline", {("壊した", "重大"): len(broke), ("無くなったテスト", "注意"): len(lost), ("新しく落ちた", "注意"): len(new_fail)})
    if a.json:
        print(json.dumps({"broke": broke, "lost": lost, "new_fail": new_fail, "fixed": fixed, "summary": summary}, ensure_ascii=False, indent=1))
        return 1 if broke else 0
    print("== no-fake: 変える前との比べ ==")
    for label, rows in (("通っていたのに落ちた", broke), ("通っていたのに無くなった", lost), ("新しく落ちた", new_fail)):
        for name in rows[:30]:
            print(f"  {label}  {name}")
        if len(rows) > 30:
            print(f"  {label}  ほか {len(rows) - 30} 件")
    print(summary)
    return 1 if broke else 0


if __name__ == "__main__":
    sys.exit(main())
