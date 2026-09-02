"""show の表から、線が実際に上がっているかを読む。

Config には「止めていない」までしか書いていない。線がつながっているか、
相手が生きているかは show でしか分からない。図に線があるのに片側が
notconnect なら、それは図と実機のずれそのものなので、持ち帰って突き合わせる。

読めるのは、次の表。列の並びが決まっているものだけを見る。

- Cisco `show interfaces status`（Port / Name / Status / Vlan / Duplex / Speed / Type）
- Cisco / Arista / NEC IX `show ip interface brief`
  （Interface / IP-Address / ... / Status / Protocol）
- Junos `show interfaces terse`（Interface / Admin / Link / Proto ...）
- Aruba CX `show interface brief`（Port / ... / Enabled / Status / ...）
- PAN-OS `show interface all`（name / id / speed/duplex/state / mac address）
- FortiGate `get system interface physical`（`==[port1]` の下に `status: up`）
- Cisco / NEC IX `show interfaces`（`GigabitEthernet1/0/1 is up, line protocol is up` の段落）
- Comware（NEC QX）`display interface brief`（Interface / Link / Speed / Duplex / Type / PVID）

状態は書いてあった言葉をそのまま持つ（connected / notconnect / up / down /
disabled / err-disabled）。読めない行は黙って飛ばす。
"""

from __future__ import annotations

import re

from app.services.configparse.base import ParsedConfig, ParsedInterface
from app.services.configparse.ifname import normalize

#: Arista は末尾に Flags が付く。それ以外は同じ並び
_STATUS_HEADER = re.compile(
    r"^\s*Port\s+Name\s+Status\s+Vlan\s+Duplex\s+Speed\s+Type\b", re.IGNORECASE
)
#: Aruba CX `show interface brief`（Port / Native VLAN / Mode / Type / Enabled / Status / ...）
#: 見出しは2行に分かれる（1行目 Port Native Mode Type Enabled Status …、2行目 VLAN …）
_ARUBA_HEADER = re.compile(r"^\s*Port\s+Native\s+Mode\s+Type\s+Enabled\s+Status\b", re.IGNORECASE)
_ARUBA_ROW = re.compile(r"^\s*(\S+)\s+.*?\b(yes|no)\s+(up|down)\b", re.IGNORECASE)
#: PAN-OS `show interface all`。行の3列目が `1000/full/up` の形
_PANOS_HEADER = re.compile(r"^\s*name\s+id\s+speed/duplex/state\b", re.IGNORECASE)
_PANOS_STATE = re.compile(r"^[^/\s]+/[^/\s]+/(up|down)$", re.IGNORECASE)
#: FortiGate `get system interface physical`。`==[port1]` の見出しの下に `status: up`
_FORTI_PHYSICAL = re.compile(r"^\s*==\s*\[\s*(\S+?)\s*\]", re.IGNORECASE)
_FORTI_STATUS = re.compile(r"^\s*status:\s*(up|down)\b", re.IGNORECASE)
#: `show interfaces` の段落の1行目。`X is up, line protocol is up (connected)`
_PARAGRAPH = re.compile(
    r"^(\S+) is (up|down|administratively down),\s*line protocol is (up|down)\b", re.IGNORECASE
)
#: Comware `display interface brief`（Interface / Link / Speed / Duplex / Type / PVID ...）
_COMWARE_HEADER = re.compile(
    r"^\s*Interface\s+Link\s+Speed\s+Duplex\s+Type\s+PVID\b", re.IGNORECASE
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
        elif _ARUBA_HEADER.match(line):
            _read_aruba(lines[index + 1 :], parsed)
        elif _COMWARE_HEADER.match(line):
            _read_comware(lines[index + 1 :], parsed)
        elif _PANOS_HEADER.match(line):
            _read_panos(lines[index + 1 :], parsed)
    _read_forti_physical(lines, parsed)
    _read_paragraphs(lines, parsed)


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


def _read_aruba(rows: list[str], parsed: ParsedConfig) -> None:
    started = False
    for row in rows:
        if not row.strip():
            break
        if row.strip().startswith("-"):
            continue
        found = _ARUBA_ROW.match(row)
        if not found:
            # 見出しの2行目（VLAN / (Mb/s)）は読めない行として飛ばす。
            # 表が始まったあとに読めない行が来たら、そこで表は終わり
            if started:
                break
            continue
        started = True
        port = _port(parsed, found.group(1))
        if port is None:
            continue
        if found.group(2).lower() == "no":
            port.link_state = "disabled"
        else:
            port.link_state = found.group(3).lower()


def _read_comware(rows: list[str], parsed: ParsedConfig) -> None:
    for row in rows:
        columns = row.split()
        if len(columns) < 2:
            break
        link = columns[1].upper()
        if link not in ("UP", "DOWN", "ADM", "STBY"):
            break
        port = _port(parsed, columns[0])
        if port is None:
            continue
        port.link_state = {"UP": "up", "DOWN": "down", "ADM": "disabled", "STBY": "down"}[link]


def _read_panos(rows: list[str], parsed: ParsedConfig) -> None:
    for row in rows:
        if row.strip().startswith("-"):
            continue
        columns = row.split()
        if len(columns) < 3:
            break
        found = _PANOS_STATE.match(columns[2])
        if not found:
            break
        port = _port(parsed, columns[0])
        if port is not None:
            port.link_state = found.group(1).lower()


def _read_forti_physical(lines: list[str], parsed: ParsedConfig) -> None:
    """`==[port1]` の見出しごとに、その下の `status:` を読む。表ではなく段落の形。"""
    current: ParsedInterface | None = None
    for raw in lines:
        head = _FORTI_PHYSICAL.match(raw)
        if head:
            current = _port(parsed, head.group(1))
            continue
        if current is None:
            continue
        found = _FORTI_STATUS.match(raw)
        if found:
            current.link_state = found.group(1).lower()
            current = None


def _read_paragraphs(lines: list[str], parsed: ParsedConfig) -> None:
    """`show interfaces` の段落。1行目だけで状態が分かる。"""
    for raw in lines:
        found = _PARAGRAPH.match(raw.strip())
        if not found:
            continue
        port = _port(parsed, found.group(1))
        if port is None:
            continue
        admin, protocol = found.group(2).lower(), found.group(3).lower()
        if admin == "administratively down":
            port.link_state = "disabled"
        else:
            port.link_state = "up" if admin == "up" and protocol == "up" else "down"
