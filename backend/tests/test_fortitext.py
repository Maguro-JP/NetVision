"""FortiOS の `config / edit / set / next / end` を読む道具（fortitext）。"""

from app.services import configparse
from app.services.configparse import fortitext


def test_quoted_names_with_spaces_stay_one_item() -> None:
    """並べて書く値は1つずつ引用符で囲まれ、引用符の中には空白も入る。空白で割らない。

    出典: FortiOS 8.0.1 Administration Guide「Explicit proxy authentication」
    （`set groups "NTLM-FSSO-Group" "Ldap-Group"`）と、FortiOS 7.4.10 Administration Guide
    「Internet service groups in policies」（`set name "PC to Google"`）。
    引用符の無い書き方（`set srcaddr Windows_net`）は「Configuring FSSO firewall authentication」。
    """
    text = (
        "config firewall policy\n"
        "    edit 1\n"
        '        set name "PC to Google"\n'
        '        set service "Windows AD" "DNS"\n'
        "        set srcaddr Windows_net internal_net\n"
        '        set groups "NTLM-FSSO-Group" "Ldap-Group"\n'
        "    next\n"
        "end\n"
    )
    [(name, values)] = list(fortitext.blocks(text, "firewall policy"))
    assert name == "1"
    assert values.get("name") == "PC to Google"
    assert fortitext.items(values, "service") == ["Windows AD", "DNS"]
    assert fortitext.items(values, "srcaddr") == ["Windows_net", "internal_net"]
    assert fortitext.items(values, "groups") == ["NTLM-FSSO-Group", "Ldap-Group"]
    assert fortitext.items(values, "dstaddr") == []


def test_nat_source_with_a_space_in_its_name_is_one_name() -> None:
    """送信元の名前に空白があっても、NAT の対象は1つの名前として読む。"""
    text = (
        "config firewall policy\n"
        "    edit 1\n"
        '        set srcintf "port2"\n'
        '        set dstintf "port1"\n'
        '        set srcaddr "Sales LAN"\n'
        "        set nat enable\n"
        "    next\n"
        "end\n"
    )
    parsed = configparse.parse('config system global\nset hostname "fw-01"\nend\n' + text)
    [rule] = parsed.nat.rules
    assert rule.matched == "Sales LAN"
    assert rule.matched_is_name is True
