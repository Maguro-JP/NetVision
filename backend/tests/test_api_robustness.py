"""崩れた要求や入力で 500 を返さないこと。経路や項目を足したときの再発を防ぐ。

個別の不具合の試験は、それぞれの機能の試験に置いてある。ここは OpenAPI の定義と
見本の Config から自動で回すので、新しい経路・項目・読み手を足すと自動で対象に入る。
"""

import glob
import random
from pathlib import Path

import pytest

from app.main import app
from app.services import configparse
from tests.conftest import API

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def seeded(client):
    """更新の経路が ID 1 で当たるものを、ひととおり作っておく。"""
    site = client.post(f"{API}/sites", json={"code": "HQ", "name": "本社"}).json()
    client.post(
        f"{API}/devices",
        json={"hostname": "sw1", "vendor": "Cisco", "model": "C9300-48P", "site_id": site["id"]},
    )
    scenario = client.post(f"{API}/scenarios", json={"name": "更改"}).json()
    for url, body in [
        (f"{API}/reference/products", {"vendor": "Cisco", "model": "C9300-48P"}),
        (f"{API}/topology/vlans/catalog", {"vlan_id": 10, "name": "users"}),
        (f"{API}/topology/groups", {"name": "g1", "device_ids": [1]}),
        (
            f"{API}/devices/1/contracts",
            {"provider": "保守会社", "start_date": "2026-01-01", "end_date": "2027-01-01"},
        ),
        (f"{API}/scenarios/{scenario['id']}/items", {"action": "replace", "device_id": 1}),
    ]:
        assert client.post(url, json=body).status_code < 300, url
    return client


def _update_operations() -> list[tuple[str, str, list[str]]]:
    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    found = []
    for path, operations in spec["paths"].items():
        for method in ("patch", "put"):
            operation = operations.get(method)
            if not operation:
                continue
            content = operation.get("requestBody", {}).get("content", {})
            schema = content.get("application/json", {}).get("schema")
            if not schema:
                continue
            while "$ref" in schema:
                schema = schemas[schema["$ref"].split("/")[-1]]
            fields = sorted(schema.get("properties", {}))
            if fields:
                found.append((method, path, fields))
    return found


@pytest.mark.parametrize(("method", "path", "fields"), _update_operations())
def test_null_in_any_update_field_is_not_a_server_error(seeded, method, path, fields):
    """必須の列に null を送ると NOT NULL 違反の 500 になっていた（機器・拠点・契約など）。"""
    url = path
    for name in ("device_id", "site_id", "scenario_id", "item_id", "product_id", "contract_id"):
        url = url.replace("{" + name + "}", "1")
    url = url.replace("{vlan_id}", "10").replace("{group_id}", "1")
    for field in fields:
        response = seeded.request(method.upper(), url.replace("/api/v1", API), json={field: None})
        assert response.status_code < 500, f"{method} {path} {field}: {response.status_code}"


def _mutated(text: str, rnd: random.Random) -> str:
    lines = text.splitlines()
    for _ in range(rnd.randrange(1, 4)):
        if not lines:
            break
        i = rnd.randrange(len(lines))
        op = rnd.randrange(4)
        if op == 0:
            del lines[i]
        elif op == 1:
            lines[i] = lines[i][: rnd.randrange(len(lines[i]) + 1)]
        elif op == 2:
            lines[i] += " " + rnd.choice(["x", "999999", "-", "/", "0.0.0.0", "dynamic", '"'])
        else:
            lines = lines[:i]
    return "\n".join(lines)


@pytest.mark.parametrize(
    "fixture", sorted(Path(p).name for p in glob.glob(str(FIXTURES / "*.txt")))
)
def test_a_broken_config_does_not_stop_the_reader(fixture):
    """行が切れた・語が足された Config で、読み手が例外を出さない。

    以前は `switchport access vlan dynamic`（IOS）や切れたマスク（EXOS）で
    int() が失敗し、その Config 全体が読めなかった。種を固定して毎回同じ入力で見る。
    """
    text = (FIXTURES / fixture).read_text(encoding="utf-8")
    rnd = random.Random(fixture)
    for _ in range(30):
        configparse.parse(_mutated(text, rnd))


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_non_finite_numbers_in_json_are_refused(seeded, literal):
    """NaN・Infinity は保存も 500 もせず 422 で断る。

    以前は Infinity がそのまま保存され、NaN は検証エラーの応答が JSON に書けず 500 だった。
    """
    body = (
        '{"provider": "p", "start_date": "2026-01-01", "end_date": "2027-01-01", '
        f'"annual_cost": {literal}}}'
    )
    response = seeded.post(
        f"{API}/devices/1/contracts", content=body, headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert literal in response.json()["detail"]


def test_ordinary_and_broken_json_behave_as_before(seeded):
    ok = seeded.post(
        f"{API}/devices/1/contracts",
        json={
            "provider": "p",
            "start_date": "2026-01-01",
            "end_date": "2027-01-01",
            "annual_cost": 1.5e5,
        },
    )
    assert ok.status_code == 201
    broken = seeded.post(
        f"{API}/devices/1/contracts", content="{", headers={"content-type": "application/json"}
    )
    assert broken.status_code == 422
