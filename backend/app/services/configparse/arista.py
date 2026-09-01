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
from app.services.configparse.base import ParsedConfig, ParsedRoute, Vendor
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
_MAXIMUM_ROUTES = re.compile(r"^\s*neighbor\s+(\S+)\s+maximum-routes\s+(\d+)", re.IGNORECASE)


def detect(text: str) -> Vendor | None:
    """EOS かどうか。IOS と見分けが付く手掛かりだけで見る。"""
    if _HEADER.search(text) or re.search(r"^Arista\s+DCS", text, re.MULTILINE | re.IGNORECASE):
        return Vendor.ARISTA
    for pattern in (
        r"^\s*Software image version:\s*4\.",
        r"^management api http-commands",
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
    return parsed


def _parse_routes(text: str, parsed: ParsedConfig) -> None:
    """`ip route 0.0.0.0/0 10.0.0.254` の形。IOS の読み手は拾えない。"""
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
