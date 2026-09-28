"""別のサイトのページから届く書き換えを、入口で断る。

認証が無く 127.0.0.1 で待ち受けているので、利用者がブラウザで開いた別のサイトの
ページからも要求は届く。CORS は応答を読ませないだけで、送ること自体は止めない。
JSON の本文は事前確認（preflight）で止まるが、multipart のファイル送信と本文の無い
POST は事前確認なしに送られ、台帳の取り込みやバックアップの作成がそのまま動いていた。

ブラウザは別のサイトからの POST・PUT・PATCH・DELETE に必ず Origin を付ける。
Origin が付いていて、それが同じ宛先（Host と同じ）でも CORS で許した宛先でもなければ 403 にする。
手元（localhost・127.0.0.1・::1）の Origin も通す。開発時の Vite は Host を書き換えて
送るので、127.0.0.1:5173 で開いた画面は Host と一致しない。別のサイトのページは
手元の Origin を名乗れない。
Origin の無い要求（curl、スクリプト、MCP）はブラウザからではないので通す。
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


class RejectCrossSiteWrites:
    """別のサイトの Origin が付いた書き換えの要求に 403 を返す。"""

    def __init__(self, app: ASGIApp, allowed_origins: list[str]) -> None:
        self.app = app
        self.any_origin = "*" in allowed_origins
        self.allowed = {origin.rstrip("/").lower() for origin in allowed_origins}

    def _is_allowed(self, scope: Scope, origin: str) -> bool:
        if self.any_origin or origin.rstrip("/").lower() in self.allowed:
            return True
        if urlsplit(origin).hostname in _LOOPBACK:
            return True
        # 同じ宛先から配った画面（NETVISION_SERVE_FRONTEND、LAN の IP で開いた場合も）は通す
        host = _header(scope, b"host")
        return host is not None and urlsplit(origin).netloc.lower() == host.lower()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in _WRITES:
            origin = _header(scope, b"origin")
            if origin is not None and not self._is_allowed(scope, origin):
                response = JSONResponse(
                    {
                        "detail": f"別のサイト（{origin}）からの書き換えは受け付けません。"
                        "この画面を正規に使っているなら、NETVISION_CORS_ORIGINS に"
                        "この宛先を足してください"
                    },
                    status_code=403,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
