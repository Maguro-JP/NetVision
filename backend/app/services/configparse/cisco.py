"""Cisco IOS / IOS-XE / NX-OS の config と show 出力を読む。

現場でアップロードされるのは running-config だけとは限らず、
`show version` `show cdp neighbors detail` などを1ファイルにまとめたものも多い。
どちらが来ても読めるよう、行を走査しながら見つかったものを拾っていく。
"""

from __future__ import annotations

import re

from app.services.configparse import policy, routing, tunnels, vlanlist
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

#: 末尾に何も続かない形だけ。show の見出し（Interface  IP-Address …）を取り違えない
_INTERFACE = re.compile(r"^interface\s+(\S+)\s*$", re.IGNORECASE)
#: `interface range Gi1/0/1 - 24` や `interface range Gi1/0/1 - 3, Gi1/0/8`
_INTERFACE_RANGE = re.compile(r"^interface\s+range\s+(.+)$", re.IGNORECASE)
_RANGE_PART = re.compile(
    r"^([A-Za-z][A-Za-z\-]*)\s*([\d/]*?)(\d+)(?:\s*-\s*(\d+))?$", re.IGNORECASE
)
#: `vlan 10` に加えて `vlan 10,20,30` `vlan 100-110` も定義になる。
#: 並べ書きを読み落とすと、その VLAN が「台帳にしか無い」ことになってしまう
_VLAN_DEF = re.compile(r"^vlan\s+([\d,\s-]+)$", re.IGNORECASE)


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
    else:
        # NEC QX / Comware は hostname ではなく sysname。判別できなかった
        # 抜粋でも、機器名だけは台帳と突き合わせられるように拾う
        sysname = re.search(r'^\s*sysname\s+"?([^"\n]+?)"?\s*$', text, re.IGNORECASE | re.MULTILINE)
        if sysname:
            parsed.hostname = sysname.group(1).strip()

    _parse_version(text, parsed)
    _parse_inventory(text, parsed)
    _parse_vrfs(text, parsed)
    _parse_interfaces(text, parsed)
    _parse_vlans(text, parsed)
    _parse_cdp_detail(text, parsed)
    _parse_lldp_detail(text, parsed)
    _parse_neighbor_table(text, parsed)
    tunnels.parse_block_style(text, parsed)
    routing.parse_cisco(text, parsed)
    policy.parse_cisco(text, parsed)

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


def expand_range(spec: str) -> list[str]:
    """`Gi1/0/1 - 24, Gi1/0/30` を、ポート名の一覧にする。

    範囲でまとめて書いてあるポートは、現場では端末を挿す口であることが多い。
    畳んだままだと「そのポートは何も設定されていない」と読めてしまう。
    """
    names: list[str] = []
    for chunk in spec.split(","):
        part = chunk.strip()
        if not part:
            continue
        match = _RANGE_PART.match(part)
        if not match:
            names.append(part)
            continue
        head, middle, start, end = match.groups()
        first = int(start)
        last = int(end) if end else first
        if last < first or last - first > 512:
            names.append(part)
            continue
        names.extend(f"{head}{middle}{number}" for number in range(first, last + 1))
    return names


def _parse_interfaces(text: str, parsed: ParsedConfig) -> None:
    current: ParsedInterface | None = None
    #: `interface range` のときは、同じ中身を全部のポートに書く
    group: list[ParsedInterface] = []
    # 許可 VLAN を明示したポート。書いていないトランクは「全部通す」が既定なので、
    # 「書いていない」と「none と書いた」を取り違えないよう覚えておく
    allowed_written: set[str] = set()

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue

        ranged = _INTERFACE_RANGE.match(line.strip())
        if ranged and not line.startswith(" "):
            group = [
                parsed.add_interface(ParsedInterface(name=name))
                for name in expand_range(ranged.group(1))
            ]
            current = group[0] if group else None
            continue

        match = _INTERFACE.match(line.strip())
        if match and not line.startswith(" "):
            # 既に同じポートが出ていればマージ結果を受け取る（以降の行はそちらに書く）
            current = parsed.add_interface(ParsedInterface(name=match.group(1)))
            group = [current]
            continue

        if current is None:
            continue
        if not line.startswith(" "):
            current, group = None, []  # インデントが戻ったらブロックの外
            continue

        body = line.strip()
        if body.startswith("description "):
            current.description = body[len("description ") :].strip()
        elif body.startswith("ip address "):
            _address(current, body)
        elif body.startswith("ipv6 address "):
            # IPv6 は長さ付きで書く。v4 が無いポートでは、これがそのポートの住所
            written = body.split(maxsplit=2)
            if len(written) >= 3 and "/" in written[2]:
                _remember(current, written[2].split()[0])
        elif body.startswith("switchport voice vlan "):
            spoken = body.rsplit(maxsplit=1)[-1]
            if spoken.isdigit():
                current.voice_vlan = int(spoken)
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
        elif body.startswith(("mtu ", "ip mtu ", "system mtu ")):
            value = body.rsplit(maxsplit=1)[-1]
            if value.isdigit():
                current.mtu = int(value)
        elif body.startswith("speed "):
            current.speed = body.rsplit(maxsplit=1)[-1]
        elif body.startswith("duplex "):
            current.duplex = body.rsplit(maxsplit=1)[-1]
        elif body.startswith("channel-group "):
            parts = body.split()
            if len(parts) >= 2 and parts[1].isdigit():
                current.lag_group = int(parts[1])
            if "mode" in parts and parts.index("mode") + 1 < len(parts):
                current.lag_mode = parts[parts.index("mode") + 1]
        elif body.startswith("ip ospf ") or body.startswith("ospf "):
            _ospf_option(current, body)
        elif body == "shutdown":
            current.enabled = False
        elif body == "no switchport":
            current.mode = "routed"

        _copy_to_group(current, group)

    for interface in parsed.interfaces:
        if interface.mode == "trunk" and interface.key not in allowed_written:
            interface.trunk_all = True


