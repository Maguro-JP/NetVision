"""JSON の本文にある NaN / Infinity を、読む前に断る。

Python の json は標準の外の NaN・Infinity・-Infinity も数として読む。
そのまま通すと、金額や図の位置に無限大が保存されたり、検証エラーの応答に
その値を載せようとして JSON に書けず、500 になったりする。
ブラウザの JSON.stringify はこれらを出さない（NaN は null になる）ので、
画面からの操作には影響しない。項目ごとに型で塞ぐと抜けが出るので、入口で塞ぐ。
"""

from __future__ import annotations

import json
import math

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_WITH_BODY = frozenset({"POST", "PUT", "PATCH"})


class _NonFinite(ValueError):
    pass


def _refuse(constant: str) -> float:
    raise _NonFinite(constant)


def _finite(text: str) -> float:
    """1e400 のように、書き方は数でも float に直すと無限大になるものも断る。"""
    value = float(text)
    if math.isinf(value):
        raise _NonFinite(text)
    return value


class RejectNonFiniteJson:
    """application/json の本文に NaN・Infinity があれば 422 を返す。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _WITH_BODY:
            await self.app(scope, receive, send)
            return
        content_type = dict(scope["headers"]).get(b"content-type", b"")
        if content_type.split(b";")[0].strip().lower() != b"application/json":
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        first: Message | None = None
        while True:
            message = await receive()
            if message["type"] != "http.request":
                first = message  # 切断など。下流にそのまま渡す
                break
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)

        if first is None:
            try:
                json.loads(body or b"null", parse_constant=_refuse, parse_float=_finite)
            except _NonFinite as refused:
                response = JSONResponse(
                    status_code=422,
                    content={
                        "detail": f"数として {refused} は使えません。"
                        "有限の数か null を送ってください"
                    },
                )
                await response(scope, receive, send)
                return
            except ValueError:
                pass  # 壊れた JSON は、今までどおり FastAPI が 422 で返す

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                if first is not None and not chunks:
                    return first
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
