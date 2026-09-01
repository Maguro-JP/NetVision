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


def test_a_clean_config_gets_no_findings_but_the_real_ones_still_come_out():
    text = load().replace("ip filter 200500 pass * * tcp * 22\n", "")
    assert titles(text) == []
    broken = text.replace(
        "dhcp scope option 1 router=192.168.100.1", "dhcp scope option 1 router=10.9.9.9"
    )
    assert [t for t in titles(broken) if "既定ゲートウェイが、配る範囲の外です" in t]