def _ospf_option(current: ParsedInterface, body: str) -> None:
    """ポートに書いた OSPF の決めごと。

    エリア・hello / dead・線の見立て・合言葉の掛け方は、両端でそろって
    いないと隣どうしにならない。合言葉そのものは持たない（掛けてあるか
    どうかだけが要る）。
    """
    parts = body.split()
    # `ip ospf 1 area 0` の形。番号は機器ごとの都合なので、エリアだけ拾う
    if "area" in parts:
        index = parts.index("area")
        if index + 1 < len(parts):
            current.ospf_area = parts[index + 1]
        return
    if "hello-interval" in parts:
        value = parts[parts.index("hello-interval") + 1 :]
        if value and value[0].isdigit():
            current.ospf_hello = int(value[0])
        return
    if "dead-interval" in parts:
        value = parts[parts.index("dead-interval") + 1 :]
        if value and value[0].isdigit():
            current.ospf_dead = int(value[0])
        return
    if "network" in parts:
        value = parts[parts.index("network") + 1 :]
        if value:
            current.ospf_network = value[0]
        return
    if "authentication" in parts:
        current.ospf_auth = "md5" if "message-digest" in parts else "text"
        return
    if "message-digest-key" in parts:
        current.ospf_auth = current.ospf_auth or "md5"
        return
    if "authentication-key" in parts:
        current.ospf_auth = current.ospf_auth or "text"


def _copy_to_group(current: ParsedInterface, group: list[ParsedInterface]) -> None:
    """範囲でまとめて書かれたときに、1つ目に書いた内容を残りへ写す。"""
    if len(group) < 2:
        return
    for other in group:
        if other is current:
            continue
        for name in (
            "description",
            "vlan",
            "mode",
            "native_vlan",
            "vrf",
            "mtu",
            "speed",
            "duplex",
            "lag_group",
            "lag_mode",
            "ospf_area",
            "ospf_hello",
            "ospf_dead",
            "ospf_network",
            "ospf_auth",
        ):
            setattr(other, name, getattr(current, name))
        other.trunk_vlans = list(current.trunk_vlans)
        other.trunk_all = current.trunk_all
        other.enabled = current.enabled


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


def _address(interface: ParsedInterface, body: str) -> None:
    """`ip address` の1行を読む。

    2つ目以降（secondary）で1つ目を上書きしない。上書きすると、その機器が
    本来そのセグメントで名乗っているアドレスが台帳から消え、線の突き合わせも
    重複の検出も外れる。`dhcp` は住所ではないので、住所としては持たない。
    """
    parts = body.split()
    if len(parts) < 3 or parts[2] == "dhcp":
        return
    if "/" in parts[2]:
        # 長さで書く流儀（Arista / AOS-CX）
        _remember(interface, parts[2])
    elif len(parts) >= 4:
        _remember(interface, f"{parts[2]}/{_prefix_length(parts[3])}")


def _remember(interface: ParsedInterface, address: str) -> None:
    if interface.ip_address is None:
        interface.ip_address = address
    elif address != interface.ip_address and address not in interface.extra_addresses:
        interface.extra_addresses.append(address)


def _prefix_length(netmask: str) -> int:
    try:
        return sum(bin(int(octet)).count("1") for octet in netmask.split("."))
    except ValueError:
        return 32


def _vlan_numbers(text: str) -> list[int]:
    """`10,20,30` や `100-110` を、番号の並びにする。"""
    out: list[int] = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            head, _, tail = part.partition("-")
            if not (head.isdigit() and tail.isdigit()):
                return []
            first, last = int(head), int(tail)
            if first > last or last - first > 4094:
                return []
            out.extend(range(first, last + 1))
        elif part.isdigit():
            out.append(int(part))
        else:
            return []
    return [number for number in out if 1 <= number <= 4094]


def _parse_vlans(text: str, parsed: ParsedConfig) -> None:
    current: int | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        match = _VLAN_DEF.match(stripped)
        if match and not raw.startswith(" "):
            numbers = _vlan_numbers(match.group(1))
            for number in numbers:
                parsed.vlans.setdefault(number, "")
            # 名前を付けられるのは1つだけ書いたとき（並べ書きに name は書けない）
            current = numbers[0] if len(numbers) == 1 else None
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
