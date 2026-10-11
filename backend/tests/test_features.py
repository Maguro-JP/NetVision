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


NECQX = """sysname edge-sw-01
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
    found = _found(NECQX)
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
    edit "web-vip"
        set type server-load-balance
        set extip 203.0.113.10
    next
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


def test_the_newer_vendors_are_picked_up_too():
    """ベンダーを足したら、機能の拾い出しにも同じ意味の行を足す。

    書き方は違っても落ちる先は同じ。ここが抜けていると、台帳で
    「この機器は何をしていたのか」が空のままになる。
    """
    from pathlib import Path

    from app.services import configparse

    fixtures = Path(__file__).parent / "fixtures"

    def names(filename: str) -> set[str]:
        parsed = configparse.parse((fixtures / filename).read_text())
        return {feature.name for feature in parsed.features}

    # Aruba CX は VSX で2台を1台に見せる。束ねは `lag 1`
    assert {"VSX", "LAG"} <= names("aruba-cx-01.txt")
    # アライドテレシスの束ねは `static-channel-group`
    assert "LAG" in names("allied-x530-01.txt")
    # ヤマハと NEC IX の拠点間 VPN
    assert "IPsec" in names("rtx-osaka-01.txt")
    assert "IPsec" in names("ix-rt-01.txt")


def test_syslog_and_ntp_are_found_in_every_vendor_s_wording():
    """送り先が入っているのに「入っていません」と言うのが、いちばん信用を落とす。"""
    from app.services.configparse import features

    samples = {
        "yamaha": "syslog host 192.168.100.10\nschedule at 1 */* 06:00 * ntpdate ntp.nict.jp\n",
        "junos": "set system syslog host 10.0.0.5 any any\nset system ntp server 10.0.0.6\n",
        "asa": "logging host inside 10.0.0.5\nntp server 10.0.0.6 source inside\n",
        "allied": "log host 10.0.0.5\nntp server 10.0.0.6\n",
        "fortios": "config log syslogd setting\n    set status enable\nend\n"
        "config system ntp\n    set ntpsync enable\nend\n",
        "panos": "set shared log-settings syslog SRV server s1 server 10.0.0.5\n"
        "set deviceconfig system ntp-servers primary-ntp-server ntp-server-address 10.0.0.6\n",
    }
    for vendor, text in samples.items():
        names = {f.name for f in features.parse(text)}
        assert {"Syslog", "NTP"} <= names, (vendor, names)


def test_a_static_nat_vip_is_not_load_balancing() -> None:
    """FortiGate の vip は静的 NAT にも使う。負荷分散なのは server-load-balance だけ。

    出典: FortiOS CLI Reference の config firewall vip（set type の選択肢に static-nat と
    server-load-balance がある）。
    """
    text = """config firewall vip
    edit "dnat-web"
        set extip 203.0.113.10
        set mappedip "192.168.1.10"
    next
