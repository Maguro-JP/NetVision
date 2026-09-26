"""動かした位置で線を引き直す API（/topology/routes）。"""

from app.services import topology_routes
from tests.conftest import API


def test_an_empty_diagram_returns_nothing_to_draw(client) -> None:
    """機器の無い拠点を開いても 500 にしない。以前は空の図で KeyError になっていた。"""
    topology_routes.clear()
    site = client.post(f"{API}/sites", json={"code": "EMPTY", "name": "空の拠点"}).json()
    response = client.post(f"{API}/topology/routes", json={"site_id": site["id"]})
    assert response.status_code == 200
    assert response.json() == {"boxes": [], "wires": []}


def test_an_unknown_site_is_treated_as_empty(client) -> None:
    topology_routes.clear()
    response = client.post(f"{API}/topology/routes", json={"site_id": 99999})
    assert response.status_code == 200
    assert response.json() == {"boxes": [], "wires": []}


def test_the_boxes_of_the_devices_are_returned(client) -> None:
    topology_routes.clear()
    device = client.post(
        f"{API}/devices", json={"hostname": "sw1", "vendor": "Cisco", "model": "C9300-48P"}
    ).json()
    response = client.post(f"{API}/topology/routes", json={})
    assert response.status_code == 200
    boxes = response.json()["boxes"]
    assert [box["device_id"] for box in boxes] == [device["id"]]
    assert boxes[0]["width"] > 0
