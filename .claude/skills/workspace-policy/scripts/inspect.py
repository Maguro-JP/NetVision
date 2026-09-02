#!/usr/bin/env python3
"""リポジトリの運用規約を読み、自動化してよい範囲の判断材料を出す。

  inspect.py [リポジトリのパス]        人が読む形で出す
  inspect.py --json [パス]             機械が読む形で出す

規約ファイルと PR に関わる CI を探し、そこに人間の関与を求める記述が
あるかを見る。判定はせず、材料と根拠だけを出す。決めるのは人。
"""
import argparse, hashlib, json, re, subprocess, sys
from pathlib import Path

POLICY_FILES = [
    "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md",
    ".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md",
    ".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS",
]
POLICY_GLOBS = [".cursor/rules/*.mdc", ".github/*.md"]

# 人間がマージする運用を示す記述
HUMAN_MERGE = [
    (r"人が.{0,10}(見て|確認して|レビューして).{0,10}(Merge|マージ)", "人がレビューしてマージする運用"),
    (r"(人|人間|レビュアー|担当者).{0,20}(承認|approve)", "人の承認を求めている"),
    (r"(先に|必ず).{0,10}Merge してください", "人にマージを依頼する記述"),
    (r"(require|requires?).{0,20}(human|manual).{0,20}(review|approval)", "human review required"),
    (r"do not (auto[- ]?merge|merge automatically)", "auto-merge を禁止"),
    (r"自動.{0,4}マージ.{0,10}(しない|禁止|不可)", "自動マージを禁止"),
]

# 同時に開いてよい PR の本数を絞る記述
ONE_AT_A_TIME = [
    (r"未マージ.{0,20}(新しい|次の).{0,10}PR.{0,10}(作らない|出さない)", "未マージがある間は新しい PR を作らない"),
    (r"PR は.{0,20}1本", "PR は1本まで"),
    (r"積み上げ.{0,10}PR.{0,10}(禁止|しない)", "積み上げ PR を禁止"),
    (r"one (open )?(PR|pull request) at a time", "one PR at a time"),
]

# 枝の作り方の指定
BRANCH_RULE = [
    (r"`?([a-z][a-z0-9_-]*)/`?\s*で始まる枝", "枝の接頭辞"),
    (r"から\s*`?([a-z][a-z0-9_-]*)/<", "枝の接頭辞"),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def find_policy_files(root: Path):
    out = []
    for rel in POLICY_FILES:
        p = root / rel
        if p.is_file():
            out.append(p)
    for g in POLICY_GLOBS:
        out.extend(sorted(x for x in root.glob(g) if x.is_file()))
    seen, uniq = set(), []
    for p in out:
        if p.resolve() not in seen:
            seen.add(p.resolve())
            uniq.append(p)
    return uniq


def scan(text, rules):
    hits = []
    for pat, label in rules:
        m = re.search(pat, text, re.I)
        if m:
            line = text[: m.start()].count("\n") + 1
            hits.append({"label": label, "line": line, "match": m.group(0)[:80]})
    return hits


def pr_workflows(root: Path):
    out = []
    d = root / ".github" / "workflows"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.y*ml")):
        t = f.read_text(errors="replace")
        if not re.search(r"^\s*pull_request", t, re.M):
            continue
        # 名前と、規約の検査らしさ
        name = (re.search(r"^name:\s*(.+)$", t, re.M) or [None, f.stem])[1].strip()
        policy_like = bool(re.search(r"policy|規約|ルール|lint.*pr|pr.*check", f.stem + " " + name, re.I))
        out.append({"file": str(f.relative_to(root)), "name": name,
                    "policy_check": policy_like})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()

    files = find_policy_files(root)
    findings, digests = [], {}
    for p in files:
        text = p.read_text(errors="replace")
        rel = str(p.relative_to(root))
        digests[rel] = sha(p)
        for kind, rules in (("human_merge", HUMAN_MERGE),
                            ("one_at_a_time", ONE_AT_A_TIME),
                            ("branch_rule", BRANCH_RULE)):
            for h in scan(text, rules):
                findings.append({"file": rel, "kind": kind, **h})

    wf = pr_workflows(root)
    result = {
        "repo": root.name,
        "policy_files": [str(p.relative_to(root)) for p in files],
        "policy_digests": digests,
        "findings": findings,
        "pr_workflows": wf,
        "signals": {
            "human_merge": any(f["kind"] == "human_merge" for f in findings),
            "one_at_a_time": any(f["kind"] == "one_at_a_time" for f in findings)
                             or any(w["policy_check"] for w in wf),
            "codeowners": any(p.name == "CODEOWNERS" for p in files),
        },
    }

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"== {result['repo']} の運用規約 ==\n")
    if not files:
        print("  規約ファイルなし")
    else:
        print("規約ファイル:")
        for p in result["policy_files"]:
            print(f"  {p}")
    print()
    if wf:
        print("PR で走る CI:")
        for w in wf:
            tag = "  ← 規約の検査らしい" if w["policy_check"] else ""
            print(f"  {w['file']}  ({w['name']}){tag}")
        print()
    if findings:
        print("見つかった記述:")
        for f in findings:
            print(f"  [{f['kind']}] {f['file']}:{f['line']}  {f['label']}")
            print(f"      {f['match']}")
        print()
    s = result["signals"]
    print("判断材料:")
    print(f"  人がマージする運用   : {'あり' if s['human_merge'] else '見当たらない'}")
    print(f"  PR を1本ずつ         : {'あり' if s['one_at_a_time'] else '見当たらない'}")
    print(f"  CODEOWNERS           : {'あり' if s['codeowners'] else 'なし'}")
    print()
    if s["human_merge"] or s["codeowners"]:
        print("  自動マージは選ばせないこと。規約が人の関与を求めている。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
