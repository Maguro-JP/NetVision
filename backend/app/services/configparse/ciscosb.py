"""Cisco Business / Small Business（SG・SF・CBS・SX）の config を読む。

Catalyst と同じ Cisco だが、中身は別の系統で、書き方も出力も違う。
IOS の読み手に任せると、`vlan database` の行でアライドテレシスと
取り違える（実際に起きていた）。

機器が印字するものから読む。

- config の頭は `config-file-header`、次の行がホスト名、その次が
  `v2.5.8.15 / RCBS3.1_930_412_016` の形の版数。`CLI v1.0` が続く
- スタックの型は `unit-type unit 1 network gi uplink te`
- `show system` は `System Description: SG350-28P` と、Unit Type の表
- `show version` は `Active-image: … Version: 2.2.5.68`
- VLAN は `vlan database` の中の `vlan 10,20`、名前は `interface vlan 10` の `name`
- 口は `interface GigabitEthernet1/0/1`、または `gi1` / `te1/0/1` の略記
- 許可 VLAN は `switchport trunk allowed vlan add 10,20`（IOS と同じ読み手で読める）
- 出口は `ip default-gateway`

OS の名前は機器が印字しないので入れない。

出典（公式）:
- `unit-type unit X network … uplink …` が config の頭に保存される
  … Cisco Business 350 Series CLI Guide「Stack Commands」set stack unit-type
- 口の略記（GigabitEthernet / gi / GE、TwoPointFiveGigabitEthernet / tw、
  FiveGigabitEthernet / fi、Port-Channel / po、tunnel / tu、スタックの `GigabitEthernetX/0/Z`）
  … 同ガイド「Interface Naming Conventions」
- `config-file-header` → 機器名 → `v2.4.5.71 / RTESLA…` → `CLI v1.0`、`vlan database` の
  `vlan 10,60-70`、`interface vlan 10` の `name "…"` … 実機の running-config（SG350 / CBS350）
"""

from __future__ import annotations

import re

from app.services.configparse import cisco, ciscomodel
from app.services.configparse.base import ParsedConfig, ParsedInterface, ParsedRoute, Vendor

