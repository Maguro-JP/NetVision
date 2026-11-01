#!/usr/bin/env python3
"""PostToolUse フック（Edit、Write）: 書き込んだ画面の中身を ui_check.py で見て、1 行で知らせる。

ユーザーの決まりは「UI はインテュイティブ・ユーザーフレンドリー・ストレスフリー」。
書いている最中に気づける方が、あとで画面ごと直すより安い。止めはしない。知らせるだけで、3 件まで。

環境変数:
  UI_DESIGN_OFF=1   止める
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from ui_check import check_text  # noqa: E402


def main() -> int:
    if os.environ.get("UI_DESIGN_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    if d.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        return 0
    ti = d.get("tool_input") or {}
    path = str(ti.get("file_path") or "")
    new = [str(ti.get("content") or ""), str(ti.get("new_string") or "")]
    new += [str((e or {}).get("new_string") or "") for e in ti.get("edits") or []]
    new_text = "\n".join(new)
    if not path or not new_text.strip():
        return 0
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")  # 書き込みのあとなので、ファイル全体で見る
    except OSError:
        text = new_text
    notes = [(no, snip, msg) for no, snip, msg in check_text(path, text) if no == 0 or snip in new_text]
    if not notes:
        return 0
    shown = [f"{no or ''}{'行 ' if no else ''}「{snip}」{msg}" for no, snip, msg in notes[:3]]
    more = f"（ほか {len(notes) - 3} 件）" if len(notes) > 3 else ""
    print(f"ui-design: {path}: " + "／".join(shown) + more + "（ui-design の references/checklist.md）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
