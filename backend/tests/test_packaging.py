"""配布用スクリプトが Windows でも動く形になっていること。

ここが壊れると、閉域へ持ち込む ZIP がそもそも作れない。しかも失敗するのは
渡した相手の手元なので、こちらでは気づけない。過去に踏んだのは次の2つ。

1. Windows の Git は既定で改行を CRLF に書き換える。shebang が "bash\\r" と
   読まれ、Git Bash が bad interpreter で止まる。
2. Windows に python3 というコマンドは無い。python か py になる。
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((REPO / "packaging").glob("*.sh"))
# 配る側が Windows で動かすもの。Linux 前提のものは対象外
FOR_WINDOWS = REPO / "packaging" / "make-bundle-windows.sh"


def test_there_are_shell_scripts_to_check():
    assert SCRIPTS, "packaging/*.sh が見つからない"


def test_shell_scripts_keep_lf_endings():
    for script in SCRIPTS:
        assert b"\r\n" not in script.read_bytes(), f"{script.name} が CRLF になっている"


def test_gitattributes_pins_line_endings():
    """clone した時点で CRLF にされるのを止める。ファイル自体の改行だけでは足りない。"""
    text = (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert re.search(r"^\*\.sh\s+text\s+eol=lf", text, re.MULTILINE)
    assert re.search(r"^\*\.bat\s+text\s+eol=crlf", text, re.MULTILINE)


def test_the_windows_bundle_script_does_not_hardcode_python3():
    """python3 は Windows に無い。実際に動くものを選んでから使うこと。"""
    lines = FOR_WINDOWS.read_text(encoding="utf-8").splitlines()
    calls = [
        line
        for line in lines
        if re.search(r"(^|[|(&;]\s*)python3\b", line) and not line.lstrip().startswith("#")
    ]
    # 候補として名前を並べる for 行だけは許す
    calls = [line for line in calls if "for candidate in" not in line]
    assert not calls, f"python3 を直接呼んでいる: {calls}"
    assert '"$PY"' in FOR_WINDOWS.read_text(encoding="utf-8")
