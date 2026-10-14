"""シンプル図と、結線が運ぶ VLAN。

現場で使う図に型番や保守状態が載っていると、読み違いのもとになる。
用途で図を分けられるようにしてある。
"""

from tests.conftest import API

CORE = """
hostname core-sw-01
!
Model Number: C9500-40X
!
vlan 10
 name USERS
!
vlan 20
 name SERVERS
!
interface GigabitEthernet1/0/24
 description to-acc-sw-01
 switchport mode trunk
 switchport trunk native vlan 999
 switchport trunk allowed vlan 10,20
!
"""

ACC = """
hostname acc-sw-01
!
Model Number: C9200-48P
!
vlan 10
 name USERS
!
interface GigabitEthernet1/0/1
 description to-core-sw-01
 switchport mode trunk
 switchport trunk native vlan 999
 switchport trunk allowed vlan 10
!
"""


def setup_pair(client):
    for name, body in (("core-sw-01.txt", CORE), ("acc-sw-01.txt", ACC)):
        client.post(
            f"{API}/configs/upload",
            files=[("files", (name, body.encode(), "text/plain"))],
            data={"create_missing": "true"},
        )
    # LLDP が無い構成なので、線は手で引く（現場でも手で足すことが多い）
    devices = {d["hostname"]: d["id"] for d in client.get(f"{API}/devices").json()}
    client.post(
        f"{API}/topology/links",
        json={
            "a_device_id": devices["core-sw-01"],
            "a_interface": "GigabitEthernet1/0/24",
            "b_device_id": devices["acc-sw-01"],
            "b_interface": "GigabitEthernet1/0/1",
        },
    )
    return devices


def test_link_carries_the_vlans_both_ends_agree_on(client):
    setup_pair(client)
    edge = client.get(f"{API}/topology").json()["edges"][0]

    assert edge["vlans"] == [10, 999]  # 両端が通している VLAN
    assert edge["vlan_mismatch"] == [20]  # 片側しか許可していない＝許可漏れ
    assert edge["native_vlan"] == 999
    assert edge["native_mismatch"] is False


def test_native_vlan_mismatch_is_flagged(client):
    """片側だけネイティブが違うのは事故のもと。図の側で気づけるようにする。"""
    devices = setup_pair(client)
    assert devices
    client.post(
        f"{API}/configs/upload",
        files=[
            (
                "files",
                (
                    "acc-sw-01.txt",
                    ACC.replace("native vlan 999", "native vlan 1").encode(),
                    "text/plain",
                ),
            )
        ],
    )

    edge = client.get(f"{API}/topology").json()["edges"][0]
    assert edge["native_mismatch"] is True


def test_simple_export_drops_everything_but_names_and_lines(client):
    setup_pair(client)
    res = client.get(f"{API}/topology/export.svg", params={"style": "simple"})

    assert res.status_code == 200
    assert "core-sw-01" in res.text
    assert "acc-sw-01" in res.text
    # 型番・保守状態とその凡例は載せない
    assert "C9500-40X" not in res.text
    assert "保守・EoL の状態" not in res.text
    assert "期限切れ" not in res.text
    assert "simple" in res.headers["content-disposition"]


def test_detail_export_still_carries_the_extra_information(client):
    setup_pair(client)
    res = client.get(f"{API}/topology/export.svg")

    assert "C9500-40X" in res.text
    assert "保守・EoL の状態" in res.text


def test_simple_export_keeps_the_date(client):
    """日付の無い構成図はあとから信用できないので、そこだけは残す。"""
    setup_pair(client)
    res = client.get(f"{API}/topology/export.svg", params={"style": "simple"})
    assert "時点" in res.text


def test_a_line_that_is_down_on_the_device_is_flagged_on_the_edge(client):
    """図には線があるのに、実機では上がっていない。図だけの線をそのまま信じない。"""
    setup_pair(client)
    edge = client.get(f"{API}/topology").json()["edges"][0]
    assert edge["link_down"] is False  # show の表が無ければ何も言わない

    status = (
        "\nshow interfaces status\n"
        "Port      Name               Status       Vlan       Duplex  Speed Type\n"
        "Gi1/0/1   to-core            notconnect   trunk      auto    auto 10/100/1000BaseTX\n"
    )
    client.post(
        f"{API}/configs/upload",
        files=[("files", ("acc-sw-01.txt", (ACC + status).encode(), "text/plain"))],
    )
    edge = client.get(f"{API}/topology").json()["edges"][0]
    assert edge["link_down"] is True


def test_simple_export_explains_its_boxes_and_lines(client):
    """簡易図にも凡例を置く。名前と線だけの図でも、渡された人には
    四角と線が何かを図の中で説明しておく。保守状態の説明は載せない。"""
    setup_pair(client)
    res = client.get(f"{API}/topology/export.svg", params={"style": "simple"})

    assert "四角 = 機器" in res.text
    assert "線 = 機器どうしの結線" in res.text
    assert "保守・EoL の状態" not in res.text


def test_detail_legend_names_the_device_glyphs_and_the_stopped_shade(client):
    """箱の左上の記号と、灰色の地（稼働していない機器）を凡例で説明する。
    使っている種別だけを並べ、止まっている機器が無ければ灰色の説明は出さない。"""
    setup_pair(client)
    res = client.get(f"{API}/topology/export.svg")
    assert "機器の種別" in res.text
    # 試験の機器は種別が「その他」なので、その記号だけが並ぶ
    assert "▪ その他" in res.text
    assert "◇ ルーター" not in res.text
    assert "稼働していない機器" not in res.text

    devices = client.get(f"{API}/devices").json()
    target = next(d for d in devices if d["hostname"] == "acc-sw-01")
    assert (
        client.patch(f"{API}/devices/{target['id']}", json={"status": "standby"}).status_code == 200
    )
    res = client.get(f"{API}/topology/export.svg")
    assert "灰色の地 = 稼働していない機器" in res.text
