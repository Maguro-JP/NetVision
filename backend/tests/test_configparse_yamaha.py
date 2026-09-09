"""ヤマハ RTX / NVR を読めること。

日本の拠点ルーターでいちばん見かける機種のひとつ。書き方は Cisco とも
Junos とも違うが、決めていることは同じなので、同じ器へ読み替えたうえで
同じ目で見られることまで確かめる。
"""

from pathlib import Path

from app.services import configparse, policyreview
from app.services.configparse.base import Vendor

FIXTURES = Path(__file__).parent / "fixtures"


def load() -> str:
    return (FIXTURES / "rtx-osaka-01.txt").read_text()


def parsed():
    return configparse.parse(load())


def titles(text: str) -> list[str]:
    return [f.title for f in policyreview.review(configparse.parse(text), text)]


def test_it_is_detected_and_the_model_is_read():
    p = parsed()
    assert p.vendor is Vendor.YAMAHA
    assert (p.model, p.os_version, p.serial_number) == ("RTX830", "15.02.28", "S4H012345")
    assert configparse.VENDOR_NAMES[p.vendor] == "ヤマハ"


def test_lan_and_the_pppoe_side_are_both_ports():
    """pp は物理ポートではないが、外へ出ている口として扱えたほうが図で追える。"""
    ports = {i.name: i for i in parsed().interfaces}
    assert ports["LAN1"].ip_address == "192.168.100.1/24"
    assert "LAN2" in (ports["PP1"].description or "")


def test_a_route_that_points_at_the_pppoe_side_is_kept_as_a_port():
    routes = {r.destination: r for r in parsed().routes}
    assert routes["0.0.0.0/0"].interface == "PP1"
    assert routes["10.20.0.0/16"].next_hop == "192.168.100.254"


def test_filters_are_grouped_by_where_they_are_applied():
    """ip filter は番号ごとの1行で、並びは当て先の側に書いてある。"""
    acls = {a.name: a for a in parsed().acls}
    assert [e.seq for e in acls["PP1 入り"].entries] == ["200003", "200020"]
    # 並べた順がそのまま評価の順になる。落とす行を先に、通す行を後に置く
    assert [e.seq for e in acls["PP1 出"].entries] == ["200099", "200013"]
    assert acls["PP1 入り"].applied_to == ["interface PP1 入り"]
    assert [e.seq for e in acls["LAN1 入り"].entries] == ["200030", "200031"]
    # reject-log は落としたことが記録に残る
    assert acls["LAN1 入り"].entries[1].log is True


def test_a_filter_number_nobody_uses_is_named_by_its_number():
    """「未使用の filter」とだけ出しても、どれを消せばいいのか分からない。"""
    assert "ACL ip filter 200500 は、どこにも当たっていません" in titles(load())


def test_the_outbound_side_with_a_dynamic_filter_is_not_called_wide_open():
    """戻りを動的に通す並びでは、出る側を広く開けるのが普通の書き方になる。"""
    acls = {a.name: a for a in parsed().acls}
    assert acls["PP1 出"].stateful is True
    assert not [t for t in titles(load()) if "実質すべて通しています" in t]


def test_masquerade_is_read_as_napt():
    nat = parsed().nat
    assert nat is not None
    dynamic = next(r for r in nat.rules if r.kind == "dynamic")
    assert dynamic.overload is True
    assert "回線から受け取る" in (dynamic.translated or "")
    # 外から中の1台へ通す指定（ポート開放）
    static = next(r for r in nat.rules if r.kind == "static-port")
    assert (static.translated, static.protocol) == ("192.168.100.10", "tcp")
    assert nat.outside_interfaces == ["PP1"]


def test_the_dhcp_scope_keeps_the_range_and_the_segment():
    pool = parsed().dhcp.pools[0]
    # 書いてあるのは範囲の始まり。そのままセグメントにすると検算がずれる
    assert pool.network == "192.168.100.0/24"
    assert (pool.range_start, pool.range_end) == ("192.168.100.2", "192.168.100.191")
    assert pool.default_routers == ["192.168.100.1"]
    assert pool.dns_servers == ["192.168.100.1", "8.8.8.8"]


def test_a_pass_all_placed_first_hides_the_rest():
    """番号を並べる順を間違えると、後ろの行は読まれない。"""
    text = load().replace(
        " ip pp secure filter out 200099 200013 dynamic 200080",
        " ip pp secure filter out 200013 200099 dynamic 200080",
    )
    assert [t for t in titles(text) if "200099" in t and "読まれません" in t]


def test_a_clean_config_gets_no_findings_but_the_real_ones_still_come_out():
    text = load().replace("ip filter 200500 pass * * tcp * 22\n", "")
    assert titles(text) == []
    broken = text.replace(
        "dhcp scope option 1 router=192.168.100.1", "dhcp scope option 1 router=10.9.9.9"
    )
    assert [t for t in titles(broken) if "既定ゲートウェイが、配る範囲の外です" in t]


def test_the_tunnels_are_read():
    """`tunnel select` の下と、その外の `ipsec ike ...` を番号でつなぐ。"""
    tunnels = {t.name: t for t in parsed().tunnels}
    assert tunnels["tunnel1"].kind == "ipsec"
    assert tunnels["tunnel1"].source == "192.168.100.1"
    assert tunnels["tunnel1"].destination == "203.0.113.2"
    assert tunnels["tunnel1"].description == "to-honsha"


def test_where_a_key_is_placed_is_remembered_without_the_key():
    """合言葉そのものは持たない。置いてある相手だけを残す。"""
    crypto = parsed().crypto
    assert crypto is not None
    assert crypto.keyed_peers == ["203.0.113.2"]
    assert "himitsu" not in str(crypto)


