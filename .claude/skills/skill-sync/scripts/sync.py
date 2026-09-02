#!/usr/bin/env python3
"""Ciel のスキルと、対象リポジトリの .claude/skills/ を突き合わせる。

  sync.py <対象>                 差分を出すだけ（何も書き換えない）
  sync.py <対象> --apply         差分を反映する
  sync.py <対象> --only a,b       指定したスキルだけ見る
  sync.py <対象> --json          機械が読む形で出す

差分が無ければ何も出さず 0 で終わる。あるときだけ 1 を返す。
黙ることが目的なので、同じものを「同じです」と報告しない。
"""
import argparse, filecmp, hashlib, json, shutil, sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[4]   # Ciel のルート
SRC = SRC_ROOT / ".claude" / "skills"
SELF = "skill-sync"


def digest(d: Path) -> str:
    """ディレクトリの中身をまとめた要約。パスと内容の両方を見る。"""
    h = hashlib.sha256()
    for f in sorted(p for p in d.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(d)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    target_root = Path(a.target).resolve()
    if not target_root.is_dir():
        print(f"対象がありません: {target_root}", file=sys.stderr)
        return 2
    dst = target_root / ".claude" / "skills"

    names = [d.name for d in sorted(SRC.iterdir()) if d.is_dir()]
    if a.only:
        want = {x.strip() for x in a.only.split(",")}
        unknown = want - set(names)
        if unknown:
            print(f"そんなスキルはありません: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        names = [n for n in names if n in want]

    add, upd, same = [], [], []
    for n in names:
        s, d = SRC / n, dst / n
        if not d.is_dir():
            add.append(n)
        elif digest(s) != digest(d):
            upd.append(n)
        else:
            same.append(n)

    if a.apply:
        dst.mkdir(parents=True, exist_ok=True)
        for n in add + upd:
            if (dst / n).exists():
                shutil.rmtree(dst / n)
            shutil.copytree(SRC / n, dst / n)
            for f in (dst / n).rglob("*"):
                if f.suffix in (".py", ".sh"):
                    f.chmod(0o755)

    if a.json:
        print(json.dumps({"target": str(target_root), "add": add,
                          "update": upd, "same": same,
                          "applied": a.apply}, ensure_ascii=False, indent=2))
        return 0 if not (add or upd) else (0 if a.apply else 1)

    if not add and not upd:
        return 0          # 差分なし。何も言わない

    verb = "入れました" if a.apply else "入っていません"
    for n in add:
        print(f"  追加 {n}  ({verb})")
    verb = "更新しました" if a.apply else "古いままです"
    for n in upd:
        print(f"  更新 {n}  ({verb})")
    if not a.apply:
        print(f"\n反映するには --apply を付けて実行します。")
    elif SELF in add + upd:
        print(f"\n{SELF} 自身を更新しました。次回から新しい方が動きます。")
    return 0 if a.apply else 1


if __name__ == "__main__":
    sys.exit(main())
