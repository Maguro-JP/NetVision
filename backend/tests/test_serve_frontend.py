"""NETVISION_SERVE_FRONTEND で画面を配るときに、配信元の外を返さないことの試験。

httpx（TestClient）は送る前に ".." を畳むので、ASGI を直接呼んで生のパスを渡す。
uvicorn は %2F を解いたパスをそのまま渡すため、ここで渡す形が実際に届く形になる。
"""

import asyncio

import pytest
from fastapi import FastAPI

from app import main


def _get(app: FastAPI, path: str) -> tuple[int, bytes]:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "http_version": "1.1",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 1),
        "root_path": "",
    }
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b""}

    async def send(message: dict) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    body = b"".join(m.get("body", b"") for m in messages[1:])
    return messages[0]["status"], body


@pytest.fixture
def served(tmp_path, monkeypatch) -> FastAPI:
    site = tmp_path / "dist"
    (site / "assets").mkdir(parents=True)
    (site / "index.html").write_text("INDEX", encoding="utf-8")
    (site / "favicon.svg").write_text("ICON", encoding="utf-8")
    (tmp_path / "netvision.db").write_text("SECRET", encoding="utf-8")
    fresh = FastAPI()
    monkeypatch.setattr(main, "app", fresh)
    main._mount_frontend(str(site))
    return fresh


def test_files_inside_the_site_are_served(served):
    assert _get(served, "/favicon.svg") == (200, b"ICON")
    assert _get(served, "/devices/12") == (200, b"INDEX")


@pytest.mark.parametrize(
    "path",
    ["/../netvision.db", "/assets/../../netvision.db", "/x/../../netvision.db"],
)
def test_a_path_climbing_out_of_the_site_is_not_served(served, path):
    # /assets 配下は StaticFiles が 404 を返し、それ以外は画面の入口に落とす。
    status, body = _get(served, path)
    assert b"SECRET" not in body
    assert (status, body) in {(200, b"INDEX"), (404, b'{"detail":"Not Found"}')}
