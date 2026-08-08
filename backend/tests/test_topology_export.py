"""構成図のファイル出力。設計書や報告書に貼るための SVG。"""

from pathlib import Path
from xml.etree import ElementTree

from tests.conftest import API

FIXTURES = Path(__file__).parent / "fixtures"


def build_topology(client):
    files = [
        ("files", (p.name, p.read_bytes(), "text/plain")) for p in sorted(FIXTURES.glob("*.txt"))
    ]
    client.post(f"{API}/configs/upload", files=files, data={"create_missing": "true"})
    client.post(f"{API}/topology/rebuild")


def fetch(client, **params):
    return client.get(f"{API}/topology/export.svg", params=params)


def test_export_returns_a_valid_svg_file(client):
    build_topology(client)
    res = fetch(client)

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/svg+xml")
    assert "attachment" in res.headers["content-disposition"]
    assert "topology-physical.svg" in res.headers["content-disposition"]

    # 壊れた XML を渡すと Office で開けないので、必ず構文を確かめる
    root = ElementTree.fromstring(res.text)
    assert root.tag.endswith("svg")


def test_export_contains_the_devices_and_links(client):
    build_topology(client)
    body = fetch(client).text

    assert "core-sw-01" in body
    assert "C9500-40X" in body
    # 結線が線として入っていること
    assert "<line" in body


def test_export_is_vector_not_an_image(client):
    """スクリーンショットではなくベクタで出す（拡大・印刷で潰れないため）。"""
    build_topology(client)
    body = fetch(client).text

    assert "<rect" in body and "<text" in body
    assert "base64" not in body


def test_export_has_a_legend_and_timestamp(client):
    """資料に貼ったとき、いつ時点の図かが分からないと使えない。"""
    build_topology(client)
    body = fetch(client).text

    assert "時点" in body
    assert "保守・EoL の状態" in body
    assert "両側の機器から確認できた結線" in body


def test_title_can_be_given(client):
    build_topology(client)
    body = fetch(client, title="本社ネットワーク構成図").text
    assert "本社ネットワーク構成図" in body


def test_special_characters_are_escaped(client, device_payload):
    """ホスト名に < や & が入っても壊れた SVG にしない。"""
    client.post(f"{API}/devices", json=device_payload(hostname="sw<1>&test"))
    body = fetch(client).text

    ElementTree.fromstring(body)  # 壊れていないこと
    assert "&lt;" in body or "&amp;" in body


def test_empty_topology_is_handled(client):
    res = fetch(client)
    assert res.status_code == 200
    ElementTree.fromstring(res.text)
    assert "機器がありません" in res.text


def test_layer_is_reflected(client):
    build_topology(client)
    res = fetch(client, layer="l3")
    assert "topology-l3.svg" in res.headers["content-disposition"]
    assert "レイヤ: l3" in res.text