end
"""
    assert f"{features.LOAD_BALANCE}:仮想サーバー" not in _found(text)


def test_a_standalone_catalyst_is_not_a_stack() -> None:
    """単体の Catalyst にも `switch 1 provision` が1行ある。2台分以上でスタックとする。

    出典: Cisco IOS XE（Catalyst 9300）Stack Manager and High Availability Configuration Guide。
    """
    one = "hostname sw1\nswitch 1 provision c9300-48p\n"
    two = one + "switch 2 provision c9300-48p\n"
    assert f"{features.HIGH_AVAILABILITY}:スタック" not in _found(one)
    assert _found(two)[f"{features.HIGH_AVAILABILITY}:スタック"].count == 2


def test_one_member_irf_or_stack_ports_are_not_a_stack() -> None:
    """組む前の1台にも自分の番号の IRF ポート・スタックポートが書かれる。2台分以上で組とする。

    出典: H3C「Automatic IRF Setup Configuration Examples」の 210235A045B05B0004354.cfg
    （`irf-port 1/2` だけの1台分）と Configuration files（`irf-port 1/2` と `irf-port 2/1`）、
    Huawei CloudEngine 8800/6800/5800 Configuration Guide - Virtualization「Example for
    Establishing a Stack of Two Switches」の Configuration Files
    （物理口の下の `stack-port 1/1`・`2/1`）。
    """
    irf_one = (
        "irf member 1 priority 1\nirf-port 1/2\n port group interface Ten-GigabitEthernet1/1/1\n"
    )
    irf_two = irf_one + "irf-port 2/1\n port group interface Ten-GigabitEthernet2/1/1\n"
    ce_one = (
        "interface Stack-Port1/1\n#\n"
        "interface 10GE1/0/1\n port mode stack\n stack-port 1/1\n"
        "interface 10GE1/0/2\n port mode stack\n stack-port 1/1\n"
    )
    ce_two = ce_one + "interface 10GE2/0/1\n port mode stack\n stack-port 2/1\n"
    key = f"{features.HIGH_AVAILABILITY}:スタック"
    assert key not in _found(irf_one)
    assert key not in _found(ce_one)
    assert _found(irf_two)[key].count == 2
    assert _found(ce_two)[key].count == 2


def test_pim_written_the_arista_aoscx_and_junos_way_is_found() -> None:
    """Arista・AOS-CX・Junos の PIM と、Junos の IGMP スヌーピングを拾う。

    出典: Arista EOS User Manual、AOS-CX Multicast Guide、Junos OS の各資料。
    """
    for text in (
        "interface Vlan10\n   pim ipv4 sparse-mode\n",
        "interface vlan10\n    ip pim-sparse enable\n",
        "set protocols pim interface all mode sparse\n",
    ):
        assert f"{features.MULTICAST}:PIM" in _found(text), text
    junos = "set protocols igmp-snooping vlan SALES\n"
    assert f"{features.MULTICAST}:IGMP スヌーピング" in _found(junos)


def test_access_control_nat_and_snmp_written_other_vendors_ways_are_found() -> None:
    """ヤマハ・PAN-OS・H3C の ACL・NAT・SNMP を拾う。

    出典:
    - ヤマハ「NAT ディスクリプター コマンド仕様」「SNMP」（rtpro.yamaha.co.jp）と
      「ローカルルーターで複数の LAN を接続（動的 IP マスカレード）」のコマンド設定
    - Palo Alto Networks Knowledge Base「Viewing the configuration in set and XML format」
      「How to Create and View NAT Policies Using the CLI」
    - H3C「EAD Authentication (IPv6) Configuration Examples」の Configuration files
    """
    yamaha = (
        "ip lan2 secure filter in 2000\n"
        "ip lan2 nat descriptor 1\n"
        "ip filter 2000 reject * *\n"
        "nat descriptor type 1 masquerade\n"
        "snmpv2c host 192.168.1.1\n"
    )
    found = _found(yamaha)
    assert found[f"{features.ACCESS_CONTROL}:ACL"].count == 1
    assert found[f"{features.ACCESS_CONTROL}:ACL の適用"].count == 1
    assert found[f"{features.NAT}:NAT"].count == 2
    assert f"{features.MANAGEMENT}:SNMP" in found
    panos = (
        "set rulebase security rules rule1 action allow\n"
        "set rulebase nat rules StaticNAT from DMZ to L3-Untrust service any\n"
    )
    found = _found(panos)
    assert f"{features.ACCESS_CONTROL}:ポリシー" in found
    assert f"{features.NAT}:NAT" in found
    h3c = "acl ipv6 advanced 3000\n rule 0 permit ipv6\n"
    assert f"{features.ACCESS_CONTROL}:ACL" in _found(h3c)


def test_yamaha_snmp_host_none_is_not_snmp() -> None:
    """`snmp host none` はどこからも受け付けない（初期値）。SNMP が入っているとは言わない。

    出典: ヤマハ「SNMP」（rtpro.yamaha.co.jp）の snmp host の書式と初期値。
    """
    assert f"{features.MANAGEMENT}:SNMP" not in _found("snmp host none\nsnmpv2c host none\n")


def test_dhcp_written_other_vendors_ways_is_found() -> None:
    """ヤマハ・FortiGate・Huawei VRP・Junos・AOS-CX の DHCP サーバーと、VRP の DHCP リレーを拾う。

    出典:
    - ヤマハ「NAT ディスクリプターの設定例 (7)」ほか（`dhcp service server` と `dhcp scope 1 ...`）
    - FortiOS Administration Guide「Basic configuration」（`config system dhcp server`）
    - Huawei S200/S300/S500/S600 Configuration Guide「Example for Configuring DHCPv4 Relay」の
      Configuration Files（`dhcp select global`、`dhcp select relay`、`dhcp relay server-ip`）
    - Juniper Support「[EX] Secondary TFTP cannot be configured in the same DHCP pool」
      （`set system services dhcp-local-server group ... interface ...`）
    - AOS-CX 10.12 IP Services Guide「Troubleshooting」（`dhcp-server vrf default` の塊の `enable`）
    """
    server = f"{features.DHCP}:DHCP サーバー"
    for text in (
        "dhcp service server\ndhcp scope 1 192.168.0.2-192.168.0.191/24\n",
        'config system dhcp server\n    edit 1\n        set interface "port1"\n',
        "interface Vlanif200\n ip address 10.10.20.2 255.255.255.0\n dhcp select global\n",
        "set system services dhcp-local-server group VOICE interface irb.688\n",
        "dhcp-server vrf default\n    pool pool1\n        range 172.16.10.1 172.16.10.20\n"
        "        exit\n    enable\n",
    ):
        assert server in _found(text), text
    relay = _found("interface Vlanif100\n dhcp select relay\n dhcp relay server-ip 10.10.20.2\n")
    assert relay[f"{features.DHCP}:DHCP リレー"].detail == "転送先: 10.10.20.2"


def test_aoscx_dhcp_server_without_enable_is_not_a_server() -> None:
    """AOS-CX の DHCP サーバーは塊の中の `enable` で動く。enable の無い塊は配っていない。

    出典: AOS-CX 10.12 IP Services Guide「Troubleshooting」
    （「is in enabled state」を確かめる手順）。
    """
    text = "dhcp-server vrf default\n    pool pool1\n        range 172.16.10.1 172.16.10.20\n"
    assert f"{features.DHCP}:DHCP サーバー" not in _found(text)


def test_stp_ssh_and_ipsec_written_other_vendors_ways_are_found() -> None:
    """Junos の RSTP と SSH、Huawei の STelnet、PAN-OS の IPsec を拾う。

    出典:
    - Juniper「Example: Faster Convergence and Improved Network Stability with RSTP on EX Series
      Switches」（`set protocols rstp bridge-priority 16k`）
    - Juniper Support「[EX] How to enable remote management access to CLI on EX switches」
      （`set system services ssh`）
    - Huawei S300/S500/S2700/S5700/S6700 V200R023C10 Configuration Guide の STelnet の例の
      Configuration File（`stelnet server enable`）
    - Palo Alto Networks Knowledge Base「How to Configure an IPSEC VPN with Route and Tunnel
      Configuration from CLI」（`set network tunnel ipsec ... auto-key ike-gateway ...`）
    """
    assert f"{features.SPANNING_TREE}:STP" in _found("set protocols rstp bridge-priority 16k\n")
    for text in ("set system services ssh\n", "stelnet server enable\n"):
        assert f"{features.MANAGEMENT}:SSH" in _found(text), text
    panos = "set network tunnel ipsec NY auto-key ike-gateway NY\n"
    assert f"{features.VPN}:IPsec" in _found(panos)


def test_yamaha_sshd_is_found_as_ssh():
    """ヤマハの `sshd service on` を SSH として拾う。`off` は拾わない。

    出典: ヤマハ「TELNET複数セッション機能、SSHサーバー機能」（sshd service on で
    SSH サーバーを動かす。既定は off で、ホスト鍵を sshd host key generate で
    作るまでつながらない）。
    """
    head = "# RTX830 Rev.15.02.28 (Fri Jan 26 09:52:25 2024)\nip lan1 address 192.168.100.1/24\n"
    assert f"{features.MANAGEMENT}:SSH" in _found(head + "sshd service on\n")
    assert f"{features.MANAGEMENT}:SSH" not in _found(head + "sshd service off\n")


def test_h3c_ssh_server_enable_is_found_as_ssh():
    """H3C の `ssh server enable` を SSH として拾う。`undo` の形は拾わない。

    出典: H3C「13-SSH commands」の ssh server enable（Stelnet サーバーを動かす。
    既定は無効。undo ssh server enable で止める）。
    """
    head = "H3C Comware Software, Version 7.1.064\nsysname h3c-s1\n#\n"
    assert f"{features.MANAGEMENT}:SSH" in _found(head + "ssh server enable\n#\n")
    assert f"{features.MANAGEMENT}:SSH" not in _found(head + "undo ssh server enable\n#\n")


def test_h3c_dhcp_select_server_is_found_as_dhcp_server():
    """H3C の口の下の `dhcp select server`（Comware 7）と `... global-pool`（Comware 5）を拾う。

    出典: H3C「03-DHCP configuration」Enabling the DHCP server on an interface
    （dhcp select server）、「03-DHCP Commands」の dhcp select server global-pool [ subaddress ]。
    """
    head = "H3C Comware Software, Version 7.1.064\nsysname h3c-s1\n#\n"
    for line in ("dhcp select server", "dhcp select server global-pool"):
        text = head + "interface Vlan-interface10\n " + line + "\n#\n"
        assert f"{features.DHCP}:DHCP サーバー" in _found(text), line
