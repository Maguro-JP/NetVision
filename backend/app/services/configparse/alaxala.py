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
from app.services.configparse.base import (
    NeighborProtocol,
    ParsedConfig,
    ParsedNeighbor,
    Vendor,
)

#: `show version` の型番と版数。AX は S/W と H/W を分けて出す
_SW_VERSION = re.compile(r"^\s*S/W\s*:\s*\S+\s+Ver\.\s*(\S+)", re.IGNORECASE | re.MULTILINE)
#: 同じ行の、版の前に出る OS の名前（`OS-L3SA` など）
_SW_NAME = re.compile(r"^\s*S/W\s*:\s*(OS-\S+)\s+Ver\.", re.IGNORECASE | re.MULTILINE)
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
#: 行の途中に出てくる口の名前。種別と番号が離れたままだと、種別だけを
#: 名前として拾ってしまう（`... interface gigabitethernet 0/1`）
_INLINE_PORT = re.compile(
    r"\b(?P<kind>gigabitethernet|tengigabitethernet|fortygigabitethernet|"
    r"hundredgigabitethernet|fastethernet|port-channel|loopback)"
    r"\s+(?P<number>\d+(?:[/.:]\d+)*)\b",
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
        rows.append(_join_ports(raw))
    return "\n".join(rows)


def _join_ports(line: str) -> str:
    def rewrite(found: re.Match[str]) -> str:
        kind = _KINDS.get(found.group("kind").lower())
        return f"{kind}{found.group('number')}" if kind else found.group(0)

    return _INLINE_PORT.sub(rewrite, line)


def parse(text: str) -> ParsedConfig:
    """IOS の読み手に任せてから、AX だけの書き方を上から直す。"""
    parsed = cisco.parse(_to_ios(text), Vendor.CISCO_IOS)
    parsed.vendor = Vendor.ALAXALA
    # OS の名前は機器の印字（`S/W: OS-L3SA Ver. 12.1`）から読む。印字が無ければ空
    parsed.os_name = None
    found = _SW_NAME.search(text)
    if found:
        parsed.os_name = found.group(1)
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
    _lldp_detail(text, parsed)
    for neighbor in parsed.neighbors:
        # 表に出てくる `0/1` を、config 側と同じ名前にそろえる。そろって
        # いないと、隣を見つけていても口に結び付かず、図に線が出ない
        if re.fullmatch(r"\d+(?:/\d+)+", neighbor.local_interface or ""):
            neighbor.local_interface = f"GigabitEthernet{neighbor.local_interface}"
    for vlan_id, name in list(parsed.vlans.items()):
        if name:
            parsed.vlans[vlan_id] = name.strip('"')
    for interface in parsed.interfaces:
        if interface.description:
            interface.description = interface.description.strip('"')
    return parsed


#: `show lldp detail` の口の行（`Port 1/1 (CH: 10) Link: Up ...`）
_LLDP_PORT = re.compile(r"^\s*Port\s+(\d+(?:/\d+)+)\b", re.IGNORECASE)
#: その口で見えている隣の始まり（Draft6.0 の相手も同じ扱い）
_LLDP_NEIGHBOR = re.compile(r"^\s*(?:Draft\s+)?Neighbor\s+\d+\b", re.IGNORECASE)
_LLDP_SYSTEM_NAME = re.compile(r"^\s*System\s+Name\s*:\s*(\S+)", re.IGNORECASE)
_LLDP_PORT_DESCR = re.compile(r"^\s*Port\s+Description\s*:\s*(.+?)\s*$", re.IGNORECASE)


def _lldp_detail(text: str, parsed: ParsedConfig) -> None:
    """`show lldp detail` から隣を読む。

    `show lldp neighbors` の簡易表は相手を MAC でしか出さないので、名前の付く
    詳細表示のほうを読む。名前（System Name）が出ていない相手は作らない。
    名乗っていない相手に MAC で名前を付けると、台帳に居ない機器が増える。

    出典: アラクサラ AX8600R 運用コマンドレファレンス Vol.2「show lldp」
    図18-2（LLDP 設定および隣接装置情報の詳細表示）。
    """
    local: str | None = None
    #: いま読んでいる隣（None なら、まだ自分の口の情報を読んでいる）
    remote_host: str | None = None
    remote_port: str | None = None
    in_neighbor = False

    def flush() -> None:
        nonlocal remote_host, remote_port, in_neighbor
        if local and remote_host:
            parsed.neighbors.append(
                ParsedNeighbor(
                    local_interface=local,
                    remote_hostname=remote_host,
                    remote_interface=remote_port,
                    protocol=NeighborProtocol.LLDP,
                )
            )
        remote_host = None
        remote_port = None
        in_neighbor = False

    for raw in text.splitlines():
        port = _LLDP_PORT.match(raw)
        if port and not _LLDP_PORT_DESCR.match(raw):
            flush()
            local = port.group(1)
            continue
        if _LLDP_NEIGHBOR.match(raw):
            flush()
            in_neighbor = True
            continue
        if not in_neighbor:
            continue
        name = _LLDP_SYSTEM_NAME.match(raw)
        if name:
            remote_host = name.group(1)
            continue
        descr = _LLDP_PORT_DESCR.match(raw)
        if descr and remote_port is None:
            remote_port = descr.group(1)
    flush()
