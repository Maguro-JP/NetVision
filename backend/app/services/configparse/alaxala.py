"""アラクサラ AX シリーズ（AX2500S / AX3650S / AX8600S など）の config を読む。

国内の企業ネットワークでよく残っている機種。書き方は IOS にかなり近いので、
違うところだけをこちらで整えてから IOS の読み手に渡す。違うのは3つ。

- ポート名が種別と番号で分かれている（`interface gigabitethernet 0/1`）
- VLAN の口も分かれている（`interface vlan 10`）
- ホスト名と VLAN 名が引用符で囲われている（`hostname "ax01"`）

公開されている書式から実装したもので、実機の出力では確かめていない。
読めない書き方が出たら、その出力を控えて渡してもらう前提にしている。
"""

from __future__ import annotations

import re

from app.services.configparse import cisco, lldptable
from app.services.configparse.base import ParsedConfig, Vendor

#: `show version` の型番と版数。AX は S/W と H/W を分けて出す
_SW_VERSION = re.compile(r"^\s*S/W\s*:\s*\S+\s+Ver\.\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_HW_MODEL = re.compile(
    r"^\s*(?:H/W|Model)\s*:?\s*(AX\d{3,4}S?[\w-]*)", re.IGNORECASE | re.MULTILINE
)
#: 名乗りが出ている行。これがあれば取り違えようがない
_STRONG = (r"ALAXALA", r"AX-Networks", r"^\s*S/W\s*:\s*OS-L\d", r"^\s*licensed internal code")
#: 型番だけの行。AX2500S のような並びは他社の config に出てこない
_MODEL_ONLY = re.compile(r"\bAX\d{3,4}S\b")

#: 種別と番号が離れている口の書き方。IOS の形に寄せてから渡す
_SPLIT_PORT = re.compile(
    r"^(?P<head>\s*interface\s+)"
    r"(?P<kind>gigabitethernet|tengigabitethernet|fortygigabitethernet|"
    r"hundredgigabitethernet|fastethernet|port-channel|vlan|loopback|管理)"
    r"\s+(?P<number>[\d/.:]+)\s*$",
    re.IGNORECASE,
)
_KINDS = {
    "gigabitethernet": "GigabitEthernet",
    "tengigabitethernet": "TenGigabitEthernet",
    "fortygigabitethernet": "FortyGigabitEthernet",
    "hundredgigabitethernet": "HundredGigabitEthernet",
    "fastethernet": "FastEthernet",
    "port-channel": "Port-channel",
    "vlan": "Vlan",
    "loopback": "Loopback",
}


def detect(text: str) -> Vendor | None:
    """アラクサラかどうか。名乗りか型番が出ているときだけ言う。

    IOS 風の書き方は他社と重なる。目印が無いまま名乗ると、Cisco の config を
    別物として読んでしまうので、そのときは何も言わない。
    """
    for pattern in _STRONG:
        if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
            return Vendor.ALAXALA
    if _MODEL_ONLY.search(text):
        return Vendor.ALAXALA
    return None


def _to_ios(text: str) -> str:
    """種別と番号が離れた口の名前を、IOS と同じ並びに寄せる。"""
    rows: list[str] = []
    for raw in text.splitlines():
        found = _SPLIT_PORT.match(raw)
        if found:
            kind = _KINDS.get(found.group("kind").lower())
            if kind:
                rows.append(f"{found.group('head')}{kind}{found.group('number')}")
                continue
        rows.append(raw)
    return "\n".join(rows)


def parse(text: str) -> ParsedConfig:
    """IOS の読み手に任せてから、AX だけの書き方を上から直す。"""
    parsed = cisco.parse(_to_ios(text), Vendor.CISCO_IOS)
    parsed.vendor = Vendor.ALAXALA
    parsed.os_name = "AX-OS"
    parsed.os_version = None
    parsed.model = None

    if parsed.hostname:
        # `hostname "ax01"` の形。引用符を名前の一部として持たない
        parsed.hostname = parsed.hostname.strip('"')

    found = _SW_VERSION.search(text)
    if found:
        parsed.os_version = found.group(1)
    found = _HW_MODEL.search(text)
    if found:
        parsed.model = found.group(1)
    elif _MODEL_ONLY.search(text):
        parsed.model = _MODEL_ONLY.search(text).group(0)  # type: ignore[union-attr]

    lldptable.parse(text, parsed)
    for vlan_id, name in list(parsed.vlans.items()):
        if name:
            parsed.vlans[vlan_id] = name.strip('"')
    for interface in parsed.interfaces:
        if interface.description:
            interface.description = interface.description.strip('"')
    return parsed
