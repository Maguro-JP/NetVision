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


def test_the_default_launcher_opens_the_app_window():
    """既定はアプリのウィンドウで開く。ブラウザは開かない。

    黒い画面の Ctrl+C では止め方が分かりにくかったので、ウィンドウの×で止める形に揃える。
    失敗したときに様子を見るため、黒い画面つきの起動（NetVision-console.bat）も残す。
    """
    bat = _bat_from_shell()
    assert "pythonw.exe" in bat and "scripts.launcher" in bat
    assert "http://" not in bat, "ブラウザで開く起動が残っている"
    assert "scripts.standalone" not in bat
    for script in (FOR_WINDOWS, POWERSHELL):
        text = script.read_text(encoding="utf-8")
        assert "NetVision-console.bat" in text, f"{script.name} に様子を見る起動が無い"
        assert "pywebview" in text and "pythonnet" in text, f"{script.name} に部品が無い"


def test_the_local_launchers_open_the_app_window():
    """手元の導入（start.sh / start.bat）もウィンドウで開く。URL を開かせない。"""
    for name in ("start.sh", "start.bat"):
        text = (REPO / "packaging" / name).read_text(encoding="utf-8")
        assert "scripts.launcher" in text, name
        assert "http://" not in text, name
    for name in ("install.sh", "install.bat"):
        text = (REPO / "packaging" / name).read_text(encoding="utf-8")
        assert "netvision-backend[desktop]" in text, name
        assert "http://" not in text, name


def test_the_launcher_module_is_bundled():
    """起動役そのものが同梱されていること。無いと起動しない。"""
    assert (REPO / "backend" / "scripts" / "launcher.py").is_file()
    for script in (FOR_WINDOWS, POWERSHELL):
        assert "scripts" in script.read_text(encoding="utf-8")


def test_the_windowed_launcher_leaves_a_record():
    """窓のある起動は、失敗しても何も出ないまま消える。記録だけが手がかりになる。"""
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


def test_the_model_can_be_bundled_without_adding_an_executable():
    """端末に新しいソフトを入れられない職場向け。

    モデルはアプリの中で動かす。増えるのは lib/ のライブラリだけで、実行ファイルは
    増えないので、ライブラリは既定で入れる。使う人がやることを「GGUF を1つ置く」
    だけにするため。モデルの実体は数 GB あるので、指定したときだけ入れる。
    """
    for script in (FOR_WINDOWS, POWERSHELL):
        text = script.read_text(encoding="utf-8")
        assert "llama-cpp-python" in text, f"{script.name} にモデルを動かす道が無い"
        assert "models" in text


def test_the_bundle_carries_every_guide():
    """手引きは丸ごと持ち込むこと。

    個別に並べていると、新しく書いたものが入らない。実際に LOCAL_LLM.md が
    抜けたまま配りかけた。渡した相手の手元では、書いてあるはずの手順が無い、
    という形で困る。
    """
    for script in (FOR_WINDOWS, POWERSHELL):
        text = script.read_text(encoding="utf-8")
        assert "docs/*.md" in text or "docs\\*.md" in text, (
            f"{script.name} が手引きを個別に並べている"
        )


SETUP_SCRIPT = REPO / "packaging" / "make-setup-windows.sh"
INSTALLER = REPO / "packaging" / "windows" / "installer.nsi"


def test_the_setup_installs_per_user_and_opens_the_app_window():
    """Setup.exe は利用者ごとの場所に入れ、ショートカットからアプリのウィンドウで開く。

    管理者の権限を求めると、職場の端末では入れられない。ショートカットが URL やバッチを
    指すと、ブラウザや黒い画面が開いてしまう。
    """
    nsi = INSTALLER.read_text(encoding="utf-8")
    assert "RequestExecutionLevel user" in nsi
    assert r'InstallDir "$LOCALAPPDATA\Programs\NetVision"' in nsi
    shortcuts = [line for line in nsi.splitlines() if line.strip().startswith("CreateShortcut")]
    app_links = [line for line in shortcuts if "NetVision.lnk" in line]
    assert len(app_links) == 2, "スタートメニューとデスクトップの両方に置く"
    for line in app_links:
        assert r"python\pythonw.exe" in line and "-m scripts.launcher" in line
    assert "http://" not in nsi and "127.0.0.1" not in nsi


def test_uninstalling_keeps_the_data_and_the_models():
    """消すのは入れたものだけ。データ（.netvision）とモデル（C:\\LLM）は入れた場所の外にあり、触らない。"""
    nsi = INSTALLER.read_text(encoding="utf-8")
    uninstall = nsi.split('Section "Uninstall"', 1)[1]
    # 消す命令の行だけを見る（説明のコメントと、残したことを知らせる文は除く）
    removals = [
        line.strip()
        for line in uninstall.splitlines()
        if line.strip().startswith(("RMDir", "Delete"))
    ]
    assert 'RMDir /r "$INSTDIR"' in removals
    for line in removals:
        assert ".netvision" not in line and "LLM" not in line and "$PROFILE" not in line, line


def test_the_setup_build_checks_for_missing_parts_before_packing():
    """欠けたまま組むと、入れた先で起動して初めて分かる。組む前に止める。"""
    script = SETUP_SCRIPT.read_text(encoding="utf-8")
    parts = ("python/pythonw.exe", "backend/scripts/launcher.py", "frontend/dist/index.html")
    for part in (*parts, "lib/webview"):
        assert part in script, part
    assert "make-bundle-windows.sh" in script, "ZIP と Setup.exe の中身をそろえる"
