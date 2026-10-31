#!/usr/bin/env python3
"""PR を作る前・マージの前・main に push する前の確認を 1 本で行い、結果を記録する。

出したあとに間違いが分かると、直すのはユーザーになる。検査を手で選ぶと抜けるので、ここで全部を順に動かし、
HEAD ごとの結果を ~/.claude/state/pr-gate/<repo>.json に残す。pr_gate.py（PreToolUse）がこの記録を見て、
通っていない HEAD の PR の作成と main への push を止める。

動かすもの（あるものだけ）: コミットメッセージの ID・URL・メールの検査、git diff --check、./scripts/validate.sh、.claude/skills/*/scripts/test_*.py、pytest、
ja-proofread・content-audit・secret-leak-check・user-terms の差分（base から。user-terms は注意で、落とさない）、npm test（package.json に test があり node_modules があるとき）。

使い方: precheck.py [--base <ref>]   base の既定は origin のデフォルトブランチ
環境変数: PR_GATE_STATE=dir  記録の保存先（既定は ~/.claude/state/pr-gate）
"""

import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def sh(cmd, timeout=900):
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def repo_name() -> str:
    code, url = sh(["git", "remote", "get-url", "origin"])
    name = re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1]) if code == 0 and url else Path.cwd().name
    return name.lower()


def default_base() -> str:
    code, ref = sh(["git", "symbolic-ref", "-q", "refs/remotes/origin/HEAD"])
    if code == 0 and ref:
        return ref.replace("refs/remotes/", "")
    for b in ("origin/main", "origin/master", "origin/develop"):
        if sh(["git", "rev-parse", "-q", "--verify", b])[0] == 0:
            return b
    return "HEAD~1"


def pass_by_text(name: str, code: int, out: str) -> bool:
    if name == "proofread":
        # 注意（訳語、強調の記号、読点）は直す目安で、出す前に止める理由にはしない。重大（文字の混入、英語の出だし）だけで判定する
        return bool(re.search(r"重大 0 /", out))
    if name == "content-audit":
        return bool(re.search(r"重大 0 / 注意 0", out))
    if name == "secret-leak-check":
        return "検出なし" in out
    return code == 0


def main() -> int:
    base = default_base()
    if len(sys.argv) >= 3 and sys.argv[1] == "--base":
        base = sys.argv[2]
    code, sha = sh(["git", "rev-parse", "HEAD"])
    if code != 0:
        print("git のリポジトリではない"); return 1
    steps = [("git diff --check", f"git diff --check {base}")]
    if Path("scripts/validate.sh").exists():
        steps.append(("validate", "./scripts/validate.sh"))
    for t in sorted(glob.glob(".claude/skills/*/scripts/test_*.py")):
        steps.append((t.split("/")[2] + "/" + Path(t).name, f"{sys.executable} -I {t}"))
    has_pytest = sh([sys.executable, "-c", "import pytest"])[0] == 0
    if has_pytest and (Path("tests").is_dir() or Path("pytest.ini").exists()):
        steps.append(("pytest", f"{sys.executable} -m pytest -q -x"))
    for name, path in (("proofread", ".claude/skills/ja-proofread/scripts/proofread.py"),
                       ("content-audit", ".claude/skills/content-audit/scripts/audit.py"),
                       ("secret-leak-check", ".claude/skills/secret-leak-check/scripts/scan.py"),
                       ("user-terms", ".claude/skills/user-terms/scripts/terms.py")):
        if Path(path).exists():
            steps.append((name, f"{sys.executable} {path} --diff {base}"))
    if Path("package.json").exists() and Path("node_modules").is_dir():
        try:
            if "test" in json.loads(Path("package.json").read_text(encoding="utf-8")).get("scripts", {}):
                steps.append(("npm test", "npm test --silent"))
        except ValueError:
            pass
    results = []
    # コミットメッセージに、セッションの URL・Routine の ID・noreply 以外のメールが無いこと（public では書かない）
    code, msgs = sh(["git", "log", f"{base}..HEAD", "--format=%B"])
    leak = re.findall(r"claude\.ai/code/session_\w+|\bsession_01\w+|\btrig_01\w+|[\w.+-]+@(?!anthropic\.com\b|users\.noreply\.github\.com\b)[\w-]+\.[a-z]{2,}", msgs) if code == 0 else []
    results.append({"name": "commit messages", "exit": 1 if leak else 0, "ok": not leak, "tail": ("ID や URL やメールが入っている: " + ", ".join(sorted(set(leak))))[:200] if leak else "ID・URL・メール無し"})
    print(f"  {'OK' if not leak else 'NG'}  commit messages: {results[-1]['tail']}")
    for name, cmd in steps:
        code, out = sh(cmd)
        ok = pass_by_text(name, code, out)
        tail = [l for l in out.splitlines() if l.strip()][-1:] or [""]
        results.append({"name": name, "exit": code, "ok": ok, "tail": tail[0][:200]})
        print(f"  {'OK' if ok else 'NG'}  {name}: {tail[0][:160]}")
    ok = all(r["ok"] for r in results)
    name = repo_name()
    root = Path(os.environ.get("PR_GATE_STATE") or Path.home() / ".claude" / "state" / "pr-gate")
    root.mkdir(parents=True, exist_ok=True)
    stamp = {"repo": name, "sha": sha, "base": base, "ok": ok, "ts": int(time.time()), "steps": results}
    (root / f"{name}.json").write_text(json.dumps(stamp, ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(1 for r in results if r["ok"])
    print(f"確認 {name} {sha[:7]} base={base}: {'通った' if ok else '落ちた'} {n}/{len(results)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
