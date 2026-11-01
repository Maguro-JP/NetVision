#!/usr/bin/env python3
"""依存とツールの棚卸し。手元のリポジトリから、何をどの版で使っているかを 1 つの一覧にする。

脆弱性のニュースは「どの部品のどの版か」が分からないと自分の事か判断できない。先に棚卸しを機械で作り、
scan.py がそれを OSV などと突き合わせる。見るもの: package-lock.json / package.json、requirements*.txt、
pyproject.toml、poetry.lock、Pipfile.lock、go.mod、Cargo.lock、Dockerfile の FROM、.github/workflows の uses。
node_modules や .cache などの中は見ない（自分の依存ではなく、取り込んだ物の中身なので）。

使い方: inventory.py <ルート>... [--json]   ルートはリポジトリの親でも、リポジトリそのものでもよい
出力: 1 行 1 件の JSON（repo, file, ecosystem, name, version, exact, dev）。version が無いものは「版が決まっていない」
"""

import json
import os
import re
import sys
import tomllib
from pathlib import Path

SKIP_DIRS = {"node_modules", ".cache", ".venv", "venv", "dist", "build", ".git", "__pycache__", ".tox", "site-packages", ".next", "target"}
NAMES = {"package-lock.json", "package.json", "pyproject.toml", "poetry.lock", "Pipfile.lock", "go.mod", "Cargo.lock", "Dockerfile"}
REQ = re.compile(r"^requirements[\w.-]*\.txt$")
PEP508 = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(?:\(([^)]*)\)|([<>=!~][^;#]*))?")
NPM_EXACT = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w.-]+)?$")


def repo_of(path: Path, roots):
    """ファイルがどのリポジトリの物か。.git のある最も近い親の名前。"""
    for p in [path.parent, *path.parents]:
        if (p / ".git").exists():
            return p.name
    return path.parent.name


def find_manifests(roots):
    for root in roots:
        root = Path(root)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            d = Path(dirpath)
            for f in filenames:
                if f in NAMES or REQ.match(f) or f.startswith("Dockerfile."):
                    yield d / f
            if d.name == "workflows" and d.parent.name == ".github":
                for f in filenames:
                    if f.endswith((".yml", ".yaml")):
                        yield d / f


def spec_version(spec: str):
    """">=1.2" "^1.2.3" "~=2.0" "==1.0" のような指定から版を 1 つ取る。exact は == か素の版のときだけ。"""
    spec = (spec or "").strip().strip(",")
    if not spec or spec in ("*", "latest") or "||" in spec or " - " in spec or spec.lower().startswith(("http", "git", "file")):
        return None, False
    m = re.match(r"^(==|===|>=|<=|~=|~|\^|>|<|=|!=)?\s*v?([0-9][\w.+-]*)", spec)
    if not m:
        return None, False
    op, ver = m.group(1), m.group(2)
    if "x" in ver.split(".") or "*" in ver:
        return None, False
    return ver, op in (None, "==", "===", "=")


def row(repo, file, eco, name, version, exact, dev=False):
    return {"repo": repo, "file": str(file), "ecosystem": eco, "name": name, "version": version, "exact": bool(exact and version), "dev": dev}


def parse_package_lock(path: Path, repo):
    d = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    out = []
    if "packages" in d:  # lockfileVersion 2 / 3
        for key, info in d["packages"].items():
            if not key or "node_modules/" not in key or not info.get("version"):
                continue
            name = key.rsplit("node_modules/", 1)[1]
            if info.get("link"):
                continue
            out.append(row(repo, path, "npm", name, info["version"], True, bool(info.get("dev"))))
    else:  # lockfileVersion 1
        def walk(deps, dev=False):
            for name, info in (deps or {}).items():
                if info.get("version"):
                    out.append(row(repo, path, "npm", name, info["version"], True, bool(info.get("dev", dev))))
                walk(info.get("dependencies"), dev)
        walk(d.get("dependencies"))
    return out


def parse_package_json(path: Path, repo):
    d = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    out = []
    for key, dev in (("dependencies", False), ("devDependencies", True), ("optionalDependencies", False)):
        for name, spec in (d.get(key) or {}).items():
            ver, exact = spec_version(str(spec))
            out.append(row(repo, path, "npm", name, ver, exact and bool(NPM_EXACT.match(ver or "")), dev))
    return out


def parse_requirements(path: Path, repo):
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "--")):
            continue
        m = PEP508.match(line)
        if not m:
            continue
        ver, exact = spec_version(m.group(3) or m.group(4) or "")
        out.append(row(repo, path, "PyPI", m.group(1).lower(), ver, exact))
    return out


