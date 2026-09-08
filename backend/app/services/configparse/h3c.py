"""H3C（Comware）の config を読む。

書き方は NEC QX と一致する箇所が多い。だが別の製品である。似ているだけの
別の製品を同じ読み手に通すと、片方の都合で直した読み方がもう片方にも黙って効き、
H3C だけに出てくる書き方（`port link-mode` など）を足す場所も無くなる。
読み手も判別も、この機種のものとして独立して持つ。

読める書き方（H3C の `display current-configuration` に出る形）:

- 名乗りと型番 … `H3C Comware Software, Version ...`、`H3C S5560X-...`、
  `display device manuinfo` の DEVICE_NAME / DEVICE_SERIAL_NUMBER
- 口 … `port link-mode bridge|route`、`port link-type`、`port access vlan`、
  `port trunk permit vlan`（undo で外す）、`port hybrid vlan ... tagged|untagged`、
  `port trunk pvid vlan`、`ip address`、`ip binding vpn-instance`、
  `port link-aggregation group`、`link-aggregation mode`、mtu / speed / duplex、
  `shutdown`
- VLAN … `vlan N` と `name`、`vlan 10 to 20` のまとめ書き
- VRF … `ip vpn-instance`
- 隣 … `display lldp neighbor-information`（詳細と list）

経路（`ip route-static`・OSPF・BGP）、経路の道具、トンネルは、否定を `undo` で
書く書式の共通読み手に渡す。書式が共通でも、機種の名乗りと判別はここが持つ。
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

# --- 名乗り -----------------------------------------------------------------

#: H3C の OS の名乗り。`display version` の1行目に出る。これだけでは
#: 製品を決めない（製品の印が別にあるならそちらが機器の名前）
_OS_BANNER = re.compile(r"\bH3C\s+Comware\b", re.IGNORECASE)
#: H3C の製品の名乗り。型番の前に会社名が付く、または manuinfo の VENDOR_NAME
_PRODUCT = re.compile(
    r"\bH3C\s+(?:S|MSR|SR|WX|WA|LS)\d[\w-]*\b|\bNew\s+H3C\s+Technologies\b"
    r"|^\s*VENDOR_NAME\s*:\s*H3C\b",
    re.IGNORECASE | re.MULTILINE,
)
#: H3C の型番。スイッチは S、ルーターは MSR / SR、無線は WX
_MODEL = re.compile(
    r"\bH3C\s+((?:S|MSR|SR|WX|WA|LS)\d[\w-]*)\b|\b((?:S5[0-9]{3}|S6[0-9]{3}|S7[0-9]{3}|"
    r"S9[0-9]{3}|S10[0-9]{3}|S12[0-9]{3}|MSR\d+|SR\d+|WX\d+)[A-Z]?[\w-]*)\s+uptime\s+is\b",
    re.IGNORECASE,
)
#: `H3C Comware Software, Version 7.1.070, Release 6127P02`
_DISPLAY_VERSION = re.compile(r"H3C\s+Comware\s+Software,\s*Version\s+([\w.]+)", re.IGNORECASE)
#: config の頭の `version 7.1.070, Release 6127P02`
_CONFIG_VERSION = re.compile(r"^\s*version\s+(\d[\w.]*)(?:,|\s|$)", re.IGNORECASE | re.MULTILINE)
_SYSNAME = re.compile(r'^\s*sysname\s+"?([^"\n]+?)"?\s*$', re.IGNORECASE | re.MULTILINE)
#: `<h3c-sw-01>` / `[h3c-sw-01]` のプロンプト。sysname が無い show 出力用
_PROMPT = re.compile(r"^[<\[]([A-Za-z0-9][\w.\-]*)[>\]]", re.MULTILINE)
#: `display device manuinfo` の項目
_MANUINFO_HEADER = re.compile(
    r"^(Slot\s+\d+|Chassis\s+\d+|Subslot\s+\d+|Fan\s+\d+|Power\s+\d+)\s*:?\s*$", re.I
)
_MANUINFO_SERIAL = re.compile(r"^DEVICE_SERIAL_NUMBER\s*:\s*(\S+)", re.IGNORECASE)
_MANUINFO_NAME = re.compile(r"^DEVICE_NAME\s*:\s*(.+?)\s*$", re.IGNORECASE)

#: NEC の製品の名乗り。書き方が重なるので、製品の印で見分ける
_NEC_PRODUCT = re.compile(
    r"\bNEC\s+QX-S\d|\bUNIVERGE\b|\bQX-S\d[\w-]*\b|^\s*VENDOR_NAME\s*:\s*NEC\b",
    re.IGNORECASE | re.MULTILINE,
)

# --- 口と VLAN ----------------------------------------------------------------

_INTERFACE = re.compile(r"^interface\s+(\S+)\s*$", re.IGNORECASE)
_DESCRIPTION = re.compile(r"^\s+description\s+(.+?)\s*$", re.IGNORECASE)
_LINK_MODE = re.compile(r"^\s+port\s+link-mode\s+(bridge|route)\s*$", re.IGNORECASE)
_LINK_TYPE = re.compile(r"^\s+port\s+link-type\s+(access|trunk|hybrid)\s*$", re.IGNORECASE)
_ACCESS_VLAN = re.compile(r"^\s+port\s+access\s+vlan\s+(\d+)", re.IGNORECASE)
_TRUNK_PERMIT = re.compile(r"^\s+(undo\s+)?port\s+trunk\s+permit\s+vlan\s*(.*?)\s*$", re.IGNORECASE)
_HYBRID = re.compile(
    r"^\s+port\s+hybrid\s+vlan\s+(?P<vlans>.+?)\s+(?P<how>tagged|untagged)\s*$", re.IGNORECASE
)
_PVID = re.compile(r"^\s+port\s+(?:trunk|hybrid)\s+pvid\s+vlan\s+(\d+)", re.IGNORECASE)
_IP = re.compile(
    r"^\s+ip\s+address\s+(?P<ip>\d+\.\d+\.\d+\.\d+)\s+(?P<mask>\d+\.\d+\.\d+\.\d+|\d{1,2})"
    r"(?P<sub>\s+sub)?\s*$",
    re.IGNORECASE,
)
_IP_DHCP = re.compile(r"^\s+ip\s+address\s+dhcp-alloc\s*$", re.IGNORECASE)
_BIND_VPN = re.compile(r"^\s+ip\s+binding\s+vpn-instance\s+(\S+)", re.IGNORECASE)
_LAG_MEMBER = re.compile(r"^\s+port\s+link-aggregation\s+group\s+(\d+)", re.IGNORECASE)
_LAG_MODE = re.compile(r"^\s+link-aggregation\s+mode\s+(dynamic|static)\s*$", re.IGNORECASE)
_MTU = re.compile(r"^\s+(?:ip\s+)?mtu\s+(\d+)\s*$", re.IGNORECASE)
_SPEED = re.compile(r"^\s+speed\s+(\S+)\s*$", re.IGNORECASE)
_DUPLEX = re.compile(r"^\s+duplex\s+(\S+)\s*$", re.IGNORECASE)
_SHUTDOWN = re.compile(r"^\s+shutdown\s*$", re.IGNORECASE)
_UNDO_SHUTDOWN = re.compile(r"^\s+undo\s+shutdown\s*$", re.IGNORECASE)

_VLAN_HEAD = re.compile(r"^vlan\s+(\d+)\s*$", re.IGNORECASE)
_VLAN_MANY = re.compile(r"^vlan\s+(\d[\d\s]*(?:to\s+\d+)?[\d\s]*)$", re.IGNORECASE)
_VLAN_NAME = re.compile(r"^\s+name\s+(.+?)\s*$", re.IGNORECASE)
_VPN_INSTANCE = re.compile(r"^ip\s+vpn-instance\s+(\S+)", re.IGNORECASE)

# --- 隣 -------------------------------------------------------------------

#: `LLDP neighbor-information of port 1[GigabitEthernet1/0/1]:`
_LLDP_PORT = re.compile(
    r"LLDP\s+neighbor-information\s+of\s+port\s+\d+\s*\[([^\]]+)\]", re.IGNORECASE
)
_LLDP_FIELD = re.compile(
    r"^\s*(System name|Port ID|Port description)\s*:\s*(.+?)\s*$", re.IGNORECASE
)
#: `display lldp neighbor-information list` の見出し
_LLDP_LIST_HEADER = re.compile(r"^\s*System Name\s+Local Interface\s+.*Port ID\s*$", re.IGNORECASE)
_MAC = re.compile(r"^(?:[0-9a-f]{4}-){2}[0-9a-f]{4}$|^(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}$", re.I)


def detect(text: str) -> Vendor | None:
    """H3C かどうか。製品の印で決める。OS の名乗りでは決めない。

    製品の印（型番・VENDOR_NAME・型番の前の会社名）がその機器の名前で、
    OS の名乗りはその機器の名前ではない。NEC の製品の印がある config を、
    OS の名乗りを理由に H3C と呼ぶことはしない。逆に、どちらかに寄せる理由も
    持たない。それだけの規則。

    - NEC の製品の印がある … NEC（こちらは名乗らない）
    - H3C の製品の印がある … H3C
    - 製品の印は無く、OS の名乗りだけがある … H3C（他の製品を名乗るものが無い）
    """
    if _NEC_PRODUCT.search(text):
        return None
    if _PRODUCT.search(text) or _OS_BANNER.search(text):
        return Vendor.H3C
    return None


def parse(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.H3C, os_name="Comware")

    found = _DISPLAY_VERSION.search(text) or _CONFIG_VERSION.search(text)
    if found:
        parsed.os_version = found.group(1)
    found = _MODEL.search(text)
    if found:
        parsed.model = found.group(1) or found.group(2)
    found = _SYSNAME.search(text)
    if found:
        parsed.hostname = found.group(1).strip()
    else:
        found = _PROMPT.search(text)
        if found:
            parsed.hostname = found.group(1)

    _read_manuinfo(text, parsed)
    _read_interfaces_and_vlans(text, parsed)
    _read_vpn_instances(text, parsed)
    _read_lldp(text, parsed)
    _read_lldp_list(text, parsed)

    # 経路と経路の道具、トンネルは、undo 書式の共通読み手に渡す
    tunnels.parse_block_style(text, parsed)
    routing.parse_undoform(text, parsed)
    policy.parse_undoform(text, parsed)
    return parsed


def _read_manuinfo(text: str, parsed: ParsedConfig) -> None:
    """`display device manuinfo`。シャーシの型番とシリアルだけ取る。

    ファンや電源にも同じ項目があるので、区画を見ずに拾うと電源のシリアルが
    機器のシリアルになる。
    """
    section: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        header = _MANUINFO_HEADER.match(line)
        if header:
            section = header.group(1).lower()
            continue
        chassis = section is None or section.startswith(("slot", "chassis"))
        if not chassis:
            continue
        found = _MANUINFO_SERIAL.match(line)
        if found and parsed.serial_number is None and found.group(1).upper() != "NONE":
            parsed.serial_number = found.group(1)
            continue
        found = _MANUINFO_NAME.match(line)
        if found and parsed.model is None and found.group(1).upper() != "NONE":
            parsed.model = found.group(1)


def _read_interfaces_and_vlans(text: str, parsed: ParsedConfig) -> None:
    current: ParsedInterface | None = None
    current_vlan: int | None = None
    #: 通す VLAN を1行でも書いたトランク。書いていないトランクは VLAN 1 だけ通す
    permitted: set[str] = set()

    for raw in text.splitlines():
        head = _INTERFACE.match(raw)
        if head:
            current = parsed.add_interface(ParsedInterface(name=head.group(1)))
            current_vlan = None
            continue
        vlan = _VLAN_HEAD.match(raw)
        if vlan:
            current = None
            current_vlan = int(vlan.group(1))
            parsed.vlans.setdefault(current_vlan, "")
            continue
        many = _VLAN_MANY.match(raw)
        if many:
            for number in vlanlist.parse_ranges(many.group(1)):
                parsed.vlans.setdefault(number, "")
            current = None
            current_vlan = None
            continue
        if raw and not raw[0].isspace():
            current = None
            current_vlan = None
            continue

        if current_vlan is not None:
            name = _VLAN_NAME.match(raw)
            if name:
                parsed.vlans[current_vlan] = name.group(1)
            continue
        if current is None:
            continue

        _interface_line(current, raw, permitted)

    # 通す VLAN を書いていないトランクは、VLAN 1 だけがタグ無しで通る既定。
    # 「書いていない」と読み落としの区別は付かないので、全許可とはみなさない
    for port in parsed.interfaces:
        if port.mode == "trunk" and port.key not in permitted:
            port.native_vlan = port.native_vlan or 1


def _interface_line(port: ParsedInterface, raw: str, permitted: set[str]) -> None:
    found = _DESCRIPTION.match(raw)
    if found:
        port.description = port.description or found.group(1)
        return
    found = _LINK_MODE.match(raw)
    if found:
        # route の口は VLAN を持たない。bridge の口は link-type で決まる
        if found.group(1).lower() == "route":
            port.mode = "routed"
        return
    found = _LINK_TYPE.match(raw)
    if found:
        port.mode = found.group(1).lower()
        return
    found = _ACCESS_VLAN.match(raw)
    if found:
        port.vlan = int(found.group(1))
        return
    found = _TRUNK_PERMIT.match(raw)
    if found:
        permitted.add(port.key)
        removing = bool(found.group(1))
        values = found.group(2)
        if vlanlist.is_all(values) or not values:
            port.trunk_all = not removing
            if removing:
                port.trunk_vlans = []
        elif removing:
            drop = set(vlanlist.parse_ranges(values))
            port.trunk_vlans = [v for v in port.trunk_vlans if v not in drop]
        else:
            port.trunk_vlans = sorted(set(port.trunk_vlans) | set(vlanlist.parse_ranges(values)))
        return
    found = _HYBRID.match(raw)
    if found:
        permitted.add(port.key)
        values = vlanlist.parse_ranges(found.group("vlans"))
        port.trunk_vlans = sorted(set(port.trunk_vlans) | set(values))
        return
    found = _PVID.match(raw)
    if found:
        port.native_vlan = int(found.group(1))
        return
    if _IP_DHCP.match(raw):
        # 貰う設定は「無い」ではない。空欄にすると付け忘れと見分けが付かない
        port.address_source = "dhcp"
        return
    found = _IP.match(raw)
    if found:
        address = f"{found.group('ip')}/{_prefixlen(found.group('mask'))}"
        if port.ip_address is None and not found.group("sub"):
            port.ip_address = address
        else:
            port.extra_addresses.append(address)
        return
    found = _BIND_VPN.match(raw)
    if found:
        port.vrf = found.group(1)
        return
    found = _LAG_MEMBER.match(raw)
    if found:
        port.lag_group = int(found.group(1))
        return
    found = _LAG_MODE.match(raw)
    if found:
        port.lag_mode = "active" if found.group(1).lower() == "dynamic" else "on"
        return
    found = _MTU.match(raw)
    if found:
        port.mtu = int(found.group(1))
        return
    found = _SPEED.match(raw)
    if found:
        port.speed = found.group(1)
        return
    found = _DUPLEX.match(raw)
    if found:
        port.duplex = found.group(1)
        return
    if _SHUTDOWN.match(raw):
        port.enabled = False
    elif _UNDO_SHUTDOWN.match(raw):
        port.enabled = True


def _read_vpn_instances(text: str, parsed: ParsedConfig) -> None:
    current: ParsedVrf | None = None
    for raw in text.splitlines():
        found = _VPN_INSTANCE.match(raw)
        if found:
            current = parsed.add_vrf(ParsedVrf(name=found.group(1)))
            continue
        if current is None:
            continue
        if raw and not raw[0].isspace():
            current = None
            continue
        line = raw.strip()
        if line.startswith("route-distinguisher "):
            current.rd = line.split(maxsplit=1)[1]
        elif line.startswith("description "):
            current.description = line.split(maxsplit=1)[1]


def _read_lldp(text: str, parsed: ParsedConfig) -> None:
    """`display lldp neighbor-information` の詳細。口ごとの塊で出る。"""
    local: str | None = None
    remote_host: str | None = None
    remote_port: str | None = None
    port_descr: str | None = None

    def flush() -> None:
        port = remote_port
        if port is None or _MAC.match(port or ""):
            # Port ID が MAC のときは、口の名前は description 側に入っている
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
        head = _LLDP_PORT.search(raw)
        if head:
            flush()
            local, remote_host, remote_port, port_descr = head.group(1), None, None, None
            continue
        field = _LLDP_FIELD.match(raw)
        if field and local:
            key = field.group(1).lower()
            value = field.group(2).strip('"')
            if key == "system name":
                remote_host = value
            elif key == "port id":
                remote_port = value
            else:
                port_descr = value
    flush()


def _read_lldp_list(text: str, parsed: ParsedConfig) -> None:
    """`display lldp neighbor-information list` の表。詳細が無いときの手掛かり。"""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _LLDP_LIST_HEADER.match(line):
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


def _prefixlen(mask: str) -> int:
    """`24` とも `255.255.255.0` とも書かれる。"""
    if "." not in mask:
        return int(mask)
    return sum(bin(int(part)).count("1") for part in mask.split("."))
