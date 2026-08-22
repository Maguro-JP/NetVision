"""Junos の config（set 形式 / 波括弧形式）と show 出力を読む。"""

from __future__ import annotations

import re

from app.services.configparse import routing, tunnels
from app.services.configparse.base import (
    NeighborProtocol,
    ParsedConfig,
    ParsedInterface,
    ParsedNeighbor,
    ParsedVrf,
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
    r"^set interfaces (\S+) unit \d+ family ethernet-switching vlan members\s+(.+)$", re.MULTILINE
)
_SET_PORT_MODE = re.compile(
    r"^set interfaces (\S+) unit \d+ family ethernet-switching (?:interface-mode|port-mode)\s+"
    r"(\S+)",
    re.MULTILINE,
)
_SET_NATIVE = re.compile(r"^set interfaces (\S+) native-vlan-id\s+(\d+)", re.MULTILINE)
# VLAN の定義。Junos は名前で参照するので、名前→番号の対応が要る
_SET_VLAN_ID = re.compile(r"^set vlans (\S+) vlan-id\s+(\d+)", re.MULTILINE)
_SET_VLAN_DESC = re.compile(r"^set vlans (\S+) description\s+(.+)$", re.MULTILINE)
# Junos の仮想ルーター。instance-type virtual-router / vrf のものだけを対象にする
_SET_INSTANCE_TYPE = re.compile(r"^set routing-instances (\S+) instance-type\s+(\S+)", re.MULTILINE)
_SET_INSTANCE_RD = re.compile(
    r"^set routing-instances (\S+) route-distinguisher\s+(\S+)", re.MULTILINE
)
_SET_INSTANCE_IF = re.compile(r"^set routing-instances (\S+) interface\s+(\S+)", re.MULTILINE)

_VIRTUAL_ROUTER_TYPES = {"virtual-router", "vrf", "l3vpn", "virtual-switch"}


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
    tunnels.parse_junos(text, parsed)
    routing.parse_junos(text, parsed)
    return parsed


def _parse_set_interfaces(text: str, parsed: ParsedConfig) -> None:
    for name, description in _SET_DESCRIPTION.findall(text):
        parsed.add_interface(ParsedInterface(name=name, description=description.strip().strip('"')))
    for name, address in _SET_ADDRESS.findall(text):
        parsed.add_interface(ParsedInterface(name=name, ip_address=address))
    for name, mode in _SET_PORT_MODE.findall(text):
        # Junos は trunk / access という語をそのまま使う
        parsed.add_interface(ParsedInterface(name=name)).mode = mode.strip()

    vlan_ids = _parse_vlan_definitions(text, parsed)

    for name, members in _SET_VLAN.findall(text):
        interface = parsed.add_interface(ParsedInterface(name=name))
        # `vlan members [ v10 v20 ]` のように名前で書けるので、定義から番号に直す
        values: list[int] = []
        for token in members.replace("[", " ").replace("]", " ").split():
            if token.isdigit():
                values.append(int(token))
            elif token in vlan_ids:
                values.append(vlan_ids[token])
            elif token.lower() == "all":
                interface.trunk_all = True

        if interface.mode == "trunk" or len(values) > 1:
            interface.mode = interface.mode or "trunk"
            interface.trunk_vlans = sorted(set(interface.trunk_vlans) | set(values))
        elif values:
            interface.mode = interface.mode or "access"
            interface.vlan = values[0]

    for name, native in _SET_NATIVE.findall(text):
        parsed.add_interface(ParsedInterface(name=name)).native_vlan = int(native)

    for name in _SET_DISABLE.findall(text):
        parsed.add_interface(ParsedInterface(name=name)).enabled = False

    _parse_routing_instances(text, parsed)


def _parse_vlan_definitions(text: str, parsed: ParsedConfig) -> dict[str, int]:
    """`set vlans NAME vlan-id N` を読む。名前→番号の対応表も返す。"""
    descriptions = {name: value.strip().strip('"') for name, value in _SET_VLAN_DESC.findall(text)}
    mapping: dict[str, int] = {}
    for name, vlan_id in _SET_VLAN_ID.findall(text):
        mapping[name] = int(vlan_id)
        # Junos は VLAN 名が一級の識別子なので、そのまま名前として採用する
        parsed.vlans[int(vlan_id)] = descriptions.get(name) or name
    return mapping


def _parse_routing_instances(text: str, parsed: ParsedConfig) -> None:
    """routing-instances のうち、仮想ルーターにあたるものを VRF として扱う。

    `instance-type` が forwarding などのものは経路表を分ける用途ではないので除く。
    """
    types = dict(_SET_INSTANCE_TYPE.findall(text))
    rds = dict(_SET_INSTANCE_RD.findall(text))

    for name, instance_type in types.items():
        if instance_type not in _VIRTUAL_ROUTER_TYPES:
            continue
        parsed.add_vrf(ParsedVrf(name=name, rd=rds.get(name), description=instance_type))

    for name, interface_name in _SET_INSTANCE_IF.findall(text):
        if name not in types or types[name] not in _VIRTUAL_ROUTER_TYPES:
            continue
        # `ge-0/0/1.0` のように unit 付きで書かれるので、物理側に寄せる
        parsed.add_interface(ParsedInterface(name=interface_name.split(".")[0])).vrf = name


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
