"""2台で1台に見せる仕組みが、組として成り立っているか。"""

from app.models import Device
from app.models.topology import ConfigSnapshot
from app.services import configparse, hareview
from app.services.configparse import hapair
from tests.conftest import API

FGT_A = (
    'config system ha\n    set group-name "FGT-HA"\n    set mode a-p\n    set priority 200\nend\n'
)
FGT_B = FGT_A.replace("priority 200", "priority 100")
ASA_P = (
    "hostname fw-a\n"
    "failover\n"
    "failover lan unit primary\n"
    "failover lan interface FO GigabitEthernet1/8\n"
)
ASA_S = "hostname fw-b\nfailover\nfailover lan unit secondary\n"
VSX_P = (
    "!Version ArubaOS-CX FL.10.09.0010\n"
    "vsx\n"
    "    system-mac 02:01:00:00:01:00\n"
    "    inter-switch-link lag 1\n"
    "    role primary\n"
)
VSX_S = VSX_P.replace("role primary", "role secondary")
MLAG = (
    "mlag configuration\n"
    "   domain-id DC1\n"
    "   peer-address 10.255.0.2\n"
    "   peer-link Port-Channel10\n"
)


def _device(db, hostname: str, text: str) -> Device:
    device = Device(hostname=hostname, vendor="x")
    db.add(device)
    db.flush()
    db.add(
        ConfigSnapshot(
            device_id=device.id,
            hostname=hostname,
            filename=f"{hostname}.txt",
            content=text,
            sha256=hostname.ljust(64, "0")[:64],
        )
    )
    db.commit()
    return device


def _titles(db) -> list[str]:
    return [f.title for f in hareview.review(db).findings]


def test_each_vendor_is_read_into_the_same_shape():
    assert hapair.parse(FGT_A).group == "FGT-HA"
    assert hapair.parse(FGT_A).priority == 200
    assert hapair.parse(ASA_P).role == "primary"
    assert hapair.parse(ASA_P).link == "GigabitEthernet1/8"
    assert hapair.parse(VSX_P).group == "02:01:00:00:01:00"
    assert hapair.parse(VSX_P).role == "primary"
    assert hapair.parse(MLAG).group == "DC1"
    assert hapair.parse(MLAG).peer_address == "10.255.0.2"
    # 組んだあとの IRF は全メンバーの priority が並ぶ（H3C の IRF の設定例）
    irf = hapair.parse("irf domain 10\nirf member 1 priority 32\nirf member 2 priority 1\n")
    assert (irf.kind, irf.group, irf.priority) == ("irf", "10", 32)
    assert hapair.parse("hostname sw\ninterface Gi1\n") is None


def test_a_standalone_fortigate_is_not_a_pair():
    assert hapair.parse("config system ha\n    set mode standalone\nend\n") is None


def test_a_proper_pair_gets_no_complaints(db_session):
    _device(db_session, "fw-a", FGT_A)
    _device(db_session, "fw-b", FGT_B)
    assert _titles(db_session) == []


def test_a_member_without_its_partner_is_pointed_out(db_session):
    _device(db_session, "fw-a", FGT_A)
    assert [t for t in _titles(db_session) if "相手が台帳にありません" in t]


def test_two_primaries_are_pointed_out(db_session):
    _device(db_session, "sw-a", VSX_P)
    _device(db_session, "sw-b", VSX_P)
    assert [t for t in _titles(db_session) if "両方とも primary" in t]


def test_a_proper_vsx_pair_is_quiet(db_session):
    _device(db_session, "sw-a", VSX_P)
    _device(db_session, "sw-b", VSX_S)
    assert _titles(db_session) == []


def test_equal_priorities_are_pointed_out(db_session):
    _device(db_session, "fw-a", FGT_A)
    _device(db_session, "fw-b", FGT_A)
    assert [t for t in _titles(db_session) if "優先度が両方同じ" in t]


def test_the_api_returns_the_groups(client, db_session):
    _device(db_session, "fw-a", ASA_P)
    _device(db_session, "fw-b", ASA_S)
    body = client.get(f"{API}/topology/ha-check").json()
    assert body["devices_seen"] == 2
    assert list(body["groups"].values()) == [["fw-a", "fw-b"]]
    assert body["findings"] == []


def test_a_stack_is_read_as_one_failure_domain() -> None:
    """組んであるスイッチは、外から見ると1台になる。

    読めないと、落ちる範囲を数えるときに別々の機器として数えてしまう。
    2台あるから大丈夫、と読める形で、実際は1台ぶんしか無い。
    """
    vrp = "\n".join(
        [
            "VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)",
            "sysname hw1",
            "#",
            "stack",
            " stack member 1 domain 10",
            " stack member 1 priority 150",
            " stack member 2 domain 10",
            " stack member 2 priority 120",
        ]
    )
    catalyst = "\n".join(
        [
            "hostname c1",
            "switch 1 provision c9300-48p",
            "switch 2 provision c9300-48p",
            "switch 1 priority 15",
        ]
    )
    assert configparse.parse(vrp).ha_pair is not None
    assert configparse.parse(vrp).ha_pair.kind == "stack"

    stacked = configparse.parse(catalyst).ha_pair
    assert stacked is not None
    assert stacked.kind == "stackwise"
    assert stacked.group == "2"
    assert stacked.priority == 15


