"""MCP の口の試験。読むだけであること、道具が動くことを見る。"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.mcp_server import handle
from app.models import Device, DeviceType, Site
from app.models.topology import Link
from app.services import mcp_tools


@pytest.fixture()
def stocked(db_session):
    site = Site(name="本社")
    db_session.add(site)
    db_session.flush()
    core = Device(
        hostname="core-sw-01",
        code="NW-0001",
        label="本社コア1号機",
        vendor="Cisco",
        model="C9500-40X",
        device_type=DeviceType.SWITCH,
        site_id=site.id,
        eol_date=date(2020, 1, 1),
    )
    edge = Device(hostname="edge-sw-01", code="NW-0002", vendor="NEC", model="QX-S5")
    db_session.add_all([core, edge])
    db_session.flush()
    db_session.add(
        Link(
            a_device_id=core.id,
            a_interface="Gi1/0/1",
            b_device_id=edge.id,
            b_interface="Gi0/1",
        )
    )
    db_session.commit()
    return db_session


def _call(db, name, args=None):
    reply = handle(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        },
        session_factory=lambda: db,
    )
    body = reply["result"]
    return body["isError"], json.loads(body["content"][0]["text"])


def test_initialize_answers_with_the_protocol(stocked):
    reply = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert reply["result"]["serverInfo"]["name"] == "netvision"
    assert reply["result"]["capabilities"] == {"tools": {}}


def test_notifications_get_no_answer():
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_is_an_error():
    reply = handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
    assert reply["error"]["code"] == -32601


def test_tools_are_all_read_only():
    names = {t["name"] for t in mcp_tools.descriptors()}
    assert names == {"list_devices", "get_device", "device_summary", "list_links", "list_sites"}


def test_list_devices_finds_by_any_word(stocked):
    failed, payload = _call(stocked, "list_devices", {"q": "Cisco"})
    assert not failed
    assert payload["total"] == 1
    assert payload["devices"][0]["hostname"] == "core-sw-01"


def test_list_devices_caps_the_count(stocked):
    _, payload = _call(stocked, "list_devices", {"limit": 9999})
    assert payload["shown"] <= mcp_tools.MAX_LIMIT


def test_get_device_takes_a_code_or_a_hostname_or_a_label(stocked):
    for key in ("NW-0001", "core-sw-01", "本社コア1号機"):
        _, payload = _call(stocked, "get_device", {"device": key})
        assert payload["found"], key
        assert payload["device"]["hostname"] == "core-sw-01"


def test_get_device_says_so_when_missing(stocked):
    _, payload = _call(stocked, "get_device", {"device": "そんな機器"})
    assert payload["found"] is False


def test_get_device_carries_the_links(stocked):
    _, payload = _call(stocked, "get_device", {"device": "NW-0001"})
    assert payload["device"]["links"] == [
        {
            "interface": "Gi1/0/1",
            "peer": "edge-sw-01",
            "peer_interface": "Gi0/1",
            "origin": "discovered",
            "confirmed_both_ways": False,
        }
    ]


def test_links_are_seen_from_the_asked_side(stocked):
    _, payload = _call(stocked, "list_links", {"device": "edge-sw-01"})
    assert payload["links"][0]["interface"] == "Gi0/1"
    assert payload["links"][0]["peer"] == "core-sw-01"


def test_summary_counts_the_whole_ledger(stocked):
    _, payload = _call(stocked, "device_summary")
    assert payload["total"] == 2
    assert payload["by_severity"]["expired"] == 1
    assert {"vendor": "Cisco", "count": 1} in payload["by_vendor"]


def test_sites_carry_their_device_count(stocked):
    _, payload = _call(stocked, "list_sites")
    assert payload["sites"] == [
        {
            "id": payload["sites"][0]["id"],
            "code": None,
            "name": "本社",
            "status": "active",
            "devices": 1,
        }
    ]


def test_an_unknown_tool_answers_instead_of_falling_over(stocked):
    failed, payload = _call(stocked, "delete_everything")
    assert failed
    assert "そんな道具" in payload["message"]
