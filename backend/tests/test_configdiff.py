"""差分エンジンの検証。

一番大事なのは「差分を当てた結果が将来フルと一致すること」なので、
個々のコマンドの見た目より先に、そこを繰り返し確かめる。
"""

from __future__ import annotations

import pytest

from app.services.configdiff import build
from app.services.configdiff.dialects import for_vendor
from app.services.configdiff.engine import apply_script, equivalent, normalize
from app.services.configdiff.tree import parse

CURRENT = """\
hostname core-sw-01
!
vlan 10
 name USERS
!
interface GigabitEthernet1/0/1
 description ->edge-rt-01
 switchport mode trunk
 switchport trunk allowed vlan 10,20
!
interface GigabitEthernet1/0/2
 description spare
 switchport mode access
 switchport access vlan 10
 shutdown
!
ip route 0.0.0.0 0.0.0.0 10.0.0.1
"""


def converges(current: str, target: str, vendor: str | None = None) -> None:
    """差分を当てた結果が将来 config と一致することを確かめる。"""
    result = build(current, target, vendor)
    assert result.verified, f"検証が通らなかった: {result.mismatches}"

    dialect = for_vendor(vendor)
    applied = apply_script(normalize(parse(current, dialect), dialect), result.script, dialect)
    expected = normalize(parse(target, dialect), dialect)
    assert equivalent(applied, expected, dialect, ()), result.script


# ---------------------------------------------------------------------------
# 収束すること（ここが本丸）
# ---------------------------------------------------------------------------


def test_no_change_produces_no_commands():
    result = build(CURRENT, CURRENT)
    assert result.is_empty
    assert result.verified


def test_adding_a_line_converges():
    target = CURRENT.replace(
        " switchport access vlan 10\n", " switchport access vlan 10\n spanning-tree portfast\n"
    )
    converges(CURRENT, target)

    result = build(CURRENT, target)
    assert "spanning-tree portfast" in result.script
    # 見出しを再掲して配下に入る
    assert "interface GigabitEthernet1/0/2" in result.script


def test_removing_a_line_converges():
    target = CURRENT.replace(" shutdown\n", "")
    converges(CURRENT, target)
    assert "no shutdown" in build(CURRENT, target).script


def test_changing_a_value_is_one_command_not_two():
    """description の書き換えが「削除＋追加」に割れると、投入時に一瞬消える。"""
    target = CURRENT.replace("description spare", "description ->fw-01")
    converges(CURRENT, target)

    result = build(CURRENT, target)
    assert "description ->fw-01" in result.script
    assert "no description" not in result.script
    assert result.changed == 1


def test_new_block_brings_its_children():
    target = CURRENT + "interface Vlan20\n ip address 192.168.20.1 255.255.255.0\n"
    converges(CURRENT, target)

    result = build(CURRENT, target)
    assert "interface Vlan20" in result.script
    assert "ip address 192.168.20.1 255.255.255.0" in result.script


def test_removed_block_is_negated_whole():
    target = CURRENT.replace(
        "interface GigabitEthernet1/0/2\n"
        " description spare\n"
        " switchport mode access\n"
        " switchport access vlan 10\n"
        " shutdown\n!\n",
        "",
    )
    converges(CURRENT, target)
    assert "no interface GigabitEthernet1/0/2" in build(CURRENT, target).script


def test_many_simultaneous_changes_converge():
    target = """\
hostname core-sw-01
!
vlan 10
 name SERVERS
!
vlan 30
 name MGMT
!
interface GigabitEthernet1/0/1
 description ->edge-rt-01 (10G)
 switchport mode trunk
 switchport trunk allowed vlan 10,20,30
!
interface GigabitEthernet1/0/2
 description ->fw-01
 switchport mode access
 switchport access vlan 30
!
ip route 0.0.0.0 0.0.0.0 10.0.0.254
"""
    converges(CURRENT, target)


