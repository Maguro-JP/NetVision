#!/usr/bin/env python3
"""code-check の 3 つのスクリプト（crash_check、vuln_scan、info_scan）とフックが共有する部品。

対象の集め方（追跡しているファイル、差分のファイル、飛ばす場所）、抑止の印、検出の形、出力の形をここに置く。
3 本で別々に持つと、飛ばす場所や印の扱いが食い違い、片方だけ通る穴ができるため。
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {"node_modules", ".venv", "venv", ".env", "env", "dist", "build", "vendor", "__pycache__", ".git",
             ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".next", "coverage", "site-packages"}
CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".bash"}
IGNORE_LINE = "code-check: ignore"
IGNORE_FILE = "code-check: ignore-file"
SEV_ORDER = {"重大": 0, "注意": 1, "情報": 2}
# 配った先にスキルが無いとき、Ciel の clone の物を使う（repo_check.py と同じ決まり）
CIEL_SKILLS = Path(os.environ.get("CIEL_ROOT", "/home/user/Ciel")) / ".claude" / "skills"


@dataclass
class Finding:
    sev: str      # 重大 / 注意 / 情報
    path: str     # root からの相対パス（無い物は ""）
    line: int     # 0 は行が無い物
    id: str       # 型の ID（V01 など）
    what: str     # 何が見つかったか
    fix: str      # 直し方

    def text(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else (self.path or "-")
        return f"{self.sev}  {loc}  {self.id} {self.what}｜直し方: {self.fix}"

    def as_dict(self) -> dict:
        return {"sev": self.sev, "path": self.path, "line": self.line, "id": self.id, "what": self.what, "fix": self.fix}


def run(cmd, cwd, timeout=300, env=None):
    e = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    if env:
        e.update(env)
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=e)
        return r.returncode, (r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError as ex:
        return 127, str(ex)


def git(root, *args) -> str:
    code, out = run(["git", *args], root, timeout=60)
    return out.strip() if code == 0 else ""


def relpath(root: Path, p) -> str:
    """ツールが返すパス（./x、絶対、root からの相対）を root からの相対にそろえる。"""
    q = Path(str(p))
    q = q if q.is_absolute() else root / q
    try:
        return str(q.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/").removeprefix("./")


def is_skipped(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return True
    if rel.endswith(".min.js") or rel.endswith(".min.css"):
        return True
    if "code-check/references/" in rel:
        return True
    return False


def is_test(rel: str) -> bool:
    """テストのファイル。中に悪い例を書くので、型の検査は飛ばす（構文の検査は飛ばさない）。"""
    name = rel.replace("\\", "/").split("/")[-1]
    parts = rel.replace("\\", "/").split("/")
    if any(p in ("tests", "test", "__tests__", "spec", "fixtures") for p in parts[:-1]):
        return True
    return bool(re.match(r"test_.*\.py$|.*_test\.py$|.*\.(test|spec)\.[jt]sx?$|conftest\.py$", name))


def tracked(root: Path) -> list:
    """追跡しているファイル（git ls-files）。git が無ければ歩く。"""
    out = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")  # 追跡している物と、まだ足していない新しい物
    if out:
        return sorted(p for p in set(out.split("\0")) if p and not is_skipped(p) and (root / p).is_file())
    files = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for f in fns:
            rel = os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/")
            if not is_skipped(rel):
                files.append(rel)
    return sorted(files)


def changed(root: Path, base: str) -> list:
    """base から変わったファイル（コミット済みと作業ツリーの両方）と、追跡していない新しいファイル。"""
    names = set()
    out = git(root, "diff", "--name-only", "--diff-filter=ACMR", base)
    names.update(l for l in out.splitlines() if l.strip())
    out = git(root, "ls-files", "--others", "--exclude-standard")
    names.update(l for l in out.splitlines() if l.strip())
    return sorted(p for p in names if not is_skipped(p) and (root / p).is_file())


def changed_lines(root: Path, base: str) -> dict:
    """base から変わった行。{相対パス: 行番号の集合}。追跡していない新しいファイルは入らない（全部の行が新しい）。"""
    out, cur = {}, None
    for line in git(root, "diff", "-U0", "--diff-filter=ACMR", base).splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            cur = p[2:] if p.startswith("b/") else None
            if cur:
                out.setdefault(cur, set())
        elif line.startswith("@@") and cur:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start, n = int(m.group(1)), int(m.group(2) or 1)
                out[cur].update(range(start, start + n))
    return out


def scope_to_diff(findings, root: Path, base: str) -> list:
    """差分の検査では、変えた行の重大だけを重大のままにし、前からある行の重大は注意に落とす。
    触っていない古い行で PR を止めると、直しを出す作業まで止まるため。古い行は木全体の検査（月に 1 回、公開の前）で見る。
    構文の誤り（C01）と、行の無い物（ファイル全体の話）はそのまま。"""
    lines = changed_lines(root, base)
    for f in findings:
        if f.sev != "重大" or not f.line or f.id == "C01":
            continue
        if f.path in lines and f.line not in lines[f.path]:
            f.sev = "注意"
            f.what += "（前からある行）"
    return findings


def code_files(files, exts=CODE_EXT) -> list:
    return [f for f in files if Path(f).suffix.lower() in exts]


def read_text(path: Path):
    """文字として読めなければ None（画像や zip を正規表現に当てない）。"""
    try:
        b = path.read_bytes()
    except OSError:
        return None
    if b"\0" in b[:4096]:
        return None
    return b.decode("utf-8", errors="replace")


def file_ignored(text: str) -> bool:
    return IGNORE_FILE in text[:4000]


def line_ignored(line: str) -> bool:
    return IGNORE_LINE in line


def strip_strings(line: str) -> str:
    """文字列リテラルの中身を落とし、補間（{x}、${x}）だけ残す。
    print("Enter password:") のような案内の文を、値を出す行と区別するため。"""
    out, i, n = [], 0, len(line)
    while i < n:
        c = line[i]
        if c in "\"'`":
            q = c
            j = i + 1
            while j < n and line[j] != q:
                if line[j] == "\\":
                    j += 1
                j += 1
            body = line[i + 1:j]
            keep = re.findall(r"\$?\{[^{}]*\}", body)
            out.append(q + " ".join(keep) + q)
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def near(lines, i, pattern, span=3) -> bool:
    lo, hi = max(0, i - span), min(len(lines), i + span + 1)
    return any(re.search(pattern, lines[k], re.I) for k in range(lo, hi))


def dedupe(findings) -> list:
    """同じ場所に同じ型が 2 回出たら 1 つに畳み、重い順に並べる。"""
    seen, out = {}, []
    for f in findings:
        k = (f.path, f.line) if f.line else (f.path, f.line, f.id)
        if k in seen:
            first = seen[k]
            if f.id not in first.what and f.id != first.id:
                first.what += f"＋{f.id}"  # 他のツールも同じ行を見つけた
            if SEV_ORDER.get(f.sev, 9) < SEV_ORDER.get(first.sev, 9):
                first.sev = f.sev
            continue
        seen[k] = f
        out.append(f)
    out.sort(key=lambda f: (SEV_ORDER.get(f.sev, 9), f.path, f.line))
    return out


def counts(findings) -> dict:
    return {k: sum(1 for f in findings if f.sev == k) for k in ("重大", "注意", "情報")}


def summary(label: str, findings, with_info=True) -> str:
    c = counts(findings)
    s = f"{label}: 重大 {c['重大']} / 注意 {c['注意']}"
    if with_info:
        s += f" / 情報 {c['情報']}"
    return s


def report(label: str, findings, json_mode: bool, with_info=True, extra=None) -> int:
    """検出を 1 行ずつ出し、最後に要約を 1 行。重大があれば 1。"""
    findings = dedupe(findings)
    if json_mode:
        print(json.dumps({"label": label, "findings": [f.as_dict() for f in findings], "counts": counts(findings),
                          "summary": summary(label, findings, with_info), "extra": extra or {}}, ensure_ascii=False, indent=1))
    else:
        print(f"== code-check: {label} ==")
        if extra and extra.get("tools"):
            print("  " + extra["tools"])
        for f in findings:
            print("  " + f.text())
        if not findings:
            print("  検出なし")
        print(summary(label, findings, with_info))
    return 1 if counts(findings)["重大"] else 0


def parse_args(doc: str, extra=None):
    import argparse
    ap = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--diff", metavar="BASE", help="base から変わったファイルだけ見る")
    ap.add_argument("--json", action="store_true")
    for args, kw in (extra or []):
        ap.add_argument(*args, **kw)
    a = ap.parse_args()
    a.root = Path(a.root).resolve()
    return a


def target_files(root: Path, base) -> list:
    return changed(root, base) if base else tracked(root)
