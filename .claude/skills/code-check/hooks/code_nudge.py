#!/usr/bin/env python3
"""PostToolUse フック（Edit、Write、MultiEdit）: 書いたコードを 1 ファイルだけその場で見て、重大だけを 1 行で差し込む。

ユーザーの決まり: Claude が作る物には問題が多いので、書き込みのたびに機械で見る。
構文（compile、node --check、shellcheck か bash -n）、脆弱性の型（ruff の S があれば使い、無ければ vuln_scan の正規表現）、
情報の扱い（info_scan の出力・URL・権限の型）。速さのために、テスト・bandit・semgrep はここでは動かさない（precheck が回す）。
止めはしない。重大が 0 なら何も出さない。

環境変数:
  CODE_CHECK_OFF=1   止める
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import info_scan  # noqa: E402
import vuln_scan  # noqa: E402
from common import Finding, is_test, line_ignored  # noqa: E402

EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".bash"}


def syntax(path: Path, text: str) -> list:
    ext = path.suffix.lower()
    if ext == ".py":
        try:
            compile(text, str(path), "exec")
        except SyntaxError as e:
            return [Finding("重大", path.name, e.lineno or 0, "C01", f"構文の誤り: {e.msg}", "その行を直す")]
        return []
    if ext in (".js", ".mjs", ".cjs") and shutil.which("node"):
        r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            m = re.search(r":(\d+)", r.stderr)
            return [Finding("重大", path.name, int(m.group(1)) if m else 0, "C01", "構文の誤り: " + (r.stderr.strip().splitlines() or ["?"])[-1][:80], "その行を直す")]
        return []
    if ext in (".sh", ".bash"):
        sc = shutil.which("shellcheck")
        if sc:
            r = subprocess.run([sc, "-f", "gcc", "-S", "error", str(path)], capture_output=True, text=True, timeout=30)
            out = []
            for line in r.stdout.splitlines():
                m = re.match(r".*?:(\d+):\d+: error: (.*)", line)
                if m:
                    out.append(Finding("重大", path.name, int(m.group(1)), "SC", m.group(2)[:80] + "（shellcheck）", "その行を直す"))
            return out
        r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            m = re.search(r"line (\d+)", r.stderr)
            return [Finding("重大", path.name, int(m.group(1)) if m else 0, "C01", "構文の誤り: " + (r.stderr.strip().splitlines() or ["?"])[-1][:80], "その行を直す")]
    return []


def ruff_s(path: Path) -> list:
    exe = shutil.which("ruff")
    if not exe or path.suffix != ".py":
        return []
    r = subprocess.run([exe, "check", "--select", "S,E9,F82", "--isolated", "--no-cache", "--exit-zero", "--output-format", "json", str(path)],
                       capture_output=True, text=True, timeout=60)
    out = []
    try:
        data = json.loads(r.stdout[r.stdout.index("["):]) if "[" in r.stdout else []
    except ValueError:
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for d in data:
        c = d.get("code") or ""
        row = int(d.get("location", {}).get("row", 0))
        if c in vuln_scan.RUFF_SKIP or (row and row <= len(lines) and line_ignored(lines[row - 1])):
            continue
        if c in vuln_scan.RUFF_HIGH or c.startswith(("E9", "F82")):
            out.append(Finding("重大", path.name, row, c, d.get("message", "")[:80] + f"（ruff {c}）", vuln_scan.RUFF_FIX.get(c, "直す")))
    return out


def main() -> int:
    if os.environ.get("CODE_CHECK_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    if d.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        return 0
    path = Path(str((d.get("tool_input") or {}).get("file_path") or ""))
    if not path.name or path.suffix.lower() not in EXT or not path.is_file():
        return 0
    rel = path.name
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    if "code-check: ignore-file" in text[:4000]:
        return 0
    findings = syntax(path, text)
    test = is_test(str(path))
    if shutil.which("ruff") and path.suffix == ".py":
        findings += ruff_s(path) if not test else []
    else:
        findings += [f for f in vuln_scan.scan_text(rel, text, test) if f.sev == "重大"]
    findings += [f for f in info_scan.scan_text(rel, text, test) if f.sev == "重大"]
    findings = [f for f in findings if f.sev == "重大"]
    if not findings:
        return 0
    shown = "／".join(f"{f.line}行 {f.id} {f.what}" for f in findings[:3])
    more = f"（ほか {len(findings) - 3} 件）" if len(findings) > 3 else ""
    print((f"code-check: {path.name}: 重大 {len(findings)} 件。{shown}{more}。直してから出す（code-check の references/patterns.md）")[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
