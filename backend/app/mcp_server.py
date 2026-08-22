"""NetVision を MCP サーバーとして立てる口。

MCP は、AI の道具箱に自作の道具を足すための取り決め。標準入出力で
JSON-RPC をやり取りするだけなので、外へ出る通信は起きない。閉域の
まま、手元の AI から台帳を引けるようにするのが狙い。

置ける道具は読み取りだけ（mcp_tools を参照）。書き換えは画面から行う。

つなぎ方は docs/MCP.md に書いてある。
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from app.services import mcp_tools

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "netvision"


def _result(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _text(payload: dict[str, Any], *, failed: bool = False) -> dict[str, Any]:
    """道具の答えを MCP の形にする。中身は JSON の文字列で渡す。"""
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1)}],
        "isError": failed,
    }


def handle(message: dict[str, Any], *, session_factory: Any = None) -> dict[str, Any] | None:
    """要求を1件処理する。通知（id 無し）には返さない。"""
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": _version()},
            },
        )
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": mcp_tools.descriptors()})
    if method == "tools/call":
        return _call(request_id, params, session_factory)
    if request_id is None:
        return None
    return _error(request_id, -32601, f"知らない手続きです: {method}")


def _version() -> str:
    from app.main import app

    return app.version


def _call(request_id: Any, params: dict[str, Any], session_factory: Any) -> dict[str, Any]:
    name = params.get("name") or ""
    args = params.get("arguments") or {}
    if session_factory is None:
        from app.db import new_session

        session_factory = new_session
    db = session_factory()
    try:
        payload = mcp_tools.call(db, name, args)
    except KeyError:
        return _result(
            request_id, _text({"message": f"そんな道具はありません: {name}"}, failed=True)
        )
    except Exception as exc:  # 道具の失敗で口ごと落とさない
        return _result(
            request_id, _text({"message": f"うまくいきませんでした: {exc}"}, failed=True)
        )
    finally:
        db.close()
    return _result(request_id, _text(payload))


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    """標準入出力で待つ。1行に1つの JSON。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            reply: dict[str, Any] | None = _error(None, -32700, "JSON として読めません")
        else:
            reply = handle(message)
        if reply is None:
            continue
        stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        stdout.flush()


if __name__ == "__main__":  # pragma: no cover - 入口
    serve()
