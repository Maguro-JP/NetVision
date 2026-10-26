"""アプリのウィンドウで開く起動役。

サーバーを裏で起動し、応答が返ってからウィンドウを開き、閉じたら止めること。
ブラウザを開かないこと。置き場所の決め方が黒い画面の起動と同じであることを見る。

本物のウィンドウ（pywebview）はこの環境で開けないので、同じ呼び方を受ける
偽物の webview を差し込んで、呼ばれ方と順番を見る。
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


class _FakeWebview:
    """pywebview の代わり。開いた URL と、閉じた時点のサーバーの状態を残す。"""

    def __init__(self, runner_box):
        self.runner_box = runner_box
        self.windows = []
        self.alive_when_shown = None

    def create_window(self, title, url, **kwargs):
        self.windows.append((title, url))

    def start(self):
        # ウィンドウが出ている間はサーバーが動いていること
        self.alive_when_shown = self.runner_box[0].running


def test_the_window_opens_on_the_server_and_closing_it_stops_the_server(monkeypatch, tmp_path):
    """応答が返ってからウィンドウを開き、閉じたらサーバーを止める。ブラウザは開かない。"""
    pytest.importorskip("uvicorn")
    monkeypatch.delenv("NETVISION_DATABASE_URL", raising=False)
    monkeypatch.delenv("NETVISION_DOCUMENT_STORAGE_PATH", raising=False)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setenv("NETVISION_SERVE_FRONTEND", str(dist))
    # 画面をビルドする前（CI の backend の試験など）でも、組んだ画面があるものとして動かす
    monkeypatch.setattr(standalone, "frontend_dist", lambda: dist)

    box = []
    original = launcher.Runner

    class Recorded(original):
        def __post_init__(self):
            super().__post_init__()
            box.append(self)

    fake = _FakeWebview(box)
    monkeypatch.setattr(launcher, "Runner", Recorded)
    monkeypatch.setitem(__import__("sys").modules, "webview", fake)
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: opened.append(a))

    port = _free_port()
    code = launcher.main(["--data-dir", str(tmp_path / "data"), "--port", str(port)])

    assert code == 0
    assert fake.windows == [("NetVision", f"http://127.0.0.1:{port}")]
    assert fake.alive_when_shown is True
    assert not box[0].running, "ウィンドウを閉じてもサーバーが動き続けている"
    assert opened == [], "ブラウザを開いている"


def test_without_the_window_part_it_says_so_instead_of_opening_a_browser(monkeypatch, tmp_path):
    """pywebview が無ければ、ブラウザで代わりに開かず、足りない部品を言って終わる。"""
    monkeypatch.delenv("NETVISION_DATABASE_URL", raising=False)
    monkeypatch.delenv("NETVISION_DOCUMENT_STORAGE_PATH", raising=False)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setenv("NETVISION_SERVE_FRONTEND", str(dist))
    # 画面をビルドする前（CI の backend の試験など）でも、組んだ画面があるものとして動かす
    monkeypatch.setattr(standalone, "frontend_dist", lambda: dist)
    monkeypatch.setitem(__import__("sys").modules, "webview", None)
    reported = []
    monkeypatch.setattr(launcher, "_report", reported.append)

    code = launcher.main(["--data-dir", str(tmp_path / "data"), "--port", str(_free_port())])

    assert code == 1
    assert reported == [launcher.NO_WINDOW]


def test_the_launcher_never_opens_a_browser():
    """起動の経路にブラウザを使わない（desktop-app の決まり）。"""
    from pathlib import Path

    text = Path(launcher.__file__).read_text(encoding="utf-8")
    assert "webbrowser" not in text
