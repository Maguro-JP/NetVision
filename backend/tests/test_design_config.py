"""図に描いた構成から、想定される Config を起こせること。

新規構築やテスト環境で、図を描いてから Config を書き起こす手間を減らすための機能。
図に無い情報は起こせないので、そこを黙って省いていないかを重点的に見る。
省かれると「生成されたから完成している」と読まれてしまう。
"""

from tests.conftest import API


def make_device(client, hostname: str, vendor: str, model: str, **extra) -> int:
    body = {
        "hostname": hostname,
        "vendor": vendor,
        "model": model,
        "device_type": "switch",
        **extra,
    }
    response = client.post(f"{API}/devices", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def design(client, device_id: int) -> dict:
    response = client.get(f"{API}/devices/{device_id}/design-config")
    assert response.status_code == 200, response.text
    return response.json()


def test_config_is_built_from_the_drawn_links(client):
    """線を引いた分だけインターフェース設定が出ること。"""
    core = make_device(client, "core-sw-01", "Cisco", "C9500-40X", mgmt_ip="10.0.0.11")
    access = make_device(client, "acc-sw-01", "Cisco", "C9200-48P")

    client.post(
        f"{API}/topology/links",
        json={
            "a_device_id": core,
            "a_interface": "GigabitEthernet1/0/1",
            "b_device_id": access,
            "b_interface": "GigabitEthernet1/0/48",
        },
    )
    client.put(f"{API}/topology/vlans/catalog/10", json={"name": "USERS"})
    client.put(f"{API}/topology/vlans/catalog/20", json={"name": "SERVERS"})

    result = design(client, core)
    body = result["body"]

    assert "hostname core-sw-01" in body
    assert "interface GigabitEthernet1/0/1" in body
    assert "description to acc-sw-01 GigabitEthernet1/0/48" in body
    # 機器同士をつなぐ線はトランクにする
    assert "switchport mode trunk" in body
    assert "switchport trunk allowed vlan 10,20" in body
    assert result["link_count"] == 1


def test_what_cannot_be_derived_is_marked_not_omitted(client):
    """図から起こせない部分を、黙って省いていないこと。

    ここが空欄のまま出ると「これで完成」と読まれる。一番危ない省き方。
    """
    device = make_device(client, "sw-01", "Cisco", "C9300-48P")
    body = design(client, device)["body"]

    assert "投入前に必ず内容を確認してください" in body
    assert "アクセスポートの割り当て" in body
    assert "経路制御" in body
    assert "記入が必要" in body


def test_vlan_name_falls_back_to_something_the_device_accepts(client):
    """台帳の VLAN 名が日本語でも、投入できる名前で出すこと。

    実機の VLAN 名に日本語は入らない。そのまま出すと投入時に弾かれる。
    """
    device = make_device(client, "sw-01", "Cisco", "C9300-48P")
    client.put(f"{API}/topology/vlans/catalog/10", json={"name": "全社ユーザー"})

    body = design(client, device)["body"]
    assert "vlan 10" in body
    assert "全社ユーザー" not in body
    assert "name VLAN10" in body


def test_vendor_specific_syntax(client):
    """ベンダーごとに書式を変えること。混ざると投入できない。"""
    qx = make_device(client, "qx-sw-01", "NEC", "QX-S5528P")
    ix = make_device(client, "ix-rt-01", "NEC", "IX2215", device_type="router")
    junos = make_device(client, "mx-rt-01", "Juniper", "MX204", device_type="router")

    assert "sysname qx-sw-01" in design(client, qx)["body"]
    assert "device-name ix-rt-01" in design(client, ix)["body"]
    assert "set system host-name mx-rt-01" in design(client, junos)["body"]


def test_unknown_vendor_gets_the_design_not_invented_syntax(client):
    """書式が分からないベンダーで、それらしい別ベンダーの構文を出さないこと。

    もっともらしい嘘の Config は、空欄より有害。
    """
    device = make_device(client, "sw-01", "Arista", "DCS-7050")
    body = design(client, device)["body"]

    assert "未対応" in body
    assert "hostname sw-01" not in body  # Cisco 風に出さない
    assert "sw-01" in body  # 設計内容としては出す


def test_plan_can_start_from_the_design(client):
    """Config計画の初期値として使えること。

    ここに乗せておけば、既存の差分生成と収束検証がそのまま効く。
    """
    device = make_device(client, "sw-01", "Cisco", "C9300-48P")
    response = client.post(
        f"{API}/config-plans",
        json={"device_id": device, "name": "新規構築", "seed_from": "design"},
    )
    assert response.status_code == 201, response.text
    assert "hostname sw-01" in response.json()["body"]


def test_design_config_for_unknown_device_is_404(client):
    assert client.get(f"{API}/devices/9999/design-config").status_code == 404
