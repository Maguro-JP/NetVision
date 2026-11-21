#!/usr/bin/env python3
"""作った物をちゃんと確認する入口。バグやクラッシュ・不具合や脆弱性・情報の扱いや漏洩の 3 本を回し、1 つの表にする。

Claude が作る物には問題が多いという前提で、出す前に機械で見る。3 本を別々に呼ぶと 1 本飛ばす穴ができるので、ここから回す。
書き込みのたびの短い検査は hooks/code_nudge.py、PR の前は precheck.py が 3 本を差分で回す。

使い方: code_check.py [root] [--diff <base>] [--md OUT] [--json] [--no-tests] [--no-smoke] [--history]
終了コード: どれかに重大があれば 1
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))
PARTS = [("バグやクラッシュ", "crash_check.py", ["--no-tests", "--no-smoke"]),
         ("不具合や脆弱性", "vuln_scan.py", []),
         ("情報の扱いや漏洩", "info_scan.py", ["--history"])]


def run_part(script: str, root: Path, base, flags: list):
    cmd = [sys.executable, str(HERE / script), str(root), "--json", *flags]
    if base:
        cmd += ["--diff", base]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        text = r.stdout
        data = json.loads(text[text.index("{"):]) if "{" in text else None
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except ValueError:
        return None, (r.stdout + r.stderr).strip()[-200:]
    return data, (r.stderr or "").strip()[-200:]


def table(rows: list) -> str:
    lines = ["| 観点 | 重大 | 注意 | 情報 | 何で見たか | 主な検出 |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['観点']} | {r['重大']} | {r['注意']} | {r['情報']} | {r['ツール']} | {r['主な検出']} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--diff", metavar="BASE")
    ap.add_argument("--md", metavar="OUT")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-tests", action="store_true")
    ap.add_argument("--no-smoke", action="store_true")
    ap.add_argument("--history", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    rows, all_findings = [], []
    for label, script, allowed in PARTS:
        flags = [f for f in allowed if getattr(a, f.lstrip("-").replace("-", "_"), False)]
        data, err = run_part(script, root, a.diff, flags)
        if data is None:
            rows.append({"観点": label, "重大": "?", "注意": "?", "情報": "?", "ツール": "", "主な検出": f"動かなかった: {err}"})
            continue
        c = data.get("counts", {})
        fs = data.get("findings", [])
        all_findings += [dict(f, 観点=label) for f in fs]
        top = "、".join(f"{f['id']} {f['path']}" + (f":{f['line']}" if f.get("line") else "") for f in fs if f["sev"] == "重大")[:160]
        tools_line = (data.get("extra") or {}).get("tools", "").replace("ツール: ", "")
        rows.append({"観点": label, "重大": c.get("重大", 0), "注意": c.get("注意", 0), "情報": c.get("情報", 0),
                     "ツール": tools_line.split("／")[0].replace("使った ", "").strip(), "主な検出": top or "無し"})
    tot = {k: sum(int(r[k]) for r in rows if isinstance(r[k], int)) for k in ("重大", "注意", "情報")}
    summary = f"コードの確認: 重大 {tot['重大']} / 注意 {tot['注意']} / 情報 {tot['情報']}"
    if a.json:
        print(json.dumps({"root": str(root), "rows": rows, "findings": all_findings, "summary": summary}, ensure_ascii=False, indent=1))
    else:
        print(f"== code-check: {root.name}{'（差分 ' + a.diff + '）' if a.diff else ''} ==")
        print(table(rows))
        for f in all_findings:
            if f["sev"] == "重大":
                loc = f"{f['path']}:{f['line']}" if f.get("line") else (f["path"] or "-")
                print(f"  重大  {loc}  {f['id']} {f['what']}｜直し方: {f['fix']}")
        print(summary)
    if a.md:
        now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
        body = [f"## コードの確認: {root.name}（{now}）", "", table(rows), ""]
        for f in all_findings:
            if f["sev"] != "情報":
                loc = f"{f['path']}:{f['line']}" if f.get("line") else (f["path"] or "-")
                body.append(f"- {f['sev']} {loc} {f['id']} {f['what']}（直し方: {f['fix']}）")
        body += ["", summary, ""]
        Path(a.md).write_text("\n".join(body), encoding="utf-8")
    return 1 if tot["重大"] or any(r["重大"] == "?" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
