"""アプリのウィンドウで開く起動役。

    pythonw.exe -m scripts.launcher

サーバーを裏で起動し、応答が返ったら NetVision の画面をアプリのウィンドウ
（pywebview。Windows は WebView2、macOS は WebKit、Linux は WebKitGTK か Qt）で開く。
ウィンドウを閉じたらサーバーも止める。ブラウザは開かない。URL を人に見せない。

止め方はウィンドウの×だけにする。黒い画面で動かしていたときは Ctrl+C しか無く、
Windows が続けて聞く「バッチ ジョブを終了しますか (Y/N)」で、どちらを押しても
意図とずれていた。アプリとして当たり前の止め方に揃える。

黙って消えないこと
------------------

窓のある起動では、標準の出力先が無い（pythonw では sys.stdout が None）。
何も書かずに落ちると、画面も出ず、記録も残らず、原因が一切分からない。
実際に「一瞬だけ黒い窓が出て、あとは何も起きない」という状態になった。

そのため、起動したら真っ先に記録の口を開き、置き場所に launcher.log を書く。
落ちたときは、その場でダイアログにも出す。同梱している Windows 版の Python
（embeddable）には tkinter が入っていないので、ダイアログは Windows の標準の
もの（ctypes 経由）を先に使う。
"""

from __future__ import annotations

import sys
import threading
import time
import urllib.request
from dataclasses import dataclass

from scripts import standalone

#: 起動の記録。何が起きたか分からないまま消えるのを防ぐ
LOG_NAME = "launcher.log"
#: ウィンドウの見出し
TITLE = "NetVision"
#: サーバーの応答を待つ上限（秒）。初回は DB の作成とマイグレーションが走る
READY_TIMEOUT = 60.0

NO_WINDOW = (
    "アプリのウィンドウを開く部品（pywebview）が見つかりません。\n\n"
    "配布された一式を使っている場合は、一式を作り直してください。\n"
    "手元で動かしている場合は、backend で `pip install -e .[desktop]` を実行してください。"
)
NOT_READY = "NetVision のサーバーが起動しませんでした。記録（launcher.log）を確認してください。"


@dataclass
class Runner:
    """サーバーの起動と停止。ウィンドウの作りから切り離しておく。

    ここをウィンドウと混ぜると、ウィンドウの出せない環境で試験できなくなる。
    """

    host: str = "127.0.0.1"
    port: int = 8000

    def __post_init__(self) -> None:
        self._server = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        import uvicorn

        config = uvicorn.Config("app.main:app", host=self.host, port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="netvision", daemon=True)
        self._thread.start()

    def wait_until_ready(self, timeout: float = READY_TIMEOUT) -> bool:
        """`/health` が返るまで待つ。返る前にウィンドウを開くと、空の画面が出る。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.running:
                return False
            try:
                with urllib.request.urlopen(f"{self.url}/health", timeout=2) as response:
                    if response.status == 200:
                        return True
            except OSError:
                pass
            time.sleep(0.2)
        return False

    def stop(self, timeout: float = 10.0) -> None:
        """止める。待っても終わらなければ、そのまま抜ける（daemon なので落ちる）。"""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=timeout)


def open_window(runner: Runner) -> int:
    """アプリのウィンドウを開き、閉じるまで待つ。閉じたらサーバーを止める。"""
    import webview

    webview.create_window(TITLE, runner.url, width=1360, height=860, min_size=(960, 600))
    try:
        webview.start()
    finally:
        # ウィンドウを閉じたのに動き続けるのが一番困る
        runner.stop()
    return 0


def attach_log(data_dir) -> None:
    """記録の口を開く。窓のある起動には出力先が無いので、まずここを作る。

    pythonw では sys.stdout / sys.stderr が None になる。そのままだと、
    uvicorn がログを書こうとした時点で落ち、しかも何も残らない。
    """
    from pathlib import Path

    path = Path(data_dir) / LOG_NAME
    try:
        stream = path.open("a", encoding="utf-8", buffering=1)
    except OSError:
        return
    # 黒い画面から動かしたときは、そちらの出力を潰さない
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except BaseException as exc:  # noqa: BLE001  何が起きても黙って消えない
        import traceback

        detail = "".join(traceback.format_exception(exc))
        try:
            print(detail, file=sys.stderr)
        except Exception:
            pass
        _report(f"NetVision を起動できませんでした。\n\n{detail[-1500:]}")
        return 1


def _main(argv: list[str] | None = None) -> int:
    args = standalone.build_parser().parse_args(argv)
    data_dir = standalone.prepare(
        args.data_dir or str(standalone.default_data_dir()), demo=args.demo
    )
    attach_log(data_dir)
    # AI のモデルは自作のアプリどうしで共有する置き場（C:\LLM か LLM_HOME）に置く
    from app.services import llm_inproc

    llm_inproc.prepare_shared_home()

    if standalone.frontend_dist() is None:
        _report(standalone.NO_FRONTEND)
        return 1

    # 部品が無いのに先にサーバーを立てると、ウィンドウが出ないまま動き続ける
    try:
        import webview  # noqa: F401
    except ImportError:
        _report(NO_WINDOW)
        return 1

    runner = Runner(host=args.host, port=args.port)
    runner.start()
    if not runner.wait_until_ready():
        # 止まったのか、遅いだけなのかで直し方が違う。記録に残してから知らせる
        waited = f"{READY_TIMEOUT:.0f} 秒待っても応答が無い"
        reason = "途中で止まった" if not runner.running else waited
        print(f"サーバーが起動しない: {reason}（{runner.url}/health）", file=sys.stderr)
        runner.stop()
        _report(NOT_READY)
        return 1
    return open_window(runner)


def _report(message: str) -> None:
    """窓のある起動では標準出力が誰にも見えないので、ダイアログで出す。

    同梱の Windows 版 Python には tkinter が無いので、Windows の標準のダイアログを先に試す。
    """
    try:
        print(message, file=sys.stderr)
    except Exception:
        pass
    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)
        if windll is not None:
            #: MB_ICONERROR
            windll.user32.MessageBoxW(None, message, TITLE, 0x00000010)
            return
    except Exception:
        pass
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(TITLE, message)
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
