"""NEC の機器を読む。

NEC は系統が2つあり、書式がまったく違うので分けて扱う。

- IX シリーズ（IX2000 / IX3000）… 自社系のルータ。`device-name` でホスト名、
  インターフェースは `GigaEthernet0.0` のようにサブインターフェース番号まで含む
- QX シリーズ（QX-S）… H3C Comware ベースのスイッチ。機器名は `sysname`
  （`hostname` ではない）。否定は `no` ではなく `undo`。running-config
  だけでも sysname で判別する

どちらも Cisco 風に見える箇所があるため、判別は特徴的な語で確実に行う。
`interface` や `description` のような共通語で判定すると Cisco と混同する。
"""

from __future__ import annotations

import re

from app.services.configparse import tunnels, vlanlist
from app.services.configparse.base import (
    NeighborProtocol,
    ParsedConfig,
    ParsedInterface,
    ParsedNeighbor,
    ParsedVrf,
    Vendor,
)

# --- IX -------------------------------------------------------------------

_IX_BANNER = re.compile(
    r"IX\s*Series\s+(IX\d+\w*)\s*\([^)]*\)\s*Software,\s*Version\s+([\w.]+)", re.IGNORECASE
)
_IX_DEVICE_NAME = re.compile(r"^\s*device-name\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_IX_HOSTNAME_LINE = re.compile(r"^\s*Hostname:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_IX_SERIAL = re.compile(
    r"^\s*(?:System\s+)?[Ss]erial\s*(?:number|No\.?)\s*[:=]?\s*(\S+)", re.MULTILINE
)
_IX_INTERFACE = re.compile(r"^interface\s+(\S+)", re.IGNORECASE)
_IX_IP = re.compile(r"^\s*ip\s+address\s+(\d+\.\d+\.\d+\.\d+(?:/\d+)?)", re.IGNORECASE)
# サブインターフェースへの VLAN 割り当て。書き方が数種あるのでまとめて見る
_IX_VLAN_TAG = re.compile(
    r"^\s*(?:encapsulation\s+dot1q|vlan-id|ip\s+dot1q\s+vlan)\s+(\d+)", re.IGNORECASE
)
_IX_VRF_DEF = re.compile(r"^(?:ip\s+)?vrf\s+(?:definition\s+)?(\S+)\s*$", re.IGNORECASE)
_IX_VRF_FORWARDING = re.compile(r"^\s*(?:ip\s+)?vrf\s+forwarding\s+(\S+)", re.IGNORECASE)

# --- QX（Comware）---------------------------------------------------------

_QX_MODEL = re.compile(r"\b(?:NEC\s+)?(QX-S\d+\w*)\b", re.IGNORECASE)
_QX_COMWARE_VERSION = re.compile(
    r"Comware\s+(?:Software|Platform Software).*?Version\s+([\w.]+)", re.IGNORECASE | re.DOTALL
)
_QX_SYSNAME = re.compile(r'^\s*sysname\s+"?([^"\n]+?)"?\s*$', re.IGNORECASE | re.MULTILINE)
_HOSTNAME_CMD = re.compile(r"^\s*hostname\s+\S+", re.IGNORECASE | re.MULTILINE)
_IX_IFACE = re.compile(r"^interface\s+GigaEthernet", re.IGNORECASE | re.MULTILINE)
_QX_HINT = re.compile(
    r"Comware|\bH3C\b|\bNEC\b|port\s+link-type|interface\s+Vlan-interface|^\s*undo\s+",
    re.IGNORECASE | re.MULTILINE,
)
_QX_INTERFACE = re.compile(r"^interface\s+(\S+)", re.IGNORECASE)
_QX_IP = re.compile(
    r"^\s*ip\s+address\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)", re.IGNORECASE
)
_QX_VLAN = re.compile(r"^vlan\s+(\d+)\s*$", re.IGNORECASE)
_QX_ACCESS_VLAN = re.compile(r"^\s*port\s+access\s+vlan\s+(\d+)", re.IGNORECASE)
_QX_LINK_TYPE = re.compile(r"^\s*port\s+link-type\s+(\S+)", re.IGNORECASE)
# `port trunk permit vlan 10 20 30 to 40` / `port trunk permit vlan all`
_QX_TRUNK_PERMIT = re.compile(r"^\s*port\s+trunk\s+permit\s+vlan\s+(.+?)\s*$", re.IGNORECASE)
# ネイティブ VLAN にあたるのが PVID
_QX_PVID = re.compile(r"^\s*port\s+(?:trunk|hybrid)\s+pvid\s+vlan\s+(\d+)", re.IGNORECASE)
# hybrid ポートは tagged / untagged を並べて書く
_QX_HYBRID_VLAN = re.compile(
    r"^\s*port\s+hybrid\s+vlan\s+(.+?)\s+(tagged|untagged)\s*$", re.IGNORECASE
)
# VRF にあたるのが VPN インスタンス
_QX_VPN_INSTANCE = re.compile(r"^ip\s+vpn-instance\s+(\S+)", re.IGNORECASE)
_QX_BIND_VPN = re.compile(
    r"^\s*(?:ip\s+binding\s+vpn-instance|vpn-instance)\s+(\S+)", re.IGNORECASE
)

_DESCRIPTION = re.compile(r"^\s*description\s+(.+?)\s*$", re.IGNORECASE)


def _unquote(value: str) -> str:
    return value.strip().strip('"')


def detect(text: str) -> Vendor | None:
    """NEC かどうかを見る。共通語ではなく、その機種にしか出ない語で判別する。"""
    if _IX_BANNER.search(text) or _IX_DEVICE_NAME.search(text) or _IX_IFACE.search(text):
        return Vendor.NEC_IX
    # QX は Comware ベース。現場では running-config だけ（sysname のみ、
    # Comware のバナーも型番も無い）が来ることが多い
    if _QX_MODEL.search(text):
        return Vendor.NEC_QX
    if _QX_SYSNAME.search(text) and (_QX_HINT.search(text) or not _HOSTNAME_CMD.search(text)):
        return Vendor.NEC_QX
    return None


def parse(text: str, vendor: Vendor) -> ParsedConfig:
    if vendor is Vendor.NEC_QX:
        return _parse_qx(text)
    return _parse_ix(text)


# ---------------------------------------------------------------------------
# IX
# ---------------------------------------------------------------------------


def _parse_ix(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.NEC_IX, os_name="IX")

    banner = _IX_BANNER.search(text)
    if banner:
        parsed.model = banner.group(1)
        parsed.os_version = banner.group(2)

    name = (
        _IX_DEVICE_NAME.search(text) or _IX_HOSTNAME_LINE.search(text) or _QX_SYSNAME.search(text)
    )
    if name:
        parsed.hostname = _unquote(name.group(1))

    serial = _IX_SERIAL.search(text)
    if serial:
        parsed.serial_number = serial.group(1)

    current: ParsedInterface | None = None
    for raw in text.splitlines():
        match = _IX_INTERFACE.match(raw)
        if match:
            current = parsed.add_interface(ParsedInterface(name=match.group(1)))
            continue
        if current is None:
            continue
        if raw and not raw[0].isspace():
            # インデントが戻ったらブロックの外
            current = None
            continue

        description = _DESCRIPTION.match(raw)
        if description and current.description is None:
            current.description = description.group(1)
            continue
        ip = _IX_IP.match(raw)
        if ip and current.ip_address is None:
            current.ip_address = ip.group(1)
            continue
        # IX はサブインターフェースにタグを載せる（GigaEthernet0.1 に VLAN 10 など）
        tag = _IX_VLAN_TAG.match(raw)
        if tag:
            current.vlan = int(tag.group(1))
            current.mode = current.mode or "access"
            continue
        vrf = _IX_VRF_FORWARDING.match(raw)
        if vrf:
            current.vrf = vrf.group(1)
            continue
        if re.match(r"^\s*shutdown\s*$", raw, re.IGNORECASE):
            current.enabled = False

    _parse_ix_vrfs(text, parsed)
    _parse_ix_lldp(text, parsed)
    tunnels.parse_block_style(text, parsed)
    return parsed


def _parse_ix_vrfs(text: str, parsed: ParsedConfig) -> None:
    """IX の VRF（VRF-lite）を読む。ルータの中に経路表を分けて持つ機能。"""
    current: ParsedVrf | None = None
    for raw in text.splitlines():
        match = _IX_VRF_DEF.match(raw)
        if match:
            current = parsed.add_vrf(ParsedVrf(name=match.group(1)))
            continue
        if current is None:
            continue
        if raw and not raw[0].isspace():
            current = None
            continue
        stripped = raw.strip()
        if stripped.startswith("rd "):
            current.rd = stripped.split(maxsplit=1)[1]
        elif stripped.startswith("description "):
            current.description = stripped.split(maxsplit=1)[1]


# `show lldp info remote-device detail` は Port ID が System Name より先に来る。
# 「Name のあとに Port」で拾うと、隣の機器のポートを掴んでしまう
_IX_LLDP_IFACE = re.compile(r"^Interface\s*:\s*(\S+)", re.IGNORECASE)
_IX_LLDP_FIELD = re.compile(
    r"^\s*(System Name|Port ID|Port Description)\s*:\s*(.+?)\s*$", re.IGNORECASE
)
_IX_LLDP_TABLE_HEADER = re.compile(
    r"^\s*Interface\s+Chassis ID\s+Port ID\s+System Name\s*$", re.IGNORECASE
)


def _parse_ix_lldp(text: str, parsed: ParsedConfig) -> None:
    _parse_ix_lldp_detail(text, parsed)
    _parse_ix_lldp_table(text, parsed)


def _parse_ix_lldp_detail(text: str, parsed: ParsedConfig) -> None:
    local: str | None = None
    remote_host: str | None = None
    remote_port: str | None = None

    def flush() -> None:
        if local and remote_host:
            parsed.add_neighbor(
                ParsedNeighbor(
                    local_interface=local,
                    remote_hostname=remote_host,
                    remote_interface=remote_port,
                    protocol=NeighborProtocol.LLDP,
                )
            )

    for raw in text.splitlines():
        iface = _IX_LLDP_IFACE.match(raw)
        if iface:
            flush()
            local, remote_host, remote_port = iface.group(1), None, None
            continue
        field = _IX_LLDP_FIELD.match(raw)
        if field and local:
            key = field.group(1).lower()
            value = _unquote(field.group(2))
            if key == "system name":
                remote_host = value
            elif key == "port id" and not _looks_like_mac(value):
                remote_port = value
            elif key == "port description" and remote_port is None:
                remote_port = value
    flush()


def _parse_ix_lldp_table(text: str, parsed: ParsedConfig) -> None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _IX_LLDP_TABLE_HEADER.match(line):
            continue
        for row in lines[index + 1 :]:
            if not row.strip() or row.strip().startswith(("#", "!", "Total")):
                break
            columns = row.split()
            if len(columns) < 4:
                continue
            parsed.add_neighbor(
                ParsedNeighbor(
                    local_interface=columns[0],
                    remote_hostname=columns[-1],
                    remote_interface=columns[-2],
                    protocol=NeighborProtocol.LLDP,
                )
            )


def _looks_like_mac(value: str) -> bool:
    compact = re.sub(r"[:.\-]", "", value)
    return len(compact) >= 12 and all(c in "0123456789abcdefABCDEF" for c in compact)


# ---------------------------------------------------------------------------
# QX（Comware）
# ---------------------------------------------------------------------------


def _parse_qx(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.NEC_QX, os_name="Comware")

    model = _QX_MODEL.search(text)
    if model:
        parsed.model = model.group(1)

    version = _QX_COMWARE_VERSION.search(text)
    if version:
        parsed.os_version = version.group(1)

    sysname = _QX_SYSNAME.search(text)
    if sysname:
        parsed.hostname = _unquote(sysname.group(1))

    current: ParsedInterface | None = None
    current_vlan: int | None = None
    permitted: set[str] = set()

    for raw in text.splitlines():
        match = _QX_INTERFACE.match(raw)
        if match:
            current = parsed.add_interface(ParsedInterface(name=match.group(1)))
            current_vlan = None
            continue

        vlan = _QX_VLAN.match(raw)
        if vlan:
            current = None
            current_vlan = int(vlan.group(1))
            parsed.vlans.setdefault(current_vlan, "")
            continue

        if raw and not raw[0].isspace():
            current = None
            current_vlan = None
            continue

        if current_vlan is not None:
            name = re.match(r"^\s*name\s+(.+?)\s*$", raw, re.IGNORECASE)
            if name:
                parsed.vlans[current_vlan] = name.group(1)
            continue

        if current is None:
            continue

        description = _DESCRIPTION.match(raw)
        if description and current.description is None:
            current.description = description.group(1)
            continue
        ip = _QX_IP.match(raw)
        if ip and current.ip_address is None:
            current.ip_address = ip.group(1)
            continue
        link_type = _QX_LINK_TYPE.match(raw)
        if link_type:
            # Comware の access / trunk / hybrid を、他ベンダーと同じ語に寄せる
            current.mode = link_type.group(1).lower()
            continue
        access = _QX_ACCESS_VLAN.match(raw)
        if access:
            current.vlan = int(access.group(1))
            continue
        permit = _QX_TRUNK_PERMIT.match(raw)
        if permit:
            permitted.add(current.key)
            if vlanlist.is_all(permit.group(1)):
                current.trunk_all = True
            else:
                current.trunk_vlans = sorted(
                    set(current.trunk_vlans) | set(vlanlist.parse_ranges(permit.group(1)))
                )
            continue
        hybrid = _QX_HYBRID_VLAN.match(raw)
        if hybrid:
            permitted.add(current.key)
            values = vlanlist.parse_ranges(hybrid.group(1))
            current.trunk_vlans = sorted(set(current.trunk_vlans) | set(values))
            continue
        pvid = _QX_PVID.match(raw)
        if pvid:
            current.native_vlan = int(pvid.group(1))
            continue
        binding = _QX_BIND_VPN.match(raw)
        if binding:
            current.vrf = binding.group(1)
            continue
        if re.match(r"^\s*shutdown\s*$", raw, re.IGNORECASE):
            current.enabled = False

    # Comware も、許可 VLAN を書いていないトランクは VLAN 1 だけが通る既定。
    # ただし「書いていない」と読み落としの区別がつかないので、全許可とはみなさない
    for interface in parsed.interfaces:
        if interface.mode == "trunk" and interface.key not in permitted:
            interface.native_vlan = interface.native_vlan or 1

    _parse_qx_vpn_instances(text, parsed)

    _parse_qx_lldp(text, parsed)
    tunnels.parse_block_style(text, parsed)
    return parsed


def _parse_qx_vpn_instances(text: str, parsed: ParsedConfig) -> None:
    """Comware の VPN インスタンス（他ベンダーで言う VRF）を読む。"""
    current: ParsedVrf | None = None
    for raw in text.splitlines():
        match = _QX_VPN_INSTANCE.match(raw)
        if match:
            current = parsed.add_vrf(ParsedVrf(name=match.group(1)))
            continue
        if current is None:
            continue
        if raw and not raw[0].isspace():
            current = None
            continue
        stripped = raw.strip()
        if stripped.startswith("route-distinguisher "):
            current.rd = stripped.split(maxsplit=1)[1]
        elif stripped.startswith("description "):
            current.description = stripped.split(maxsplit=1)[1]


# Comware の `display lldp neighbor-information` は
# 「LLDP neighbor-information of port N[GigabitEthernet1/0/1]」で始まる
_QX_LLDP_PORT = re.compile(
    r"LLDP\s+neighbor-information\s+of\s+port\s+\d+\s*\[([^\]]+)\]", re.IGNORECASE
)
_QX_LLDP_FIELD = re.compile(
    r"^\s*(System name|Port ID|Port description)\s*:\s*(.+?)\s*$", re.IGNORECASE
)
_QX_LLDP_LIST_HEADER = re.compile(
    r"^\s*System Name\s+Local Interface\s+.*Port ID\s*$", re.IGNORECASE
)


def _parse_qx_lldp(text: str, parsed: ParsedConfig) -> None:
    local: str | None = None
    remote_host: str | None = None
    remote_port: str | None = None
    port_descr: str | None = None

    def flush() -> None:
        port = remote_port
        if port is None or _looks_like_mac(port):
            port = port_descr or port
        if local and remote_host:
            parsed.add_neighbor(
                ParsedNeighbor(
                    local_interface=local,
                    remote_hostname=remote_host,
                    remote_interface=port,
                    protocol=NeighborProtocol.LLDP,
                )
            )

    for raw in text.splitlines():
        port = _QX_LLDP_PORT.search(raw)
        if port:
            flush()
            local, remote_host, remote_port, port_descr = port.group(1), None, None, None
            continue
        field = _QX_LLDP_FIELD.match(raw)
        if field and local:
            key = field.group(1).lower()
            value = _unquote(field.group(2))
            if key == "system name":
                remote_host = value
            elif key == "port id":
                remote_port = value
            else:
                port_descr = value
    flush()
    _parse_qx_lldp_list(text, parsed)


def _parse_qx_lldp_list(text: str, parsed: ParsedConfig) -> None:
    """`display lldp neighbor-information list` の表。詳細が無いときに使う。"""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _QX_LLDP_LIST_HEADER.match(line):
            continue
        for row in lines[index + 1 :]:
            if not row.strip() or row.strip().startswith(("#", "Total", "Displaying")):
                break
            columns = row.split()
            if len(columns) < 3:
                continue
            parsed.add_neighbor(
                ParsedNeighbor(
                    local_interface=columns[1],
                    remote_hostname=columns[0],
                    remote_interface=columns[-1],
                    protocol=NeighborProtocol.LLDP,
                )
            )