def test_the_strength_is_carried_over_in_the_same_shape_as_cisco():
    crypto = parsed().crypto
    sets = {t.name: t.transforms for t in crypto.transform_sets}
    assert sets["sa policy 101"] == ["esp-aes-cbc", "esp-sha256-hmac"]
    # 鍵の長さで書かれた群は、判断の側と同じ番号に直しておく
    policy = {p.number: p for p in crypto.ike_policies}["1"]
    assert policy.encryption == "aes-cbc"
    assert policy.group == "14"


def test_an_old_tunnel_is_pointed_out():
    """古い方式のまま残っているトンネルは、Cisco と同じ目で指摘が出る。"""
    text = (
        load()
        .replace(
            "ipsec sa policy 101 1 esp aes-cbc sha256-hmac",
            "ipsec sa policy 101 1 esp des-cbc md5-hmac",
        )
        .replace("ipsec ike group 1 modp2048", "ipsec ike group 1 modp768")
    )
    found = titles(text)
    assert [t for t in found if "破られている方式" in t]
    assert [t for t in found if "鍵の作り方が古い" in t]


def test_the_device_name_comes_from_the_console_prompt():
    """ヤマハには hostname にあたる命令が無い。

    現場では `console prompt` に機器名を入れて見分けている。ここを読まないと、
    台帳の機器名が空のまま取り込まれる。
    """
    assert parsed().hostname == "rtx-osaka-01"
    # 書いていなければ、無いものは無いままにする（推測で埋めない）
    without = load().replace("console prompt rtx-osaka-01\n", "")
    assert configparse.parse(without).hostname is None


def test_port_description_is_read() -> None:
    # ヤマハは説明を命令の側に書く。読めないと「説明が無い」と誤って言う
    text = "\n".join(
        [
            "# RTX1220 Rev.15.04.03",
            "ip lan1 address 192.168.100.1/24",
            "description lan1 事務所側",
        ]
    )
    parsed = configparse.parse(text)
    found = {item.name: item.description for item in parsed.interfaces}
    assert found["LAN1"] == "事務所側"


OFFICIAL_ROUTES = """\
# RTX830 Rev.15.02.28 (Tue Nov 26 18:39:03 2019)
ip lan1 address 192.168.100.1/24
ip route default gateway 192.168.0.1
ip route 192.168.1.0/24 gateway pp 1
ip route default gateway pp 1 weight 2 hide gateway pp 2 weight 1 hide
ip route 172.16.0.1 gateway tunnel 1
ip route 10.8.0.0/16 gateway dhcp lan2
"""


def test_two_gateways_on_one_line_become_two_routes():
    """出典: ヤマハ コマンドリファレンス 8.1.7「IP の静的経路情報の設定」。

    `ip route network gateway gateway1 [parameter] [gateway gateway2 …]` と
    書けるので、2本目を落とすと回線を2本持つ拠点の経路が資料から消える。
    設定例「マルチホーミングによる負荷分散」がこの形。
    """
    routes = configparse.parse(OFFICIAL_ROUTES).routes
    defaults = [r for r in routes if r.destination == "0.0.0.0/0"]
    assert [r.next_hop or r.interface for r in defaults] == ["192.168.0.1", "PP1", "PP2"]
    # weight / hide は器が無いので持たない。次の相手や距離として読まない
    assert all(r.distance is None for r in defaults)


def test_the_gateway_forms_of_the_manual_are_read():
    """8.1.7 の gateway は、アドレス・`pp N`・`tunnel N`・`dhcp インタフェース`。"""
    routes = {r.destination: r for r in configparse.parse(OFFICIAL_ROUTES).routes}
    assert routes["192.168.1.0/24"].interface == "PP1"
    # マスクを省いたら1台ぶん（/32）。他ベンダーと同じ形にそろえる
    assert routes["172.16.0.1/32"].interface == "TUNNEL1"
    assert routes["10.8.0.0/16"].interface == "LAN2"


OFFICIAL_SCOPE = """\
# RTX830 Rev.15.02.28 (Tue Nov 26 18:39:03 2019)
ip lan1 address 192.168.100.1/24
dhcp service server
dhcp scope 1 192.168.100.2-192.168.100.191/255.255.255.0 \
except 192.168.100.10 192.168.100.20-192.168.100.29 gateway 192.168.100.1 \
expire 12:00 maxexpire 72:00
dhcp scope 2 10.9.0.2-10.9.0.100/0xffffff00
dhcp scope option 1 router=192.168.100.1 dns=192.168.100.1
"""


def test_the_scope_line_is_read_down_to_what_it_keeps_back():
    """出典: ヤマハ コマンドリファレンス 12.1.4「DHCP スコープの定義」。

    `dhcp scope N 開始-終了/netmask [except …] [gateway …] [expire …] [maxexpire …]`。
    netmask はマスクビット数・`255.255.255.0`・`0x` の十六進数のどれでも書ける。
    """
    dhcp = configparse.parse(OFFICIAL_SCOPE).dhcp
    first = dhcp.pools[0]
    assert first.network == "192.168.100.0/24"
    assert (first.range_start, first.range_end) == ("192.168.100.2", "192.168.100.191")
    assert first.default_routers == ["192.168.100.1"]
    # 要求が無いときのリース期間。maxexpire は上限なので混ぜない
    assert first.lease == "12:00"
    assert dhcp.excluded == [
        ("192.168.100.10", "192.168.100.10"),
        ("192.168.100.20", "192.168.100.29"),
    ]
    # 十六進のマスクも同じ帯として読む
    assert dhcp.pools[1].network == "10.9.0.0/24"
