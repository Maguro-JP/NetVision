#!/usr/bin/env python3
"""そのリポジトリが版を持つ場所を探し、修正版のために patch を 1 つ上げる。

致命的なバグの修正版では、版を上げ忘れる・一部だけ上げる、が起きやすい（package.json は上げたのに tauri.conf.json が古い、など）。
版の場所はリポジトリごとに違うので、決まった場所を機械で探し、あった所を全部上げる。どこにも無ければ、何も上げない。

探す場所（根から 3 階層まで。node_modules、.git、仮想環境、ビルドの出力、tests、docs、.claude は見ない）:
  package.json と隣の package-lock.json、pyproject.toml（[project] か [tool.poetry]）、setup.cfg、setup.py、
  __version__ のある Python のファイル、Cargo.toml（[package] か [workspace.package]）と隣の Cargo.lock、tauri.conf.json、VERSION

使い方:
  version.py [--find] [--json]     版のある場所と今の版、上げたあとの版、最後のタグ、リリースの workflow を出す
  version.py --bump                patch を 1 つ上げた形を出す（書かない）
  version.py --bump --apply        書く
  どれも --root DIR でリポジトリの根を指せる（既定は git の根）

版は X.Y.Z の形だけを上げる（Semantic Versioning 2.0.0 の patch）。それ以外の形（1.2.3-beta.1、日付の版）は、注意を出して触らない。
ファイルは、版の文字だけを置き換える。JSON や TOML を書き直すと、並びや字下げが変わって差分が読めなくなる。
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "env", "dist", "build", "target", "out", "__pycache__", ".claude", "tests", "test",
             "docs", "examples", "vendor", "site-packages", ".tox", ".mypy_cache", ".pytest_cache", "coverage", ".next", ".nuxt"}
DEPTH = 3
PLAIN = re.compile(r"^\d+\.\d+\.\d+$")
PY_VERSION = re.compile(r"^(__version__\s*(?::\s*str\s*)?=\s*)([\"'])([^\"'\n]+)\2", re.M)
RELEASE_MARK = re.compile(r"action-gh-release|tauri-action|electron-builder|gh release (?:create|upload)|gh-action-pypi-publish|npm publish|cargo publish|upload-release-asset|softprops", re.I)


def root_of(arg) -> Path:
    if arg:
        return Path(arg).resolve()
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return Path.cwd()


def bump(v: str):
    """patch を 1 つ上げた版。X.Y.Z の形でなければ None。"""
    if not PLAIN.match(v or ""):
        return None
    a, b, c = v.split(".")
    return f"{a}.{b}.{int(c) + 1}"


def files(root: Path):
    """根から DEPTH 階層までのファイル。見ないディレクトリは降りない。"""
    for cur, dirs, names in os.walk(root):
        rel = Path(cur).relative_to(root)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info"))
        if len(rel.parts) >= DEPTH:
            dirs[:] = []
        for n in sorted(names):
            yield Path(cur) / n


def _section_value(text: str, sections, key: str = "version"):
    """TOML や cfg の、指定した節の中の key = "値" の (値, 値の始まり, 値の終わり)。無ければ None。値の引用符は含めない。"""
    inside = False
    pos = 0
    for line in text.splitlines(keepends=True):
        head = re.match(r"\s*\[\[?\s*([^\]]+?)\s*\]?\]", line)
        if head:
            inside = head.group(1) in sections
        elif inside:
            m = re.match(rf"(\s*{re.escape(key)}\s*=\s*)([\"']?)([^\"'\n#]+?)\2\s*(?:#.*)?$", line.rstrip("\n"))
            if m:
                start = pos + len(m.group(1)) + len(m.group(2))
                return m.group(3).strip(), start, start + len(m.group(3))
        pos += len(line)
    return None


def _json_version(text: str, old: str):
    """JSON の文の中の、いちばん外の "version": "old" の値の位置。字下げのいちばん浅い 1 個を取る（依存や入れ子の version に触らない）。"""
    hits = [(len(m.group(1)), m.start(2), m.end(2)) for m in re.finditer(rf'^([ \t]*)"version"[ \t]*:[ \t]*"({re.escape(old)})"', text, re.M)]
    return [(a, z) for _, a, z in sorted(hits)[:1]]


def _lock_version(text: str, old: str):
    """package-lock.json の、自分のパッケージの版の位置。先頭の version と、packages の "" の version。依存の行は取らない。"""
    spans = _json_version(text, old)
    m = re.search(r'"packages"\s*:\s*\{\s*""\s*:\s*\{[^{}]*?"version"\s*:\s*"(' + re.escape(old) + r')"', text)
    if m:
        spans.append((m.start(1), m.end(1)))
    return spans


def places(root: Path) -> list:
    """版のある場所。[{path, version, spans, note}]。spans は、その文の中で版が書いてある位置の列。"""
    out = []

    def add(p: Path, version, spans, note=""):
        out.append({"path": p.relative_to(root).as_posix(), "version": version or "", "spans": spans, "note": note})

    for p in files(root):
        name = p.name
        try:
            if name == "package.json":
                text = p.read_text(encoding="utf-8")
                v = json.loads(text).get("version")
                if isinstance(v, str) and v:
                    add(p, v, _json_version(text, v))
                    lock = p.with_name("package-lock.json")
                    if lock.is_file():
                        ltext = lock.read_text(encoding="utf-8")
                        if json.loads(ltext).get("version") == v:
                            add(lock, v, _lock_version(ltext, v))
            elif name == "pyproject.toml":
                text = p.read_text(encoding="utf-8")
                hit = _section_value(text, ("project", "tool.poetry"))
                if hit:
                    add(p, hit[0], [(hit[1], hit[2])])
            elif name == "setup.cfg":
                text = p.read_text(encoding="utf-8")
                hit = _section_value(text, ("metadata",))
                if hit and not hit[0].startswith(("attr:", "file:")):
                    add(p, hit[0], [(hit[1], hit[2])])
            elif name == "setup.py":
                text = p.read_text(encoding="utf-8")
                m = re.search(r"\bversion\s*=\s*([\"'])(\d[^\"'\n]*)\1", text)
                if m:
                    add(p, m.group(2), [(m.start(2), m.end(2))])
            elif name == "Cargo.toml":
                text = p.read_text(encoding="utf-8")
                hit = _section_value(text, ("package", "workspace.package"))
                if hit and PLAIN.match(hit[0].split("-")[0].split("+")[0]):
                    add(p, hit[0], [(hit[1], hit[2])])
                    crate = _section_value(text, ("package",), "name")
                    for lock in (p.with_name("Cargo.lock"), p.parent.parent / "Cargo.lock"):
                        if crate and lock.is_file():
                            ltext = lock.read_text(encoding="utf-8")
                            m = re.search(rf'^name = "{re.escape(crate[0])}"\nversion = "({re.escape(hit[0])})"', ltext, re.M)
                            if m:
                                add(lock, hit[0], [(m.start(1), m.end(1))])
                            break
            elif name in ("tauri.conf.json", "tauri.conf.json5"):
                text = p.read_text(encoding="utf-8")
                # v2 は先頭、v1 は package の中。ほかの入れ子の version と区別するため、字下げのいちばん浅い物を取る
                found = sorted(re.finditer(r'^([ \t]*)"version"[ \t]*:[ \t]*"([^"\n]+)"', text, re.M), key=lambda x: len(x.group(1)))
                m = found[0] if found else None
                if m and PLAIN.match(m.group(2).split("-")[0]):
                    add(p, m.group(2), [(m.start(2), m.end(2))])
                elif m:
                    add(p, m.group(2), [], "別のファイルの版を指している")
            elif name == "VERSION":
                text = p.read_text(encoding="utf-8")
                v = text.strip()
                if v and "\n" not in v and re.match(r"^v?\d+\.\d+", v):
                    start = text.index(v) + (1 if v.startswith("v") else 0)
                    add(p, v.lstrip("v"), [(start, text.index(v) + len(v))])
            elif name.endswith(".py") and name in ("__init__.py", "__version__.py", "_version.py", "version.py", "__about__.py"):
                text = p.read_text(encoding="utf-8")
                m = PY_VERSION.search(text)
                if m:
                    add(p, m.group(3), [(m.start(3), m.end(3))])
        except (OSError, ValueError):
            continue  # 読めない・壊れたファイルは飛ばす（版の場所として数えない）
    return out


def plan(root: Path) -> list:
    """場所ごとの、上げたあとの版。上げられない場所は new が空で、note に理由。"""
    rows = places(root)
    for r in rows:
        new = bump(r["version"])
        r["new"] = new if (new and r["spans"]) else ""
        if not r["new"] and not r["note"]:
            r["note"] = "X.Y.Z の形ではないので触らない" if not new else "版の文字の位置が分からない"
    return rows


def apply(root: Path, rows: list) -> int:
    """上げられる場所を書き換える。同じファイルに 2 行あるとき（workspace の Cargo.lock）も、後ろから置き換えて位置をずらさない。"""
    by_path = {}
    for r in rows:
        if r["new"]:
            by_path.setdefault(r["path"], []).extend((a, z, r["version"], r["new"]) for a, z in r["spans"])
    for path, spans in by_path.items():
        p = root / path
        text = p.read_text(encoding="utf-8")
        for a, z, old, new in sorted(set(spans), reverse=True):
            if text[a:z] != old:
                raise ValueError(f"{path}: 版の位置が読んだときと違う")
            text = text[:a] + new + text[z:]
        p.write_text(text, encoding="utf-8")
    return len(by_path)


def last_tag(root: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), "tag", "--list", "--sort=-v:refname"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    for t in r.stdout.splitlines() if r.returncode == 0 else []:
        if re.match(r"^v?\d+\.\d+\.\d+$", t.strip()):
            return t.strip()
    return ""


def release_workflows(root: Path) -> list:
    """リリースの workflow と、動くきっかけ。[(パス, きっかけ)]"""
    out = []
    d = root / ".github" / "workflows"
    for p in sorted(list(d.glob("*.yml")) + list(d.glob("*.yaml"))) if d.is_dir() else []:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if not (re.search(r"release|publish|dist", p.stem, re.I) or RELEASE_MARK.search(text)):
            continue
        how = []
        if re.search(r"^\s*tags\s*:", text, re.M):
            how.append("タグの push で動く")
        if re.search(r"^\s*release\s*:", text, re.M):
            how.append("Release の公開で動く")
        if re.search(r"^\s*workflow_dispatch\s*:?", text, re.M):
            how.append("手で動かせる")
        out.append((p.relative_to(root).as_posix(), "、".join(how) or "きっかけは読み取れない"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--find", action="store_true")
    ap.add_argument("--bump", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root")
    a = ap.parse_args()
    root = root_of(a.root)
    rows = plan(root)
    tag = last_tag(root)
    flows = release_workflows(root)
    news = sorted({r["new"] for r in rows if r["new"]})
    next_tag = (("v" if tag.startswith("v") or not tag else "") + news[-1]) if news else ""
    if a.json:
        print(json.dumps({"places": [{k: r[k] for k in ("path", "version", "new", "note")} for r in rows], "last_tag": tag, "next_tag": next_tag,
                          "release_workflows": [{"path": p, "trigger": h} for p, h in flows]}, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("版のある場所は見つかりません。版は上げません（タグも、今までのタグが無ければ付けません）")
    else:
        print("版のある場所:")
        width = max(len(r["path"]) for r in rows)
        for r in rows:
            print(f"  {r['path']:<{width}}  {r['version']}" + (f" → {r['new']}" if r["new"] else "") + (f"  注意: {r['note']}" if r["note"] else ""))
        olds = sorted({r["version"] for r in rows})
        if len(olds) > 1:
            print(f"注意: 版が場所によって違います（{'、'.join(olds)}）。それぞれを 1 つずつ上げます。そろえるかは、上げる前に決めます")
    print(f"最後のタグ: {tag or '無し'}" + (f"（次のタグの形: {next_tag}）" if next_tag and tag else ""))
    for p, how in flows:
        print(f"リリースの workflow: {p}（{how}）")
    if not flows:
        print("リリースの workflow: 無し（Release は、マージしたあとの main ブランチに手で作ります）")
    if a.bump and a.apply:
        n = apply(root, rows)
        print(f"{n} か所の版を上げました" if n else "上げる場所がありません")
    elif a.bump:
        print("書いていません。書くときは --bump --apply を付けます")
    return 0


if __name__ == "__main__":
    sys.exit(main())
