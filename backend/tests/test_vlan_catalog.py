"""VLAN 台帳（人が用途を書く側）と、実機の観測値の関係。

数十〜数百の VLAN を扱う現場では「10 は何のセグメントか」を人が書ける場所が要る。
そこに書いた内容が Config の取り込みで消えてしまうと、二度と書かなくなる。
"""

from tests.conftest import API

CONFIG = """
hostname acc-sw-01
!
Model Number: C9200-48P
!
vlan 10
 name USERS
!
vlan 20
 name SRV
!
interface GigabitEthernet1/0/1
 switchport mode access
 switchport access vlan 10
!
"""


def upload(client, body: str = CONFIG, name: str = "acc-sw-01.txt"):
    return client.post(
        f"{API}/configs/upload",
        files=[("files", (name, body.encode(), "text/plain"))],
        data={"create_missing": "true"},
    )


def test_catalog_name_is_used_for_display(client):
    upload(client)
    client.put(f"{API}/topology/vlans/catalog/20", json={"name": "サーバセグメント"})

    vlans = {v["vlan_id"]: v for v in client.get(f"{API}/topology/vlans").json()}
    assert vlans[20]["name"] == "サーバセグメント"
    # 実機に書いてある名前も残す。食い違いは「機器側が古い」という情報になる
    assert vlans[20]["observed_names"] == ["SRV"]
    assert vlans[20]["in_catalog"] is True


def test_catalog_survives_reimport(client):
    """取り込み直しで人が書いた用途が消えないこと。ここが消えると誰も書かなくなる。"""
    upload(client)
    client.put(
        f"{API}/topology/vlans/catalog/10",
        json={"name": "全社ユーザ", "purpose": "社員端末", "owner": "情シス"},
    )

    upload(client)  # もう一度同じ Config を取り込む

    vlans = {v["vlan_id"]: v for v in client.get(f"{API}/topology/vlans").json()}
    assert vlans[10]["name"] == "全社ユーザ"
    assert vlans[10]["purpose"] == "社員端末"
    assert vlans[10]["owner"] == "情シス"


def test_catalog_entry_without_any_device_is_still_listed(client):
    """設計しただけで未投入の VLAN も一覧に出す。作業の抜けはここに出る。"""
    upload(client)
    client.put(f"{API}/topology/vlans/catalog/99", json={"name": "新規セグメント"})

    vlans = {v["vlan_id"]: v for v in client.get(f"{API}/topology/vlans").json()}
    assert vlans[99]["device_ids"] == []
    assert vlans[99]["in_catalog"] is True


def test_vlan_on_a_device_but_not_in_the_catalog_is_flagged(client):
    """台帳に無い VLAN は素性が分からない。隠さず出して、印を付ける。"""
    upload(client)
    vlans = {v["vlan_id"]: v for v in client.get(f"{API}/topology/vlans").json()}
    assert vlans[10]["in_catalog"] is False
    assert vlans[10]["name"] == "USERS"  # 台帳が無ければ実機の名前で代用する


def test_adopt_creates_drafts_without_overwriting_written_entries(client):
    """実機の名前で下書きを作る。既に人が書いた行は触らない。"""
    upload(client)
    client.put(f"{API}/topology/vlans/catalog/10", json={"name": "全社ユーザ"})

    result = client.post(f"{API}/topology/vlans/catalog/adopt").json()
    assert result["created"] == 1  # VLAN 20 だけが作られる

    catalog = {v["vlan_id"]: v for v in client.get(f"{API}/topology/vlans/catalog").json()}
    assert catalog[10]["name"] == "全社ユーザ"  # 上書きしない
    assert catalog[20]["name"] == "SRV"


def test_adopt_can_overwrite_when_asked(client):
    upload(client)
    client.put(f"{API}/topology/vlans/catalog/10", json={"name": "全社ユーザ"})

    client.post(f"{API}/topology/vlans/catalog/adopt", params={"overwrite": "true"})
    catalog = {v["vlan_id"]: v for v in client.get(f"{API}/topology/vlans/catalog").json()}
    assert catalog[10]["name"] == "USERS"


def test_deleting_a_catalog_entry_leaves_the_device_alone(client):
    """台帳から消すのは「用途の記録を消す」だけ。実機の設定には触らない。"""
    upload(client)
    client.put(f"{API}/topology/vlans/catalog/10", json={"name": "全社ユーザ"})

    assert client.delete(f"{API}/topology/vlans/catalog/10").status_code == 204

    vlans = {v["vlan_id"]: v for v in client.get(f"{API}/topology/vlans").json()}
    assert vlans[10]["in_catalog"] is False
    assert vlans[10]["device_ids"]  # 機器からは消えていない


def test_out_of_range_vlan_is_rejected(client):
    assert client.put(f"{API}/topology/vlans/catalog/5000", json={"name": "x"}).status_code == 422


def test_deleting_an_unknown_entry_is_404(client):
    assert client.delete(f"{API}/topology/vlans/catalog/777").status_code == 404
