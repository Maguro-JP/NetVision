#!/usr/bin/env python3
"""リポジトリが毎回・毎セッション読ませている量を数える。削るかはユーザーが決める。数字を横に置くのがこの道具の仕事。

  budget.py            このリポジトリ
  budget.py <path>     別のリポジトリ

数えるもの:
  常時     CLAUDE.md と、全スキルの description（名前と description は毎ターン読まれる）
  毎ターン UserPromptSubmit のフックの出力
  毎セッション SessionStart のフックの出力（開始・再開・圧縮のたび）
トークンは概算（日本語は 1 文字 ≈ 1 トークン、英数は 4 文字 ≈ 1 トークンとして数える）。
"""
import json, re, subprocess, sys
from pathlib import Path

LIMIT_DESC = 300   # description の目安。発動の語だけなら足りる


def tokens(s: str) -> int:
    ja = sum(1 for c in s if ord(c) > 0x2E7F)
    return ja + (len(s) - ja) // 4


def run_hook(cmd: str, cwd: Path) -> int:
    """フックが文脈に足す文字数。JSON で返すフックは additionalContext だけを数える。
    1ターン目だけ全文を出すフックがあるので、2回動かして2回目を測る（定常状態）。"""
    out = ""
    try:
        for _ in range(2):
            r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=20,
                               input='{"session_id":"budget","user_input":"x"}')
            out = r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return -1
    try:
        d = json.loads(out)
        return len(d.get("hookSpecificOutput", {}).get("additionalContext", ""))
    except (json.JSONDecodeError, AttributeError):
        return len(out)


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    rows = []
    claude_md = root / "CLAUDE.md"
    n_claude = len(claude_md.read_text(errors="replace")) if claude_md.exists() else 0
    descs = []
    for f in sorted((root / ".claude/skills").glob("*/SKILL.md")) if (root / ".claude/skills").exists() else []:
        m = re.search(r"^description:\s*(.*?)^---", f.read_text(errors="replace"), re.S | re.M)
        d = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        descs.append((len(d), f.parent.name))
    n_desc = sum(n for n, _ in descs)
    hooks = {"SessionStart": 0, "UserPromptSubmit": 0}
    unmeasured = []
    settings = root / ".claude/settings.json"
    if settings.exists():
        try:
            conf = json.loads(settings.read_text())
        except json.JSONDecodeError:
            conf = {}
        for ev in hooks:
            for entry in (conf.get("hooks", {}).get(ev) or []):
                for h in entry.get("hooks", []):
                    n = run_hook(h.get("command", ""), root)
                    if n < 0:
                        unmeasured.append(h.get("command", ""))
                    else:
                        hooks[ev] += n

    print(f"== token-efficiency: {root.name} の常時の量 ==")
    print(f"  常時（毎ターン）   CLAUDE.md {n_claude} 文字（約 {tokens(claude_md.read_text(errors='replace')) if claude_md.exists() else 0} トークン）"
          f" + description {len(descs)} 本 {n_desc} 文字")
    print(f"  毎ターン（フック） UserPromptSubmit {hooks['UserPromptSubmit']} 文字")
    print(f"  毎セッション       SessionStart {hooks['SessionStart']} 文字（開始・再開・圧縮のたび）")
    long_desc = sorted((x for x in descs if x[0] > LIMIT_DESC), reverse=True)
    if long_desc:
        print(f"  description が {LIMIT_DESC} 文字を超えるもの {len(long_desc)} 本（発動の語だけに絞る候補）:")
        for n, name in long_desc:
            print(f"    {n:4d}  {name}")
    for u in unmeasured:
        print(f"  ?? 測れなかったフック: {u}")
    per_turn = n_claude + n_desc + hooks["UserPromptSubmit"]
    print(f"\n  1ターンの固定分 約 {per_turn} 文字。100 ターンのセッションで約 {per_turn * 100 // 1000} 千文字が、仕事と関係なく読まれる")
    print("  削るかはユーザーが決める。候補は description の絞り込み、SessionStart で差し込む本文の要約、フックの統合")


if __name__ == "__main__":
    main()
