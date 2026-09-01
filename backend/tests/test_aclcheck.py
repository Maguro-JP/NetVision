"""ACL の行と行を、意味で比べられること。

字面で比べていたときは、`permit ip any any` の下にある行が読まれないことに
気づけなかった。ここで見るのは、集合として覆っているかを言えるか、そして
読み取れない書き方で言い切らないか。
"""

from app.services import aclcheck
from app.services.configparse.base import ParsedAclEntry


def rule(action: str = "permit", **kwargs) -> ParsedAclEntry:
    return ParsedAclEntry(action=action, **kwargs)


def test_everything_covers_anything():
    everything = rule(protocol="ip", source="any", destination="any")
    narrow = rule(
        "deny", protocol="tcp", source="host 10.0.0.5", destination="any", destination_port="eq 23"
    )
    assert aclcheck.covers(everything, narrow)
    assert not aclcheck.covers(narrow, everything)


def test_a_wider_network_covers_a_host_inside_it():
    wide = rule(protocol="tcp", source="10.0.0.0 0.0.0.255", destination="any")
    inside = rule(protocol="tcp", source="host 10.0.0.5", destination="any")
    outside = rule(protocol="tcp", source="host 10.9.0.5", destination="any")
    assert aclcheck.covers(wide, inside)
    assert not aclcheck.covers(wide, outside)


def test_a_port_range_covers_a_port_inside_it():
    wide = rule(protocol="tcp", source="any", destination="any", destination_port="range 20 100")
    inside = rule(protocol="tcp", source="any", destination="any", destination_port="eq www")
    outside = rule(protocol="tcp", source="any", destination="any", destination_port="eq 443")
    assert aclcheck.covers(wide, inside)
    assert not aclcheck.covers(wide, outside)


def test_a_named_port_is_the_same_as_its_number():
    assert aclcheck.port_range("eq telnet") == (23, 23)
    assert aclcheck.port_range("eq 23") == (23, 23)


def test_a_protocol_does_not_cover_another_one():
    tcp = rule(protocol="tcp", source="any", destination="any")
    udp = rule(protocol="udp", source="any", destination="any")
    assert not aclcheck.covers(tcp, udp)


def test_what_we_cannot_read_is_not_called_covered():
    """object-group の中身は、その Config だけでは分からない。"""
    group = rule(protocol="ip", source="object-group INSIDE", destination="any")
    other = rule("deny", protocol="tcp", source="host 10.0.0.5", destination="any")
    assert not aclcheck.covers(group, other)
    assert aclcheck.network_of("object-group INSIDE") is None


def test_a_wildcard_that_is_not_one_range_is_not_read():
    """0.0.0.254 のような飛び飛びの指定は、1つの集合にならない。"""
    assert aclcheck.network_of("10.0.0.0 0.0.0.255") is not None
    assert aclcheck.network_of("10.0.0.0 0.0.0.254") is None


def test_neq_is_not_read_as_a_range():
    """1つの範囲にならないものを、範囲として読むと嘘になる。"""
    assert aclcheck.port_range("neq 80") is None


def test_a_rule_with_no_limits_is_wide_open():
    assert aclcheck.is_wide_open(rule(protocol="ip", source="any", destination="any"))
    assert not aclcheck.is_wide_open(
        rule(protocol="tcp", source="any", destination="any", destination_port="eq 443")
    )


# ---------------------------------------------------------------------------
# ゾーンで許否が決まる機種
# ---------------------------------------------------------------------------


def test_the_zone_is_part_of_the_condition():
    """アドレスが同じでも、ゾーンが違えば別の条件。覆っているとは言えない。"""
    upper = ParsedAclEntry(seq="1", action="permit", source="trust:any", destination="untrust:any")
    lower = ParsedAclEntry(seq="2", action="permit", source="untrust:any", destination="dmz:any")
    assert aclcheck.covers(upper, lower) is False


def test_the_same_zone_pair_still_covers():
    upper = ParsedAclEntry(seq="1", action="permit", source="untrust:any", destination="dmz:any")
    lower = ParsedAclEntry(
        seq="2", action="permit", source="untrust:any", destination="dmz:10.9.0.10/32"
    )
    assert aclcheck.covers(upper, lower) is True


def test_a_rule_without_a_zone_covers_any_zone():
    """ゾーンを書いていない側は、どのゾーンにも当たる。"""
    upper = ParsedAclEntry(seq="1", action="permit", source="any", destination="any")
    lower = ParsedAclEntry(seq="2", action="permit", source="trust:any", destination="untrust:any")
    assert aclcheck.covers(upper, lower) is True


def test_an_address_written_as_a_name_is_compared_by_its_value():
    """表には名前を出したいが、覆っているかどうかは中身で見ないと言えない。"""
    upper = ParsedAclEntry(seq="1", action="permit", source="any", destination="10.9.0.0/24")
    lower = ParsedAclEntry(
        seq="2",
        action="permit",
        source="any",
        destination="WEB-SERVER",
        destination_value="10.9.0.10/32",
    )
    assert aclcheck.covers(upper, lower) is True


def test_a_name_whose_value_is_unknown_is_not_called_covered():
    upper = ParsedAclEntry(seq="1", action="permit", source="any", destination="10.9.0.0/24")
    lower = ParsedAclEntry(seq="2", action="permit", source="any", destination="WEB-SERVER")
    assert aclcheck.covers(upper, lower) is False
