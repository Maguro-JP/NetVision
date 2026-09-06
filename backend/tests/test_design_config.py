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
    device = make_device(client, "sw-01", "Extreme", "X465")
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


def test_the_management_address_is_written_out_when_the_ledger_knows_the_subnet(client):
    """管理IP が台帳のどの区画に入るかは計算で決まる。手で書かせると写し間違いが入る。"""
    device = make_device(client, "sw-01", "Cisco", "C9300-48P", mgmt_ip="10.20.30.11")
    client.put(
        f"{API}/topology/vlans/catalog/99",
        json={"name": "MGMT", "subnet": "10.20.30.0/24"},
    )

    body = design(client, device)["body"]
    assert "interface Vlan99" in body
    assert "ip address 10.20.30.11 255.255.255.0" in body


def test_it_does_not_guess_a_subnet_it_was_not_given(client):
    """近い VLAN を当てずっぽうで選ぶと、通らない Config が完成した形で出る。"""
    device = make_device(client, "sw-01", "Cisco", "C9300-48P", mgmt_ip="10.20.30.11")
    client.put(f"{API}/topology/vlans/catalog/99", json={"name": "MGMT"})

    body = design(client, device)["body"]
    assert "interface Vlan99" not in body
    assert "管理VLAN" in body
    assert "記入が必要" in body


def test_the_terminal_side_port_is_left_as_an_access_port(client):
    """端末を挿す口であることは図から分かる。番号だけが分からない。"""
    switch = make_device(client, "acc-sw-01", "Cisco", "C9200-48P")
    server = make_device(client, "srv-01", "Cisco", "UCS", device_type="server")
    client.post(
        f"{API}/topology/links",
        json={
            "a_device_id": switch,
            "a_interface": "GigabitEthernet1/0/10",
            "b_device_id": server,
            "b_interface": "eth0",
        },
    )

    body = design(client, switch)["body"]
    assert "switchport mode access" in body
    assert "spanning-tree bpduguard enable" in body
    assert "アクセス VLAN" in body


def test_the_generated_config_starts_from_the_safe_side(client):
    """新規構築の Config が、そのまま点検で赤くなるのはおかしい。"""
    device = make_device(client, "sw-01", "Cisco", "C9300-48P")
    body = design(client, device)["body"]

    assert "no ip http server" in body
    assert "transport input ssh" in body
    assert "service password-encryption" in body


def test_the_untagged_vlan_is_called_out_on_every_trunk(client):
    """1 のままだと、分けたはずのものが1つにつながる。"""
    core = make_device(client, "core-sw-01", "Cisco", "C9500-40X")
    access = make_device(client, "acc-sw-02", "Cisco", "C9200-48P")
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

    body = design(client, core)["body"]
    assert "タグ無し VLAN" in body
    assert "1 のままにしない" in body


def test_fortigate_gets_its_own_syntax_and_says_what_it_cannot_decide(client):
    """FortiGate は通す通信を決めないと何も通らない。そこは設計の中身そのもの。"""
    device = make_device(client, "fw-01", "Fortinet", "FG-100F", device_type="firewall")
    body = design(client, device)["body"]

    assert "config system global" in body
    assert 'set hostname "fw-01"' in body
    assert "何も通りません" in body


def _wire(client, core: int, access: int, a: str, b: str) -> None:
    client.post(
        f"{API}/topology/links",
        json={"a_device_id": core, "a_interface": a, "b_device_id": access, "b_interface": b},
    )
    client.put(f"{API}/topology/vlans/catalog/10", json={"name": "USERS"})
    client.put(f"{API}/topology/vlans/catalog/20", json={"name": "SERVERS"})


