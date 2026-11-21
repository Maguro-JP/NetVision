#!/usr/bin/env python3
"""code-check が使う、実績のある検査のツールの一覧。あるかを調べ、無ければ入れる。

ユーザーの決まり: Claude だけで済ませず、便利なツールを使う。検査は実績のあるツールを先に入れて使い、
自分の正規表現は無いときの補いにする。自前の検査だけに頼ると、知られた穴の型を取りこぼすため。

使い方: tools.py [--install] [--json] [--only ruff,bandit]
環境変数: CODE_CHECK_NO_TOOLS=1  ツールを全部無い物として扱う（正規表現の補いだけで動かすとき）
  何も付けなければ、あるものと無いものを 1 行ずつ出す
  --install は無いものを pip（--user）と npm（-g）で入れる。sudo は使わない。入らなかったものは理由を出す
環境変数: CODE_CHECK_NO_INSTALL=1  --install を無効にする（配った先で勝手に入れたくないとき）
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 名前、何を見るか、入れ方、実行ファイル、版を出す引数、ライセンス、使うスクリプト
TOOLS = [
    {"name": "ruff", "cover": "Python の構文と未定義と不具合（E9、F、B）、脆弱性の型（S）", "kind": "pip", "pkg": "ruff", "exe": "ruff",
     "ver": ["--version"], "license": "MIT", "used": "crash_check、vuln_scan、code_nudge"},
    {"name": "bandit", "cover": "Python の脆弱性の型（B 番号）", "kind": "pip", "pkg": "bandit", "exe": "bandit",
     "ver": ["--version"], "license": "Apache-2.0", "used": "vuln_scan"},
    {"name": "semgrep", "cover": "Python と JS/TS の脆弱性の型（規則は registry、無ければ同梱の references/semgrep-rules.yml）", "kind": "pip",
     "pkg": "semgrep", "exe": "semgrep", "ver": ["--version"], "license": "LGPL-2.1", "used": "vuln_scan"},
    {"name": "shellcheck", "cover": "シェルの不具合（引用の抜け、未定義、危ない形）", "kind": "pip", "pkg": "shellcheck-py", "exe": "shellcheck",
     "ver": ["--version"], "license": "GPL-3.0（外部のツールとして呼ぶだけ。取り込まない）", "used": "crash_check、code_nudge"},
    {"name": "detect-secrets", "cover": "鍵の漏洩（作業ツリー）", "kind": "pip", "pkg": "detect-secrets", "exe": "detect-secrets",
     "ver": ["--version"], "license": "Apache-2.0", "used": "info_scan"},
    {"name": "gitleaks", "cover": "鍵の漏洩（作業ツリーと履歴）", "kind": "binary", "pkg": "gitleaks/gitleaks（GitHub Releases）", "exe": "gitleaks",
     "ver": ["version"], "license": "MIT", "used": "info_scan"},
    {"name": "pip-audit", "cover": "Python の依存の脆弱性（PyPI の advisory）", "kind": "pip", "pkg": "pip-audit", "exe": "pip-audit",
     "ver": ["--version"], "license": "Apache-2.0", "used": "vuln_scan（依存の行。週 1 回の本番は vuln-watch）"},
    {"name": "npm", "cover": "JS の依存の脆弱性（npm audit）", "kind": "system", "pkg": "Node.js に同梱", "exe": "npm",
     "ver": ["--version"], "license": "Artistic-2.0", "used": "vuln_scan"},
    {"name": "node", "cover": "JS の構文（node --check）", "kind": "system", "pkg": "Node.js", "exe": "node",
     "ver": ["--version"], "license": "MIT", "used": "crash_check、code_nudge"},
    {"name": "eslint", "cover": "JS/TS の不具合（リポジトリに設定があるとき）", "kind": "npm", "pkg": "eslint", "exe": "eslint",
     "ver": ["--version"], "license": "MIT", "used": "crash_check"},
    {"name": "tsc", "cover": "TypeScript の型（tsconfig.json があるとき）", "kind": "npm", "pkg": "typescript", "exe": "tsc",
     "ver": ["--version"], "license": "Apache-2.0", "used": "crash_check"},
    {"name": "pytest", "cover": "Python のテスト", "kind": "pip", "pkg": "pytest", "exe": "pytest",
     "ver": ["--version"], "license": "MIT", "used": "crash_check"},
    {"name": "markdownlint-cli2", "cover": "README と docs の書式", "kind": "npm", "pkg": "markdownlint-cli2", "exe": "markdownlint-cli2",
     "ver": ["--version"], "license": "MIT", "used": "readme-showcase の readme_check"},
    {"name": "markdown-link-check", "cover": "README と docs のリンク切れ（外のリンクも）", "kind": "npm", "pkg": "markdown-link-check",
     "exe": "markdown-link-check", "ver": ["--version"], "license": "ISC", "used": "readme-showcase の readme_check"},
]
INSTALL_CMD = "python3 .claude/skills/code-check/scripts/tools.py --install"


def _extra_paths():
    paths = [str(Path.home() / ".local" / "bin")]
    try:
        r = subprocess.run(["npm", "prefix", "-g"], capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            paths.append(str(Path(r.stdout.strip()) / "bin"))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return paths


def ensure_path():
    """~/.local/bin と npm の global の bin を PATH に足す（pip --user と npm -g で入れた物を見つけるため）。"""
    cur = os.environ.get("PATH", "")
    for p in _extra_paths():
        if p not in cur.split(os.pathsep) and Path(p).is_dir():
            cur = p + os.pathsep + cur
    os.environ["PATH"] = cur


ensure_path()


def find(name: str):
    """実行ファイルの場所。一覧に無い名前はそのまま探す。CODE_CHECK_NO_TOOLS=1 なら全部無し（補いの経路を確かめるとき）。"""
    if os.environ.get("CODE_CHECK_NO_TOOLS") == "1":
        return None
    t = next((t for t in TOOLS if t["name"] == name), None)
    return shutil.which(t["exe"] if t else name)


def version(name: str) -> str:
    t = next((t for t in TOOLS if t["name"] == name), None)
    exe = find(name)
    if not t or not exe:
        return ""
    try:
        r = subprocess.run([exe, *t["ver"]], capture_output=True, text=True, timeout=30)
        line = (r.stdout + r.stderr).strip().splitlines()
        return (line[0] if line else "").replace(t["exe"], "").strip()[:30]
    except (OSError, subprocess.TimeoutExpired):
        return "?"


def present() -> dict:
    """ある物の名前と実行ファイルの対応。"""
    return {t["name"]: shutil.which(t["exe"]) for t in TOOLS if shutil.which(t["exe"])}


def tools_line(used, missing) -> str:
    """検査の出力の先頭に置く 1 行。何で見たか、何が無いかを隠さない。"""
    u = "、".join(used) if used else "無し"
    m = ("、".join(missing) + f"（入れ方: {INSTALL_CMD}）") if missing else "無し"
    return f"ツール: 使った {u} ／ 無し {m}"


def install(names=None) -> list:
    """無い物を入れる。(名前, 入った/入らなかった, 理由) の列。"""
    out = []
    want = [t for t in TOOLS if (not names or t["name"] in names) and not shutil.which(t["exe"])]
    if os.environ.get("CODE_CHECK_NO_INSTALL") == "1":
        return [(t["name"], False, "CODE_CHECK_NO_INSTALL=1") for t in want]
    pip = [t for t in want if t["kind"] == "pip"]
    npm = [t for t in want if t["kind"] == "npm"]
    if pip:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "-q", *[t["pkg"] for t in pip]],
                           capture_output=True, text=True, timeout=900)
        for t in pip:
            ok = bool(shutil.which(t["exe"]))
            out.append((t["name"], ok, "" if ok else (r.stderr.strip().splitlines() or ["pip が失敗"])[-1][:120]))
    if npm and shutil.which("npm"):
        r = subprocess.run(["npm", "install", "-g", "--silent", *[t["pkg"] for t in npm]], capture_output=True, text=True, timeout=900)
        ensure_path()
        for t in npm:
            ok = bool(shutil.which(t["exe"]))
            out.append((t["name"], ok, "" if ok else (r.stderr.strip().splitlines() or ["npm が失敗"])[-1][:120]))
    elif npm:
        out += [(t["name"], False, "npm が無い") for t in npm]
    for t in want:
        if t["kind"] == "binary" and t["name"] == "gitleaks":
            out.append(("gitleaks", _install_gitleaks(), ""))
        elif t["kind"] == "system":
            out.append((t["name"], False, f"{t['pkg']} を入れる（OS のパッケージ）"))
    return out


def _install_gitleaks() -> bool:
    """GitHub Releases の tar.gz を ~/.local/bin に置く。proxy で取れなければ諦める（detect-secrets が代わりになる）。"""
    import tarfile
    import tempfile
    import urllib.request
    ver = "8.24.3"
    url = f"https://github.com/gitleaks/gitleaks/releases/download/v{ver}/gitleaks_{ver}_linux_x64.tar.gz"
    dst = Path.home() / ".local" / "bin"
    dst.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "g.tgz"
            urllib.request.urlretrieve(url, p)  # code-check: ignore https の固定の URL
            with tarfile.open(p) as tf:
                m = next((m for m in tf.getmembers() if m.name == "gitleaks"), None)
                if not m:
                    return False
                tf.extract(m, d, filter="data")
            (Path(d) / "gitleaks").replace(dst / "gitleaks")
            (dst / "gitleaks").chmod(0o755)
        return bool(shutil.which("gitleaks"))
    except Exception:  # noqa: BLE001  取れない理由は何でもよい。無ければ無しと出す
        return False


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", metavar="NAMES", help="名前をコンマで")
    a = ap.parse_args()
    names = [n.strip() for n in a.only.split(",")] if a.only else None
    results = install(names) if a.install else []
    rows = []
    for t in TOOLS:
        if names and t["name"] not in names:
            continue
        exe = shutil.which(t["exe"])
        rows.append({"name": t["name"], "present": bool(exe), "version": version(t["name"]) if exe else "", "cover": t["cover"],
                     "install": {"pip": f"pip install --user {t['pkg']}", "npm": f"npm install -g {t['pkg']}"}.get(t["kind"], t["pkg"]),
                     "license": t["license"]})
    if a.json:
        print(json.dumps({"tools": rows, "installed": [{"name": n, "ok": ok, "why": w} for n, ok, w in results]}, ensure_ascii=False, indent=1))
        return 0
    for n, ok, w in results:
        print(f"  {'入れた' if ok else '入らなかった'}  {n}" + (f": {w}" if w else ""))
    for r in rows:
        print(f"  {'あり' if r['present'] else '無し'}  {r['name']:<20} {r['version']:<12} {r['cover']}" + ("" if r["present"] else f"（入れ方: {r['install']}）"))
    have = [r["name"] for r in rows if r["present"]]
    print(tools_line(have, [r["name"] for r in rows if not r["present"]]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