def test_one_provisioned_switch_is_not_called_a_stack() -> None:
    # 1台だけ書いてあるのは組ではない。組んでいると読むと、冗長に見えてしまう
    text = "hostname c1\nswitch 1 provision c9300-48p\n"
    assert configparse.parse(text).ha_pair is None


def test_two_separate_stacks_are_not_treated_as_one_pair(client):
    """スタックは1台の中の話。別々の機器を「同じ組」と読まないこと。

    台数が同じというだけで組にすると、優先度がぶつかっていると言うことに
    なる。2台で1台に見せる仕組みとは、そもそも別の話。
    """
    body = (
        "hostname {name}\n"
        "switch 1 provision c9300-48p\n"
        "switch 2 provision c9300-48p\n"
        "switch 1 priority 15\n"
        "interface GigabitEthernet1/0/1\n description a\n"
    )
    files = [
        ("files", (f"{name}.txt", body.format(name=name).encode(), "text/plain"))
        for name in ("sw-01", "sw-02")
    ]
    client.post(f"{API}/configs/upload", files=files, data={"create_missing": "true"})

    report = client.get(f"{API}/topology/ha-check").json()
    # 読めていることは見せる
    assert report["groups"]
    # 機器をまたいだ突き合わせはしない
    assert report["findings"] == []


def test_the_heartbeat_address_is_not_taken_as_the_peer():
    """`peer-address heartbeat` は二重主の検出用の別の宛先（Arista Security Advisory 0161 の作例）。

    `show mlag` でも `peer-address` と `hb-peer-address` は別の項目として出る。
    """
    text = (
        "mlag configuration\n"
        "   domain-id mlagDomain\n"
        "   local-interface Vlan4094\n"
        "   peer-address 10.0.0.2\n"
        "   peer-address heartbeat 172.30.118.190 vrf MGMT\n"
        "   peer-link Port-Channel10\n"
        "   dual-primary detection delay 5 action errdisable all-interfaces\n"
    )
    pair = hapair.parse(text)
    assert pair.peer_address == "10.0.0.2"
    assert pair.link == "Port-Channel10"


#: Cisco「CLI Book 1: Cisco ASA Series General Operations」の failover の作例の形。
#: Stateful 用の口（failover link）の住所を先に書いて、取り違えないことも見る
ASA_PAIR = (
    "failover\n"
    "failover lan unit {role}\n"
    "failover lan interface fover gigabitethernet0/8\n"
    "failover link stlink gigabitethernet0/7\n"
    "failover interface ip stlink 10.9.9.1 255.255.255.0 standby 10.9.9.2\n"
    "failover interface ip fover 192.168.240.254 255.255.255.0 standby 192.168.240.253\n"
)


def test_the_asa_peer_is_the_address_on_the_other_side_of_the_failover_link():
    """前の住所は primary、standby の後ろは secondary に付く。相手はその反対側。"""
    primary = hapair.parse(ASA_PAIR.format(role="primary"))
    secondary = hapair.parse(ASA_PAIR.format(role="secondary"))
    assert primary.peer_address == "192.168.240.253"
    assert secondary.peer_address == "192.168.240.254"
    assert primary.link == "gigabitethernet0/8"


def test_the_asa_peer_is_read_from_an_ipv6_failover_link():
    pair = hapair.parse(
        "failover lan unit primary\n"
        "failover lan interface folink GigabitEthernet0/3\n"
        "failover interface ip folink 2001:a0a:b00::a0a:b70/64 standby 2001:a0a:b00::a0a:b71\n"
    )
    assert pair.peer_address == "2001:a0a:b00::a0a:b71"


def test_a_vpc_domain_from_the_official_example_is_read():
    """公式の作例の vPC を読む。

    Cisco「Understand and Configure Nexus 9000 vPC with Best Practices」の
    show running-config vpc の形。

    `virtual peer-link destination` を keepalive の相手と取り違えず、別の口の `vpc 20` を
    peer-link と取り違えない。
    """
    pair = hapair.parse(
        "feature vpc\n"
        "vpc domain 1\n"
        "  peer-switch\n"
        "  role priority 150\n"
        "  peer-keepalive destination 10.201.182.26 source 10.201.182.25\n"
        "  virtual peer-link destination 10.1.1.3 source 10.1.1.4 dscp 56\n"
        "  peer-gateway\n"
        "\n"
        "interface port-channel10\n"
        "  vpc peer-link\n"
        "\n"
        "interface port-channel20\n"
        "  vpc 20\n"
    )
    assert pair.kind == "vpc"
    assert pair.group == "1"
    assert pair.priority == 150
    assert pair.peer_address == "10.201.182.26"
    assert pair.link == "port-channel10"


