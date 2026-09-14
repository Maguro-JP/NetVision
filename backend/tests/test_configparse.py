from pathlib import Path

import pytest

from app.services import configparse
from app.services.configparse import Vendor, detect_vendor, expand, normalize_hostname, parse

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# インターフェース名の正規化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Gi0/1", "GigabitEthernet0/1"),
        ("Gig 0/1", "GigabitEthernet0/1"),
        ("GigabitEthernet1/0/24", "GigabitEthernet1/0/24"),
        ("Te1/1/1", "TenGigabitEthernet1/1/1"),
        ("Fas 0/1", "FastEthernet0/1"),
        ("Po1", "Port-channel1"),
        ("Vl10", "Vlan10"),
        ("ge-0/0/0", "ge-0/0/0"),
        ("port1", "port1"),
        ("GE1/0/1", "GigabitEthernet1/0/1"),
        ("Ten-GigabitEthernet1/0/1", "TenGigabitEthernet1/0/1"),
        ("GigaEthernet0.1", "GigaEthernet0.1"),
    ],
)
def test_interface_names_are_normalized(raw, expected):
    assert expand(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sw2.example.com", "sw2"),
        ("acc-sw-01(FOC1234ABCD)", "acc-sw-01"),
        ("CORE-SW-01", "core-sw-01"),
    ],
)
def test_hostnames_are_normalized_for_matching(raw, expected):
    assert normalize_hostname(raw) == expected


# ---------------------------------------------------------------------------
# ベンダー判別
# ---------------------------------------------------------------------------


def test_vendor_detection():
    assert detect_vendor(load("core-sw-01")) is Vendor.CISCO_IOS
    assert detect_vendor(load("edge-rt-01")) is Vendor.JUNIPER
    assert detect_vendor(load("fw-01")) is Vendor.FORTINET
    assert detect_vendor("これはただのメモです") is Vendor.UNKNOWN


# ---------------------------------------------------------------------------
# Cisco
# ---------------------------------------------------------------------------


def test_cisco_identity_and_inventory():
    parsed = parse(load("core-sw-01"))

    assert parsed.hostname == "core-sw-01"
    assert parsed.os_name == "IOS-XE"
    assert parsed.os_version == "17.09.04a"
    assert parsed.model == "C9500-40X"
    assert parsed.serial_number == "FOC2401A001"


def test_cisco_interfaces_and_vlans():
    parsed = parse(load("core-sw-01"))
    interfaces = {i.name: i for i in parsed.interfaces}

    assert interfaces["GigabitEthernet1/0/1"].description == "->edge-rt-01"
    assert interfaces["GigabitEthernet1/0/1"].mode == "trunk"

    unused = interfaces["GigabitEthernet1/0/24"]
    assert unused.vlan == 20
    assert unused.enabled is False  # shutdown を拾う

    assert interfaces["Vlan10"].ip_address == "10.0.0.11/24"  # サブネットマスクを長さに直す
    assert parsed.vlans == {10: "SERVER", 20: "OFFICE"}


def test_cisco_cdp_detail_neighbors():
    parsed = parse(load("core-sw-01"))
    neighbors = {n.remote_key: n for n in parsed.neighbors}

    assert set(neighbors) == {"edge-rt-01", "acc-sw-osaka-01"}

    juniper = neighbors["edge-rt-01"]
    assert juniper.local_interface == "GigabitEthernet1/0/1"
    assert juniper.remote_interface == "ge-0/0/0"
    assert juniper.remote_platform == "Juniper MX204"
    # FQDN やシリアル付きでも機器名として突き合わせられる
    assert neighbors["acc-sw-osaka-01"].remote_interface == "GigabitEthernet0/24"


def test_cisco_cdp_table_neighbors():
    """`show cdp neighbors`（表形式）でも、略記を正式名に直して読む。"""
    parsed = parse(load("acc-sw-osaka-01"))
    neighbors = {n.remote_key: n for n in parsed.neighbors}

    core = neighbors["core-sw-01"]
    assert core.local_interface == "GigabitEthernet0/24"
    assert core.remote_interface == "GigabitEthernet1/0/2"
    assert "unmanaged-hub" in neighbors


# ---------------------------------------------------------------------------
# Juniper
# ---------------------------------------------------------------------------


