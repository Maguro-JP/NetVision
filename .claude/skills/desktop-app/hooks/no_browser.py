#!/usr/bin/env python3
"""PostToolUse フック（Edit、Write）: ブラウザ起動と CLI だけの入口を見つけて1行で知らせる。

ユーザーの決まりは「起動はブラウザではなくアプリのウィンドウ、機能は CLI ではなく画面」。
書いている最中に気づける方が、あとで README ごと直すより安い。止めはしない。知らせるだけ。

環境変数:
  DESKTOP_APP_OFF=1   止める
"""

import json
import os
import re
import sys

BROWSER = re.compile(r"webbrowser\.open|xdg-open\s+['\"]?https?://|\bstart\s+['\"]?https?://|\bopen\s+['\"]?https?://|https?://[^\s）)]+[^\n]{0,12}(?:を開(?:い|く|け)|にアクセス)|ブラウザー?で開")
CLI = re.compile(r"argparse\.ArgumentParser\(|@click\.command|typer\.Typer\(|@app\.command")
SKIP = re.compile(r"(^|/)(tests?|tools|scripts|\.claude|\.github|docs/knowledge|node_modules)(/|$)")


def main() -> int:
    if os.environ.get("DESKTOP_APP_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    if d.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        return 0
    ti = d.get("tool_input") or {}
    path = str(ti.get("file_path") or "")
    texts = [str(ti.get("content") or ""), str(ti.get("new_string") or "")]
    for e in ti.get("edits") or []:
        texts.append(str((e or {}).get("new_string") or ""))
    text = "\n".join(texts)
    if not text.strip() or SKIP.search(path):
        return 0
    notes = []
    m = BROWSER.search(text)
    if m:
        notes.append(f"ブラウザ起動「{m.group(0)[:40]}」。起動はアプリのウィンドウで開く（pywebview など）。URL を人に見せない")
    m = CLI.search(text)
    if m and re.search(r"(^|/)(app|src|gui|desktop)/", path):
        notes.append(f"CLI の入口「{m.group(0)}」。ユーザーが触る機能は画面に置く")
    if notes:
        print(f"desktop-app: {path}: " + "／".join(notes) + "（desktop-app）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