def test_fortigate_ha_settings_after_a_nested_block_are_read():
    """FortiOS CLI Reference の `config system ha` には入れ子が入る。

    入れ子の `end` で読み終えると、後ろの `set hbdev`・`set priority` が抜ける。
    `config vcluster` の中の `set priority` は仮想クラスターの優先度なので、機器の優先度にしない。
    """
    pair = hapair.parse(
        "config system ha\n"
        "    set group-id 10\n"
        '    set group-name "FGT-HA"\n'
        "    set mode a-p\n"
        "    config ha-mgmt-interfaces\n"
        "        edit 1\n"
        '            set interface "port5"\n'
        "            set gateway 10.0.0.1\n"
        "        next\n"
        "    end\n"
        '    set hbdev "port3" 50 "port4" 50\n'
        "    set priority 200\n"
        "    config vcluster\n"
        "        edit 2\n"
        "            set priority 50\n"
        "        next\n"
        "    end\n"
        "end\n"
    )
    assert pair.group == "FGT-HA"
    assert pair.link == "port3"
    assert pair.priority == 200


def test_a_standalone_3750_with_a_priority_is_not_a_stack() -> None:
    """単体の Catalyst 3750 にも `switch 1 provision` が自動で作られ、`switch 1 priority` も書ける。

    Cisco「Create and Manage Catalyst 3750 Switch Stacks」。優先度の行だけで組と読むと、
    冗長でない機器を冗長に見せてしまう。
    """
    standalone = "hostname c3750\nswitch 1 provision ws-c3750x-48p\nswitch 1 priority 15\n"
    assert configparse.parse(standalone).ha_pair is None
    assert configparse.parse("hostname c3750\nswitch 1 priority 15\n").ha_pair is None


def test_a_vsx_pair_from_the_official_example_is_read():
    """AOS-CX VSX Guide と Aruba Developer Hub の構成例の `vsx` の形。

    2台で同じ system-mac を組の名前として持ち、keepalive の相手をそれぞれ反対側に持つ。
    """
    body = (
        "vsx\n"
        "    system-mac 00:00:00:00:01:01\n"
        "    inter-switch-link lag 1\n"
        "    role {role}\n"
        "    keepalive peer {peer} source {source} vrf KA\n"
    )
    primary = hapair.parse(
        body.format(role="primary", peer="192.168.1.101", source="192.168.1.100")
    )
    secondary = hapair.parse(
        body.format(role="secondary", peer="192.168.1.100", source="192.168.1.101")
    )
    assert primary.group == secondary.group == "00:00:00:00:01:01"
    assert (primary.role, secondary.role) == ("primary", "secondary")
    assert primary.peer_address == "192.168.1.101"
    assert secondary.peer_address == "192.168.1.100"
    assert primary.link == "lag 1"


def test_a_standalone_comware_device_is_not_an_irf_fabric():
    """H3C「Automatic IRF Setup Configuration Examples」の組む前の設定。

    単体の機器にも `irf member 1 priority 1` が書かれる。1台分で組と読むと、
    冗長でない機器を冗長に見せてしまう。
    """
    single = (
        " irf mac-address persistent timer\n"
        " irf auto-update enable\n"
        " undo irf link-delay\n"
        " irf member 1 priority 1\n"
        "irf-port 1/2\n"
        " port group mdc 1 interface HundredGigE3/0/1 mode enhanced\n"
    )
    assert hapair.parse(single) is None


def test_an_irf_fabric_from_the_official_example_is_read():
    fabric = (
        " irf domain 1\n"
        " irf mac-address persistent timer\n"
        " irf member 1 priority 2\n"
        " irf member 2 priority 1\n"
        "irf-port 1/1\n"
        " port group interface Ten-GigabitEthernet1/1/0/1 mode enhanced\n"
        "irf-port 2/2\n"
        " port group interface Ten-GigabitEthernet2/1/0/1 mode enhanced\n"
    )
    pair = hapair.parse(fabric)
    assert (pair.kind, pair.group, pair.priority) == ("irf", "1", 2)


def test_a_vrp_switch_before_joining_a_stack_is_not_a_stack():
    """Huawei「Example for Establishing a Stack of Two Switches」の組む前の設定。

    組む前のスイッチにも `stack` の塊があり、1台分の行だけが書かれる。
    """
    single = (
        "#\nsysname SwitchA\n#\nstack\n #\n"
        " stack member 1 domain 10\n stack member 1 priority 150\n#\n"
    )
    assert hapair.parse(single) is None


def test_a_formed_vrp_stack_from_the_official_example_is_read():
    formed = (
        "#\nsysname SwitchA\n#\nstack\n #\n"
        " stack member 1 domain 10\n stack member 1 priority 150\n #\n"
        " stack member 2 domain 10\n stack member 2 priority 120\n#\n"
    )
    pair = hapair.parse(formed)
    assert (pair.kind, pair.group, pair.priority) == ("stack", "10", 150)