#: この系統にしか出てこない書き方
_STRONG = (
    re.compile(r"^config-file-header\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^unit-type\s+unit\s+\d+\s+network\s", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^unit-type-control-start\s*$", re.IGNORECASE | re.MULTILINE),
    # SNMP の OID は 1.3.6.1.4.1.9.6.1 で始まる（Catalyst は 1.3.6.1.4.1.9.1）
    re.compile(r"System Object ID:\s*1\.3\.6\.1\.4\.1\.9\.6\.", re.IGNORECASE),
    re.compile(r"^v\d+\.\d+\.\d+\.\d+\s*/\s*R", re.MULTILINE),
)
_HEADER_VERSION = re.compile(r"^v(\d+(?:\.\d+){2,3})\s*/\s*R", re.MULTILINE)
_IMAGE_VERSION = re.compile(r"^\s*Active-image:.*?^\s*Version:\s*(\S+)", re.MULTILINE | re.DOTALL)
_SYSTEM_DESCRIPTION = re.compile(r"^System Description:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_UNIT_TYPE_ROW = re.compile(r"^\s*\d+\s+((?:SG|SF|SX|CBS|SLM|SRW)\d{3}\S*)\s*$", re.MULTILINE)
_SERIAL = re.compile(r"^\s*Serial [Nn]umber:\s*(\S+)", re.MULTILINE)
_VLAN_LIST = re.compile(r"^vlan\s+([\d,\-]+)\s*$", re.IGNORECASE)
_INTERFACE_VLAN = re.compile(r"^interface\s+vlan\s+(\d+)\s*$", re.IGNORECASE)
_VLAN_NAME = re.compile(r"^\s+name\s+(.+?)\s*$", re.IGNORECASE)
_DEFAULT_GATEWAY = re.compile(
    r"^ip default-gateway\s+(\d+\.\d+\.\d+\.\d+)", re.IGNORECASE | re.MULTILINE
)
#: 略記の口名。`gi1` `te1/0/1` `po1`
_SHORT_PORT = re.compile(r"^(gi|ge|te|fa|xg|tw|fi|po|tu)(\d+(?:/\d+)*)$", re.IGNORECASE)
_SHORT_FULL = {
    "gi": "GigabitEthernet",
    "ge": "GigabitEthernet",
    "te": "TenGigabitEthernet",
    "fa": "FastEthernet",
    "xg": "TenGigabitEthernet",
    "tw": "TwoPointFiveGigabitEthernet",
    "fi": "FiveGigabitEthernet",
    "po": "Port-channel",
    "tu": "Tunnel",
}


def detect(text: str) -> Vendor | None:
    for pattern in _STRONG:
        if pattern.search(text):
            return Vendor.CISCO_SB
    return None


def parse(text: str) -> ParsedConfig:
    """IOS 風の口の書き方は IOS の読み手に任せ、この系統の書き方で上から直す。"""
    parsed = cisco.parse(_expand_short_ports(text), Vendor.CISCO_IOS)
    parsed.vendor = Vendor.CISCO_SB
    parsed.os_name = None
    parsed.os_version = None
    parsed.model = None

    found = _HEADER_VERSION.search(text) or _IMAGE_VERSION.search(text)
    if found:
        parsed.os_version = found.group(1)
    if parsed.hostname is None:
        parsed.hostname = _header_hostname(text)

    model = _SYSTEM_DESCRIPTION.search(text) or _UNIT_TYPE_ROW.search(text)
    if model and ciscomodel.looks_like_pid(model.group(1)):
        parsed.model = model.group(1)
    serial = _SERIAL.search(text)
    if serial and parsed.serial_number is None:
        parsed.serial_number = serial.group(1)

    _read_vlans(text, parsed)
    _read_default_gateway(text, parsed)
    return parsed


def _header_hostname(text: str) -> str | None:
    """`config-file-header` の次の行がホスト名。`hostname` 行が無い config でも読める。"""
    lines = text.splitlines()
    for index, line in enumerate(lines[:-1]):
        if line.strip().lower() == "config-file-header":
            candidate = lines[index + 1].strip()
            return candidate or None
    return None


def _expand_short_ports(text: str) -> str:
    """`interface gi1` を `interface GigabitEthernet1` にそろえる。"""
    out = []
    for line in text.splitlines():
        head = re.match(r"^(interface\s+)(\S+)\s*$", line, re.IGNORECASE)
        if head:
            short = _SHORT_PORT.match(head.group(2))
            if short:
                line = f"{head.group(1)}{_SHORT_FULL[short.group(1).lower()]}{short.group(2)}"
        out.append(line)
    return "\n".join(out)


def _read_vlans(text: str, parsed: ParsedConfig) -> None:
    """`vlan database` の `vlan 10,20` と、`interface vlan 10` の `name`。"""
    in_database = False
    current_vlan: int | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.lower() == "vlan database":
            in_database = True
            continue
        if in_database:
            if (
                line.lower() == "exit"
                or not raw.startswith((" ", "\t"))
                and not line.lower().startswith("vlan ")
            ):
                in_database = False
            else:
                found = _VLAN_LIST.match(line)
                if found:
                    for vlan_id in cisco._vlan_numbers(found.group(1)):
                        parsed.vlans.setdefault(vlan_id, "")
                continue
        head = _INTERFACE_VLAN.match(line)
        if head and not raw.startswith((" ", "\t")):
            current_vlan = int(head.group(1))
            parsed.vlans.setdefault(current_vlan, "")
            parsed.add_interface(ParsedInterface(name=f"Vlan{current_vlan}"))
            continue
        if raw.strip() and not raw.startswith((" ", "\t")):
            current_vlan = None
            continue
        if current_vlan is None:
            continue
        name = _VLAN_NAME.match(raw)
        if name:
            parsed.vlans[current_vlan] = name.group(1).strip('"')
            continue
        address = re.match(
            r"^\s+ip address\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)", raw, re.IGNORECASE
        )
        if address:
            port = parsed.add_interface(ParsedInterface(name=f"Vlan{current_vlan}"))
            if port.ip_address is None:
                port.ip_address = f"{address.group(1)}/{cisco._prefix_length(address.group(2))}"


def _read_default_gateway(text: str, parsed: ParsedConfig) -> None:
    found = _DEFAULT_GATEWAY.search(text)
    if found and not any(r.destination == "0.0.0.0/0" for r in parsed.routes):
        parsed.routes.append(ParsedRoute(destination="0.0.0.0/0", next_hop=found.group(1)))