def parse_pyproject(path: Path, repo):
    d = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    out = []
    for dep in (d.get("project") or {}).get("dependencies") or []:
        m = PEP508.match(dep)
        if m:
            ver, exact = spec_version(m.group(3) or m.group(4) or "")
            out.append(row(repo, path, "PyPI", m.group(1).lower(), ver, exact))
    for group in ((d.get("project") or {}).get("optional-dependencies") or {}).values():
        for dep in group:
            m = PEP508.match(dep)
            if m:
                ver, exact = spec_version(m.group(3) or m.group(4) or "")
                out.append(row(repo, path, "PyPI", m.group(1).lower(), ver, exact, True))
    poetry = ((d.get("tool") or {}).get("poetry") or {})
    for key, dev in (("dependencies", False), ("dev-dependencies", True)):
        for name, spec in (poetry.get(key) or {}).items():
            if name.lower() == "python":
                continue
            s = spec.get("version", "") if isinstance(spec, dict) else str(spec)
            ver, exact = spec_version(s)
            out.append(row(repo, path, "PyPI", name.lower(), ver, exact, dev))
    return out


def parse_poetry_lock(path: Path, repo):
    d = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    return [row(repo, path, "PyPI", p["name"].lower(), p.get("version"), True) for p in d.get("package", []) if p.get("name")]


def parse_pipfile_lock(path: Path, repo):
    d = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    out = []
    for key, dev in (("default", False), ("develop", True)):
        for name, info in (d.get(key) or {}).items():
            ver, exact = spec_version(str(info.get("version", "")))
            out.append(row(repo, path, "PyPI", name.lower(), ver, exact, dev))
    return out


def parse_go_mod(path: Path, repo):
    out = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"^\s*(?:require\s+)?([\w./~-]+\.[\w./~-]+)\s+v([0-9][\w.+-]*)", text, re.M):
        out.append(row(repo, path, "Go", m.group(1), m.group(2), True))
    return out


def parse_cargo_lock(path: Path, repo):
    d = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    return [row(repo, path, "crates.io", p["name"], p.get("version"), True) for p in d.get("package", []) if p.get("name")]


def parse_dockerfile(path: Path, repo):
    out = []
    for m in re.finditer(r"^\s*FROM\s+(?:--platform=\S+\s+)?([\w./:@-]+)", path.read_text(encoding="utf-8", errors="replace"), re.M | re.I):
        image = m.group(1)
        if image.lower() == "scratch":
            continue
        name, _, tag = image.partition(":")
        out.append(row(repo, path, "Docker", name, tag or None, bool(tag and tag != "latest")))
    return out


def parse_workflow(path: Path, repo):
    out = []
    for m in re.finditer(r"^\s*-?\s*uses:\s*['\"]?([\w.-]+/[\w.-]+)(?:/[\w./-]+)?@([\w.-]+)", path.read_text(encoding="utf-8", errors="replace"), re.M):
        ref = m.group(2)
        out.append(row(repo, path, "GitHub Actions", m.group(1), ref, bool(re.fullmatch(r"[0-9a-f]{40}", ref))))
    return out


PARSERS = [
    (lambda p: p.name == "package-lock.json", parse_package_lock),
    (lambda p: p.name == "package.json", parse_package_json),
    (lambda p: bool(REQ.match(p.name)), parse_requirements),
    (lambda p: p.name == "pyproject.toml", parse_pyproject),
    (lambda p: p.name == "poetry.lock", parse_poetry_lock),
    (lambda p: p.name == "Pipfile.lock", parse_pipfile_lock),
    (lambda p: p.name == "go.mod", parse_go_mod),
    (lambda p: p.name == "Cargo.lock", parse_cargo_lock),
    (lambda p: p.name == "Dockerfile" or p.name.startswith("Dockerfile."), parse_dockerfile),
    (lambda p: p.parent.name == "workflows", parse_workflow),
]


def inventory(roots):
    rows = []
    seen_lock_dirs = set()
    files = sorted(set(find_manifests(roots)))
    for f in files:
        if f.name == "package-lock.json":
            seen_lock_dirs.add(f.parent)
    for f in files:
        if f.name == "package.json" and f.parent in seen_lock_dirs:
            continue  # lock がある所では lock を正とする
        repo = repo_of(f, roots)
        for match, parser in PARSERS:
            if match(f):
                try:
                    rows.extend(parser(f, repo))
                except Exception as e:  # 1 つの壊れたファイルで全部を止めない
                    rows.append({"repo": repo, "file": str(f), "ecosystem": "?", "name": f"(読めない: {e.__class__.__name__})", "version": None, "exact": False, "dev": False})
                break
    return rows


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rows = inventory(args or ["."])
    if "--json" in sys.argv:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
    else:
        by = {}
        for r in rows:
            by.setdefault((r["repo"], r["ecosystem"]), []).append(r)
        for (repo, eco), rs in sorted(by.items()):
            loose = sum(1 for r in rs if not r["exact"])
            print(f"{repo:24} {eco:15} {len(rs):4} 件（版が決まっていない {loose}）")
        print(f"合計 {len(rows)} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
