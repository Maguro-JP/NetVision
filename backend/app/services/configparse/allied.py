"""アライドテレシス AlliedWare Plus（x230 / x530 など）の config を読む。

書き方は IOS にかなり近く、`switchport` まわりはそのまま読める。違うのは
次の3つで、そこだけこちらで読み直す。

- ポート名が `port1.0.1` / `vlan10` / `po1`（束）の形
- アドレスと経路が長さ付き（`ip address 10.0.10.1/24`）
- VLAN の名前は `vlan database` の下に `vlan 10 name USERS` と1行で並ぶ。
  IOS のように `vlan 10` の下の `name` ではないので、そのままだと名前が入らない。
  名前を付けずに `vlan 20-25 state enable` と範囲でまとめて作ることもある
- 同じ設定の口は `interface port1.0.1-1.0.24` と範囲でまとめて書く

出典: Allied Telesis「VLANs Feature Overview and Configuration Guide」
（C613-22001-00 REV N）の「Configuring VLANs」p.13〜14。VLAN は
`vlan <vid> [name <vlan-name>] [state {enable|disable}]` か
`vlan <vid-range> [state {enable|disable}]` で作る。口の範囲書きは同社
「Getting Started with AlliedWare Plus」の既定の設定の作例
（`interface port1.0.1-1.0.24`）。表番号は無い。`<vid-range>` にカンマ区切りが
使えるかはコマンドリファレンスで未確認なので、`-` と `,` の両方を読む。
アドレスの長さ付きの書き方（`ip address 192.168.10.1/24`）は同社サポートポータルの
作例で、経路の書き方は「Route Selection Feature Overview and Configuration Guide」
（C613-22029-00 REV G）の Static routes で、`access-list hardware` は「ACL Feature Overview and
Configuration Guide」（C613-22039-00 REV M）で確かめた。
"""

from __future__ import annotations

import copy
import re

from app.services.configparse import cisco, policy
from app.services.configparse.base import (
    NeighborProtocol,
    ParsedAcl,
    ParsedAclEntry,
    ParsedConfig,
    ParsedNeighbor,
    ParsedRoute,
    Vendor,
)
from app.services.configparse.routing import to_prefix
from app.services.configparse.vlanlist import parse_ranges

