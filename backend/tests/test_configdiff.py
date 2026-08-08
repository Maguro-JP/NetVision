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

    result = engine.build(CURRENT, CURRENT.replace(" shutdown\n", ""))
    assert not result.verified
    assert result.commands == []
    assert result.mismatches


def test_mismatch_message_names_the_place(monkeypatch):
    from app.services.configdiff import engine

    monkeypatch.setattr(engine, "apply_script", lambda base, script, dialect: base)
    result = engine.build(CURRENT, CURRENT + "ntp server 10.0.0.5\n")

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
