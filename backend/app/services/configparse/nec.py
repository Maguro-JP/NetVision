"""NEC の機器を読む。

NEC は系統が2つあり、書式がまったく違うので分けて扱う。

- **IX シリーズ**（IX2000 / IX3000）… 自社系のルータ。`device-name` でホスト名、
  インターフェースは `GigaEthernet0.0` のようにサブインターフェース番号まで含む
- **QX シリーズ**（QX-S）… H3C Comware ベースのスイッチ。`sysname` でホスト名、
  否定は `no` ではなく `undo`

どちらも Cisco 風に見える箇所があるため、判別は**特徴的な語**で確実に行う。
`interface` や `description` のような共通語で判定すると Cisco と混同する。
"""

from __future__ import annotations

import re

from app.services.configparse.base import (
    NeighborProtocol,
    ParsedConfig,
    ParsedInterface,
    ParsedNeighbor,
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

# --- QX（Comware）---------------------------------------------------------

_QX_MODEL = re.compile(r"\b(?:NEC\s+)?(QX-S\d+\w*)\b", re.IGNORECASE)
_QX_COMWARE_VERSION = re.compile(
    r"Comware\s+(?:Software|Platform Software).*?Version\s+([\w.]+)", re.IGNORECASE | re.DOTALL
)
_QX_SYSNAME = re.compile(r"^\s*sysname\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_QX_INTERFACE = re.compile(r"^interface\s+(\S+)", re.IGNORECASE)
_QX_IP = re.compile(
    r"^\s*ip\s+address\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)", re.IGNORECASE
)
_QX_VLAN = re.compile(r"^vlan\s+(\d+)\s*$", re.IGNORECASE)
_QX_ACCESS_VLAN = re.compile(r"^\s*port\s+access\s+vlan\s+(\d+)", re.IGNORECASE)
_QX_LINK_TYPE = re.compile(r"^\s*port\s+link-type\s+(\S+)", re.IGNORECASE)

_DESCRIPTION = re.compile(r"^\s*description\s+(.+?)\s*$", re.IGNORECASE)


def detect(text: str) -> Vendor | None:
    """NEC かどうかを見る。共通語ではなく、その機種にしか出ない語で判別する。"""
    if _IX_BANNER.search(text) or _IX_DEVICE_NAME.search(text):
        return Vendor.NEC_IX
    # QX は Comware ベースなので、型番か sysname + Comware の組み合わせで見る
    if _QX_MODEL.search(text):
        return Vendor.NEC_QX
    if _QX_SYSNAME.search(text) and re.search(r"Comware", text, re.IGNORECASE):
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

    name = _IX_DEVICE_NAME.search(text) or _IX_HOSTNAME_LINE.search(text)
    if name:
        parsed.hostname = name.group(1)

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
        if re.match(r"^\s*shutdown\s*$", raw, re.IGNORECASE):
            current.enabled = False

    _parse_ix_lldp(text, parsed)
    return parsed


# `show lldp info remote-device detail` 相当の出力から隣接を拾う
_IX_LLDP_BLOCK = re.compile(
    r"Interface\s*:\s*(\S+).*?System Name\s*:\s*(\S+).*?Port (?:ID|Description)\s*:\s*(\S+)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_ix_lldp(text: str, parsed: ParsedConfig) -> None:
    for match in _IX_LLDP_BLOCK.finditer(text):
        local, remote_host, remote_port = match.groups()
        parsed.add_neighbor(
            ParsedNeighbor(
                local_interface=local,
                remote_hostname=remote_host,
                remote_interface=remote_port,
                protocol=NeighborProtocol.LLDP,
            )
        )


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
        parsed.hostname = sysname.group(1)

    current: ParsedInterface | None = None
    current_vlan: int | None = None

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
        if re.match(r"^\s*shutdown\s*$", raw, re.IGNORECASE):
            current.enabled = False

    _parse_qx_lldp(text, parsed)
    return parsed


# Comware の `display lldp neighbor-information` は
# 「LLDP neighbor-information of port N[GigabitEthernet1/0/1]」で始まる
_QX_LLDP_PORT = re.compile(
    r"LLDP\s+neighbor-information\s+of\s+port\s+\d+\s*\[([^\]]+)\]", re.IGNORECASE
)
_QX_LLDP_FIELD = re.compile(r"^\s*(System name|Port ID)\s*:\s*(\S+)", re.IGNORECASE)


def _parse_qx_lldp(text: str, parsed: ParsedConfig) -> None:
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
        port = _QX_LLDP_PORT.search(raw)
        if port:
            flush()
            local, remote_host, remote_port = port.group(1), None, None
            continue
        field = _QX_LLDP_FIELD.match(raw)
        if field and local:
            if field.group(1).lower() == "system name":
                remote_host = field.group(2)
            else:
                remote_port = field.group(2)
    flush()