_VERSION = re.compile(r"AlliedWare Plus.*?(?:version|Version)\s+([\d.\-A-Za-z]+)", re.IGNORECASE)
_BUILD = re.compile(r"^!?\s*Build name\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_MODEL = re.compile(r"^!?\s*(?:Base|Product)\s+model\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_SERIAL = re.compile(r"^!?\s*Serial number\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_VLAN_LINE = re.compile(r"^vlan\s+(\d+)\s+name\s+(\S+)", re.IGNORECASE)
#: 名前を付けずに作る形。`vlan 20-25 state enable`、`vlan 30`
_VLAN_RANGE = re.compile(r"^vlan\s+(?P<vids>[\d,\-]+)(?:\s+state\s+\w+)?\s*$", re.IGNORECASE)
#: 口の範囲。`port1.0.1-1.0.24`。前の2つ（スタック番号・モジュール番号）が同じときだけ開く
_PORT_RANGE = re.compile(
    r"^port(?P<stack>\d+)\.(?P<module>\d+)\.(?P<start>\d+)-(?P=stack)\.(?P=module)\.(?P<end>\d+)$",
    re.IGNORECASE,
)
_IP_ADDRESS = re.compile(r"^ip address\s+(\d+\.\d+\.\d+\.\d+/\d+)", re.IGNORECASE)
_INTERFACE = re.compile(r"^interface\s+(\S+)\s*$", re.IGNORECASE)
_ACL_HARDWARE = re.compile(r"^access-list\s+hardware\s+(\S+)\s*$", re.IGNORECASE)
#: 番号付きのハードウェア IP ACL（3000-3699）。出典: C613-22039-00 REV M の
#: Defining hardware IP ACLs
_ACL_HARDWARE_NUMBERED = re.compile(r"^access-list\s+(3[0-6]\d\d)\s+(\S.*)$", re.IGNORECASE)
_ACCESS_GROUP = re.compile(r"^access-group\s+(\S+)\s*$", re.IGNORECASE)
#: `show lldp neighbors` の見出し
_LLDP_HEADER = re.compile(
    r"^\s*Local Port\s+Neighbor Chassis ID\s+Neighbor Port\s+System Name\s*$", re.IGNORECASE
)
_ROUTE = re.compile(
    r"^ip route\s+(?:vrf\s+(?P<vrf>\S+)\s+)?(?P<dest>\d+\.\d+\.\d+\.\d+/\d+)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)


def detect(text: str) -> Vendor | None:
    """AlliedWare Plus かどうか。IOS と見分けが付く手掛かりだけで見る。"""
    if re.search(r"AlliedWare Plus", text, re.IGNORECASE):
        return Vendor.ALLIED
    for pattern in (
        r"^interface\s+port\d+\.\d+\.\d+",
        r"^vlan database\s*$",
        r"^\s*Allied Telesis",
    ):
        if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
            return Vendor.ALLIED
    return None


def parse(text: str) -> ParsedConfig:
    """IOS の読み手に任せてから、AlliedWare Plus の書き方で上から直す。"""
    parsed = cisco.parse(text, Vendor.CISCO_IOS)
    parsed.vendor = Vendor.ALLIED
    parsed.os_name = "AlliedWare Plus"
    parsed.os_version = None
    parsed.model = None

    found = _VERSION.search(text) or _BUILD.search(text)
    if found:
        parsed.os_version = found.group(1)
    found = _MODEL.search(text)
    if found:
        parsed.model = found.group(1)
    found = _SERIAL.search(text)
    if found:
        parsed.serial_number = found.group(1)

    _parse_vlan_database(text, parsed)
    _open_port_ranges(parsed)
    _parse_addresses(text, parsed)
    _parse_routes(text, parsed)
    _parse_hardware_acls(text, parsed)
    _parse_lldp_table(text, parsed)
    return parsed


def _parse_vlan_database(text: str, parsed: ParsedConfig) -> None:
    """`vlan database` の下の `vlan 10 name USERS`。

    IOS の読み手は `vlan 10` を定義として拾うが、名前は次の行の `name` を
    見にいくので入らない。1行で書かれたものをここで入れる。
    """
    in_database = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if not raw.startswith((" ", "\t")):
            in_database = stripped.lower() == "vlan database"
        found = _VLAN_LINE.match(stripped)
        if found:
            parsed.vlans[int(found.group(1))] = found.group(2)
            continue
        # 名前の無い形は `vlan database` の下だけで読む。口の下の行と取り違えない
        bare = _VLAN_RANGE.match(stripped) if in_database else None
        if bare:
            for number in parse_ranges(bare.group("vids")):
                parsed.vlans.setdefault(number, "")


def _open_port_ranges(parsed: ParsedConfig) -> None:
    """`interface port1.0.1-1.0.24` を口1つずつに開く。

    IOS の読み手は範囲を1つの口として持つので、そのままだと台帳にも図にも
    `port1.0.1-1.0.24` という口が1つだけ出て、間の口が抜ける。
    """
    opened: list = []
    for interface in parsed.interfaces:
        found = _PORT_RANGE.match(interface.name)
        if not found or int(found.group("start")) > int(found.group("end")):
            opened.append(interface)
            continue
        head = f"port{found.group('stack')}.{found.group('module')}."
        for number in range(int(found.group("start")), int(found.group("end")) + 1):
            port = copy.deepcopy(interface)
            port.name = f"{head}{number}"
            opened.append(port)
    parsed.interfaces = opened


def _parse_addresses(text: str, parsed: ParsedConfig) -> None:
    """`ip address 10.0.10.1/24`。IOS の読み手はマスク付きしか読めない。"""
    ports = {interface.key: interface for interface in parsed.interfaces}
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        match = _INTERFACE.match(line.strip())
        if match and not line.startswith(" "):
            from app.services.configparse.ifname import normalize

            current = ports.get(normalize(match.group(1)))
            continue
        if current is None or not line.startswith(" "):
            continue
        found = _IP_ADDRESS.match(line.strip())
        if found:
            current.ip_address = found.group(1)


def _parse_routes(text: str, parsed: ParsedConfig) -> None:
    """`ip route 0.0.0.0/0 10.0.10.254`。長さ付きなので IOS の読み手は拾えない。"""
    seen = {(route.destination, route.next_hop) for route in parsed.routes}
    for raw in text.splitlines():
        found = _ROUTE.match(raw.strip())
        if not found:
            continue
        destination = to_prefix(found.group("dest"))
        if destination is None:
            continue
        route = ParsedRoute(destination=destination, vrf=found.group("vrf"))
        for token in found.group("rest").split():
            # `weight <1-255>` と `description <文>` は距離でも口でもない。ここから後ろは読まない
            # （出典: Allied Telesis「Route Selection Feature Overview and Configuration Guide」
            # C613-22029-00 REV G の Static routes）
            if token.lower() in ("weight", "description"):
                break
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", token):
                route.next_hop = token
            elif token.isdigit():
                route.distance = int(token)
            elif re.match(r"^(port|vlan|po|sa|tunnel)", token, re.IGNORECASE):
                route.interface = token
        key = (route.destination, route.next_hop)
        if key in seen:
            # IOS の読み手が先に同じ行を読んでいる。こちらの書式で読んだ方に置き換える
            # （IOS の読み手は weight を口、その後ろの数を距離と取り違える）
            parsed.routes = [
                r for r in parsed.routes if (r.destination, r.next_hop) != key or r.vrf != route.vrf
            ]
        seen.add(key)
        parsed.routes.append(route)


#: ハードウェア ACL の動作を、通す（permit）か捨てる（deny）かに寄せる。出典: Allied Telesis
#: 「ACL Feature Overview and Configuration Guide」C613-22039-00 REV M の Table 2: Hardware ACL
#: parameter actions（copy-to-* は転送もする。send-to-cpu・send-to-mirror・deny-and-not-cpu は
#: 転送しない）。
#: send-to-vlan-port は別の口へ送り直すので、どちらにも寄せない（読まない）
_HARDWARE_ACTIONS = {
    "copy-to-cpu": "permit",
    "copy-to-mirror": "permit",
    "send-to-cpu": "deny",
    "send-to-mirror": "deny",
    "deny-and-not-cpu": "deny",
}


def _hardware_acl_entry(line: str) -> ParsedAclEntry | None:
    """名前付きのハードウェア ACL の1行。動作の語を寄せてから IOS と同じ形で読み、元の行を残す。"""
    words = line.split(maxsplit=2)
    index = 1 if words and words[0].isdigit() else 0
    if len(words) <= index:
        return None
    action = _HARDWARE_ACTIONS.get(words[index].lower())
    if action is None:
        return policy.acl_entry(line)
    words[index] = action
    entry = policy.acl_entry(" ".join(words))
    if entry is not None:
        entry.raw = line
    return entry


def _parse_hardware_acls(text: str, parsed: ParsedConfig) -> None:
    """ハードウェア ACL と、ポートへの当て方。

    番号付き（3000 番台）は IOS の読み手がそのまま読める。名前付きは
    `access-list hardware 名前` の下に並ぶので、ここで読む。当てるのは
    `access-group 名前` で、向きは書かない（入ってくる側に効く）。
    """
    by_name = {acl.name: acl for acl in parsed.acls}
    current: ParsedAcl | None = None
    interface: str | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        stripped = line.strip()

        if not line.startswith(" "):
            numbered = _ACL_HARDWARE_NUMBERED.match(stripped)
            if numbered and numbered.group(2).split()[0].lower() in _HARDWARE_ACTIONS:
                # IOS の読み手は permit / deny の行しか拾わないので、ほかの動作の行をここで足す
                entry = _hardware_acl_entry(numbered.group(2))
                if entry is not None:
                    acl = by_name.setdefault(
                        numbered.group(1), ParsedAcl(name=numbered.group(1), kind="extended")
                    )
                    if acl not in parsed.acls:
                        parsed.acls.append(acl)
                    acl.entries.append(entry)
            head = _ACL_HARDWARE.match(stripped)
            if head:
                current = by_name.setdefault(
                    head.group(1), ParsedAcl(name=head.group(1), kind="extended")
                )
                if current not in parsed.acls:
                    parsed.acls.append(current)
                interface = None
                continue
            port = _INTERFACE.match(stripped)
            current = None
            interface = port.group(1) if port else None
            continue

        if current is not None:
            entry = _hardware_acl_entry(stripped)
            if entry is not None:
                current.entries.append(entry)
            continue

        if interface is not None:
            applied = _ACCESS_GROUP.match(stripped)
            if applied and applied.group(1) in by_name:
                by_name[applied.group(1)].applied_to.append(f"interface {interface} 入り")


def _parse_lldp_table(text: str, parsed: ParsedConfig) -> None:
    """`show lldp neighbors` の表。列は Local Port / Chassis ID / Neighbor Port / System Name。"""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _LLDP_HEADER.match(line):
            continue
        for row in lines[index + 1 :]:
            stripped = row.strip()
            if not stripped:
                break
            if stripped.startswith("-"):
                continue
            columns = stripped.split()
            if len(columns) < 4:
                break
            parsed.add_neighbor(
                ParsedNeighbor(
                    local_interface=columns[0],
                    remote_hostname=columns[-1],
                    remote_interface=" ".join(columns[2:-1]),
                    protocol=NeighborProtocol.LLDP,
                )
            )
