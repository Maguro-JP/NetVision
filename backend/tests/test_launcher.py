"""窓のある起動役。

黒い画面で動かしていたときは、止め方が Ctrl+C しか無く、そのあとに Windows が
出す「バッチ ジョブを終了しますか」で、どちらを押しても意図とずれていた。
窓から止められること、置き場所の決め方が黒い画面と同じであることを見る。

窓そのもの（tkinter）はこの環境に無いので、サーバーの起動と停止だけを試す。
"""

import socket

import pytest

from scripts import launcher, standalone


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_the_data_folder_is_decided_the_same_way(tmp_path, monkeypatch):
    """置き場所の決め方は、黒い画面の起動と同じところを通す。
    片方だけ直すと、同じアプリなのに置き場所が違うことになる。"""
    monkeypatch.delenv("NETVISION_DATABASE_URL", raising=False)
    monkeypatch.delenv("NETVISION_DOCUMENT_STORAGE_PATH", raising=False)

    path = standalone.prepare(str(tmp_path / "data"))

    assert path.is_dir()
    assert str(path / "netvision.db") in __import__("os").environ["NETVISION_DATABASE_URL"]


def test_the_runner_starts_and_stops():
    """窓から止められること。止まらないと、次に起動したときに港が塞がる。"""
    pytest.importorskip("uvicorn")
    runner = launcher.Runner(port=_free_port())
    runner.start()
    for _ in range(100):
        if runner.running:
            break
    assert runner.running

    runner.stop(timeout=15)
    assert not runner.running


def test_the_url_points_at_this_machine_only():
    """既定は自分の端末だけ。認証がまだ無いので、ここは緩めない。"""
    assert launcher.Runner().url == "http://127.0.0.1:8000"


def test_it_can_wait_without_tkinter(monkeypatch):
    """同梱の Windows 版 Python には tkinter が入っていない。

    それだけに頼ると、窓が出ないまま止め方が無くなる。標準のダイアログへ
    落ちる道があることを見る（この環境は Windows ではないので、道が
    用意されていることだけを確かめる）。
    """
    runner = launcher.Runner(port=_free_port())
    assert launcher._dialog(runner, "/tmp") is False, "Windows 以外では使わない"
    assert hasattr(launcher.Runner, "wait")


def test_a_synced_folder_is_called_out(monkeypatch, tmp_path):
    """OneDrive の中やネットワークの共有に置くと、起動が戻ってこないことがある。

    実際に「一瞬だけ黒い窓が出て、あとは何も起きない」という形で踏んだ。
    黙って遅いより、置き場所を言えたほうが早い。
    """
    monkeypatch.setattr(standalone, "REPO", tmp_path / "OneDrive - Business1" / "NetVision")
    assert standalone.on_synced_folder()

    monkeypatch.setattr(standalone, "REPO", tmp_path / "NetVision")
    assert standalone.on_synced_folder() is None


def test_the_data_folder_can_be_set_without_touching_the_command(monkeypatch, tmp_path):
    """置き場所を環境変数でも渡せること。

    画面を触れない端末では、起動用のファイルの中で移せる必要がある。
    C ドライブにデータを置けない決まりの端末がある。
    """
    monkeypatch.setenv(standalone.DATA_DIR_ENV, str(tmp_path / "elsewhere"))
    assert standalone.default_data_dir() == tmp_path / "elsewhere"

    monkeypatch.delenv(standalone.DATA_DIR_ENV)
    assert standalone.default_data_dir() == standalone.DEFAULT_DATA


def test_the_log_is_written_next_to_the_data(tmp_path):
    """記録は置き場所に残す。窓のある起動には出力先が無いので、ここだけが手がかり。"""
    launcher.attach_log(tmp_path)
    assert (tmp_path / launcher.LOG_NAME).exists()
