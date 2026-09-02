"""show の表から、線が実際に上がっているかを読む。

Config には「止めていない」までしか書いていない。線がつながっているか、
相手が生きているかは show でしか分からない。図に線があるのに片側が
notconnect なら、それは図と実機のずれそのものなので、持ち帰って突き合わせる。

読めるのは、次の表。列の並びが決まっているものだけを見る。

- Cisco `show interfaces status`（Port / Name / Status / Vlan / Duplex / Speed / Type）
- Cisco / Arista / NEC IX `show ip interface brief`
  （Interface / IP-Address / ... / Status / Protocol）
- Junos `show interfaces terse`（Interface / Admin / Link / Proto ...）

状態は書いてあった言葉をそのまま持つ（connected / notconnect / up / down /
disabled / err-disabled）。読めない行は黙って飛ばす。
"""

from __future__ import annotations

import re

from app.services.configparse.base import ParsedConfig, ParsedInterface
from app.services.configparse.ifname import normalize

_STATUS_HEADER = re.compile(
    r"^\s*Port\s+Name\s+Status\s+Vlan\s+Duplex\s+Speed\s+Type\s*$", re.IGNORECASE
)
_BRIEF_HEADER = re.compile(r"^\s*Interface\s+IP-Address\s+.*\bStatus\s+Protocol\s*$", re.I)
_TERSE_HEADER = re.compile(r"^\s*Interface\s+Admin\s+Link\s+Proto\b", re.I)

#: `show interfaces status` の Status 列に出る言葉
_STATUS_WORDS = (
    "connected",
    "notconnect",
    "notconnec",
    "disabled",
    "err-disabled",
    "inactive",
    "monitoring",
)


def parse(text: str, parsed: ParsedConfig) -> None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _STATUS_HEADER.match(line):
            _read_status(lines[index + 1 :], parsed)
        elif _BRIEF_HEADER.match(line):
            _read_brief(lines[index + 1 :], parsed)
        elif _TERSE_HEADER.match(line):
            _read_terse(lines[index + 1 :], parsed)


def _port(parsed: ParsedConfig, name: str) -> ParsedInterface | None:
    """Config 側に無いポートは作らない。show にしか出ないポートは線の材料にならない。"""
    key = normalize(name)
    for interface in parsed.interfaces:
        if interface.key == key:
            return interface
    return None


def _read_status(rows: list[str], parsed: ParsedConfig) -> None:
    for row in rows:
        columns = row.split()
        if len(columns) < 2:
            break
        status = next((c.lower() for c in columns[1:] if c.lower() in _STATUS_WORDS), None)
        if status is None:
            break
        port = _port(parsed, columns[0])
        if port is None:
            continue
        # 言葉は機種ごとに違うので、up / notconnect / disabled / err-disabled にそろえる
        if status == "connected":
            port.link_state = "up"
        elif status.startswith("notconnec"):
            port.link_state = "notconnect"
        else:
            port.link_state = status


def _read_brief(rows: list[str], parsed: ParsedConfig) -> None:
    for row in rows:
        columns = row.split()
        if len(columns) < 4:
            break
        # 末尾2つが Status と Protocol。`administratively down` は2語になる
        protocol = columns[-1].lower()
        status = (
            " ".join(columns[-3:-1]).lower()
            if columns[-2].lower() == "down" and columns[-3].lower() == "administratively"
            else columns[-2].lower()
        )
        if protocol not in ("up", "down") or status not in ("up", "down", "administratively down"):
            break
        port = _port(parsed, columns[0])
        if port is None:
            continue
        if status == "administratively down":
            port.link_state = "disabled"
        else:
            port.link_state = "up" if status == "up" and protocol == "up" else "down"


def _read_terse(rows: list[str], parsed: ParsedConfig) -> None:
    for row in rows:
        columns = row.split()
        if len(columns) < 3:
            break
        admin, link = columns[1].lower(), columns[2].lower()
        if admin not in ("up", "down") or link not in ("up", "down"):
            break
        port = _port(parsed, columns[0].split(".")[0]) or _port(parsed, columns[0])
        if port is None:
            continue
        if admin == "down":
            port.link_state = "disabled"
        elif port.link_state is None or link == "up":
            port.link_state = "up" if link == "up" else "down"
