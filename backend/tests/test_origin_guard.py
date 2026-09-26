"""別のサイトのページから届く書き換えを断る（app/originguard.py）。

ブラウザは multipart のファイル送信と本文の無い POST を事前確認なしに送る。
開いている別のサイトのページから、台帳の取り込みやバックアップの作成が動いていた。
"""

import pytest

from tests.conftest import API

EVIL = "https://evil.example"
CSV = b"hostname,vendor,model\nevil-sw,Cisco,C9300\n"


def _import(client, **headers):
    return client.post(
        f"{API}/import/devices",
        files={"file": ("d.csv", CSV, "text/csv")},
        headers=headers,
    )


def test_a_file_sent_from_another_site_is_refused(client):
    res = _import(client, Origin=EVIL)

    assert res.status_code == 403
    # 正規の利用者が当たったときに直し方が分かる
    assert "NETVISION_CORS_ORIGINS" in res.json()["detail"]
    assert client.get(f"{API}/devices").json() == []


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_every_write_method_is_checked(client, method):
    res = client.request(method.upper(), f"{API}/sites/1", headers={"Origin": EVIL})
    assert res.status_code == 403


@pytest.mark.parametrize(
    "origin",
    [
        None,  # curl・スクリプト・MCP
        "http://localhost:5173",  # CORS で許した宛先
        "http://127.0.0.1:5173",  # 手元の Vite
        "http://[::1]:5173",
        "http://testserver",  # 同じ宛先から配った画面
    ],
)
def test_requests_from_the_app_itself_are_accepted(client, origin):
    headers = {"Origin": origin} if origin else {}
    assert _import(client, **headers).status_code == 200


def test_reading_from_another_site_is_left_to_cors(client):
    assert client.get(f"{API}/devices", headers={"Origin": EVIL}).status_code == 200