@pytest.mark.parametrize(
    "target",
    [
        "",  # 全部消す
        CURRENT,  # 変化なし
        "hostname other\n",  # ほぼ全部入れ替え
        CURRENT + "ntp server 10.0.0.5\n",
    ],
)
def test_edge_cases_converge(target):
    converges(CURRENT, target)


# ---------------------------------------------------------------------------
# `no X` の扱い
# ---------------------------------------------------------------------------


def test_negated_line_in_config_means_absence():
    """running-config の `no ip domain-lookup` を独立した行として数えない。"""
    dialect = for_vendor("cisco_ios")
    with_negation = parse("ip domain-lookup\nno ip domain-lookup\n", dialect)
    assert with_negation.children == {}


def test_target_written_with_no_form_is_understood():
    current = "interface Gi1/0/1\n shutdown\n"
    target = "interface Gi1/0/1\n no shutdown\n"
    converges(current, target)
    assert "no shutdown" in build(current, target).script


def test_no_switchport_and_absence_are_the_same_state():
    current = "interface Gi1/0/1\n no switchport\n ip address 10.0.0.1 255.255.255.0\n"
    target = "interface Gi1/0/1\n ip address 10.0.0.1 255.255.255.0\n"
    assert build(current, target).is_empty


# ---------------------------------------------------------------------------
# 順序が意味を持つブロック
# ---------------------------------------------------------------------------

ACL_CURRENT = """\
ip access-list extended BLOCK
 permit tcp any any eq 443
 deny ip any any
"""


def test_appending_to_an_acl_is_just_an_append():
    target = ACL_CURRENT + " permit udp any any eq 53\n"
    # 末尾に足すだけなら消さずに入れられる
    result = build(ACL_CURRENT, target)
    assert result.verified
    assert "no ip access-list extended BLOCK" not in result.script


def test_inserting_into_an_acl_rebuilds_the_whole_block():
    """途中に挟むと評価順が変わるので、行単位で入れてはいけない。"""
    target = """\
ip access-list extended BLOCK
 permit tcp any any eq 443
 permit tcp any any eq 22
 deny ip any any
"""
    result = build(ACL_CURRENT, target)
    assert result.verified
    assert "no ip access-list extended BLOCK" in result.script
    assert any("順序" in w for w in result.warnings)
    converges(ACL_CURRENT, target)


def test_reordering_an_acl_rebuilds_it():
    target = """\
ip access-list extended BLOCK
 deny ip any any
 permit tcp any any eq 443
"""
    converges(ACL_CURRENT, target)
    assert "no ip access-list extended BLOCK" in build(ACL_CURRENT, target).script


def test_acl_order_is_not_silently_accepted_as_equal():
    """順序違いを「同じ」と判定したら差分が出ず、事故になる。"""
    reordered = """\
ip access-list extended BLOCK
 deny ip any any
 permit tcp any any eq 443
"""
    assert not build(ACL_CURRENT, reordered).is_empty


# ---------------------------------------------------------------------------
# 検証が落ちたときの振る舞い
# ---------------------------------------------------------------------------


def test_unverified_diff_returns_no_commands(monkeypatch):
    """収束しない差分は、出さずに理由を返す。"""
    from app.services.configdiff import engine

    def broken(base, script, dialect):  # 生成側が壊れている状況を作る
        return base

    monkeypatch.setattr(engine, "apply_script", broken)

    result = build(CURRENT, CURRENT.replace(" shutdown\n", ""))
    assert not result.verified
    assert result.commands == []
    assert result.mismatches


def test_mismatch_message_names_the_place(monkeypatch):
    from app.services.configdiff import engine

    monkeypatch.setattr(engine, "apply_script", lambda base, script, dialect: base)
    result = build(CURRENT, CURRENT + "ntp server 10.0.0.5\n")

    assert any("ntp server 10.0.0.5" in m for m in result.mismatches)


# ---------------------------------------------------------------------------
# 切り戻し
# ---------------------------------------------------------------------------


