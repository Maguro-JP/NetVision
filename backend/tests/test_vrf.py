"""VRF（機器の中の仮想ルータ）が読めること。

呼び方はベンダーごとに違うが、1台の中で経路表を分けるという点は同じ。
図の上で同じ線に見えても VRF が違えば通信できないので、
「繋がっているのに通らない」の原因として真っ先に確認したいところになる。
"""

from app.services import configparse
from tests.conftest import API

CISCO = """
hostname core-rt-01
!
Model Number: ISR4451
!
vrf definition SHANAI
 rd 65000:100
 description 社内系
!
vrf definition KOKYAKU
 rd 65000:200
!
interface GigabitEthernet0/0/0
 vrf forwarding SHANAI
 ip address 10.10.0.1 255.255.255.0
!
interface GigabitEthernet0/0/1
 vrf forwarding KOKYAKU
 ip address 10.20.0.1 255.255.255.0
!
interface GigabitEthernet0/0/2
 ip address 192.168.0.1 255.255.255.0
!
"""

COMWARE = """
Comware Software, Version 5.20.99
NEC QX-S5528P
#
 sysname qx-rt-01
#
ip vpn-instance SHANAI
 route-distinguisher 65000:100
 description 社内系
#
interface GigabitEthernet1/0/1
 ip binding vpn-instance SHANAI
#
"""

IX = """
IX Series IX2215 (magellan-sec) Software, Version 10.6.21
device-name ix-rt-01
!
ip vrf SHANAI
 rd 65000:100
!
interface GigaEthernet0.1
 ip vrf forwarding SHANAI
 ip address 10.10.0.2/24
!
"""

JUNOS = """
set system host-name mx-rt-01
set routing-instances SHANAI instance-type virtual-router
set routing-instances SHANAI interface ge-0/0/1.0
set routing-instances LOGGING instance-type forwarding
"""

FORTI = """
#config-version=FGT100F-7.2.8-FW-build1639-230926:opmode=0:vdom=1
config system global
    set hostname "fw-01"
end
config vdom
edit "SHANAI"
next
edit "KOKYAKU"
next
end
config system interface
    edit "port1"
        set vdom "SHANAI"
        set ip 10.10.0.3 255.255.255.0
    next
end
"""


def test_cisco_vrfs_and_their_interfaces():
    parsed = configparse.parse(CISCO)
    vrfs = {v.name: v for v in parsed.vrfs}

    assert set(vrfs) == {"SHANAI", "KOKYAKU"}
    assert vrfs["SHANAI"].rd == "65000:100"
    assert vrfs["SHANAI"].description == "社内系"

    ports = {i.key: i for i in parsed.interfaces}
    assert ports["gigabitethernet0/0/0"].vrf == "SHANAI"
    assert ports["gigabitethernet0/0/1"].vrf == "KOKYAKU"
    # VRF に入れていないポートはグローバル。空のままにしておく
    assert ports["gigabitethernet0/0/2"].vrf is None


def test_comware_vpn_instance_is_treated_as_a_vrf():
    parsed = configparse.parse(COMWARE)
    vrfs = {v.name: v for v in parsed.vrfs}

    assert vrfs["SHANAI"].rd == "65000:100"
    ports = {i.key: i for i in parsed.interfaces}
    assert ports["gigabitethernet1/0/1"].vrf == "SHANAI"


def test_nec_ix_vrf_is_read():
    parsed = configparse.parse(IX)
    assert parsed.vendor is configparse.Vendor.NEC_IX

    vrfs = {v.name: v for v in parsed.vrfs}
    assert vrfs["SHANAI"].rd == "65000:100"
    assert parsed.interfaces[0].vrf == "SHANAI"


def test_junos_only_routing_instances_that_split_the_table_count():
    """forwarding インスタンスは経路表を分ける用途ではないので数えない。"""
    parsed = configparse.parse(JUNOS)
    names = {v.name for v in parsed.vrfs}

    assert "SHANAI" in names
    assert "LOGGING" not in names

    ports = {i.key: i for i in parsed.interfaces}
    assert ports["ge-0/0/1"].vrf == "SHANAI"


def test_fortigate_vdom_is_treated_as_a_vrf():
    """FortiGate で1台を分けるのは VDOM。他ベンダーと同じ列に並べる。"""
    parsed = configparse.parse(FORTI)
    names = {v.name for v in parsed.vrfs}

    assert {"SHANAI", "KOKYAKU"} <= names
    ports = {i.key: i for i in parsed.interfaces}
    assert ports["port1"].vrf == "SHANAI"


def test_vrf_referenced_without_a_definition_is_still_listed():
    """定義が別ファイルでも、ポートが入っている以上は一覧に出す。"""
    parsed = configparse.parse(
        "hostname sw\nModel Number: C9200\n!\n"
        "interface GigabitEthernet1/0/1\n vrf forwarding ORPHAN\n!\n"
    )
    assert [v.name for v in parsed.vrfs] == ["ORPHAN"]


# --- API -------------------------------------------------------------------


def upload(client, name: str, body: str):
    return client.post(
        f"{API}/configs/upload",
        files=[("files", (name, body.encode(), "text/plain"))],
        data={"create_missing": "true"},
    )


def test_vrf_summary_groups_by_name(client):
    """同じ VRF を複数台に延ばすのが普通なので、名前でまとめる。"""
    upload(client, "core-rt-01.txt", CISCO)
    upload(client, "qx-rt-01.txt", COMWARE)

    vrfs = {v["name"]: v for v in client.get(f"{API}/topology/vrfs").json()}
    assert len(vrfs["SHANAI"]["device_names"]) == 2
    assert vrfs["SHANAI"]["interface_count"] == 2
    assert vrfs["SHANAI"]["rd_mismatch"] is False
    assert vrfs["KOKYAKU"]["device_names"] == ["core-rt-01"]


def test_rd_mismatch_is_flagged(client):
    """同じ VRF 名で RD が食い違っていれば印を付ける。設計ミスの兆候になる。"""
    upload(client, "core-rt-01.txt", CISCO)
    upload(client, "qx-rt-01.txt", COMWARE.replace("65000:100", "65000:999"))

    vrfs = {v["name"]: v for v in client.get(f"{API}/topology/vrfs").json()}
    assert vrfs["SHANAI"]["rd_mismatch"] is True


def test_topology_nodes_carry_their_vrfs(client):
    upload(client, "core-rt-01.txt", CISCO)
    nodes = client.get(f"{API}/topology").json()["nodes"]
    node = next(n for n in nodes if n["hostname"] == "core-rt-01")
    assert sorted(node["vrfs"]) == ["KOKYAKU", "SHANAI"]


def test_export_can_be_filtered_by_vrf(client):
    upload(client, "core-rt-01.txt", CISCO)
    upload(client, "qx-rt-01.txt", COMWARE)

    res = client.get(f"{API}/topology/export.svg", params={"vrf": "KOKYAKU"})
    assert res.status_code == 200
    assert "core-rt-01" in res.text
    assert "qx-rt-01" not in res.text  # KOKYAKU に入っていない機器は描かない
