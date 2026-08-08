"""Junos の config（set 形式 / 波括弧形式）と show 出力を読む。"""

from __future__ import annotations

import re

from app.services.configparse.base import (
    NeighborProtocol,
    ParsedConfig,
    ParsedInterface,
    ParsedNeighbor,
    Vendor,
)

_SET_HOSTNAME = re.compile(r"^set system host-name\s+(\S+)", re.MULTILINE)
_BRACE_HOSTNAME = re.compile(r"^\s*host-name\s+(\S+);", re.MULTILINE)
_VERSION = re.compile(r"^(?:Junos:|JUNOS Software Release \[|version )\s*([\w.\-]+)", re.MULTILINE)
_MODEL = re.compile(r"^Model:\s*(\S+)", re.MULTILINE)
# show chassis hardware の1行目。LLDP 表の "Chassis Id" 見出しを拾わないよう、
# シリアルらしい英数字の並びだけを対象にする
_SERIAL = re.compile(r"^Chassis\s+([A-Za-z0-9]{6,})\b", re.MULTILINE)

_SET_DESCRIPTION = re.compile(r"^set interfaces (\S+) description\s+(.+)$", re.MULTILINE)
_SET_ADDRESS = re.compile(
    r"^set interfaces (\S+) unit \d+ family inet address\s+(\S+)", re.MULTILINE
)
_SET_DISABLE = re.compile(r"^set interfaces (\S+) disable\s*$", re.MULTILINE)
_SET_VLAN = re.compile(
    r"^set interfaces (\S+) unit \d+ family ethernet-switching vlan members\s+(\S+)", re.MULTILINE
)


def detect(text: str) -> Vendor | None:
    if _SET_HOSTNAME.search(text) or re.search(r"^set interfaces ", text, re.MULTILINE):
        return Vendor.JUNIPER
    if re.search(r"^system \{", text, re.MULTILINE) and _BRACE_HOSTNAME.search(text):
        return Vendor.JUNIPER
    if "JUNOS" in text or re.search(r"^Junos:", text, re.MULTILINE):
        return Vendor.JUNIPER
    return None


def parse(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.JUNIPER, os_name="Junos")

    match = _SET_HOSTNAME.search(text) or _BRACE_HOSTNAME.search(text)
    if match:
        parsed.hostname = match.group(1).strip('"')

    match = _VERSION.search(text)
    if match:
        parsed.os_version = match.group(1).rstrip("]")

    match = _MODEL.search(text)
    if match:
        parsed.model = match.group(1)

    match = _SERIAL.search(text)
    if match:
        parsed.serial_number = match.group(1)

    _parse_set_interfaces(text, parsed)
    _parse_lldp_table(text, parsed)
    return parsed


def _parse_set_interfaces(text: str, parsed: ParsedConfig) -> None:
    for name, description in _SET_DESCRIPTION.findall(text):
        parsed.add_interface(ParsedInterface(name=name, description=description.strip().strip('"')))
    for name, address in _SET_ADDRESS.findall(text):
        parsed.add_interface(ParsedInterface(name=name, ip_address=address))
    for name, vlan in _SET_VLAN.findall(text):
        interface = parsed.add_interface(ParsedInterface(name=name))
        interface.mode = interface.mode or "access"
        if vlan.isdigit():
            interface.vlan = int(vlan)
    for name in _SET_DISABLE.findall(text):
        parsed.add_interface(ParsedInterface(name=name)).enabled = False


# show lldp neighbors:
# Local Interface  Parent Interface  Chassis Id         Port info  System Name
# ge-0/0/1         -                 00:11:22:33:44:55  ge-0/0/2   sw2
_LLDP_HEADER = re.compile(
    r"^\s*Local Interface\s+Parent Interface\s+Chassis Id\s+Port info\s+System Name\s*$",
    re.IGNORECASE,
)


def _parse_lldp_table(text: str, parsed: ParsedConfig) -> None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _LLDP_HEADER.match(line):
            continue
        for row in lines[index + 1 :]:
            if not row.strip():
                break
            columns = row.split()
            if len(columns) < 5:
                continue
            parsed.add_neighbor(
                ParsedNeighbor(
                    local_interface=columns[0],
                    remote_hostname=columns[-1],
                    remote_interface=columns[-2],
                    protocol=NeighborProtocol.LLDP,
                )
            )
