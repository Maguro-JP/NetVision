"""Config に何の機能が入っているかを、ベンダーをまたいで拾う。

更改のときに「この機器は何をしていたのか」が分からないと、同じものを
用意できない。中身の解釈まではせず、入っているかどうかと主な値を拾う。
"""

from app.services import capabilities, configparse
from app.services.configparse import features
from tests.conftest import API

IOS = """hostname core-sw-01
!
ip dhcp pool OFFICE
!
spanning-tree mode rapid-pvst
!
interface Vlan10
 ip address 10.10.10.1 255.255.255.0
 ip helper-address 10.0.0.5
 ip access-group GUEST in
 standby 10 ip 10.10.10.254
!
interface GigabitEthernet1/0/1
 channel-group 1 mode active
 switchport port-security
!
ip access-list extended GUEST
 permit tcp any any eq 443
!
ip nat inside source list 1 interface Gi0/0 overload
!
snmp-server community public RO
logging 10.0.0.9
ntp server 10.0.0.10
aaa new-model
!
crypto isakmp policy 10
!
ip pim sparse-mode
ip igmp snooping
"""


def _found(text: str) -> dict[str, features.ParsedFeature]:
    return {f"{f.category}:{f.name}": f for f in configparse.parse(text).features}


def test_it_finds_what_the_device_is_doing():
    found = _found(IOS)
    assert f"{features.ACCESS_CONTROL}:ACL" in found
    assert f"{features.NAT}:NAT" in found
    assert f"{features.DHCP}:DHCP サーバー" in found
    assert f"{features.SPANNING_TREE}:STP" in found
    assert f"{features.AGGREGATION}:LAG" in found
    assert f"{features.MULTICAST}:PIM" in found
    assert f"{features.VPN}:IPsec" in found
    assert f"{features.HIGH_AVAILABILITY}:HSRP" in found


def test_the_destination_is_kept_where_it_matters():
    """送り先が分からないと、更改のときに同じものを用意できない。"""
    found = _found(IOS)
    assert "10.0.0.9" in found[f"{features.MANAGEMENT}:Syslog"].detail
    assert "10.0.0.10" in found[f"{features.MANAGEMENT}:NTP"].detail
    assert "10.0.0.5" in found[f"{features.DHCP}:DHCP リレー"].detail
    assert "rapid-pvst" in found[f"{features.SPANNING_TREE}:STP"].detail


def test_every_find_carries_the_line_it_came_from():
    for feature in configparse.parse(IOS).features:
        assert feature.evidence
        assert feature.count >= 1


COMWARE = """sysname edge-sw-01
#
acl number 3000
#
dhcp relay server-address 10.0.0.5
#
stp global enable
#
interface GigabitEthernet1/0/1
 port link-aggregation group 2
 port-security enable
#
nat outbound
#
snmp-agent community read public
info-center loghost 10.0.0.9
ntp-service unicast-server 10.0.0.10
#
igmp-snooping enable
#
interface Vlan-interface10
 vrrp vrid 10 virtual-ip 10.10.10.254
"""


def test_the_same_meaning_is_found_in_another_vendor_wording():
    found = _found(COMWARE)
    assert f"{features.ACCESS_CONTROL}:ACL" in found
    assert f"{features.NAT}:NAT" in found
    assert f"{features.DHCP}:DHCP リレー" in found
    assert f"{features.SPANNING_TREE}:STP" in found
    assert f"{features.AGGREGATION}:LAG" in found
    assert f"{features.AUTHENTICATION}:ポートセキュリティ" in found
    assert f"{features.MANAGEMENT}:Syslog" in found
    assert f"{features.MULTICAST}:IGMP スヌーピング" in found
    assert f"{features.HIGH_AVAILABILITY}:VRRP" in found


FORTIOS = """config system global
    set hostname "fw-01"
end
config firewall policy
    edit 1
    next
end
config system zone
end
config vpn ipsec phase1-interface
end
config firewall vip
end
"""


def test_a_firewall_shows_its_policies_and_zones():
    found = _found(FORTIOS)
    assert f"{features.ACCESS_CONTROL}:ポリシー" in found
    assert f"{features.ACCESS_CONTROL}:ゾーン" in found
    assert f"{features.VPN}:IPsec" in found
    assert f"{features.LOAD_BALANCE}:仮想サーバー" in found


def test_a_plain_switch_config_finds_nothing_extra():
    """曖昧な語で拾うと、入っていない機能が台帳に載る。"""
    found = _found("hostname sw-01\n!\ninterface Gi1/0/1\n switchport mode access\n")
    assert found == {}


def test_a_line_is_counted_once():
    text = "ip access-list extended A\nip access-list extended B\n"
    found = _found(text)
    assert found[f"{features.ACCESS_CONTROL}:ACL"].count == 2


# ---------------------------------------------------------------------------
# 対応状況の一覧
# ---------------------------------------------------------------------------


def test_the_matrix_says_what_cannot_be_done_too():
    """できることだけ並べると、できないことが伝わらない。"""
    names = {item.name: item for item in capabilities.CAPABILITIES}
    assert names["実機への投入・監視"].read == capabilities.NONE
    assert names["実機への投入・監視"].generate == capabilities.NONE
    assert names["静的経路"].generate == capabilities.FULL
    for item in capabilities.CAPABILITIES:
        assert item.note, item.name


def test_every_feature_we_read_has_a_row_in_the_matrix():
    """読めるのに一覧に無いと、何ができるのか分からなくなる。"""
    names = {item.name for item in capabilities.CAPABILITIES}
    for category, capability_name in capabilities.FEATURE_TO_CAPABILITY.items():
        assert category in features.FEATURE_LABELS
        assert capability_name in names


def test_the_api_returns_the_matrix(client):
    body = client.get(f"{API}/capabilities").json()
    assert body["levels"]["none"] == "できない"
    assert any(item["name"] == "ログの解析" for item in body["items"])
    assert body["areas"][0] == "土台"


def test_the_device_l3_view_carries_the_features(client, db_session):
    from app.models import Device
    from app.models.topology import ConfigSnapshot

    device = Device(hostname="core-sw-01", vendor="Cisco", model="C9500-40X")
    db_session.add(device)
    db_session.flush()
    db_session.add(
        ConfigSnapshot(
            device_id=device.id,
            hostname=device.hostname,
            filename="core.txt",
            content=IOS,
            sha256="a" * 64,
        )
    )
    db_session.commit()

    body = client.get(f"{API}/devices/{device.id}/l3").json()
    assert any(f["name"] == "ACL" for f in body["features"])
    assert all(f["category_label"] for f in body["features"])
