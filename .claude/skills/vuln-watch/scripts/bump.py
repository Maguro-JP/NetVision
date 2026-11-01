#!/usr/bin/env python3
"""依存の版を、直す版に上げる。scan.py の報告の「直す版」をそのまま当てるための道具。

使い方: bump.py <リポジトリの場所> <種類> <部品> <直す版>
  種類: PyPI（requirements*.txt の == と pyproject.toml の下限を書き換える）、npm（lock があれば npm install --package-lock-only、
  無ければ package.json の指定を書き換える）、GitHub Actions（.github/workflows の uses の @ の後ろを書き換える）
書き換えたファイルを 1 行ずつ出す。テストと PR はこの道具の外（solo-pr-flow）。
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inventory import SKIP_DIRS  # noqa: E402


def files(root: Path, pred):
    for p in root.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and pred(p):
            yield p


def bump_pypi(root: Path, name: str, ver: str):
    changed = []
    pat = re.compile(rf"^(\s*{re.escape(name)}(?:\[[^\]]*\])?\s*)(==|>=|~=)\s*[0-9][\w.+-]*", re.I | re.M)
    for p in files(root, lambda p: re.fullmatch(r"requirements[\w.-]*\.txt", p.name) or p.name == "pyproject.toml"):
        s = p.read_text(encoding="utf-8")
        if p.name == "pyproject.toml":
            new = re.sub(rf'("{re.escape(name)}(?:\[[^\]]*\])?\s*)(==|>=|~=)\s*[0-9][\w.+-]*', lambda m: f"{m.group(1)}{m.group(2)}{ver}", s, flags=re.I)
        else:
            new = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}{ver}", s)
        if new != s:
            p.write_text(new, encoding="utf-8")
            changed.append(p)
    return changed


def bump_npm(root: Path, name: str, ver: str):
    changed = []
    for p in files(root, lambda p: p.name == "package.json"):
        s = p.read_text(encoding="utf-8")
        if f'"{name}"' not in s:
            continue
        if (p.parent / "package-lock.json").exists() and shutil.which("npm"):
            r = subprocess.run(["npm", "install", f"{name}@{ver}", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=p.parent, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"npm が失敗: {r.stderr.strip()[-200:]}", file=sys.stderr)
                continue
            changed += [p, p.parent / "package-lock.json"]
        else:
            new = re.sub(rf'("{re.escape(name)}"\s*:\s*")([\^~]?)[^"]*(")', lambda m: f"{m.group(1)}{m.group(2)}{ver}{m.group(3)}", s)
            if new != s:
                p.write_text(new, encoding="utf-8")
                changed.append(p)
    return changed


def bump_action(root: Path, name: str, ver: str):
    changed = []
    for p in files(root, lambda p: p.parent.name == "workflows" and p.suffix in (".yml", ".yaml")):
        s = p.read_text(encoding="utf-8")
        new = re.sub(rf"(uses:\s*['\"]?{re.escape(name)}(?:/[\w./-]+)?@)[\w.-]+", lambda m: m.group(1) + ver, s)
        if new != s:
            p.write_text(new, encoding="utf-8")
            changed.append(p)
    return changed


def main() -> int:
    if len(sys.argv) != 5:
        print(__doc__)
        return 2
    root, eco, name, ver = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
    fn = {"PyPI": bump_pypi, "npm": bump_npm, "GitHub Actions": bump_action}.get(eco)
    if not fn:
        print(f"この種類は書き換えられない: {eco}")
        return 2
    changed = fn(root, name, ver)
    for p in changed:
        print(p)
    print(f"書き換えたファイル {len(set(changed))}")
    return 0 if changed else 1


if __name__ == "__main__":
    sys.exit(main())
