#!/usr/bin/env python3
"""バグやクラッシュ: 構文、未定義、テスト、起動を機械で見る。

実績のあるツールを先に使う（ruff、node --check、shellcheck、eslint、tsc、pytest）。無い物は tools.py --install で入れ、
入らなければ Python 標準の compile と bash -n で補う。Claude が書いた物は動かすまで信じない、がこのスクリプトの前提。

使い方: crash_check.py [root] [--diff <base>] [--json] [--no-tests] [--no-smoke]
  --no-tests  テストを流さない（precheck は別にテストを流すので重ねない）
  --no-smoke  起動の確認をしない
環境変数: CODE_CHECK_SMOKE_OFF=1 で起動の確認をしない
終了コード: 重大があれば 1
抑止: 行末に `code-check: ignore`（構文の誤りは抑止できない）
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tools  # noqa: E402
from common import Finding, code_files, line_ignored, parse_args, read_text, relpath, report, run, scope_to_diff, target_files  # noqa: E402

PY = {".py"}
JS = {".js", ".mjs", ".cjs"}
TS = {".ts", ".tsx"}
SH = {".sh", ".bash"}
GUI = re.compile(r"\b(webview|tkinter|PyQt\d|PySide\d|wx|kivy|pygame|electron|flet)\b")
ARGS = re.compile(r"\b(argparse|click|typer|docopt|sys\.argv|process\.argv|commander|yargs)\b")


def syntax(root: Path, files: list, used: list, missing: list) -> list:
    out = []
    for rel in code_files(files, PY):
        text = read_text(root / rel)
        if text is None:
            continue
        try:
            compile(text, rel, "exec")
        except SyntaxError as e:
            out.append(Finding("重大", rel, e.lineno or 0, "C01", f"構文の誤り: {e.msg}", "その行を直す。書いたら compile か ruff で見る"))
        except (ValueError, RecursionError) as e:
            out.append(Finding("重大", rel, 0, "C01", f"読めない: {e}", "文字コードと長さを見る"))
    node = tools.find("node")
    js = code_files(files, JS)
    if js and node:
        used.append("node")
        for rel in js:
            code, text = run([node, "--check", rel], root, timeout=60)
            if code != 0:
                m = re.search(r":(\d+)", text)
                out.append(Finding("重大", rel, int(m.group(1)) if m else 0, "C01", "構文の誤り: " + text.strip().splitlines()[-1][:100] if text.strip() else "構文の誤り", "その行を直す"))
    elif js:
        missing.append("node")
    sh_files = code_files(files, SH)
    if sh_files:
        sc = tools.find("shellcheck")
        if sc:
            used.append("shellcheck")
            code, text = run([sc, "-f", "json", "-S", "warning", *sh_files], root, timeout=120)
            try:
                for d in json.loads(text[text.index("["):]) if "[" in text else []:
                    rel = d.get("file", "")
                    src = read_text(root / rel) or ""
                    row = int(d.get("line", 0))
                    if row and row <= len(src.splitlines()) and line_ignored(src.splitlines()[row - 1]):
                        continue
                    sev = "重大" if d.get("level") == "error" else "注意"
                    out.append(Finding(sev, rel, row, f"SC{d.get('code', '')}", f"{d.get('message', '')[:90]}（shellcheck）", "shellcheck の wiki の番号を見て直す"))
            except ValueError:
                pass
        else:
            missing.append("shellcheck")
            bash = tools.find("bash")
            for rel in sh_files:
                code, text = run([bash, "-n", rel], root, timeout=30) if bash else (0, "")
                if code != 0:
                    m = re.search(r"line (\d+)", text)
                    out.append(Finding("重大", rel, int(m.group(1)) if m else 0, "C01", "構文の誤り: " + text.strip().splitlines()[-1][:100], "その行を直す"))
    return out


def undefined(root: Path, files: list, used: list, missing: list) -> list:
    """未定義の名前と明らかな不具合。ruff があれば E9・F・B を使う。"""
    py = code_files(files, PY)
    exe = tools.find("ruff")
    if not py:
        return []
    if not exe:
        missing.append("ruff")
        return []
    used.append("ruff")
    out = []
    for k in range(0, len(py), 200):
        code, text = run([exe, "check", "--select", "E9,F63,F7,F82,B", "--ignore", "B008,B905,B006", "--isolated", "--no-cache", "--exit-zero",
                          "--output-format", "json", *py[k:k + 200]], root, timeout=300)
        try:
            data = json.loads(text[text.index("["):]) if "[" in text else []
        except ValueError:
            continue
        for d in data:
            c = d.get("code") or ""
            rel = relpath(root, d.get("filename", ""))
            row = int(d.get("location", {}).get("row", 0))
            src = (read_text(root / rel) or "").splitlines()
            if row and row <= len(src) and line_ignored(src[row - 1]):
                continue
            sev = "重大" if c.startswith(("E9", "F82", "F7", "F63")) else "注意"
            fix = "名前を定義するか import する" if c.startswith("F82") else "ruff の規則の説明を見て直す"
            out.append(Finding(sev, rel, row, c, f"{d.get('message', '')[:90]}（ruff {c}）", fix))
    return out


def js_checks(root: Path, files: list, used: list, missing: list) -> list:
    """eslint（設定があるとき）と tsc（tsconfig があるとき）。設定の無いリポジトリでは動かさない（基準が無いため）。"""
    out = []
    js = code_files(files, JS | TS | {".jsx"})
    if not js:
        return out
    has_eslint_cfg = any((root / n).exists() for n in ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", ".eslintrc", ".eslintrc.js",
                                                         ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml"))
    local = root / "node_modules" / ".bin" / "eslint"
    exe = str(local) if local.exists() else tools.find("eslint")
    if has_eslint_cfg and exe:
        used.append("eslint")
        code, text = run([exe, "-f", "json", "--no-error-on-unmatched-pattern", *js], root, timeout=300)
        try:
            for f in json.loads(text[text.index("["):]) if "[" in text else []:
                rel = relpath(root, f.get("filePath", ""))
                for m in f.get("messages", []):
                    sev = "重大" if m.get("fatal") or m.get("severity") == 2 else "注意"
                    out.append(Finding(sev, rel, int(m.get("line") or 0), m.get("ruleId") or "eslint", f"{m.get('message', '')[:90]}（eslint）", "規則の説明を見て直す"))
        except ValueError:
            pass
    elif has_eslint_cfg:
        missing.append("eslint")
    if (root / "tsconfig.json").exists():
        local = root / "node_modules" / ".bin" / "tsc"
        exe = str(local) if local.exists() else tools.find("tsc")
        if exe:
            used.append("tsc")
            code, text = run([exe, "--noEmit", "-p", "tsconfig.json"], root, timeout=600)
            if code != 0:
                for line in text.splitlines():
                    m = re.match(r"(.+?)\((\d+),\d+\): error (TS\d+): (.*)", line)
                    if m:
                        out.append(Finding("重大", m.group(1), int(m.group(2)), m.group(3), f"{m.group(4)[:90]}（tsc）", "型を合わせる"))
        else:
            missing.append("tsc")
    return out


def tests(root: Path, used: list, missing: list) -> list:
    """そのリポジトリのテストを 1 回流す。台本（.claude/hooks/test.sh）があればそれに任せる。"""
    if (root / ".claude" / "hooks" / "test.sh").exists():
        used.append("test.sh")
        code, text = run(["bash", "-c", "echo '{}' | bash .claude/hooks/test.sh"], root, timeout=900)
        tail = (text.strip().splitlines() or [""])[-1][:120]
        return [] if code == 0 else [Finding("重大", ".claude/hooks/test.sh", 0, "C05", f"テストが落ちた: {tail}", "落ちたテストを直してから出す")]
    has_py_tests = (root / "tests").is_dir() or (root / "pytest.ini").exists() or any(root.glob("test_*.py"))
    if has_py_tests:
        exe = tools.find("pytest")
        if exe:
            used.append("pytest")
            code, text = run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], root, timeout=900)
            tail = (text.strip().splitlines() or [""])[-1][:120]
            if code in (0, 5):
                return []
            return [Finding("重大", "tests", 0, "C05", f"テストが落ちた: {tail}", "落ちたテストを直してから出す")]
        missing.append("pytest")
    pj = root / "package.json"
    if pj.exists() and (root / "node_modules").is_dir():
        try:
            scripts = json.loads(pj.read_text(encoding="utf-8")).get("scripts", {})
        except ValueError:
            scripts = {}
        if "test" in scripts and tools.find("npm"):
            used.append("npm test")
            code, text = run(["npm", "test", "--silent"], root, timeout=900)
            tail = (text.strip().splitlines() or [""])[-1][:120]
            return [] if code == 0 else [Finding("重大", "package.json", 0, "C05", f"npm test が落ちた: {tail}", "落ちたテストを直してから出す")]
    return []


def entrypoints(root: Path) -> list:
    """起動の入口。main.py、app.py、__main__.py、package.json の bin。"""
    out = []
    for n in ("main.py", "app.py", "cli.py", "run.py"):
        if (root / n).is_file():
            out.append(("py", n))
    for p in list(root.glob("src/*/__main__.py")) + list(root.glob("*/__main__.py")):
        rel = str(p.relative_to(root))
        if not any(s in rel for s in ("node_modules", ".venv", "venv")):
            out.append(("mod", rel))
    pj = root / "package.json"
    if pj.exists():
        try:
            b = json.loads(pj.read_text(encoding="utf-8")).get("bin")
            for v in ([b] if isinstance(b, str) else list((b or {}).values())):
                if v and (root / v).is_file():
                    out.append(("js", v))
        except ValueError:
            pass
    return out[:6]


def smoke(root: Path, only=None) -> list:
    """--help で起動して、Traceback や落ちる物を見つける。画面を開く物と引数を受けない物は動かさない（副作用が読めない）。
    only があれば、その中の入口だけ（差分の検査では、変えた入口だけを動かす）。"""
    out = []
    for kind, rel in entrypoints(root):
        if only is not None and rel not in only:
            continue
        text = read_text(root / rel) or ""
        if GUI.search(text):
            out.append(Finding("注意", rel, 0, "C07", "画面のある入口なので、起動は手で確かめる", "アプリのウィンドウで開き、主な操作を 1 回ずつ通す"))
            continue
        if not ARGS.search(text):
            out.append(Finding("注意", rel, 0, "C07", "引数を受けない入口なので --help で起動できない", "手で 1 回起動して、落ちないことを見る"))
            continue
        if kind == "py":
            cmd = [sys.executable, "-I", rel, "--help"]
        elif kind == "mod":
            mod = rel[:-len("/__main__.py")].replace("src/", "", 1).replace("/", ".")
            cmd = [sys.executable, "-m", mod, "--help"]
        else:
            node = tools.find("node")
            if not node:
                continue
            cmd = [node, rel, "--help"]
        env = {"PYTHONPATH": str(root / "src") if (root / "src").is_dir() else str(root)}
        code, res = run(cmd, root, timeout=20, env=env)
        if code == 124:
            out.append(Finding("重大", rel, 0, "C06", "--help で起動して 20 秒で返らない", "起動の入口で重い処理や待ちを始めない"))
        elif re.search(r"ModuleNotFoundError|No module named|Cannot find module|ERR_MODULE_NOT_FOUND", res):
            # 依存が入っていない環境では起動を確かめられない。コードの不具合とは限らないので止めない
            tail = ([l for l in res.strip().splitlines() if l.strip()] or ["?"])[-1][:100]
            out.append(Finding("注意", rel, 0, "C07", f"依存が入っていないので起動を確かめられない: {tail}", "依存を入れてから --help で起動する。依存の書き忘れなら manifest に足す"))
        elif "Traceback" in res or code not in (0, 2):
            tail = ([l for l in res.strip().splitlines() if l.strip()] or ["?"])[-1][:100]
            out.append(Finding("重大", rel, 0, "C06", f"--help で起動して落ちた（exit {code}）: {tail}", "起動の入口の例外を直す。import の失敗なら依存を書く"))
    return out


def scan(root: Path, files: list, no_tests: bool, no_smoke: bool, diff_mode: bool = False):
    used, missing = [], []
    findings = syntax(root, files, used, missing) + undefined(root, files, used, missing) + js_checks(root, files, used, missing)
    if diff_mode:
        # tsc はプロジェクト全体を見るので、差分の検査では変えたファイルの物だけ残す
        findings = [f for f in findings if not f.id.startswith("TS") or f.path in files]
    if not no_tests:
        findings += tests(root, used, missing)
    if not no_smoke:
        findings += smoke(root, set(files) if diff_mode else None)
    return findings, used, missing


def main() -> int:
    import os
    a = parse_args(__doc__, [(["--no-tests"], {"action": "store_true"}), (["--no-smoke"], {"action": "store_true"})])
    files = target_files(a.root, a.diff)
    no_smoke = a.no_smoke or os.environ.get("CODE_CHECK_SMOKE_OFF") == "1"
    findings, used, missing = scan(a.root, files, a.no_tests, no_smoke, bool(a.diff))
    if a.diff:
        findings = scope_to_diff(findings, a.root, a.diff)
    return report("バグやクラッシュ", findings, a.json, with_info=False,
                  extra={"files": len(files), "tools": tools.tools_line(sorted(set(used)), sorted(set(missing)))})


if __name__ == "__main__":
    sys.exit(main())
