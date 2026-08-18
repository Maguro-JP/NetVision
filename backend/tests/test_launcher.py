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
