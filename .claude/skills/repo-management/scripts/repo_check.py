#!/usr/bin/env python3
"""リポジトリを正しく管理できているかを、12 段で見て 1 つの表にする。

段ごとに別のスキルの検査を呼び、結果を 1 行に畳む。全部を自分で判定しないのは、各段の決まりはそのスキルが持ち、
ここは「どの段が揃っていて、どの段が欠けているか」を一目で見せる入口だから。
段: 土台（repo-bootstrap）、方針（workspace-policy）、README（readme-showcase）、文書（docs-upkeep）、鍵（secret-leak-check）、
公開の精査（content-audit）、依存の脆弱性（vuln-watch）、整理整頓（tidy）、出し方（solo-pr-flow）、
バグやクラッシュ・不具合や脆弱性・情報の扱いや漏洩（code-check の 3 本）。

使い方: repo_check.py [root] [--md OUT] [--json]
  検査のスクリプトは <root>/.claude/skills/ を先に見て、無ければ $CIEL_ROOT/.claude/skills/（既定 /home/user/Ciel）を使う
終了コード: 鍵の段が NG（秘密情報を検出）か、code-check の 3 段に重大があるときだけ 1
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CIEL = Path(os.environ.get("CIEL_ROOT", "/home/user/Ciel"))
JST = timezone(timedelta(hours=9))
JUNK = re.compile(r"(^|/)(__pycache__|\.DS_Store|Thumbs\.db|\.pytest_cache|\.mypy_cache|node_modules)(/|$)|\.(pyc|pyo|orig|rej|log|tmp|swp)$")


def script(root: Path, rel: str):
    for base in (root / ".claude" / "skills", CIEL / ".claude" / "skills"):
        p = base / rel
        if p.is_file():
            return p
    return None


def run(cmd, cwd, timeout=300):
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def git(root: Path, *args):
    code, out = run(["git", *args], root, timeout=60)
    return out if code == 0 else ""


def repo_name(root: Path) -> str:
    url = git(root, "remote", "get-url", "origin")
    return (re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1]) if url else root.name).lower()


def row(stage, status, why, fix):
    return {"段": stage, "状態": status, "根拠": why, "直し方": fix}


def missing(stage, name, fix):
    return row(stage, "注意", f"{name} が無い（Ciel のスキルが入っていない）", fix)


def stage_base(root):
    claude_md = (root / "CLAUDE.md").is_file()
    settings = root / ".claude" / "settings.json"
    hooks = False
    if settings.is_file():
        try:
            hooks = bool(json.loads(settings.read_text(encoding="utf-8")).get("hooks"))
        except ValueError:
            hooks = False
    ci = sorted(p.name for p in (root / ".github" / "workflows").glob("*.y*ml")) if (root / ".github" / "workflows").is_dir() else []
    why = f"CLAUDE.md {'あり' if claude_md else '無し'}、settings.json のフック {'あり' if hooks else '無し'}、CI {len(ci)} 本"
    fix = "repo-bootstrap で CLAUDE.md・フック入りの settings.json・最小の CI を置く"
    if claude_md and hooks:
        return row("土台（repo-bootstrap）", "OK", why, "")
    if not claude_md or not settings.is_file():
        return row("土台（repo-bootstrap）", "NG", why, fix)
    return row("土台（repo-bootstrap）", "注意", why, fix)


def stage_policy(root):
    files = sorted(p.name for p in (root / ".claude" / "policy").glob("*.json")) if (root / ".claude" / "policy").is_dir() else []
    if files:
        return row("方針（workspace-policy）", "OK", ".claude/policy/" + ", ".join(files), "")
    return row("方針（workspace-policy）", "注意", ".claude/policy/ に記録が無い", "初めて PR を作る前に workspace-policy で決めて記録する")


def stage_readme(root):
    stage = "README（readme-showcase）"
    if not (root / "README.md").is_file():
        return row(stage, "NG", "README.md が無い", "readme-showcase の assets/README.template.md から書く")
    s = script(root, "readme-showcase/scripts/readme_check.py")
    if not s:
        return missing(stage, "readme_check.py", "Ciel の readme-showcase を入れる（skill-sync）")
    code, out = run([sys.executable, str(s), str(root), "--json"], root)
    try:
        data = json.loads(out)
    except ValueError:
        return row(stage, "注意", "readme_check.py が読めない出力を返した: " + out[-120:], "readme_check.py を手で動かす")
    items = data.get("items", [])
    ng = [i["name"] for i in items if i["status"] == "NG"]
    warn = [i["name"] for i in items if i["status"] == "注意"]
    why = f"点数 {data.get('score', 0)}/{len(items)}" + (f"、NG: {'、'.join(ng)}" if ng else "") + (f"、注意: {'、'.join(warn)}" if warn else "")
    if ng:
        return row(stage, "注意", why, "readme-showcase の 10 項目で NG を直し、10/10 にする")
    return row(stage, "OK", why, "")


def stage_docs(root):
    stage = "文書（docs-upkeep）"
    s = script(root, "docs-upkeep/scripts/docs_check.py")
    if not s:
        return missing(stage, "docs_check.py", "Ciel の docs-upkeep を入れる（skill-sync）")
    code, out = run([sys.executable, str(s), str(root)], root)
    m = re.search(r"誤り\s*(\d+)\s*/\s*注意\s*(\d+)", out)
    if not m:
        return row(stage, "注意", "docs_check.py の出力が読めない: " + out[-120:], "docs_check.py を手で動かす")
    err, note = int(m.group(1)), int(m.group(2))
    why = f"誤り {err} / 注意 {note}"
    if err:
        return row(stage, "NG", why, "壊れたリンク・無いファイル・一覧の抜けを直す（docs-upkeep）")
    if note:
        return row(stage, "注意", why, "古くなる数・予定の語・重複・強調の記号を直す（目安）")
    return row(stage, "OK", why, "")


def stage_secrets(root):
    stage = "鍵（secret-leak-check）"
    s = script(root, "secret-leak-check/scripts/scan.py")
    if not s:
        return missing(stage, "scan.py", "Ciel の secret-leak-check を入れる（skill-sync）")
    code, out = run([sys.executable, str(s), "--tree"], root)
    if "検出なし" in out:
        return row(stage, "OK", "作業ツリーに検出なし（履歴は公開の前に --history で見る）", "")
    last = [l for l in out.splitlines() if l.strip()][-1:] or [""]
    return row(stage, "NG", "検出あり: " + last[0][:100], "先にローテートし、次に消す（secret-leak-check の手順）")


def stage_audit(root):
    stage = "公開の精査（content-audit）"
    s = script(root, "content-audit/scripts/audit.py")
    if not s:
        return missing(stage, "audit.py", "Ciel の content-audit を入れる（skill-sync）")
    code, out = run([sys.executable, str(s), "--tree"], root)
    m = re.search(r"重大\s*(\d+)\s*/\s*注意\s*(\d+)\s*/\s*情報\s*(\d+)", out)
    if not m:
        return row(stage, "注意", "audit.py の出力が読めない: " + out[-120:], "audit.py --tree を手で動かす")
    hi, note, info = (int(x) for x in m.groups())
    why = f"重大 {hi} / 注意 {note} / 情報 {info}"
    if hi:
        return row(stage, "NG", why, "名前・メール・ID・費用・内部の場所を伏せる（content-audit）")
    if note:
        return row(stage, "注意", why, "注意の箇所を読み、伏せるか残すかを決める")
    return row(stage, "OK", why, "")


def stage_vuln(root):
    stage = "依存の脆弱性（vuln-watch）"
    s = script(root, "vuln-watch/scripts/inventory.py")
    if not s:
        return missing(stage, "inventory.py", "Ciel の vuln-watch を入れ、週 1 回のルーティンに載せる")
    code, out = run([sys.executable, str(s), str(root)], root)
    m = re.search(r"合計\s*(\d+)\s*件", out)
    if not m:
        return row(stage, "注意", "inventory.py の出力が読めない: " + out[-120:], "inventory.py を手で動かす")
    n = int(m.group(1))
    loose = re.search(r"版が決まっていない\s*(\d+)", out)
    why = f"依存 {n} 件を棚卸し" + (f"（版が決まっていない {loose.group(1)}）" if loose else "") + "。突き合わせは週 1 回のルーティン"
    return row(stage, "OK" if n or not loose else "注意", why, "" if n else "依存の一覧が無いなら対象外")


def stage_tidy(root):
    stage = "整理整頓（tidy）"
    files = git(root, "ls-files").splitlines()
    junk = [f for f in files if JUNK.search(f)]
    gi = (root / ".gitignore").read_text(encoding="utf-8", errors="replace") if (root / ".gitignore").is_file() else ""
    want = []
    if any(f.endswith(".py") for f in files) and "__pycache__" not in gi:
        want.append("__pycache__/")
    if (root / "package.json").is_file() and "node_modules" not in gi:
        want.append("node_modules/")
    if ((root / ".env.example").is_file() or any(f.endswith((".py", ".js", ".ts")) for f in files)) and not re.search(r"^\s*\.env\b", gi, re.M):
        want.append(".env")
    head = git(root, "symbolic-ref", "-q", "refs/remotes/origin/HEAD") or "refs/remotes/origin/main"
    default = head.replace("refs/remotes/", "")
    merged = [b.strip() for b in git(root, "branch", "-r", "--merged", default).splitlines()
              if b.strip() and "->" not in b and b.strip() != default]
    why = f"余計なファイル {len(junk)}、.gitignore に無い {('、'.join(want)) if want else '無し'}、{default} に入ったリモートブランチ {len(merged)}"
    if junk or want or merged:
        fix = "tidy（余計なファイルは消して .gitignore に足す。入ったブランチは消す。月に 1 回は tidy_report.py）"
        return row(stage, "注意", why, fix)
    return row(stage, "OK", why, "")


def stage_pr(root):
    stage = "出し方（solo-pr-flow）"
    state = Path(os.environ.get("PR_GATE_STATE") or Path.home() / ".claude" / "state" / "pr-gate")
    stamp = state / f"{repo_name(root)}.json"
    if not stamp.is_file():
        return row(stage, "注意", "precheck の記録が無い", "PR の前に precheck.py を通し、❔ で返事を待つ（solo-pr-flow）")
    try:
        d = json.loads(stamp.read_text(encoding="utf-8"))
    except ValueError:
        return row(stage, "注意", "precheck の記録が読めない", "precheck.py を流し直す")
    ts = datetime.fromtimestamp(int(d.get("ts", 0)), JST).strftime("%m/%d %H:%M JST")
    why = f"最後の precheck {d.get('sha', '')[:7]} {'通った' if d.get('ok') else '落ちた'}（{ts}）"
    if not d.get("ok"):
        return row(stage, "注意", why, "落ちた検査を直して precheck.py を流し直す")
    return row(stage, "OK", why, "")


def stage_code(label, script_rel, args, fix_skill):
    """code-check の 1 本を 1 段にする。重大は NG、注意は注意、ツールの無さは根拠に出す。"""
    def stage(root):
        s = script(root, script_rel)
        if not s:
            return missing(label, Path(script_rel).name, "Ciel の code-check を入れる（skill-sync）")
        code, out = run([sys.executable, str(s), str(root), *args], root, timeout=1800)
        m = re.search(r"重大 (\d+) / 注意 (\d+)(?: / 情報 (\d+))?", out)
        if not m:
            return row(label, "注意", "出力が読めない: " + out[-120:], f"{Path(script_rel).name} を手で動かす")
        bad, warn = int(m.group(1)), int(m.group(2))
        tools = next((l.strip() for l in out.splitlines() if l.strip().startswith("ツール:")), "")
        why = f"重大 {bad} / 注意 {warn}" + (f" / 情報 {m.group(3)}" if m.group(3) else "") + (f"。{tools[:90]}" if tools else "")
        if bad:
            return row(label, "NG", why, f"{fix_skill}。重大の行を直してから出す（code-check の references/patterns.md）")
        return row(label, "注意" if warn else "OK", why, f"{fix_skill}。注意を直すか、理由を月報に 1 行" if warn else "")
    return stage


stage_crash = stage_code("バグやクラッシュ（code-check）", "code-check/scripts/crash_check.py", [], "crash_check.py")
stage_code_vuln = stage_code("不具合や脆弱性（code-check）", "code-check/scripts/vuln_scan.py", [], "vuln_scan.py")
stage_info = stage_code("情報の扱いや漏洩（code-check）", "code-check/scripts/info_scan.py", [], "info_scan.py")


def check(root: Path):
    return [f(root) for f in (stage_base, stage_policy, stage_readme, stage_docs, stage_secrets, stage_audit, stage_vuln, stage_tidy, stage_pr,
                              stage_crash, stage_code_vuln, stage_info)]


def table(rows):
    out = ["| 段 | 状態 | 根拠 | 直し方 |", "|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['段']} | {r['状態']} | {r['根拠']} | {r['直し方']} |")
    return "\n".join(out)


def summary(rows):
    c = {k: sum(1 for r in rows if r["状態"] == k) for k in ("OK", "注意", "NG")}
    return f"リポジトリ管理: OK {c['OK']} / 注意 {c['注意']} / NG {c['NG']}"


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--md", metavar="OUT")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    if not (root / ".git").exists():
        print(f"git のリポジトリではない: {root}")
        return 1
    rows = check(root)
    name = repo_name(root)
    if a.json:
        print(json.dumps({"repo": name, "rows": rows, "summary": summary(rows)}, ensure_ascii=False, indent=1))
    else:
        print(f"== repo-management: {name} ==")
        print(table(rows))
        print(summary(rows))
    if a.md:
        now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
        Path(a.md).write_text(f"## リポジトリ管理: {name}（{now}）\n\n{table(rows)}\n\n{summary(rows)}\n", encoding="utf-8")
    return 1 if any((r["段"].startswith("鍵") or "code-check" in r["段"]) and r["状態"] == "NG" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