def test_juniper_config_and_lldp():
    parsed = parse(load("edge-rt-01"))

    assert parsed.vendor is Vendor.JUNIPER
    assert parsed.hostname == "edge-rt-01"
    assert parsed.os_name == "Junos"
    assert parsed.os_version == "21.4R3-S5.4"
    assert parsed.model == "mx204"
    assert parsed.serial_number is None  # LLDP 表の "Chassis Id" を誤検出しない

    interfaces = {i.name: i for i in parsed.interfaces}
    assert interfaces["ge-0/0/0"].ip_address == "10.0.0.1/24"
    # SRX のゾーンは、PAN-OS と同じくポートの説明に足す。
    # どの口が外向きかは、ゾーンが分からないと図の上で言えない
    assert interfaces["ge-0/0/0"].description == "->core-sw-01 / zone trust"
    assert interfaces["ge-0/0/2"].enabled is False

    assert len(parsed.neighbors) == 1
    assert parsed.neighbors[0].remote_key == "core-sw-01"
    assert parsed.neighbors[0].local_interface == "ge-0/0/0"


# ---------------------------------------------------------------------------
# Fortinet
# ---------------------------------------------------------------------------


def test_fortinet_config():
    parsed = parse(load("fw-01"))

    assert parsed.vendor is Vendor.FORTINET
    assert parsed.hostname == "fw-01"
    assert parsed.os_name == "FortiOS"
    assert parsed.os_version == "7.2.8"
    assert parsed.model == "FGT100F"

    interfaces = {i.name: i for i in parsed.interfaces}
    assert interfaces["port1"].ip_address == "10.1.0.1/24"
    assert interfaces["port1"].description == "LAN"
    assert interfaces["port2"].ip_address == "203.0.113.2/30"
    assert interfaces["port8"].enabled is False


# ---------------------------------------------------------------------------
# 頑健性
# ---------------------------------------------------------------------------


def test_empty_and_garbage_input_does_not_raise():
    for text in ("", "   ", "全く関係ないテキスト\n1行目\n2行目"):
        parsed = parse(text)
        assert parsed.interfaces == []
        assert parsed.neighbors == []


def test_partial_config_returns_what_it_can():
    """ホスト名しか無いファイルでも、読めた分は返す。"""
    parsed = parse("hostname sw-partial\n")
    assert parsed.hostname == "sw-partial"
    assert parsed.interfaces == []


def test_duplicate_interface_blocks_are_merged():
    text = (
        "hostname sw-1\n"
        "interface GigabitEthernet0/1\n"
        " description first\n"
        "!\n"
        "interface Gi0/1\n"
        " switchport access vlan 30\n"
        "!\n"
    )
    parsed = parse(text)

    assert len(parsed.interfaces) == 1
    assert parsed.interfaces[0].description == "first"
    assert parsed.interfaces[0].vlan == 30


def test_cdp_and_lldp_for_the_same_link_are_merged():
    text = (
        "hostname sw-1\n"
        "sw-1#show cdp neighbors detail\n"
        "-------------------------\n"
        "Device ID: sw-2\n"
        "Interface: GigabitEthernet0/1,  Port ID (outgoing port): GigabitEthernet0/2\n"
        "\n"
        "sw-1#show lldp neighbors detail\n"
        "-------------------------\n"
        "Local Intf: Gi0/1\n"
        "Port id: Gi0/2\n"
        "System Name: sw-2\n"
    )
    parsed = parse(text)

    assert len(parsed.neighbors) == 1


def test_every_vendor_has_a_label_and_a_name():
    """ベンダーを足したら、表示名と台帳に書く名前も必ず足す。

    どちらかが抜けていると、画面では「判別できません」、台帳では空欄に
    なる。読めているのに空で出るのがいちばん分かりにくいので、増やした
    ときにここで止める。
    """
    for vendor in Vendor:
        assert vendor in configparse.VENDOR_LABELS, vendor
        if vendor is Vendor.UNKNOWN:
            continue  # 判別できなかったものは、台帳の「メーカー」を空で出す
        assert vendor in configparse.VENDOR_NAMES, vendor


def test_each_vendor_fixture_is_detected_and_named():
    """見本の config が、どれも判別できてメーカー名まで出ること。"""
    fixtures = FIXTURES
    expected = {
        "core-sw-01.txt": "Cisco",
        "edge-rt-01.txt": "Juniper",
        "fw-01.txt": "Fortinet",
        "ix-rt-01.txt": "NEC",
        "qx-sw-01.txt": "NEC",
        "rtx-osaka-01.txt": "ヤマハ",
        "asa-hq-01.txt": "Cisco",
        "panos-hq-01.txt": "Palo Alto",
        "arista-dc-01.txt": "Arista",
        "aruba-cx-01.txt": "Aruba",
        "allied-x530-01.txt": "アライドテレシス",
    }
    for filename, name in expected.items():
        parsed = configparse.parse((fixtures / filename).read_text())
        assert parsed.vendor is not Vendor.UNKNOWN, filename
        assert configparse.VENDOR_NAMES[parsed.vendor] == name, filename
        assert parsed.hostname, filename


