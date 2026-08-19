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


POWERSHELL = REPO / "packaging" / "make-bundle-windows.ps1"


def test_the_powershell_version_bundles_the_same_libraries():
    """Git を入れられない端末向けの PowerShell 版。

    2つに分かれている以上、片方だけ直して食い違うのが一番怖い。
    同梱するライブラリが一致していることを見張る。
    """
    shell = FOR_WINDOWS.read_text(encoding="utf-8")
    powershell = POWERSHELL.read_text(encoding="utf-8")

    block = shell.split("--implementation cp --abi")[1].split("\n", 1)[1]
    expected = set(block.split("find ")[0].replace("\\", " ").split())

    listed = re.search(r"\$packages\s*=\s*@\((.*?)\)", powershell, re.DOTALL)
    assert listed, "PowerShell 版にライブラリの一覧が見つからない"
    actual = set(re.findall(r'"([^"]+)"', listed.group(1)))

    assert actual == expected, f"同梱するライブラリが食い違っている: {actual ^ expected}"


def test_the_powershell_version_needs_no_python_on_the_build_machine():
    """作業用の端末に Python を入れられないこともある。
    先に取ってくる組み込み版の Python で依存を集めること。
    """
    powershell = POWERSHELL.read_text(encoding="utf-8")
    assert "get-pip.py" in powershell
    assert "& $python -m pip install" in powershell


def test_the_bundles_carry_the_time_zone_database():
    """Windows には地域の時計のデータが入っていない。

    同梱しないと、日本時間を出そうとした時点で例外になる。落ちるのは画面を
    開いた後なので、作る側では気づけない。実際に表の更新が止まった。
    """
    for script in (FOR_WINDOWS, POWERSHELL):
        assert "tzdata" in script.read_text(encoding="utf-8"), f"{script.name} に tzdata が無い"


def _bat_from_shell() -> str:
    text = FOR_WINDOWS.read_text(encoding="utf-8")
    body = text.split('bat = """', 1)[1].split('"""', 1)[0]
    # Python の文字列リテラルとして書かれているので、二重のバックスラッシュを戻す
    return body.replace("\\\\", "\\")


def _bat_from_powershell() -> str:
    text = POWERSHELL.read_text(encoding="utf-8")
    return text.split("$bat = @'\n", 1)[1].split("\n'@", 1)[0] + "\n"


def test_both_bundles_write_the_same_launcher():
    """起動用バッチが2つのスクリプトで食い違わないこと。

    片方だけ直すと、配る相手によって挙動が違うことになる。
    """
    assert _bat_from_shell() == _bat_from_powershell()


def test_the_launcher_opens_a_window_instead_of_a_console():
    """黒い画面を出さず、窓から止められる形にする。

    黒い画面だと止め方が Ctrl+C しか無く、そのあとに Windows が出す
    「バッチ ジョブを終了しますか」は、Y でも N でも意図とずれる。
    ふつうのアプリと同じく、窓の「終了する」で止められるようにする。
    """
    bat = _bat_from_shell()
    assert "pythonw.exe" in bat, "黒い画面の出ない起動になっていない"
    assert "scripts.launcher" in bat
    assert "pause" not in bat
    assert "Ctrl+C" not in bat


def test_the_launcher_module_is_bundled():
    """起動役そのものが同梱されていること。無いと起動しない。"""
    assert (REPO / "backend" / "scripts" / "launcher.py").is_file()
    for script in (FOR_WINDOWS, POWERSHELL):
        assert "scripts" in script.read_text(encoding="utf-8")


def test_a_way_to_see_the_error_is_bundled():
    """窓のある起動は、失敗しても何も出ないまま消える。

    黒い画面で動かす道を必ず同梱し、記録も残す。原因が見えないと、
    渡した相手の手元で何も分からなくなる。
    """
    for script in (FOR_WINDOWS, POWERSHELL):
        text = script.read_text(encoding="utf-8")
        assert "NetVision-check.bat" in text, f"{script.name} に診断用の起動が無い"
    assert "launcher.log" in (REPO / "backend" / "scripts" / "launcher.py").read_text(
        encoding="utf-8"
    )


def test_the_bundle_avoids_writing_bytecode_at_runtime():
    """同期フォルダに置かれたときに、中間ファイルの書き込みで固まらないこと。

    先に作って同梱し、実行時には書かせない。OneDrive の中で起動が戻って
    こない、という形で実際に踏んだ。
    """
    for script in (FOR_WINDOWS, POWERSHELL):
        text = script.read_text(encoding="utf-8")
        assert "compileall" in text, f"{script.name} が中間ファイルを作っていない"
        assert "PYTHONDONTWRITEBYTECODE" in text, f"{script.name} が実行時に書かせている"
