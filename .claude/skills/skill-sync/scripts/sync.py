#!/usr/bin/env python3
"""Ciel のスキルと、対象リポジトリの .claude/skills/ を突き合わせる。

  sync.py <対象>                 差分を出すだけ（何も書き換えない）
  sync.py <対象> --apply         差分を反映する
  sync.py <対象> --only a,b       指定したスキルだけ見る
  sync.py <対象> --json          機械が読む形で出す

差分が無ければ何も出さず 0 で終わる。あるときだけ 1 を返す。
黙ることが目的なので、同じものを「同じです」と報告しない。

lessons/LESSONS.md だけは対象側の記録。上書きせず、Ciel にしか無い項目を上に足す。
Ciel の複製で置き換えると、そのリポジトリで起きた失敗の記録が消える（実際に消しかけた）。
"""
import argparse, hashlib, json, shutil, sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[4]   # Ciel のルート
SRC = SRC_ROOT / ".claude" / "skills"
SELF = "skill-sync"
LESSONS = "lessons"
LESSONS_FILE = "LESSONS.md"


def digest(d: Path, skip=()) -> str:
    """ディレクトリの中身をまとめた要約。パスと内容の両方を見る。"""
    h = hashlib.sha256()
    for f in sorted(p for p in d.rglob("*") if p.is_file()):
        rel = str(f.relative_to(d))
        if rel in skip:
            continue
        h.update(rel.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def split_entries(text: str):
    """頭書きと、'## ' で始まる項目の並びに分ける。"""
    head, blocks, cur = [], [], None
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            cur = [line]
            blocks.append(cur)
        elif cur is None:
            head.append(line)
        else:
            cur.append(line)
    return "".join(head), ["".join(b) for b in blocks]


def merge_lessons(src_text: str, dst_text: str):
    """対象の記録を全部残し、Ciel にしか無い項目（見出しで比べる）だけを上に足す。"""
    head, dst_blocks = split_entries(dst_text)
    _, src_blocks = split_entries(src_text)
    have = {b.splitlines()[0].strip() for b in dst_blocks}
    new = [b for b in src_blocks if b.splitlines()[0].strip() not in have]
    if not new:
        return dst_text, 0
    if not head.endswith("\n\n"):
        head = head.rstrip("\n") + "\n\n"
    tidy = lambda b: b if b.endswith("\n\n") else b.rstrip("\n") + "\n\n"  # noqa: E731
    return head + "".join(tidy(b) for b in new) + "".join(dst_blocks), len(new)


def lessons_changed(s: Path, d: Path) -> bool:
    """lessons は LESSONS.md を除いて比べ、LESSONS.md は足す項目があるかで見る。"""
    if digest(s, skip=(LESSONS_FILE,)) != digest(d, skip=(LESSONS_FILE,)):
        return True
    df, sf = d / LESSONS_FILE, s / LESSONS_FILE
    if not df.is_file():
        return sf.is_file()
    return merge_lessons(sf.read_text(encoding="utf-8"), df.read_text(encoding="utf-8"))[1] > 0


# ---- フックと settings.json ------------------------------------------------
# スキルだけ配っても、フックの登録（settings.json）が無いと効かないものがある（token-efficiency、gate.sh）。
# repo-bootstrap のひな形を元に、フックのファイルと登録を差分だけ足す。対象にしかない登録は消さない。
TEMPLATE = SRC / "repo-bootstrap" / "templates"
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "PostToolUse")


def hook_file_diffs(target_root: Path):
    """ひな形の .claude/hooks/*.sh と対象を比べる。(入っていない, 古い) の名前の列。"""
    add, upd = [], []
    for f in sorted((TEMPLATE / "hooks").glob("*.sh")):
        d = target_root / ".claude" / "hooks" / f.name
        if not d.exists():
            add.append(f.name)
        elif d.read_bytes() != f.read_bytes():
            upd.append(f.name)
    return add, upd


def _command_exists(cmd: str, target_root: Path) -> bool:
    """登録のコマンドが指すファイルが対象にあるか。python3 や cat の後ろの最初のパスで見る。"""
    for tok in cmd.replace("||", " ").split():
        if tok.startswith(".claude/"):
            return (target_root / tok).exists()
    return False


