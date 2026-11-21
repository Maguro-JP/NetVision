#!/usr/bin/env python3
"""差分から、消えた関数・クラス・export・テスト・ファイル・設定・UI の要素・ルートを一覧にする。

ユーザーの指摘: わかっていてわざと壊す。
直している途中で、要る物を黙って消す。消した本人は、差分を読んでも「足した物」しか見ない。だから消えた名前を機械で数える。
頼まれていない削除は戻し、消すときは返答に「❕ 消した: <何>（理由。頼まれた、または置き換え先）」を書く。

使い方:
  removed.py [root] [--diff <base>] [--json]   base の既定は HEAD（まだコミットしていない変更）。
                                               base を渡すと、分かれ目（git merge-base）から今の作業ツリーまでを見る
終了コード: いつも 0（一覧を出すだけ。止めるのは fake_gate.py と、テストを消したときの fake_scan.py）
数えない物: 別のファイルに移した物（同じ名前が、変えたファイルのどれかに今ある）、名前を変えただけのファイル
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_scan as fs  # noqa: E402

KINDS = ("関数", "クラス", "export", "テスト", "ファイル", "設定", "UI", "ルート")
CONF_EXT = {".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".env"}
JS_DEFS = [
    ("関数", re.compile(r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)\s*[(<]")),
    ("関数", re.compile(r"^[ \t]*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=\s*(?:async\s*)?(?:\([^)\n]*\)|[\w$]+)\s*(?::[^=>\n]+)?=>", re.M)),
    ("クラス", re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)")),
    ("export", re.compile(r"^[ \t]*export\s+(?:default\s+)?(?:async\s+)?(?:const|let|var|function\s*\*?|class|type|interface|enum)\s+([A-Za-z_$][\w$]*)", re.M)),
]
JS_EXPORT_LIST = re.compile(r"^[ \t]*export\s*\{([^}]*)\}", re.M)
JS_TEST = re.compile(r"\b(?:it|test)(?:\.\w+)*\s*\(\s*(['\"`])(.+?)\1")
PY_DEF = re.compile(r"^([ \t]*)(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)", re.M)
CONF_KEY = re.compile(r"^[ \t]*(?:export\s+)?[\"']?([A-Za-z_][\w.-]*)[\"']?\s*[:=]", re.M)
UI_ATTR = re.compile(r"\b(id|data-testid|name)\s*=\s*[\"']([\w:.-]+)[\"']")
ROUTE = re.compile(r"@\w+(?:\.\w+)*\.(?:route|get|post|put|patch|delete)\(\s*[\"']([^\"']+)[\"']"
                   r"|\b(?:app|router|api)\.(?:get|post|put|patch|delete|all|use)\(\s*[\"'`](/[^\"'`]*)[\"'`]|<Route\b[^>]*\bpath\s*=\s*[\"']([^\"']+)[\"']")


def names(rel: str, text: str) -> set:
    """ファイルの中身から、(種類, 名前) の集合を取り出す。"""
    out = set()
    if not text:
        return out
    ext = fs.ext_of(rel)
    test = fs.is_test(rel)
    if ext in fs.PY:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            tree = None
        if tree is not None:
            def walk(node, prefix):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        kind = "テスト" if child.name.startswith("test") and (test or prefix.startswith("Test")) else "関数"
                        out.add((kind, prefix + child.name))
                        walk(child, prefix + child.name + ".")
                    elif isinstance(child, ast.ClassDef):
                        out.add(("クラス", prefix + child.name))
                        walk(child, prefix + child.name + ".")
                    elif isinstance(child, ast.Assign) and not prefix and any(isinstance(t, ast.Name) and t.id == "__all__" for t in child.targets):
                        out.update(("export", n.value) for n in ast.walk(child.value) if isinstance(n, ast.Constant) and isinstance(n.value, str))
            walk(tree, "")
        else:  # 構文が壊れた版は、行の頭の def と class だけを見る
            for m in PY_DEF.finditer(text):
                kind = "クラス" if m.group(2) == "class" else ("テスト" if test and m.group(3).startswith("test") else "関数")
                out.add((kind, m.group(3)))
    elif ext in fs.JS or ext in (".vue", ".svelte"):
        stripped, _ = fs.js_strip(text)
        for kind, rx in JS_DEFS:
            out.update((kind, m.group(1)) for m in rx.finditer(stripped) if m.group(1) not in fs.JS_KEYWORDS)
        for m in JS_EXPORT_LIST.finditer(stripped):
            out.update(("export", part.split(" as ")[-1].strip()) for part in m.group(1).split(",") if part.strip())
        if test:
            out.update(("テスト", m.group(2)) for m in JS_TEST.finditer(text))
    if ext in CONF_EXT or rel.split("/")[-1].startswith(".env"):
        out.update(("設定", m.group(1)) for m in CONF_KEY.finditer(text))
    if ext in fs.MARKUP:
        out.update(("UI", f"{m.group(1)}={m.group(2)}") for m in UI_ATTR.finditer(text))
    if ext in fs.PY | fs.JS | fs.MARKUP:
        out.update(("ルート", next(g for g in m.groups() if g)) for m in ROUTE.finditer(text))
    return out


def removed_items(changes: list) -> list:
    """changes は (パス, 状態, 前の中身, 今の中身) の一覧。消えた物を {種類, 場所, 名前} で返す。
    同じ名前が、変えたファイルのどれかに今あれば、移しただけなので数えない。"""
    now = set()
    for rel, _, _, new in changes:
        now |= {(k, n.split(".")[-1]) for k, n in names(rel, new or "")}
    new_lines = {l.strip() for _, _, _, new in changes for l in (new or "").splitlines() if len(l.strip()) > 8}
    out = []
    for rel, status, old, new in changes:
        if fs.is_skipped(rel) or old is None:
            continue
        if status == "D" or new is None:
            body = [l.strip() for l in old.splitlines() if len(l.strip()) > 8]
            if body and sum(l in new_lines for l in body) >= len(body) / 2:
                continue  # 中身の半分以上が別の所にあれば、移しただけ
            out.append({"kind": "テスト" if fs.is_test(rel) else "ファイル", "path": rel, "name": rel})
            continue
        for kind, name in sorted(names(rel, old) - names(rel, new)):
            if (kind, name.split(".")[-1]) in now:
                continue
            out.append({"kind": kind, "path": rel, "name": name})
    return out


def git_changes(root: Path, base: str) -> list:
    mb = fs.merge_base(root, base)
    code, out = fs.git(root, "diff", "--name-status", "-M", "--no-color", mb)
    changes = []
    for line in out.splitlines() if code == 0 else []:
        parts = line.split("\t")
        status, old_path, new_path = parts[0][:1], parts[1], parts[-1]
        if status == "A":
            new = fs.read_text(root / new_path)
            changes.append((new_path, "A", None, new))
            continue
        code, old = fs.git(root, "show", f"{mb}:{old_path}")
        if code != 0 or "\0" in old[:4096]:
            continue
        changes.append((new_path, status, old, None if status == "D" else fs.read_text(root / new_path)))
    code, out = fs.git(root, "ls-files", "-z", "--others", "--exclude-standard")
    for rel in [p for p in out.split("\0") if p] if code == 0 else []:
        changes.append((rel, "A", None, fs.read_text(root / rel)))
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--diff", metavar="BASE", default="HEAD")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    if fs.git(root, "rev-parse", "--git-dir")[0] != 0:
        print("消えた物: git のリポジトリではないので、見ない")
        return 0
    items = removed_items([c for c in git_changes(root, a.diff) if not fs.is_vendored(root, c[0])])
    ignores = fs.commit_ignores(root, a.diff)
    items = [i for i in items if i["name"] not in ignores and i["path"] not in ignores]
    count = {k: sum(1 for i in items if i["kind"] == k) for k in KINDS}
    if a.json:
        print(json.dumps({"base": a.diff, "removed": items, "counts": count}, ensure_ascii=False, indent=1))
        return 0
    print(f"== no-fake: 消えた物（{a.diff} との分かれ目から）==")
    for i in items:
        print(f"  {i['kind']}  {i['path']}  {i['name'] if i['name'] != i['path'] else ''}".rstrip())
    if not items:
        print("  無し")
    print("消えた物: " + " / ".join(f"{k} {count[k]}" for k in KINDS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
