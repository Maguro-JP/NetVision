"""窓のある起動役。黒い画面を出さずに動かし、画面の中で終了できるようにする。

    pythonw.exe -m scripts.launcher

黒い画面で動かしていたときは、止め方が Ctrl+C しか無かった。Windows は
Ctrl+C のあとに「バッチ ジョブを終了しますか (Y/N)」と聞くが、その時点で
アプリはもう止まっている。ふつう Y は「終了する」の意味で押すので、
どちらを押しても意図とずれる、という状態だった。

そこで、小さな窓を1つ出す。窓には「画面を開く」と「終了する」しか無い。
窓を閉じても終了する。Windows のアプリとして当たり前の止め方に揃える。

窓の出し方は3段構えにしてある。同梱している Windows 版の Python（embeddable）
には tkinter が入っていないため、それだけに頼ると止め方が無くなる。

  1. tkinter があれば、「画面を開く」「終了する」の付いた窓
  2. 無ければ Windows の標準のダイアログ（ctypes 経由。追加の同梱物が要らない）
  3. どちらも出せなければ、黒い画面のまま待つ
"""

from __future__ import annotations

import sys
import threading
import webbrowser
from dataclasses import dataclass

from scripts import standalone


@dataclass
class Runner:
    """サーバーの起動と停止。窓の作りから切り離しておく。

    ここを窓と混ぜると、窓の出せない環境で試験できなくなる。
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

    def stop(self, timeout: float = 10.0) -> None:
        """止める。待っても終わらなければ、そのまま抜ける（daemon なので落ちる）。"""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def wait(self) -> None:
        """止まるまで待つ。"""
        while self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.5)

    def open_browser(self) -> None:
        webbrowser.open(self.url)


def _window(runner: Runner, data_dir: str) -> int:
    import tkinter as tk

    root = tk.Tk()
    root.title("NetVision")
    root.resizable(False, False)

    frame = tk.Frame(root, padx=16, pady=14)
    frame.pack()

    tk.Label(frame, text="NetVision は動いています", font=("", 11, "bold")).pack(anchor="w")
    tk.Label(frame, text=runner.url, fg="#1a5fb4").pack(anchor="w", pady=(4, 0))
    tk.Label(frame, text=f"データの置き場所: {data_dir}", fg="#555").pack(anchor="w")

    buttons = tk.Frame(frame, pady=12)
    buttons.pack(anchor="w")

    def quit_app() -> None:
        # 押し間違いで止めてしまうと、開いている画面が急に動かなくなる。
        # 一度だけ聞く
        from tkinter import messagebox

        if not messagebox.askyesno("NetVision", "NetVision を終了しますか。"):
            return
        # 先に窓を閉じて、押した手応えを出す。停止はそのあと
        root.destroy()

    tk.Button(buttons, text="画面を開く", width=12, command=runner.open_browser).pack(side="left")
    tk.Button(buttons, text="終了する", width=12, command=quit_app).pack(side="left", padx=(8, 0))

    tk.Label(
        frame,
        text="この窓を閉じても終了します。",
        fg="#555",
    ).pack(anchor="w")

    # 窓の×も「終了する」と同じ扱いにする。閉じたのに動き続けるのが一番困る
    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.mainloop()
    runner.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = standalone.build_parser().parse_args(argv)
    data_dir = standalone.prepare(args.data_dir, demo=args.demo)

    if standalone.frontend_dist() is None:
        _report(standalone.NO_FRONTEND)
        return 1

    runner = Runner(host=args.host, port=args.port)
    runner.start()
    runner.open_browser()

    try:
        import tkinter  # noqa: F401
    except ImportError:
        pass
    else:
        return _window(runner, str(data_dir))

    if _dialog(runner, str(data_dir)):
        return 0

    # どちらも出せない環境。黒い画面のまま待つ
    print(f"NetVision は動いています: {runner.url}")
    print("止めるときは Ctrl+C")
    try:
        runner.wait()
    except KeyboardInterrupt:
        pass
    runner.stop()
    return 0


def _dialog(runner: Runner, data_dir: str) -> bool:
    """Windows の標準のダイアログで待つ。閉じたら止める。

    同梱の Python には tkinter が入っていないので、ここが実際に使われる道。
    ctypes は標準で付いてくるため、同梱物を増やさずに済む。
    """
    try:
        import ctypes
    except ImportError:
        return False
    user32 = getattr(ctypes, "windll", None)
    if user32 is None:
        return False

    text = (
        "NetVision は動いています。\n\n"
        f"画面 : {runner.url}\n"
        f"データの置き場所 : {data_dir}\n\n"
        "終了するときは、このダイアログの「OK」を押してください。"
    )
    #: MB_ICONINFORMATION / MB_YESNO+MB_ICONQUESTION / IDYES
    info, ask, yes = 0x00000040, 0x00000024, 6
    while True:
        try:
            ctypes.windll.user32.MessageBoxW(None, text, "NetVision", info)
            # 押し間違いで止めてしまうと、開いている画面が急に動かなくなる。
            # 一度だけ聞き、違うなら動かしたまま元のダイアログへ戻る
            answer = ctypes.windll.user32.MessageBoxW(
                None, "NetVision を終了しますか。", "NetVision", ask
            )
        except Exception:
            return False
        if answer == yes:
            break
    runner.stop()
    return True


def _report(message: str) -> None:
    """窓のある起動では標準出力が誰にも見えないので、出せるなら窓で出す。"""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("NetVision", message)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
