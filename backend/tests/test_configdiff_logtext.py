"""show の出力が混ざったログから差分を作っても、設定の行だけで比べる。

取り込んだ Config は `show version` → `show running-config` → `show cdp neighbors` と
続くログのまま保存される。以前は版数や隣接機器の表示まで設定として扱い、
投入手順に `no Cisco IOS XE Software, Version ...` が混ざっていた。
"""

from pathlib import Path

from app.services.configdiff import build
from app.services.configdiff.logtext import config_part

FIXTURES = Path(__file__).parent / "fixtures"
IOS = (FIXTURES / "core-sw-01.txt").read_text(encoding="utf-8")
JUNOS = (FIXTURES / "edge-rt-01.txt").read_text(encoding="utf-8")


def _commands(current: str, target: str, vendor: str) -> list[str]:
    return [c.text for c in build(current, target, vendor).commands]


def test_a_changed_show_version_is_not_a_command():
    upgraded = IOS.replace("Version 17.09.04a", "Version 17.12.01")
    result = build(IOS, upgraded, "cisco_ios")
    assert result.commands == []
    assert result.verified


def test_a_changed_neighbor_is_not_a_command():
    moved = IOS.replace("Device ID: edge-rt-01.example.local", "Device ID: other.example.local")
    assert _commands(IOS, moved, "cisco_ios") == []


def test_the_byte_count_header_is_not_a_command():
    before = IOS.replace(
        "Building configuration...",
        "Building configuration...\n\nCurrent configuration : 1000 bytes",
    )
    after = IOS.replace(
        "Building configuration...",
        "Building configuration...\n\nCurrent configuration : 1200 bytes",
    )
    assert _commands(before, after, "cisco_ios") == []


def test_a_real_change_inside_the_config_is_still_found():
    changed = IOS.replace("hostname core-sw-01", "hostname core-sw-01\nbanner motd ^test^", 1)
    assert _commands(IOS, changed, "cisco_ios") == ["banner motd ^test^"]


def test_a_log_and_the_bare_config_compare_equal():
    """計画の本文が設定だけでも、ログのままでも、同じものとして比べる。"""
    assert build(IOS, config_part(IOS), "cisco_ios").commands == []


def test_the_junos_log_keeps_only_the_set_lines():
    part = config_part(JUNOS)
    assert "show lldp" not in part
    assert all(line.startswith("set ") for line in part.splitlines() if line.strip())


def test_a_bare_config_is_left_as_it_is():
    bare = "hostname sw1\ninterface Gi1/0/1\n description up\n!\nend\n"
    assert config_part(bare) == bare