def test_rollback_returns_to_the_current_state():
    target = CURRENT.replace("description spare", "description ->fw-01")
    result = build(CURRENT, target)

    dialect = for_vendor(None)
    restored = apply_script(
        normalize(parse(target, dialect), dialect), result.rollback_script, dialect
    )
    assert equivalent(restored, normalize(parse(CURRENT, dialect), dialect), dialect, ())


def test_rollback_is_offered_for_removals_too():
    target = CURRENT.replace(" shutdown\n", "")
    result = build(CURRENT, target)
    assert "shutdown" in result.rollback_script


# ---------------------------------------------------------------------------
# 危険な操作の注意喚起
# ---------------------------------------------------------------------------


def test_dangerous_commands_are_flagged():
    target = CURRENT.replace(
        "interface GigabitEthernet1/0/1\n"
        " description ->edge-rt-01\n"
        " switchport mode trunk\n"
        " switchport trunk allowed vlan 10,20\n!\n",
        "",
    )
    result = build(CURRENT, target)
    assert any("接続が切れます" in r for r in result.risks)


def test_shutdown_is_flagged():
    target = CURRENT.replace(" description ->edge-rt-01\n", " description x\n shutdown\n")
    result = build(CURRENT, target)
    assert any("通信が止まります" in r for r in result.risks)