def settings_missing(target_root: Path):
    """ひな形にあって対象の settings.json に無いフックの登録。対象にファイルがあるものだけ。"""
    try:
        tpl = json.loads((TEMPLATE / "settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, []
    dst = target_root / ".claude" / "settings.json"
    try:
        cur = json.loads(dst.read_text(encoding="utf-8")) if dst.exists() else {}
    except json.JSONDecodeError:
        return None, []
    have = {h.get("command", "") for es in cur.get("hooks", {}).values() for e in es for h in e.get("hooks", [])}
    missing = []
    for ev in HOOK_EVENTS:
        for e in tpl.get("hooks", {}).get(ev, []):
            for h in e.get("hooks", []):
                cmd = h.get("command", "")
                if cmd not in have and _command_exists(cmd, target_root):
                    missing.append((ev, e.get("matcher"), h))
    return cur, missing


def apply_hooks(target_root: Path, add, upd, cur, missing):
    hooks_dir = target_root / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    for name in add + upd:
        shutil.copy2(TEMPLATE / "hooks" / name, hooks_dir / name)
        (hooks_dir / name).chmod(0o755)
    if missing:
        cur.setdefault("hooks", {})
        for ev, matcher, h in missing:
            entries = cur["hooks"].setdefault(ev, [])
            slot = next((e for e in entries if e.get("matcher") == matcher), None)
            if slot is None:
                slot = {"hooks": []}
                if matcher:
                    slot = {"matcher": matcher, "hooks": []}
                entries.append(slot)
            slot["hooks"].append(h)
        (target_root / ".claude" / "settings.json").write_text(
            json.dumps(cur, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
        elif (lessons_changed(s, d) if n == LESSONS else digest(s) != digest(d)):
            upd.append(n)
        else:
            same.append(n)

    if a.apply:
        dst.mkdir(parents=True, exist_ok=True)
        for n in add + upd:
            keep = None
            if n == LESSONS and (dst / n / LESSONS_FILE).is_file():
                keep = (dst / n / LESSONS_FILE).read_text(encoding="utf-8")
            if (dst / n).exists():
                shutil.rmtree(dst / n)
            shutil.copytree(SRC / n, dst / n)
            if keep is not None:
                merged, _ = merge_lessons((SRC / n / LESSONS_FILE).read_text(encoding="utf-8"), keep)
                (dst / n / LESSONS_FILE).write_text(merged, encoding="utf-8")
            for f in (dst / n).rglob("*"):
                if f.suffix in (".py", ".sh"):
                    f.chmod(0o755)

    # フックのファイルと登録。スキルを入れたあとに見る（登録はファイルがあるものだけ足すため）
    h_add, h_upd = ([], []) if a.only else hook_file_diffs(target_root)
    cur, missing = (None, []) if a.only else settings_missing(target_root)
    if a.apply and (h_add or h_upd or missing):
        # コピーしたあとに登録を見直す。コピーで初めてファイルがそろうものがある
        apply_hooks(target_root, h_add, h_upd, cur if cur is not None else {}, [])
        cur, missing = settings_missing(target_root)
        if missing:
            apply_hooks(target_root, [], [], cur, missing)

    if a.json:
        print(json.dumps({"target": str(target_root), "add": add,
                          "update": upd, "same": same,
                          "hooks_add": h_add, "hooks_update": h_upd,
                          "settings_add": [f"{ev} {h.get('command','')}" for ev, _, h in missing],
                          "applied": a.apply}, ensure_ascii=False, indent=2))
        return 0 if not (add or upd or h_add or h_upd or missing) else (0 if a.apply else 1)

    if not add and not upd and not h_add and not h_upd and not missing:
        return 0          # 差分なし。何も言わない

    # 向きが読めるように書く。Ciel が元で、対象リポジトリに入れる
    print(f"Ciel → {target_root.name} の .claude/skills/" + ("" if a.apply else "（見るだけ）"))
    for n in add:
        print(f"  {'入れた' if a.apply else '入っていない'}  {n}")
    for n in upd:
        print(f"  {'更新した' if a.apply else 'Ciel より古い'}  {n}")
    for n in h_add:
        print(f"  {'入れた' if a.apply else '入っていない'}  hooks/{n}")
    for n in h_upd:
        print(f"  {'更新した' if a.apply else 'Ciel より古い'}  hooks/{n}")
    for ev, _, h in missing:
        print(f"  {'登録した' if a.apply else '登録がない'}  {ev}: {h.get('command', '')}")
    if not a.apply:
        print("--apply で Ciel から入れる。")
    elif SELF in add + upd:
        print(f"{SELF} 自身も Ciel の新しいものに替えた。この実行は古い方で動いた。次の実行から新しい方。")
    return 0 if a.apply else 1


if __name__ == "__main__":
    sys.exit(main())