def test_two_configs_pasted_into_one_file_do_not_break_the_reader():
    """別のベンダーの出力が1ファイルに混ざっていても、読み手は落ちない。

    現場では show の出力を1ファイルに貼り足すことが多い。ある機種の見出しが
    別の機種の読み手に引っかかって落ちる、を防ぐ。
    """
    from app.services import policyreview

    texts = {path.name: load(path.stem) for path in FIXTURES.glob("*.txt")}
    texts["panos-hq-02.xml"] = (FIXTURES / "panos-hq-02.xml").read_text(encoding="utf-8")
    names = sorted(texts)
    for first in names:
        for second in names:
            if first == second:
                continue
            text = texts[first] + "\n" + texts[second]
            parsed = configparse.parse(text)
            policyreview.review(parsed, text)


def test_a_numeric_lldp_port_id_falls_back_to_the_description():
    """LLDP の Port id は、機種によって番号だけや MAC のことがある。

    そのまま図に出しても意味が分からず、相手側のポート一覧とも突き合わない。
    Port Description のほうがポート名になっていれば、そちらを使う。
    """
    text = "\n".join(
        [
            "sw1#show lldp neighbors detail",
            "------------------------------------------------",
            "Local Intf: Gi1/0/1",
            "Port id: Gi0/1",
            "Port Description: to core-sw-01",
            "System Name: acc-sw-01",
            "------------------------------------------------",
            "Local Intf: Gi1/0/2",
            "Port id: 1",
            "Port Description: GigabitEthernet1",
            "System Name: wlc-01",
            "------------------------------------------------",
            "Local Intf: Gi1/0/3",
            "Port id: aa:bb:cc:dd:ee:ff",
            "Port Description: uplink to the core",
            "System Name: other-01",
        ]
    )
    ports = {n.remote_hostname: n.remote_interface for n in parse(text).neighbors}
    # Port id がポート名なら、そのまま使う
    assert ports["acc-sw-01"] == "GigabitEthernet0/1"
    # 番号だけなら、説明側のポート名を採る
    assert ports["wlc-01"] == "GigabitEthernet1"
    # どちらもポート名に見えないなら、読み取れたものを残す（こちらで作らない）
    assert ports["other-01"] == "aa:bb:cc:dd:ee:ff"


def test_port_admission_is_seen_on_every_vendor() -> None:
    """802.1X とポートセキュリティは、書き方が違うだけで同じ仕組み。

    書き方を1つ落とすと、そのベンダーの機器だけ「入れていない」ことになる。
    """
    huawei = "\n".join(
        [
            "VRP (R) software, Version 8.180 (S5720 V200R011C10SPC600)",
            "sysname hw1",
            "dot1x enable",
            "#",
            "interface GigabitEthernet0/0/1",
            " dot1x enable",
            " port-security enable",
        ]
    )
    fastiron = "\n".join(
        [
            "  HW: Stackable ICX7150-48P",
            "hostname icx1",
            "dot1x-enable",
            " enable ethe 1/1/1",
            "interface ethernet 1/1/1",
            " dot1x port-control auto",
            " port security",
        ]
    )
    for text in (huawei, fastiron):
        found = {item.name for item in configparse.parse(text).features}
        assert "802.1X" in found, text.splitlines()[0]
        assert "ポートセキュリティ" in found, text.splitlines()[0]


def test_allowed_vlan_remove_subtracts_from_all():
    """`allowed vlan all` のあとの `remove` を、引き算として読む。

    並べ直すと 4094 個になるので、引いた分を覚える形で持つ。足し戻す `add` も
    同じ側で扱う。

    出典: VLAN Configuration Guide, Cisco IOS XE 26.x.x（Catalyst 9300）
    「Configuring VLAN Trunks」の switchport trunk allowed vlan
    { word | add | all | except | none | remove } vlan-list
    """
    parsed = configparse.parse(
        "hostname sw-trunk\n"
        "vlan 10,20,30\n"
        "interface GigabitEthernet1/0/23\n"
        " switchport mode trunk\n"
        " switchport trunk allowed vlan all\n"
        " switchport trunk allowed vlan remove 20\n"
        "!\n"
        "interface GigabitEthernet1/0/22\n"
        " switchport mode trunk\n"
        " switchport trunk allowed vlan except 999\n"
        " switchport trunk allowed vlan add 999\n"
    )
    ports = {port.name: port for port in parsed.interfaces}
    removed = ports["GigabitEthernet1/0/23"]
    assert (removed.trunk_all, removed.trunk_excluded) == (True, {20})
    assert removed.carried_vlans({10, 20, 30}) == {10, 30}
    # `except` で引いたものを `add` で戻したら、除外は残らない
    restored = ports["GigabitEthernet1/0/22"]
    assert (restored.trunk_all, restored.trunk_excluded) == (True, set())
