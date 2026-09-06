"""ファーウェイ VRP を、NEC QX と取り違えないこと。

VRP と Comware は元が同じで書き方が重なる。メーカーを取り違えると、
保守期限も脆弱性も別のメーカーのもので判定してしまい、しかも黙って通る。
"""

from __future__ import annotations

from app.services import configparse
from app.services.configparse.base import Vendor

_VRP = "\n".join(
    [
        "<hw1>display version",
        "Huawei Versatile Routing Platform Software",
        "VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)",
        "sysname hw1",
        "#",
        "vlan 10",
        " description USERS",
        "#",
        "interface 10GE1/0/1",
        " port link-type trunk",
        " port trunk allow-pass vlan 10 20",
        "#",
        "interface Vlanif10",
        " ip address 10.0.10.1 255.255.255.0",
        "#",
        "ip route-static 0.0.0.0 0.0.0.0 10.0.10.254",
    ]
)


def test_vrp_is_read_as_huawei() -> None:
    parsed = configparse.parse(_VRP)
    assert parsed.vendor is Vendor.HUAWEI
    assert parsed.hostname == "hw1"
    assert parsed.model == "CE6850"
    assert parsed.os_version == "8.180"
    assert parsed.vlans == {10: "USERS"}

    ports = {item.name: item for item in parsed.interfaces}
    assert ports["Vlanif10"].ip_address == "10.0.10.1/24"
    assert [route.destination for route in parsed.routes] == ["0.0.0.0/0"]


def test_a_comware_config_is_still_read_as_nec_qx() -> None:
    text = "\n".join(
        [
            "sysname qx1",
            "#",
            "vlan 10",
            " name USERS",
            "#",
            "interface GigabitEthernet1/0/1",
            " port link-type trunk",
        ]
    )
    assert configparse.detect_vendor(text) is Vendor.NEC_QX


def test_an_unreadable_model_stays_empty_rather_than_blank() -> None:
    # 「読めなかった」を空文字にすると、型番が空欄の機器として台帳に入る
    parsed = configparse.parse("sysname qx1\n#\nvlan 10\n name USERS\n")
    assert parsed.model is None