def test_removal_warns_about_defaults():
    """「書いていない＝消す」の解釈は原理的に確定できないので、必ず伝える。"""
    result = build(CURRENT, CURRENT.replace(" shutdown\n", ""))
    assert any("既定値" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 前提コマンド
# ---------------------------------------------------------------------------


def test_switchport_prerequisite_is_inserted():
    """routed ポートに switchport 系を入れるには、先に switchport が要る。"""
    current = "interface Gi1/0/3\n no switchport\n ip address 10.0.0.1 255.255.255.0\n"
    target = "interface Gi1/0/3\n switchport mode access\n switchport access vlan 10\n"
    result = build(current, target)

    lines = [c.text for c in result.commands]
    assert "switchport" in lines
    assert lines.index("switchport") < lines.index("switchport mode access")


# ---------------------------------------------------------------------------
# 他ベンダー
# ---------------------------------------------------------------------------

JUNOS_CURRENT = """\
set system host-name edge-rt-01
set interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/30
set interfaces ge-0/0/1 description spare
set routing-options static route 0.0.0.0/0 next-hop 10.0.0.2
"""


def test_junos_set_format_converges():
    target = JUNOS_CURRENT.replace("description spare", "description ->core-sw-01")
    converges(JUNOS_CURRENT, target, "juniper")


def test_junos_removal_uses_delete():
    target = JUNOS_CURRENT.replace("set interfaces ge-0/0/1 description spare\n", "")
    result = build(JUNOS_CURRENT, target, "juniper")

    assert result.verified
    assert "delete interfaces ge-0/0/1 description spare" in result.script
    assert "no set" not in result.script


FORTI_CURRENT = """\
config system interface
    edit "port1"
        set ip 10.0.0.1 255.255.255.0
        set allowaccess ping https ssh
        set alias "WAN"
    next
end
"""


def test_fortios_value_change_converges():
    target = FORTI_CURRENT.replace('set alias "WAN"', 'set alias "UPLINK"')
    converges(FORTI_CURRENT, target, "fortinet")

    result = build(FORTI_CURRENT, target, "fortinet")
    assert 'set alias "UPLINK"' in result.script
    assert "unset alias" not in result.script


def test_fortios_removal_uses_unset():
    target = FORTI_CURRENT.replace('        set alias "WAN"\n', "")
    result = build(FORTI_CURRENT, target, "fortinet")

    assert result.verified
    assert "unset alias" in result.script


# ---------------------------------------------------------------------------
# 総当たりに近い形での収束確認
#
# 手で書いたケースだけだと「思いついた壊れ方」しか試せない。
# config をランダムに変形して、そのすべてで収束することを確かめる。
# ---------------------------------------------------------------------------


def _mutate(lines: list[str], rng) -> list[str]:
    out = list(lines)
    for _ in range(rng.randint(1, 6)):
        if not out:
            break
        op = rng.choice(["delete", "change", "insert", "move"])
        index = rng.randrange(len(out))

        if op == "delete":
            del out[index]
        elif op == "change":
            line = out[index]
            if line.startswith(" description "):
                out[index] = f" description changed-{rng.randrange(100)}"
            elif line.startswith(" switchport access vlan "):
                out[index] = f" switchport access vlan {rng.choice([10, 20, 30])}"
            elif line.startswith("ip route "):
                out[index] = f"ip route 0.0.0.0 0.0.0.0 10.0.0.{rng.randrange(1, 254)}"
        elif op == "insert":
            body = rng.choice(
                [
                    " shutdown",
                    " spanning-tree portfast",
                    " ip helper-address 10.0.0.9",
                    "ntp server 10.0.0.5",
                    "vlan 40",
                ]
            )
            # ぶら下がり行はブロック内にしか置けないので、置ける場所を選ぶ
            inside_block = index and out[index - 1].startswith(("interface", " "))
            if body.startswith(" ") and not inside_block:
                continue
            out.insert(index, body)
        elif op == "move" and len(out) > 2:
            out.insert(rng.randrange(len(out)), out.pop(index))
    return out


@pytest.mark.parametrize("seed", range(200))
def test_random_mutations_always_converge(seed):
    import random

    rng = random.Random(seed)
    target = "\n".join(_mutate(CURRENT.splitlines(), rng)) + "\n"

    result = build(CURRENT, target)
    # 収束しない場合はコマンドを出さない、が守られていること
    if not result.verified:
        assert result.commands == []
        assert result.mismatches
        pytest.fail(f"収束しなかった:\n{target}\n{result.mismatches}")

    dialect = for_vendor(None)
    applied = apply_script(normalize(parse(CURRENT, dialect), dialect), result.script, dialect)
    expected = normalize(parse(target, dialect), dialect)
    assert equivalent(applied, expected, dialect, ()), result.script


def test_acl_rebuild_does_not_enter_the_block_first():
    """`ip access-list ...` で中に入ってから `no ip access-list ...` は実機で通らない。"""
    target = """\
ip access-list extended BLOCK
 permit tcp any any eq 22
 permit tcp any any eq 443
 deny ip any any
"""
    lines = build(ACL_CURRENT, target).script.splitlines()
    assert lines[0] == "no ip access-list extended BLOCK"


def test_removing_one_line_of_a_numbered_acl_carries_a_warning():
    """番号で書いた ACL は、1行を指定しても中身がまるごと消える機種がある。"""
    current = (
        "hostname a\n"
        "access-list 99 permit 10.0.0.0 0.0.0.255\n"
        "access-list 99 permit 10.0.1.0 0.0.0.255\n"
    )
    target = "hostname a\naccess-list 99 permit 10.0.0.0 0.0.0.255\n"
    result = build(current, target, "cisco_ios")
    assert result.verified
    assert any("まるごと消える" in risk for risk in result.risks)


def test_adding_a_line_to_a_numbered_acl_carries_no_such_warning():
    current = "hostname a\naccess-list 99 permit 10.0.0.0 0.0.0.255\n"
    target = current + "access-list 99 permit 10.0.1.0 0.0.0.255\n"
    result = build(current, target, "cisco_ios")
    assert not any("まるごと消える" in risk for risk in result.risks)


_FORTI_CURRENT = """config system global
    set hostname fw1
end
config firewall address
    edit "INSIDE"
        set subnet 10.0.0.0 255.255.0.0
    next
end
config firewall policy
    edit 1
        set name "out"
        set service "ALL"
    next
end
"""


def test_fortios_commands_close_their_blocks():
    """FortiOS は字下げでは閉じない。閉じる語が無いと、そのまま貼れない。"""
    target = _FORTI_CURRENT.replace('        set service "ALL"\n', '        set service "HTTPS"\n')
    result = build(_FORTI_CURRENT, target, "fortinet")
    assert result.verified
    lines = result.script.strip().splitlines()
    assert lines[0].strip() == "config firewall policy"
    assert lines[-1].strip() == "end"
    assert any(line.strip() == "next" for line in lines)


def test_fortios_closes_each_block_before_opening_the_next():
    target = _FORTI_CURRENT.replace(
        '    edit "INSIDE"\n        set subnet 10.0.0.0 255.255.0.0\n    next\n',
        '    edit "INSIDE"\n        set subnet 10.0.0.0 255.255.0.0\n    next\n'
        '    edit "DMZ"\n        set subnet 172.16.0.0 255.255.255.0\n    next\n',
    ).replace('        set service "ALL"\n', '        set service "HTTPS"\n')
    result = build(_FORTI_CURRENT, target, "fortinet")
    assert result.verified
    script = result.script
    # 2つ目のブロックが始まる前に、1つ目が閉じている
    assert script.index("end") < script.index("config firewall policy")


def test_the_closing_words_are_not_read_back_as_settings():
    """`next` / `end` を設定として読むと、検証がずれる。"""
    target = _FORTI_CURRENT.replace('        set service "ALL"\n', '        set service "HTTPS"\n')
    result = build(_FORTI_CURRENT, target, "fortinet")
    assert result.verified
    assert result.mismatches == []


def test_other_vendors_are_still_rendered_by_indentation():
    current = "hostname a\ninterface Gi0/1\n description old\n"
    target = "hostname a\ninterface Gi0/1\n description new\n"
    script = build(current, target, "cisco_ios").script
    assert "end" not in script
    assert "next" not in script


def test_the_entry_settings_are_replaced_in_one_line() -> None:
    """入口の設定は、新しい値がそのまま前の値を置き換える。

    「消してから入れる」を2行に分けて出すと、その間だけ入口が塞がる。
    vty ごしに貼っていると、次の行が届く前に自分が切れる。
    """
    current = "\n".join(
        [
            "hostname sw",
            "!",
            "line vty 0 4",
            " transport input telnet ssh",
            " exec-timeout 0 0",
            "!",
            "ip ssh version 1",
            "",
        ]
    )
    wanted = current.replace("transport input telnet ssh", "transport input ssh")
    wanted = wanted.replace("exec-timeout 0 0", "exec-timeout 10 0")
    wanted = wanted.replace("ip ssh version 1", "ip ssh version 2")

    result = build(current, wanted)
    assert result.verified
    assert "no transport input" not in result.script
    assert "no exec-timeout" not in result.script
    assert " transport input ssh" in result.script
    # 切り戻しは、消すのではなく元の値に戻す
    assert " transport input telnet ssh" in result.rollback_script
    assert "ip ssh version 1" in result.rollback_script


def test_the_vendor_is_read_from_the_config_when_it_is_not_given() -> None:
    """ベンダーが渡されなかったときに、Cisco だと決めつけない。

    決めつけると Junos に `no set ...`、Comware に `no ...` を出す。どちらも
    実機では通らない。しかも検証は同じ間違った規則で当てて確かめるので、
    「収束を確認しました」と出てしまう。貼った人は、弾かれるまで気づけない。
    """
    junos = build(
        "set system host-name r1\nset interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/30\n",
        "set system host-name r1\nset interfaces ge-0/0/0 unit 0 family inet address 10.0.0.5/30\n",
    )
    assert junos.verified
    assert junos.script.startswith("delete interfaces")
    assert "no set" not in junos.script

    comware_base = "sysname qx-01\nvlan 10\n name {}\n"
    comware_base += "interface GigabitEthernet1/0/1\n port link-type trunk\n"
    comware = build(comware_base.format("USERS"), comware_base.format("OFFICE"))
    assert comware.verified
    assert "undo name USERS" in comware.script

    forti = build(
        'config system interface\n    edit "port1"\n        set ip 203.0.113.2 255.255.255.248\n'
        "    next\nend\n",
        'config system interface\n    edit "port1"\n        set ip 203.0.113.3 255.255.255.248\n'
        "    next\nend\n",
    )
    assert forti.verified
    assert forti.script.rstrip().endswith("end")

    # 渡された値のほうが強い（取り込み時に判別した結果を上書きしない）
    given = build("hostname sw\nvlan 10\n name USERS\n", "hostname sw\nvlan 10\n name OFFICE\n")
    assert " name OFFICE" in given.script


ACL_BASE = """hostname r1
!
ip access-list extended EDGE
 10 permit tcp any host 203.0.113.10 eq 443
 20 permit tcp any host 203.0.113.10 eq 80
 30 deny ip any any log
"""


def test_a_numbered_line_is_inserted_without_rebuilding_the_acl() -> None:
    """行番号が付いていて足すだけなら、その行だけを入れれば番号の位置に収まる。

    作り直すと、消してから入れ終わるまでフィルターが効かない。境界に当てて
    いる ACL では、その間だけ通ってしまう。
    """
    wanted = ACL_BASE.replace(
        " 20 permit", " 15 permit tcp any host 203.0.113.11 eq 443\n 20 permit"
    )
    result = build(ACL_BASE, wanted, "cisco_ios")

    assert result.verified
    assert "no ip access-list" not in result.script
    assert " 15 permit tcp any host 203.0.113.11 eq 443" in result.script
    # 元からある行は入れ直さない
    assert "203.0.113.10 eq 443" not in result.script
    assert result.warnings == []


def test_removing_or_reordering_still_rebuilds_the_acl() -> None:
    """消す・並べ替えるは、行番号があっても入れ直すしかない。"""
    removed = ACL_BASE.replace(" 20 permit tcp any host 203.0.113.10 eq 80\n", "")
    reordered = ACL_BASE.replace(
        " 10 permit tcp any host 203.0.113.10 eq 443\n 20 permit tcp any host 203.0.113.10 eq 80\n",
        " 10 permit tcp any host 203.0.113.10 eq 80\n 20 permit tcp any host 203.0.113.10 eq 443\n",
    )
    for wanted in (removed, reordered):
        result = build(ACL_BASE, wanted, "cisco_ios")
        assert result.verified
        assert "no ip access-list extended EDGE" in result.script
        assert any("順序が意味を持つ" in note for note in result.warnings)


def test_an_acl_without_line_numbers_still_rebuilds() -> None:
    """番号が無ければ、入れた行がどこに収まるか決まらない。"""
    plain = (
        "hostname r1\n!\nip access-list extended PLAIN\n"
        " permit tcp any any eq 443\n deny ip any any\n"
    )
    wanted = plain.replace(" deny ip any any", " permit tcp any any eq 80\n deny ip any any")
    result = build(plain, wanted, "cisco_ios")

    assert result.verified
    assert "no ip access-list extended PLAIN" in result.script


def test_a_vendor_we_cannot_write_for_gets_no_commands() -> None:
    """出せないものを Cisco の書き方で出さない。

    Cisco の規則で作ると、検証も同じ規則で通るので「収束を確認しました」と
    言ってしまう。動かないコマンドに太鼓判を押す形になる。
    """
    current = "\n".join(
        [
            "set deviceconfig system hostname fw1",
            "set network interface ethernet ethernet1/1 layer3 ip 203.0.113.2/30",
        ]
    )
    target = current.replace("203.0.113.2", "203.0.113.3")
    result = build(current, target)
    assert result.commands == []
    assert result.verified is False
    assert any("出せません" in message for message in result.warnings)


def test_comware_style_vendors_use_undo() -> None:
    # VRP は Comware と同じ消し方。Cisco の no を出すと消えない
    current = "sysname a\ninterface GigabitEthernet1/0/1\n description old\n"
    target = "sysname a\ninterface GigabitEthernet1/0/1\n"
    result = build(current, target, vendor="huawei")
    assert "undo description" in result.script
