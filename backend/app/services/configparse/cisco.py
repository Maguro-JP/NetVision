"""Cisco IOS / IOS-XE / NX-OS の config と show 出力を読む。

現場でアップロードされるのは running-config だけとは限らず、
`show version` `show cdp neighbors detail` などを1ファイルにまとめたものも多い。
どちらが来ても読めるよう、行を走査しながら見つかったものを拾っていく。
"""

from __future__ import annotations

import re

from app.services.configparse import vlanlist
from app.services.configparse.base import (
    NeighborProtocol,
    ParsedConfig,
    ParsedInterface,
    ParsedNeighbor,
    ParsedVrf,
    Vendor,
)

_HOSTNAME = re.compile(r"^hostname\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_VERSION_LINE = re.compile(r"^version\s+(\S+)", re.IGNORECASE)
_SHOW_VERSION = re.compile(
    r"(?:IOS[- ]XE Software.*?|IOS Software.*?),?\s*Version\s+([\w.()]+)", re.I
)
_NXOS_VERSION = re.compile(r"^\s*(?:NXOS|system):?\s*version\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_MODEL_NUMBER = re.compile(r"^\s*Model [Nn]umber\s*:\s*(\S+)", re.MULTILINE)
_CHASSIS = re.compile(r"^\s*cisco\s+(\S+)\s*\(", re.MULTILINE)
_SERIAL = re.compile(r"^\s*System [Ss]erial [Nn]umber\s*:\s*(\S+)", re.MULTILINE)
_PID_SN = re.compile(r"PID:\s*(\S+)\s*,.*?SN:\s*(\S+)")

_INTERFACE = re.compile(r"^interface\s+(\S+)", re.IGNORECASE)
_VLAN_DEF = re.compile(r"^vlan\s+(\d+)\s*$", re.IGNORECASE)


def detect(text: str) -> Vendor | None:
    lowered = text.lower()
    if "nxos" in lowered or re.search(r"^feature\s", text, re.MULTILINE | re.IGNORECASE):
        return Vendor.CISCO_NXOS
    if _HOSTNAME.search(text) or "ios software" in lowered or "ios-xe" in lowered:
        return Vendor.CISCO_IOS
    if re.search(r"^\s*show cdp neighbors", text, re.MULTILINE | re.IGNORECASE):
        return Vendor.CISCO_IOS
    return None


def parse(text: str, vendor: Vendor = Vendor.CISCO_IOS) -> ParsedConfig:
    parsed = ParsedConfig(vendor=vendor, os_name="NX-OS" if vendor is Vendor.CISCO_NXOS else "IOS")

    match = _HOSTNAME.search(text)
    if match:
        parsed.hostname = match.group(1).strip('"')

    _parse_version(text, parsed)
    _parse_inventory(text, parsed)
    _parse_vrfs(text, parsed)
    _parse_interfaces(text, parsed)
    _parse_vlans(text, parsed)
    _parse_cdp_detail(text, parsed)
    _parse_lldp_detail(text, parsed)
    _parse_neighbor_table(text, parsed)

    return parsed


def _parse_version(text: str, parsed: ParsedConfig) -> None:
    match = _SHOW_VERSION.search(text)
    if match:
        parsed.os_version = match.group(1)
        if "ios-xe" in text.lower() or "ios xe" in text.lower():
            parsed.os_name = "IOS-XE"
        return

    match = _NXOS_VERSION.search(text)
    if match:
        parsed.os_version = match.group(1)
        parsed.os_name = "NX-OS"
        return

    # running-config 冒頭の `version 17.9`（メジャーバージョンのみ）
    for line in text.splitlines():
        match = _VERSION_LINE.match(line.strip())
        if match:
            parsed.os_version = match.group(1)
            return


def _parse_inventory(text: str, parsed: ParsedConfig) -> None:
    match = _MODEL_NUMBER.search(text) or _CHASSIS.search(text)
    if match:
        parsed.model = match.group(1)

    match = _SERIAL.search(text)
    if match:
        parsed.serial_number = match.group(1)
        return

    # show inventory の最初のエントリ（シャーシ）を採用する
    match = _PID_SN.search(text)
    if match:
        if not parsed.model:
            parsed.model = match.group(1)
        parsed.serial_number = match.group(2)


def _parse_interfaces(text: str, parsed: ParsedConfig) -> None:
    current: ParsedInterface | None = None
    # 許可 VLAN を明示したポート。書いていないトランクは「全部通す」が既定なので、
    # 「書いていない」と「none と書いた」を取り違えないよう覚えておく
    allowed_written: set[str] = set()

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue

        match = _INTERFACE.match(line.strip())
        if match and not line.startswith(" "):
            # 既に同じポートが出ていればマージ結果を受け取る（以降の行はそちらに書く）
            current = parsed.add_interface(ParsedInterface(name=match.group(1)))
            continue

        if current is None:
            continue
        if not line.startswith(" "):
            current = None  # インデントが戻ったらブロックの外
            continue

        body = line.strip()
        if body.startswith("description "):
            current.description = body[len("description ") :].strip()
        elif body.startswith("ip address "):
            parts = body.split()
            if len(parts) >= 4 and parts[2] != "dhcp":
                current.ip_address = f"{parts[2]}/{_prefix_length(parts[3])}"
            elif len(parts) >= 3:
                current.ip_address = parts[2]
        elif body.startswith("switchport access vlan "):
            current.vlan = int(body.rsplit(maxsplit=1)[-1])
            current.mode = current.mode or "access"
        elif body.startswith("switchport trunk native vlan "):
            current.native_vlan = int(body.rsplit(maxsplit=1)[-1])
        elif body.startswith("switchport trunk allowed vlan"):
            allowed_written.add(current.key)
            _apply_allowed_vlan(current, body)
        elif body.startswith("switchport mode "):
            current.mode = body.rsplit(maxsplit=1)[-1]
        elif body.startswith(("vrf forwarding ", "ip vrf forwarding ")):
            current.vrf = body.rsplit(maxsplit=1)[-1]
        elif body == "shutdown":
            current.enabled = False
        elif body == "no switchport":
            current.mode = "routed"

    for interface in parsed.interfaces:
        if interface.mode == "trunk" and interface.key not in allowed_written:
            interface.trunk_all = True


def _apply_allowed_vlan(interface: ParsedInterface, body: str) -> None:
    """`switchport trunk allowed vlan [add|remove] ...` を反映する。

    `add` は複数行に分かれて出てくるので、足し込まないと途中までしか読めない。
    `remove` / `except` も、書いてある通りに引かないと実機と食い違う。
    """
    rest = body[len("switchport trunk allowed vlan") :].strip()
    if not rest:
        return

    verb = ""
    for candidate in ("add", "remove", "except", "none"):
        if rest == candidate or rest.startswith(candidate + " "):
            verb = candidate
            rest = rest[len(candidate) :].strip()
            break

    if verb == "none":
        interface.trunk_vlans = []
        interface.trunk_all = False
        return
    if vlanlist.is_all(rest):
        interface.trunk_all = True
        interface.trunk_vlans = []
        return

    values = vlanlist.parse_ranges(rest)
    if not values:
        return
    if verb == "add":
        interface.trunk_vlans = sorted(set(interface.trunk_vlans) | set(values))
    elif verb == "remove":
        interface.trunk_vlans = [v for v in interface.trunk_vlans if v not in set(values)]
    elif verb == "except":
        # 「これ以外全部」。全部側は機器の定義 VLAN で解決するので、除外だけ覚えておく
        interface.trunk_all = True
        interface.trunk_excluded = set(values)
    else:
        interface.trunk_vlans = values
        interface.trunk_all = False


def _prefix_length(netmask: str) -> int:
    try:
        return sum(bin(int(octet)).count("1") for octet in netmask.split("."))
    except ValueError:
        return 32


def _parse_vlans(text: str, parsed: ParsedConfig) -> None:
    current: int | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        match = _VLAN_DEF.match(stripped)
        if match and not raw.startswith(" "):
            current = int(match.group(1))
            parsed.vlans.setdefault(current, "")
            continue
        if current is not None and raw.startswith(" ") and stripped.startswith("name "):
            parsed.vlans[current] = stripped[len("name ") :].strip()
            current = None
        elif not raw.startswith(" "):
            current = None


_VRF_DEF = re.compile(r"^(?:vrf definition|ip vrf)\s+(\S+)\s*$", re.IGNORECASE)


def _parse_vrfs(text: str, parsed: ParsedConfig) -> None:
    """`vrf definition NAME`（新形式）と `ip vrf NAME`（旧形式）の両方を読む。

    IOS-XE は新形式だが、現場には旧形式のまま動いている機器も残っている。
    """
    current: ParsedVrf | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        match = _VRF_DEF.match(stripped)
        if match and not raw.startswith(" "):
            current = parsed.add_vrf(ParsedVrf(name=match.group(1)))
            continue
        if current is None:
            continue
        if not raw.startswith(" "):
            current = None
            continue
        if stripped.startswith("rd "):
            current.rd = stripped[len("rd ") :].strip()
        elif stripped.startswith("description "):
            current.description = stripped[len("description ") :].strip()

    # インターフェース側にしか出てこない VRF もある（定義が別ファイルの場合）ので、
    # `vrf forwarding` の参照は _parse_interfaces のあとに拾い直す


def _parse_cdp_detail(text: str, parsed: ParsedConfig) -> None:
    """`show cdp neighbors detail` の各ブロックを読む。"""
    blocks = re.split(r"^-{5,}\s*$", text, flags=re.MULTILINE)
    for block in blocks:
        device = re.search(r"^Device ID:\s*(\S+)", block, re.MULTILINE)
        interfaces = re.search(
            r"^Interface:\s*(\S+?),?\s*Port ID \(outgoing port\):\s*(.+)$", block, re.MULTILINE
        )
        if not device or not interfaces:
            continue

        platform = re.search(r"^Platform:\s*(.+?),", block, re.MULTILINE)
        parsed.add_neighbor(
            ParsedNeighbor(
                local_interface=interfaces.group(1).rstrip(","),
                remote_hostname=device.group(1),
                remote_interface=interfaces.group(2).strip(),
                remote_platform=platform.group(1).strip() if platform else None,
                protocol=NeighborProtocol.CDP,
            )
        )


def _parse_lldp_detail(text: str, parsed: ParsedConfig) -> None:
    """`show lldp neighbors detail` の各ブロックを読む。"""
    for block in re.split(r"^-{5,}\s*$", text, flags=re.MULTILINE):
        local = re.search(r"^Local Intf:\s*(\S+)", block, re.MULTILINE)
        system = re.search(r"^System Name:\s*(\S+)", block, re.MULTILINE)
        if not local or not system:
            continue

        port = re.search(r"^Port id:\s*(\S+)", block, re.MULTILINE)
        descr = re.search(r"^System Description:\s*\n?(.+)$", block, re.MULTILINE)
        parsed.add_neighbor(
            ParsedNeighbor(
                local_interface=local.group(1),
                remote_hostname=system.group(1),
                remote_interface=port.group(1) if port else None,
                remote_platform=descr.group(1).strip() if descr else None,
                protocol=NeighborProtocol.LLDP,
            )
        )


# `show cdp neighbors` / `show lldp neighbors` の表形式
_TABLE_HEADER = re.compile(
    r"^\s*Device[- ]ID\s+Local\s*Intr?f?c?e?\s+Holdtme.*Port ID\s*$", re.IGNORECASE
)
_LLDP_TABLE_HEADER = re.compile(
    r"^\s*Device ID\s+Local Intf\s+Hold-time\s+Capability\s+Port ID\s*$", re.IGNORECASE
)


def _parse_neighbor_table(text: str, parsed: ParsedConfig) -> None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        is_cdp = bool(_TABLE_HEADER.match(line))
        if not is_cdp and not _LLDP_TABLE_HEADER.match(line):
            continue

        for row in lines[index + 1 :]:
            if not row.strip() or row.strip().startswith(("Total", "#", "!")):
                break
            # 機器名が長いと折り返されるが、その場合も残りの列は次行に揃う
            columns = row.split()
            if len(columns) < 4:
                continue

            hostname = columns[0]
            # 末尾2つは Port ID、その手前が Platform（CDP）。Local Intf は先頭寄り。
            local = _join_interface(columns, 1)
            remote = " ".join(columns[-2:]) if _looks_like_interface(columns[-2]) else columns[-1]
            parsed.add_neighbor(
                ParsedNeighbor(
                    local_interface=local,
                    remote_hostname=hostname,
                    remote_interface=remote,
                    protocol=NeighborProtocol.CDP if is_cdp else NeighborProtocol.LLDP,
                )
            )


def _looks_like_interface(token: str) -> bool:
    return bool(re.match(r"^[A-Za-z]{2,}$", token))


def _join_interface(columns: list[str], start: int) -> str:
    """`Gig 0/1` のように空白で分かれた表記を1つに戻す。"""
    if start + 1 < len(columns) and _looks_like_interface(columns[start]):
        return f"{columns[start]}{columns[start + 1]}"
    return columns[start]
