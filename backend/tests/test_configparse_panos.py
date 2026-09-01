"""Palo Alto（PAN-OS）を読めること。

PAN-OS の許否はゾーンの組み合わせで決まる。アドレスだけ見て「上の行が
下を覆っている」と読むと、別のゾーン宛ての行どうしを取り違える。
そこを含めて、同じ器（ACL）に入れたうえで正しく判断できるかを見る。
"""

from pathlib import Path

from app.services import configparse, policyreview
from app.services.configparse import panos
from app.services.configparse.base import Vendor

FIXTURES = Path(__file__).parent / "fixtures"


def load() -> str:
    return (FIXTURES / "panos-hq-01.txt").read_text()


def parsed():
    return configparse.parse(load())


def titles(text: str) -> list[str]:
    return [f.title for f in policyreview.review(configparse.parse(text), text)]


def test_it_is_detected_and_the_model_is_read():
    p = parsed()
    assert p.vendor is Vendor.PALO_ALTO
    assert (p.hostname, p.model, p.os_version, p.serial_number) == (
        "fw-pan-01",
        "PA-440",
        "11.1.4-h7",
        "001801234567",
    )


def test_ports_carry_the_zone_they_belong_to():
    """ポリシーを読んでも、どの口のことか分からないと現場で使えない。"""
    ports = {i.name: i for i in parsed().interfaces}
    assert ports["Ethernet1/1"].ip_address == "203.0.113.2/30"
    assert panos.zone_of(ports["Ethernet1/1"]) == "untrust"
    assert panos.zone_of(ports["Ethernet1/2"]) == "trust"
    assert panos.zone_of(ports["Ethernet1/3"]) == "dmz"


def test_the_static_route_is_assembled_from_several_lines():
    """set 形式は1行に1つずつ書く。宛先と次の相手は別の行にある。"""
    route = parsed().routes[0]
    assert (route.destination, route.next_hop, route.interface) == (
        "0.0.0.0/0",
        "203.0.113.1",
        "ethernet1/1",
    )


def test_rules_keep_the_zone_as_part_of_the_condition():
    acl = parsed().acls[0]
    assert acl.name == "security rules"
    first = acl.entries[0]
    assert (first.action, first.source, first.destination) == (
        "permit",
        "untrust:any",
        "dmz:WEB-SERVER",
    )
    assert first.destination_port == "サービス TCP-443"
    assert first.log is True
    # 名前の中身が分かれば、そちらも持つ（覆っているかどうかは中身で見る）
    assert first.destination_value == "dmz:10.9.0.10/32"


def test_both_directions_of_nat_are_read():
    nat = parsed().nat
    assert nat is not None
    kinds = {(r.kind, r.direction, r.translated, r.overload) for r in nat.rules}
    assert ("dynamic", "inside", "出口のアドレス（ethernet1/1）", True) in kinds
    incoming = next(r for r in nat.rules if r.direction == "outside")
    assert (incoming.matched, incoming.translated) == ("203.0.113.10", "10.9.0.10")


def test_a_clean_policy_says_nothing():
    """ゾーンを条件に含めないと、ここで誤検知が並ぶ。"""
    assert titles(load()) == []


def test_a_rule_hidden_by_one_above_it_in_the_same_zone_pair_is_reported():
    """同じゾーンの組で広く開けた行を上に置くと、下の行は読まれない。"""
    extra = (
        "set rulebase security rules wide-open from untrust\n"
        "set rulebase security rules wide-open to dmz\n"
        "set rulebase security rules wide-open source any\n"
        "set rulebase security rules wide-open destination any\n"
        "set rulebase security rules wide-open action allow\n"
    )
    text = load().replace(
        "set rulebase security rules allow-web from untrust",
        extra + "set rulebase security rules allow-web from untrust",
        1,
    )
    assert [t for t in titles(text) if "上の行と同じ意味の行があります" in t]


def test_a_rule_for_another_zone_pair_is_not_called_hidden():
    """trust → untrust の全部通しは、untrust → dmz の行を覆わない。"""
    found = titles(load())
    assert not [t for t in found if "読まれません" in t]


def test_a_disabled_rule_is_left_out_of_the_ordering_check():
    text = load().replace(
        "set rulebase security rules trust-out action allow",
        "set rulebase security rules trust-out action allow\n"
        "set rulebase security rules trust-out disabled yes",
        1,
    )
    entries = {e.remark: e for e in configparse.parse(text).acls[0].entries}
    assert entries["trust-out"].action == "disabled"