def test_the_newer_vendors_get_their_own_syntax_and_it_reads_back(client):
    """書き出した Config を、同じ道具の読み手で読み戻せること。

    書式が違うと投入できない。読み戻せれば、少なくとも自分の読み手と
    同じ理解で書けている。
    """
    from app.services import configparse
    from app.services.configparse import Vendor

    arista = make_device(client, "sw-arista-01", "Arista", "DCS-7050SX3", mgmt_ip="10.0.10.5")
    aruba = make_device(client, "sw-aruba-01", "Aruba", "6300M", mgmt_ip="10.0.10.6")
    allied = make_device(client, "at-x530-01", "Allied Telesis", "AT-x530-28GTXm")
    peer = make_device(client, "core-sw-01", "Cisco", "C9500-40X")
    _wire(client, arista, peer, "Ethernet1", "GigabitEthernet1/0/1")
    _wire(client, aruba, peer, "1/1/1", "GigabitEthernet1/0/2")
    _wire(client, allied, peer, "port1.0.1", "GigabitEthernet1/0/3")

    body = design(client, arista)["body"]
    parsed = configparse.parse(body)
    assert parsed.vendor is Vendor.ARISTA
    assert {i.name: i.trunk_vlans for i in parsed.interfaces}["Ethernet1"] == [10, 20]
    assert "encapsulation dot1q" not in body  # EOS には無い

    body = design(client, aruba)["body"]
    parsed = configparse.parse(body)
    assert parsed.vendor is Vendor.ARUBA_CX
    port = {i.name: i for i in parsed.interfaces}["1/1/1"]
    assert port.trunk_vlans == [10, 20]
    assert port.enabled is True  # no shutdown を書いている
    assert parsed.vlans[10] == "USERS"

    body = design(client, allied)["body"]
    parsed = configparse.parse(body)
    assert parsed.vendor is Vendor.ALLIED
    assert parsed.vlans[20] == "SERVERS"
    assert {i.name: i.trunk_vlans for i in parsed.interfaces}["port1.0.1"] == [10, 20]


def test_the_house_time_target_is_written_in(client):
    """記録の送り先は現場で決まっている。設計の判断は要らない。

    起こさないでおくと、出した Config を自分の点検が「入っていません」と
    咎めることになる。
    """
    device = make_device(client, "house-sw-01", "Cisco", "C9300-24T", mgmt_ip="10.0.0.31")
    # 流儀は1台では決まらない。2台以上でそろっていて初めて「この現場のやり方」
    for name in ("house-sw-02", "house-sw-03"):
        make_device(client, name, "Cisco", "C9300-24T")
        client.post(
            f"{API}/configs/upload",
            files={
                "files": (
                    f"{name}.txt",
                    f"hostname {name}\n!\nntp server 10.10.20.10\nlogging host 10.10.20.20\n!\n",
                    "text/plain",
                )
            },
        )

    body = design(client, device)["body"]
    assert "ntp server 10.10.20.10" in body
    assert "logging host 10.10.20.20" in body


def test_an_unknown_target_is_marked_instead_of_dropped(client):
    """読み取れないときは、黙って省かず要記入の印を残す。"""
    device = make_device(client, "blank-sw-01", "Cisco", "C9300-24T", mgmt_ip="10.0.0.41")
    body = design(client, device)["body"]
    assert "ログの送り先: 設計から起こせないため" in body
    assert "時刻の参照先: 設計から起こせないため" in body


def test_the_added_vendors_get_their_own_syntax(client):
    """後から足したベンダーでも、他社の構文を出さないこと。"""
    ax = make_device(client, "ax-sw-01", "アラクサラ", "AX3650S-24T4XW")
    vrp = make_device(client, "hw-sw-01", "Huawei", "CE6850")
    icx = make_device(client, "icx-sw-01", "Ruckus", "ICX7150-48P")

    assert 'hostname "ax-sw-01"' in design(client, ax)["body"]

    vrp_body = design(client, vrp)["body"]
    assert "sysname hw-sw-01" in vrp_body
    assert "user-interface vty" in vrp_body

    icx_body = design(client, icx)["body"]
    assert "hostname icx-sw-01" in icx_body
    assert "FastIron" in icx_body
    # VLAN の下に口を並べる書き方を崩さない。IOS の雛形をそのまま出さない
    assert "switchport" not in icx_body
