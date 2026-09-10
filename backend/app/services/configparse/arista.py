"""Arista EOS の config を読む。

見た目は Cisco IOS に近いが、そのまま同じ扱いにすると3か所で落ちる。

- アドレスと経路の書き方が長さ付き（`10.0.10.1/24` / `ip route 0.0.0.0/0 ...`）
- 機種と版が、見出しの1行か `show version` にしか出てこない
- BGP の受ける経路の上限（`maximum-routes`）は、書かなくても既定で掛かる。
  IOS と同じ目で見ると「上限が無い」と誤って言うことになる

大半は IOS の読み手をそのまま使い、この3つだけを EOS の側で見る。
"""

from __future__ import annotations

import re

from app.services.configparse import cisco
from app.services.configparse.base import (
    NeighborProtocol,
    ParsedConfig,
    ParsedNeighbor,
    ParsedRedundancy,
    ParsedRoute,
    Vendor,
)
from app.services.configparse.ifname import expand
from app.services.configparse.routing import to_prefix

#: EOS が既定で掛けている、受ける経路の上限。書かなくてもここで止まる
DEFAULT_MAXIMUM_ROUTES = 12000

_HEADER = re.compile(r"^!\s*device:\s*(\S+)\s*\(([^,]+),\s*EOS-([^)]+)\)", re.MULTILINE)
_SHOW_MODEL = re.compile(r"^Arista\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_SHOW_VERSION = re.compile(r"^Software image version:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_SHOW_SERIAL = re.compile(r"^Serial number:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_ROUTE = re.compile(
    r"^ip route\s+(?:vrf\s+(?P<vrf>\S+)\s+)?"
    r"(?P<dest>\d+\.\d+\.\d+\.\d+/\d+)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
#: `show lldp neighbors` の見出し。Port / Neighbor Device ID / Neighbor Port ID / TTL
_LLDP_HEADER = re.compile(
    r"^\s*Port\s+Neighbor Device ID\s+Neighbor Port ID\s+TTL\s*$", re.IGNORECASE
)
_MAXIMUM_ROUTES = re.compile(r"^\s*neighbor\s+(\S+)\s+maximum-routes\s+(\d+)", re.IGNORECASE)


def detect(text: str) -> Vendor | None:
    """EOS かどうか。IOS と見分けが付く手掛かりだけで見る。"""
    if _HEADER.search(text) or re.search(r"^Arista\s+DCS", text, re.MULTILINE | re.IGNORECASE):
        return Vendor.ARISTA
    for pattern in (
        r"^\s*Software image version:\s*4\.",
        r"^management\s+(?:api|ssh|telnet|console|security)\b",
        r"^mlag configuration",
        r"^\s*neighbor\s+\S+\s+maximum-routes\s+\d+",
    ):
        if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
            return Vendor.ARISTA
    return None


def parse(text: str) -> ParsedConfig:
    """IOS の読み手に任せてから、EOS だけの書き方を上から直す。"""
    parsed = cisco.parse(text, Vendor.CISCO_IOS)
    parsed.vendor = Vendor.ARISTA
    parsed.os_name = "EOS"
    parsed.os_version = None
    parsed.model = None

    header = _HEADER.search(text)
    if header:
        parsed.hostname = parsed.hostname or header.group(1)
        parsed.model = header.group(2).strip()
        parsed.os_version = header.group(3).strip()

    found = _SHOW_MODEL.search(text)
    if found and not parsed.model:
        parsed.model = found.group(1)
    found = _SHOW_VERSION.search(text)
    if found:
        parsed.os_version = found.group(1)
    found = _SHOW_SERIAL.search(text)
    if found:
        parsed.serial_number = found.group(1)

    _parse_routes(text, parsed)
    _maximum_routes(text, parsed)
    _parse_varp(text, parsed)
    _parse_lldp_table(text, parsed)
    return parsed


#: 出典: Arista の公式ラボガイド（Layer 3 Leaf-Spine）の設定例。
#: グローバルに `ip virtual-router mac-address 00:1c:73:00:00:99` を置き、
#: SVI に `ip virtual-router address 10.3.10.1` を書く
_VARP_INTERFACE = re.compile(r"^interface\s+(\S+)\s*$", re.IGNORECASE)
_VARP_ADDRESS = re.compile(r"^ip\s+virtual-router\s+address\s+(\S+)", re.IGNORECASE)


def _parse_varp(text: str, parsed: ParsedConfig) -> None:
    """VARP（仮想ルーター）を、出口の冗長として読む。

    2台とも同じ仮想アドレスで同時に応答する仕組みで、VRRP のように
    主と待機を決めない（グループ番号も持たない）。読まないと、端末の
    出口がどこにあるのかが資料から丸ごと消える。
    """
    current: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        head = _VARP_INTERFACE.match(line.strip())
        if head and not line.startswith((" ", "\t")):
            current = expand(head.group(1))
            continue
        if not line.startswith((" ", "\t")):
            current = None
            continue
        if current is None:
            continue
        found = _VARP_ADDRESS.match(line.strip())
        if found:
            parsed.redundancy.append(
                ParsedRedundancy(
                    interface=current,
                    virtual_ip=found.group(1),
                    protocol="varp",
                )
            )


def _parse_routes(text: str, parsed: ParsedConfig) -> None:
    """`ip route 0.0.0.0/0 10.0.0.254` の形。EOS 特有の値の並びをここで補う。"""
    for raw in text.splitlines():
        found = _ROUTE.match(raw.strip())
        if not found:
            continue
        destination = to_prefix(found.group("dest"))
        if destination is None:
            continue
        route = ParsedRoute(destination=destination, vrf=found.group("vrf"))
        for token in found.group("rest").split():
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", token):
                route.next_hop = token
            elif token.isdigit():
                route.distance = int(token)
            elif re.match(r"^[A-Za-z]", token) and token.lower() not in ("name", "tag", "track"):
                route.interface = token
        if any(
            existing.destination == route.destination
            and existing.next_hop == route.next_hop
            and existing.vrf == route.vrf
            for existing in parsed.routes
        ):
            # IOS の読み手も長さ付きの書き方を拾えるようになったので、
            # ここで足すと同じ経路が2本に見えてしまう
            continue
        parsed.routes.append(route)


def _maximum_routes(text: str, parsed: ParsedConfig) -> None:
    """受ける経路の上限。EOS は書かなくても既定で掛かる。

    書いていないことを「上限が無い」と読むと、IOS の目で誤って言うことに
    なる。既定の値を入れておく。`maximum-routes 0` と書いてあるときだけは
    本当に無制限なので、そこは空のままにする（指摘が出る）。
    """
    if parsed.bgp is None:
        return
    written: dict[str, int] = {}
    for raw in text.splitlines():
        found = _MAXIMUM_ROUTES.match(raw)
        if found:
            written[found.group(1)] = int(found.group(2))

    for neighbor in parsed.bgp.neighbors:
        limit = written.get(neighbor.address)
        if limit is None:
            neighbor.maximum_prefix = DEFAULT_MAXIMUM_ROUTES
        elif limit > 0:
            neighbor.maximum_prefix = limit
        else:
            neighbor.maximum_prefix = None


def _parse_lldp_table(text: str, parsed: ParsedConfig) -> None:
    """`show lldp neighbors` の表。図の線はここから引く。

    列は Port / Neighbor Device ID / Neighbor Port ID / TTL。ポートは `Et1` の
    ように略されるので、正式名に伸ばして持つ。
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _LLDP_HEADER.match(line):
            continue
        for row in lines[index + 1 :]:
            stripped = row.strip()
            if not stripped:
                break
            if stripped.startswith("-"):
                continue
            columns = stripped.split()
            if len(columns) < 4 or not columns[-1].isdigit():
                break
            parsed.add_neighbor(
                ParsedNeighbor(
                    local_interface=expand(columns[0]),
                    remote_hostname=columns[1],
                    remote_interface=" ".join(columns[2:-1]),
                    protocol=NeighborProtocol.LLDP,
                )
            )
