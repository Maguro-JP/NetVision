"""ファーウェイ VRP（S / CE / AR シリーズ）の config を読む。

書き方は NEC QX と一致する箇所が多い。だが別の製品である。似ているだけの
別の製品を同じ読み手に通すと、片方の都合で直した読み方がもう片方にも黙って効き、
VRP だけの書き方を足す場所も無くなる。読み手も判別も、この機種のものとして
独立して持つ。メーカーの取り違えは、別のメーカーの保守期限と脆弱性で判定する
ことになり、しかも黙って通る。

読める書き方（`display current-configuration` に出る形）:

- 名乗りと型番 … `VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)`、
  `HUAWEI S5735 ... uptime`
- 口 … `port link-type`、`port default vlan`（アクセス）、
  `port trunk allow-pass vlan`（undo で外す）、`port trunk pvid vlan`、
  `port hybrid tagged|untagged vlan`、`port hybrid pvid vlan`、`ip address`（sub 含む）、
  `ip address dhcp-alloc`、`ip binding vpn-instance`、`eth-trunk N`、
  Eth-Trunk の `mode lacp-static|lacp|manual`、mtu / speed / duplex、`shutdown`
- VLAN … `vlan N` と `description` / `name`、`vlan batch 10 20 30`
- VRF … `ip vpn-instance`（`ipv4-family` の下の `route-distinguisher`）
- 変換 … `nat address-group`、`nat outbound`、`nat server`
- 配布 … `ip pool` の `network ... mask`、`gateway-list`、`dns-list`、`excluded-ip-address`
- 優先制御 … `traffic classifier` / `traffic behavior` / `traffic policy`、`traffic-policy`
- 暗号化 … `ike proposal`、`ike peer`（`remote-address` / `pre-shared-key`）、
  `ipsec proposal`、`ipsec policy`、口の `ipsec policy`
- 隣 … `display lldp neighbor brief` などの表（見出しから列を決める読み手）

経路（`ip route-static`・OSPF・BGP）、経路の道具、トンネルは、否定を `undo` で
書く書式の共通読み手に渡す。書式が共通でも、機種の名乗りと判別はここが持つ。
"""

from __future__ import annotations

import re

from app.services.configparse import lldptable, policy, routing, tunnels, vlanlist
from app.services.configparse.base import (
    ParsedConfig,
    ParsedCrypto,
    ParsedCryptoMapEntry,
    ParsedDhcp,
    ParsedDhcpPool,
    ParsedIkePolicy,
    ParsedInterface,
    ParsedNat,
    ParsedNatPool,
    ParsedNatRule,
    ParsedQos,
    ParsedQosAction,
    ParsedQosClass,
    ParsedQosPolicy,
    ParsedTransformSet,
    ParsedVrf,
    Vendor,
)

# --- 名乗り -----------------------------------------------------------------

#: VRP の名乗り。ここが無いものを VRP だと決めない（決め打つと NEC QX を
#: ファーウェイとして読む）
_BANNER = re.compile(
    r"Huawei Versatile Routing Platform|VRP\s*\(R\)\s*software|\bHUAWEI\b", re.IGNORECASE
)
#: VRP にしか無い書き方（書き方の指紋。出自の話ではない）。`display current-configuration`
#: には名乗りが出ないことが多いので、名乗りが無くてもこれで見分ける
_VRP_ONLY = re.compile(
    r"^\s+port\s+trunk\s+allow-pass\s+vlan\b"
    r"|^\s+port\s+default\s+vlan\s+\d"
    r"|^\s+port\s+hybrid\s+(?:tagged|untagged)\s+vlan\b"
    r"|^interface\s+Vlanif\d"
    r"|^interface\s+Eth-Trunk\d"
    r"|^\s+eth-trunk\s+\d+\s*$"
    r"|^ip\s+pool\s+\S+"
    r"|^\s+traffic-policy\s+\S+\s+(?:inbound|outbound)"
    r"|^traffic\s+policy\s+\S+"
    r"|^ipsec\s+proposal\s+\S+"
    r"|^ike\s+peer\s+\S+",
    re.IGNORECASE | re.MULTILINE,
)
#: NEC QX にしか無い書き方。これが出ていれば、上の指紋があっても NEC の config
_NEC_ONLY = re.compile(
    r"^interface\s+Vlan-interface\d"
    r"|^interface\s+Bridge-Aggregation\d"
    r"|^\s+port\s+link-aggregation\s+group\s+\d"
    r"|^dhcp\s+server\s+ip-pool\s+\S+"
    r"|^qos\s+policy\s+\S+"
    r"|^ipsec\s+transform-set\s+\S+"
    r"|\bNEC\b|\bUNIVERGE\b|\bQX-S\d",
    re.IGNORECASE | re.MULTILINE,
)
_VERSION = re.compile(r"^\s*VRP\s*\(R\).*?Version\s+([\w.]+)", re.IGNORECASE | re.MULTILINE)
#: `Version 8.180 (CE6850 V200R005C10SPC800)` の括弧に型番が入る
_MODEL_IN_VERSION = re.compile(r"\(\s*((?:CE|S|AR|NE|USG)\d[\w-]*)\s", re.IGNORECASE)
_DISPLAY_MODEL = re.compile(
    r"^\s*(?:HUAWEI\s+)?((?:CE|S|AR|NE|USG)\d[\w-]*)\s+(?:Router|Switch|uptime)",
    re.IGNORECASE | re.MULTILINE,
)
_SYSNAME = re.compile(r'^\s*sysname\s+"?([^"\n]+?)"?\s*$', re.IGNORECASE | re.MULTILINE)
_PROMPT = re.compile(r"^[<\[]([A-Za-z0-9][\w.\-]*)[>\]]", re.MULTILINE)
_ESN = re.compile(r"^ESN of slot\s+\d+\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)

# --- 口と VLAN ----------------------------------------------------------------

_INTERFACE = re.compile(r"^interface\s+(\S+)\s*$", re.IGNORECASE)
_DESCRIPTION = re.compile(r"^\s+description\s+(.+?)\s*$", re.IGNORECASE)
_LINK_TYPE = re.compile(r"^\s+port\s+link-type\s+(access|trunk|hybrid|dot1q-tunnel)\s*$", re.I)
#: VRP のアクセス VLAN は `port default vlan`
_DEFAULT_VLAN = re.compile(r"^\s+port\s+default\s+vlan\s+(\d+)", re.IGNORECASE)
_ALLOW_PASS = re.compile(
    r"^\s+(undo\s+)?port\s+trunk\s+allow-pass\s+vlan\s*(.*?)\s*$", re.IGNORECASE
)
#: VRP は tagged / untagged を番号の前に置く
_HYBRID = re.compile(
    r"^\s+port\s+hybrid\s+(?P<how>tagged|untagged)\s+vlan\s+(?P<vlans>.+?)\s*$", re.IGNORECASE
)
_PVID = re.compile(r"^\s+port\s+(?:trunk|hybrid)\s+pvid\s+vlan\s+(\d+)", re.IGNORECASE)
_IP = re.compile(
    r"^\s+ip\s+address\s+(?P<ip>\d+\.\d+\.\d+\.\d+)\s+(?P<mask>\d+\.\d+\.\d+\.\d+|\d{1,2})"
    r"(?P<sub>\s+sub)?\s*$",
    re.IGNORECASE,
)
_IP_DHCP = re.compile(r"^\s+ip\s+address\s+dhcp-alloc\s*$", re.IGNORECASE)
_BIND_VPN = re.compile(r"^\s+ip\s+binding\s+vpn-instance\s+(\S+)", re.IGNORECASE)
_ETH_TRUNK_MEMBER = re.compile(r"^\s+eth-trunk\s+(\d+)\s*$", re.IGNORECASE)
_ETH_TRUNK_MODE = re.compile(r"^\s+mode\s+(lacp-static|lacp|manual\s+load-balance)\s*$", re.I)
#: VRP は口の大きさを `mtu` で書く。IPv6 だけの `ipv6 mtu` は別物なので入れない
#: 出典: Huawei NetEngine 8000 F8 Configuration Guide「MTU Configuration」
#: （IPv4 は `mtu mtu`、IPv6 は `ipv6 mtu mtu`）
_MTU = re.compile(r"^\s+mtu\s+(\d+)\s*$", re.IGNORECASE)
_SPEED = re.compile(r"^\s+speed\s+(\S+)\s*$", re.IGNORECASE)
_DUPLEX = re.compile(r"^\s+duplex\s+(\S+)\s*$", re.IGNORECASE)
_SHUTDOWN = re.compile(r"^\s+shutdown\s*$", re.IGNORECASE)
_UNDO_SHUTDOWN = re.compile(r"^\s+undo\s+shutdown\s*$", re.IGNORECASE)

_VLAN_HEAD = re.compile(r"^vlan\s+(\d+)\s*$", re.IGNORECASE)
#: `vlan batch 10 20 30 to 40`
_VLAN_BATCH = re.compile(r"^vlan\s+batch\s+(.+?)\s*$", re.IGNORECASE)
_VLAN_NAME = re.compile(r"^\s+(?:name|description)\s+(.+?)\s*$", re.IGNORECASE)
_VPN_INSTANCE = re.compile(r"^ip\s+vpn-instance\s+(\S+)", re.IGNORECASE)

# --- 変換・配布・優先制御・暗号化 -----------------------------------------------

_NAT_GROUP = re.compile(r"^nat address-group\s+(\S+)(?:\s+(\S+)\s+(\S+))?", re.IGNORECASE)
_NAT_OUTBOUND = re.compile(
    r"^\s*nat outbound(?:\s+(?P<acl>\d+|\S+))?"
    r"(?:\s+address-group\s+(?P<group>\S+))?(?P<rest>.*)$",
    re.IGNORECASE,
)
_NAT_SERVER = re.compile(
    r"^\s*nat server\s+(?:protocol\s+(?P<protocol>\S+)\s+)?global\s+(?P<global>\S+)"
    r"(?:\s+(?P<gport>\S+))?\s+inside\s+(?P<inside>\S+)(?:\s+(?P<iport>\S+))?",
    re.IGNORECASE,
)
_IP_POOL = re.compile(r"^ip pool\s+(\S+)", re.IGNORECASE)
_CLASSIFIER = re.compile(r"^traffic classifier\s+(\S+)", re.IGNORECASE)
_BEHAVIOR = re.compile(r"^traffic behavior\s+(\S+)", re.IGNORECASE)
_TRAFFIC_POLICY = re.compile(r"^traffic policy\s+(\S+)", re.IGNORECASE)
_TRAFFIC_APPLY = re.compile(r"^\s*traffic-policy\s+(\S+)\s+(inbound|outbound)", re.IGNORECASE)
_IKE_PROPOSAL = re.compile(r"^ike proposal\s+(\S+)", re.IGNORECASE)
_IKE_PEER = re.compile(r"^ike peer\s+(\S+)", re.IGNORECASE)
_IPSEC_PROPOSAL = re.compile(r"^ipsec proposal\s+(\S+)", re.IGNORECASE)
#: `ipsec policy policy1 10 isakmp template use1` はテンプレートから作る応答側専用の形で、
#: 相手を持たない。出典: Huawei AR V200R010 CLI-based Configuration Guide - VPN
#: 「Configuring an IPSec Policy Using an IPSec Policy Template」
_IPSEC_POLICY = re.compile(
    r"^ipsec policy\s+(\S+)\s+(\S+)(?:\s+(\S+))?(?:\s+template\s+(\S+))?", re.IGNORECASE
)
_IPSEC_APPLY = re.compile(r"^\s+ipsec policy\s+(\S+)\s*$", re.IGNORECASE)
_IKE_KEY = re.compile(r"^\s*pre-shared-key\s+(?:cipher|simple)?\s*\S+", re.IGNORECASE)


def detect(text: str) -> Vendor | None:
    """VRP かどうか。名乗りがあればそれで、無ければ VRP にしか無い書き方で見る。

    `display current-configuration` には名乗りが出ないことが多い。名乗りだけで
    決めると、そういう config は NEC QX として読まれ、`allow-pass` の VLAN が
    読めずに黙って落ちる（読めていないのに読めた形で通る）。
    NEC QX にしか無い書き方が出ていれば、それは NEC の config なので名乗らない。
    """
    if _BANNER.search(text):
        return Vendor.HUAWEI
    if _NEC_ONLY.search(text):
        return None
    return Vendor.HUAWEI if _VRP_ONLY.search(text) else None


def parse(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.HUAWEI, os_name="VRP")
    # 口の束（port group）は、メンバーの口それぞれに書いた形へ開いてから読む
    text = expand_port_groups(text)

    found = _VERSION.search(text)
    if found:
        parsed.os_version = found.group(1)
    found = _MODEL_IN_VERSION.search(text) or _DISPLAY_MODEL.search(text)
    if found:
        parsed.model = found.group(1)
    found = _SYSNAME.search(text)
    if found:
        parsed.hostname = found.group(1).strip()
    else:
        found = _PROMPT.search(text)
        if found:
            parsed.hostname = found.group(1)
    found = _ESN.search(text)
    if found:
        parsed.serial_number = found.group(1)

    _read_interfaces_and_vlans(text, parsed)
    _read_vpn_instances(text, parsed)
    lldptable.parse(text, parsed)

    # 経路と経路の道具、トンネルは、undo 書式の共通読み手に渡す
    tunnels.parse_block_style(text, parsed)
    routing.parse_undoform(text, parsed)
    policy.parse_undoform(text, parsed)

    _read_nat(text, parsed)
    _read_dhcp(text, parsed)
    _read_qos(text, parsed)
    _read_ipsec(text, parsed)
    return parsed


# --- 口と VLAN ----------------------------------------------------------------


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
        batch = _VLAN_BATCH.match(raw)
        if batch:
            for number in vlanlist.parse_ranges(batch.group(1)):
                parsed.vlans.setdefault(number, "")
            current = None
            current_vlan = None
            continue
        vlan = _VLAN_HEAD.match(raw)
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
            name = _VLAN_NAME.match(raw)
            if name and not parsed.vlans.get(current_vlan):
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
    found = _LINK_TYPE.match(raw)
    if found:
        port.mode = found.group(1).lower()
        return
    found = _DEFAULT_VLAN.match(raw)
    if found:
        port.vlan = int(found.group(1))
        return
    found = _ALLOW_PASS.match(raw)
    if found:
        permitted.add(port.key)
        removing = bool(found.group(1))
        values = found.group(2)
        if vlanlist.is_all(values) or not values:
            # `undo ... vlan all` と、番号を書かない `undo ... vlan` は全部外す
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
    found = _ETH_TRUNK_MEMBER.match(raw)
    if found:
        port.lag_group = int(found.group(1))
        return
    found = _ETH_TRUNK_MODE.match(raw)
    if found:
        # lacp / lacp-static は相手と決める。manual は決め打ち
        port.lag_mode = "on" if found.group(1).lower().startswith("manual") else "active"
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
    """VRP は RD を `ipv4-family` の下に書く。1段深いだけで意味は同じ。"""
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


# --- 変換 -------------------------------------------------------------------


def _read_nat(text: str, parsed: ParsedConfig) -> None:
    """変換は口の下に書く。address-group は1行に範囲まで書く形もある。"""
    nat = ParsedNat()
    interface: str | None = None

    for raw in text.splitlines():
        head = _INTERFACE.match(raw)
        if head:
            interface = head.group(1)
            continue
        found = _NAT_GROUP.match(raw)
        if found:
            nat.pools.append(
                ParsedNatPool(name=found.group(1), start=found.group(2), end=found.group(3))
            )
            interface = None
            continue
        body = raw.strip()
        outbound = _NAT_OUTBOUND.match(raw)
        if outbound and body.lower().startswith("nat outbound"):
            pool = outbound.group("group")
            nat.rules.append(
                ParsedNatRule(
                    kind="dynamic",
                    direction="inside",
                    matched=outbound.group("acl") or "内側から出る通信",
                    translated=pool or (interface or "出口のアドレス"),
                    overload="no-pat" not in outbound.group("rest").lower(),
                    matched_is_name=bool(outbound.group("acl")),
                    raw=body,
                )
            )
            if interface and interface not in nat.outside_interfaces:
                nat.outside_interfaces.append(interface)
            continue
        server = _NAT_SERVER.match(raw)
        if server:
            nat.rules.append(
                ParsedNatRule(
                    kind="static-port" if server.group("gport") else "static",
                    direction="outside",
                    matched=server.group("global"),
                    translated=server.group("inside"),
                    protocol=server.group("protocol"),
                    matched_is_name=False,
                    raw=body,
                )
            )
            if interface and interface not in nat.outside_interfaces:
                nat.outside_interfaces.append(interface)

    if nat.rules or nat.pools:
        nat.inside_interfaces = [
            i.name for i in parsed.interfaces if i.name not in nat.outside_interfaces
        ]
        parsed.nat = nat
        _mark_acl_used(parsed, [rule.matched for rule in nat.rules], "NAT")


# --- 配布 -------------------------------------------------------------------


def _read_dhcp(text: str, parsed: ParsedConfig) -> None:
    dhcp = ParsedDhcp()
    current: ParsedDhcpPool | None = None

    for raw in text.splitlines():
        head = _IP_POOL.match(raw)
        if head:
            current = ParsedDhcpPool(name=f"ip pool {head.group(1)}")
            dhcp.pools.append(current)
            continue
        if not raw.startswith((" ", "\t")):
            current = None
        if current is None:
            continue
        body = raw.strip()
        found = re.match(r"^network\s+(\S+)(?:\s+mask\s+(\S+))?", body, re.IGNORECASE)
        if found:
            current.network = (
                f"{found.group(1)} {found.group(2)}" if found.group(2) else found.group(1)
            )
            continue
        found = re.match(r"^gateway-list\s+(.+)$", body, re.IGNORECASE)
        if found:
            current.default_routers.extend(found.group(1).split())
            continue
        found = re.match(r"^dns-list\s+(.+)$", body, re.IGNORECASE)
        if found:
            current.dns_servers.extend(found.group(1).split())
            continue
        found = re.match(r"^domain-name\s+(\S+)", body, re.IGNORECASE)
        if found:
            current.domain = found.group(1)
            continue
        found = re.match(r"^excluded-ip-address\s+(\S+)(?:\s+(\S+))?", body, re.IGNORECASE)
        if found:
            dhcp.excluded.append((found.group(1), found.group(2) or found.group(1)))
            continue
        found = re.match(r"^lease\s+(.+)$", body, re.IGNORECASE)
        if found:
            current.lease = found.group(1)

    for pool in dhcp.pools:
        # gateway-list のアドレスは、機器が自動で配らない側に入れる
        pool.gateway_reserved = True
    _read_interface_dhcp(text, parsed, dhcp)

    if dhcp.pools or dhcp.relays:
        parsed.dhcp = dhcp


_INTERFACE_HEAD = re.compile(r"^interface\s+(\S+)", re.IGNORECASE)
_INTERFACE_DHCP = re.compile(
    r"^(dhcp select (?P<select>interface|relay|global)"
    r"|dhcp server (?P<key>gateway-list|dns-list|domain-name|lease|excluded-ip-address)"
    r"\s+(?P<value>.+)"
    r"|dhcp relay server-ip\s+(?P<relay>\S+)"
    r"|vrrp vrid \d+ virtual-ip\s+(?P<vip>\S+))\s*$",
    re.IGNORECASE,
)


def _read_interface_dhcp(text: str, parsed: ParsedConfig, dhcp: ParsedDhcp) -> None:
    """口の下に書く DHCP を読む。口のアドレスの区画を配る形（interface）と、他所へ回す形（relay）。

    - `dhcp select interface`: 口のアドレスの区画を配る。`dhcp server gateway-list` を書かなければ、
      VRRP の仮想アドレス（最初の1つ）、無ければ口のアドレスをゲートウェイとして配る
    - ゲートウェイのアドレスは、機器が自動で配らない側に入れる
    - `dhcp select relay` と `dhcp relay server-ip`: 他所のサーバーへ回す

    出典:
    - Huawei「S9300 and S9300X V200R025C00 Configuration Guide - IP Service」の
      「Configuring a DHCP Server」
    - 「FAT AP / Cloud AP V200R023C00 コマンドリファレンス」（中国語版）の
      「dhcp server gateway-list」
    - 「S200, S300, S500, and S600 V600R025C10 Configuration Guide」の
      「Example for Configuring a DHCPv4 Server Based on an Interface Address Pool」
    - S シリーズ Troubleshooting Guide（V600）の DHCP の FAQ
    """
    addresses = {port.name: port.ip_address for port in parsed.interfaces}
    name: str | None = None
    lines: list[re.Match[str]] = []

    def close() -> None:
        if name is None:
            return
        found: dict[str, str] = {}
        selects = [m.group("select").lower() for m in lines if m.group("select")]
        vips = [m.group("vip") for m in lines if m.group("vip")]
        for m in lines:
            if m.group("relay"):
                dhcp.relays.append((name, m.group("relay")))
            if m.group("key"):
                found[m.group("key").lower()] = m.group("value").strip()
        if "interface" not in selects:
            return
        address = addresses.get(name)
        gateways = found.get("gateway-list", "").split() or vips[:1]
        if not gateways and address:
            gateways = [address.split("/")[0]]
        pool = ParsedDhcpPool(
            name=f"interface {name}",
            network=address,
            default_routers=gateways,
            dns_servers=found.get("dns-list", "").split(),
            domain=found.get("domain-name"),
            lease=found.get("lease"),
            gateway_reserved=True,
        )
        dhcp.pools.append(pool)
        for m in lines:
            if (m.group("key") or "").lower() == "excluded-ip-address":
                ends = m.group("value").split()
                dhcp.excluded.append((ends[0], ends[-1]))

    for raw in text.splitlines():
        head = _INTERFACE_HEAD.match(raw)
        if head:
            close()
            name, lines = head.group(1), []
            continue
        if not raw.startswith((" ", "\t")):
            close()
            name, lines = None, []
            continue
        if name is not None:
            found = _INTERFACE_DHCP.match(raw.strip())
            if found:
                lines.append(found)
    close()


# --- 優先制御 -------------------------------------------------------------------


def _read_qos(text: str, parsed: ParsedConfig) -> None:
    qos = ParsedQos()
    behaviors: dict[str, list[str]] = {}
    classifier: ParsedQosClass | None = None
    behavior: str | None = None
    qos_policy: ParsedQosPolicy | None = None
    interface: str | None = None

    for raw in text.splitlines():
        head = _CLASSIFIER.match(raw)
        if head:
            classifier = ParsedQosClass(name=head.group(1))
            qos.classes.append(classifier)
            behavior = qos_policy = interface = None
            continue
        head = _BEHAVIOR.match(raw)
        if head:
            behavior = head.group(1)
            behaviors.setdefault(behavior, [])
            classifier = qos_policy = interface = None
            continue
        head = _TRAFFIC_POLICY.match(raw)
        if head:
            qos_policy = ParsedQosPolicy(name=head.group(1))
            qos.policies.append(qos_policy)
            classifier = behavior = interface = None
            continue
        head = _INTERFACE.match(raw)
        if head:
            interface = head.group(1)
            classifier = behavior = qos_policy = None
            continue

        body = raw.strip()
        if classifier is not None and body.lower().startswith("if-match"):
            classifier.matches.append(body)
            continue
        if behavior is not None and body and not body.startswith("#"):
            behaviors[behavior].append(body)
            continue
        if qos_policy is not None:
            found = re.match(r"^classifier\s+(\S+)\s+behavior\s+(\S+)", body, re.IGNORECASE)
            if found:
                qos_policy.classes.append(
                    ParsedQosAction(class_name=found.group(1), actions=[found.group(2)])
                )
            continue
        applied = _TRAFFIC_APPLY.match(raw)
        if applied and interface:
            direction = "input" if applied.group(2).lower() == "inbound" else "output"
            qos.applied.append((interface, direction, applied.group(1)))

    for policy_entry in qos.policies:
        for action in policy_entry.classes:
            action.actions.extend(behaviors.get(action.actions[0], []))

    if qos.policies or qos.classes:
        parsed.qos = qos


# --- 暗号化 -------------------------------------------------------------------


def _read_ipsec(text: str, parsed: ParsedConfig) -> None:
    """VRP は相手を `ike peer 名前` の塊にまとめ、policy 側からその名前を指す。

    Cisco の isakmp policy / transform-set / crypto map と役割が同じなので、
    そこへ読み替える。名前だけでは相手が分からないので、塊の中の宛先を
    覚えておいて後で渡す。
    """
    crypto = ParsedCrypto()
    ike: ParsedIkePolicy | None = None
    transform: ParsedTransformSet | None = None
    entry: ParsedCryptoMapEntry | None = None
    interface: str | None = None
    peer_name: str | None = None
    peers: dict[str, str] = {}

    for raw in text.splitlines():
        head = _IKE_PROPOSAL.match(raw)
        if head:
            ike = ParsedIkePolicy(number=head.group(1), version=2)
            crypto.ike_policies.append(ike)
            transform = entry = interface = peer_name = None
            continue
        head = _IPSEC_PROPOSAL.match(raw)
        if head:
            transform = ParsedTransformSet(name=head.group(1))
            crypto.transform_sets.append(transform)
            ike = entry = interface = peer_name = None
            continue
        head = _IPSEC_POLICY.match(raw)
        if head:
            entry = ParsedCryptoMapEntry(
                name=head.group(1), seq=head.group(2), kind=(head.group(3) or "isakmp").lower()
            )
            if head.group(4):
                entry.kind = f"{entry.kind} template"
                entry.profile = head.group(4)
            crypto.maps.append(entry)
            ike = transform = interface = peer_name = None
            continue
        head = _IKE_PEER.match(raw)
        if head:
            peer_name = head.group(1)
            ike = transform = entry = interface = None
            continue
        head = _INTERFACE.match(raw)
        if head:
            interface = head.group(1)
            ike = transform = entry = peer_name = None
            continue
        if raw and not raw.startswith((" ", "\t")):
            ike = transform = entry = peer_name = interface = None

        body = raw.strip()
        if ike is not None:
            _ike_detail(ike, body)
            continue
        if transform is not None:
            _transform_detail(transform, body)
            continue
        if peer_name is not None:
            found = re.match(r"^remote-address\s+(\S+)", body, re.IGNORECASE)
            if found:
                peers[peer_name] = found.group(1)
            elif _IKE_KEY.match(body):
                crypto.keyed_peers.append(peer_name)
            continue
        if entry is not None:
            _policy_detail(entry, body)
            continue
        applied = _IPSEC_APPLY.match(raw)
        if applied and interface:
            crypto.applied.append((interface, applied.group(1)))

    for item in crypto.maps:
        address = peers.get(item.profile or "")
        if address and address not in item.peers:
            item.peers.append(address)
    # 合言葉を持つ相手も、名前ではなく宛先で持つ
    crypto.keyed_peers = [peers.get(name, name) for name in crypto.keyed_peers]

    if crypto.maps or crypto.ike_policies:
        parsed.crypto = crypto
        _mark_acl_used(parsed, [item.match_address for item in crypto.maps], "IPsec")


def _ike_detail(ike: ParsedIkePolicy, body: str) -> None:
    found = re.match(r"^encryption-algorithm\s+(\S+)", body, re.IGNORECASE)
    if found:
        ike.encryption = found.group(1)
        return
    found = re.match(r"^authentication-algorithm\s+(\S+)", body, re.IGNORECASE)
    if found:
        ike.hash = found.group(1)
        return
    found = re.match(r"^dh\s+group(\S+)", body, re.IGNORECASE)
    if found:
        ike.group = found.group(1)
        return
    found = re.match(r"^authentication-method\s+(\S+)", body, re.IGNORECASE)
    if found:
        ike.authentication = found.group(1)
        return
    found = re.match(r"^sa duration\s+(\d+)", body, re.IGNORECASE)
    if found:
        ike.lifetime = int(found.group(1))


def _transform_detail(transform: ParsedTransformSet, body: str) -> None:
    found = re.match(
        r"^(esp|ah)\s+(?:encryption|authentication)-algorithm\s+(.+)$", body, re.IGNORECASE
    )
    if found:
        # Cisco の transform-set と読み比べられるよう、esp- / ah- を頭に付ける
        for value in found.group(2).split():
            transform.transforms.append(f"{found.group(1).lower()}-{value}")
        return
    found = re.match(r"^encapsulation-mode\s+(\S+)", body, re.IGNORECASE)
    if found:
        transform.mode = found.group(1)


def _policy_detail(entry: ParsedCryptoMapEntry, body: str) -> None:
    found = re.match(r"^proposal\s+(.+)$", body, re.IGNORECASE)
    if found:
        entry.transform_sets.extend(found.group(1).split())
        return
    found = re.match(r"^security acl\s+(\S+)", body, re.IGNORECASE)
    if found:
        entry.match_address = found.group(1)
        return
    found = re.match(r"^ike-peer\s+(\S+)", body, re.IGNORECASE)
    if found:
        entry.profile = found.group(1)
        return
    found = re.match(r"^pfs\s+(\S+)", body, re.IGNORECASE)
    if found:
        entry.pfs = found.group(1)


def _mark_acl_used(parsed: ParsedConfig, names: list[str | None], by: str) -> None:
    """変換や暗号化で使っている ACL は「どこにも当たっていない」ではない。"""
    by_name = {acl.name: acl for acl in parsed.acls}
    for name in names:
        acl = by_name.get(name or "")
        if acl is not None and by not in acl.referenced_by:
            acl.referenced_by.append(by)


def _prefixlen(mask: str) -> int:
    """`24` とも `255.255.255.0` とも書かれる。"""
    if "." not in mask:
        return int(mask)
    return sum(bin(int(part)).count("1") for part in mask.split("."))


# --- まとめて設定する口（port group）------------------------------------------

#: その場限りの口の束。`port-group group-member Ethernet2/0/1 to Ethernet2/0/5`
_PORT_GROUP_TEMPORARY = re.compile(r"^port-group\s+group-member\s+(.+?)\s*$", re.IGNORECASE)
#: 名前を付けた口の束。`port-group pg1` の下に `group-member …` が並ぶ
_PORT_GROUP_NAMED = re.compile(r"^port-group\s+(?!group-member\b)(\S+)\s*$", re.IGNORECASE)
_GROUP_MEMBER = re.compile(r"^\s+group-member\s+(.+?)\s*$", re.IGNORECASE)
#: `Ethernet2/0/1 to Ethernet2/0/5`。`to` の前後は同じ種類で、後ろが大きい
_MEMBER_RANGE = re.compile(
    r"^(?P<head>[A-Za-z][\w\-]*)\s*(?P<middle>[\d/]*?)(?P<first>\d+)"
    r"(?:\s+to\s+[A-Za-z][\w\-]*\s*[\d/]*?(?P<last>\d+))?$",
    re.IGNORECASE,
)


def _group_members(spec: str) -> list[str]:
    """`Ethernet2/0/1 to Ethernet2/0/5` を口の名前の一覧にする。

    `&<1-10>` の並べ書き（空白区切りで複数の範囲）にも対応する。

    出典: Huawei NetEngine AR600 Command Reference「port-group group-member」
    （`port-group group-member ethernet 2/0/0 to ethernet 2/0/1`）と、
    CloudEngine 16800 Configuration Guide「Configuring a Port Group」。
    """
    names: list[str] = []
    for part in re.split(r"(?i)(?<=\d)\s+(?!to\b)", spec.strip()):
        found = _MEMBER_RANGE.match(re.sub(r"\s+", " ", part.strip()))
        if not found:
            names.append(part.strip())
            continue
        head, middle = found.group("head"), found.group("middle")
        first = int(found.group("first"))
        last = int(found.group("last")) if found.group("last") else first
        if last < first or last - first > 512:
            names.append(part.strip())
            continue
        names.extend(f"{head}{middle}{number}" for number in range(first, last + 1))
    return names


def expand_port_groups(text: str) -> str:
    """口の束への設定を、メンバーの口それぞれに書いた形へ開く。

    公式には「port group の設定は確認も保存もできない」とあり、
    `display current-configuration` には出てこない。出てくるのは手順書や
    投入用のコマンド列をそのまま渡されたときなので、そこで口を
    読み落とさないようにするためのもの。

    出典: Huawei CloudEngine 16800 Configuration Guide「Configuring a Port
    Group」（"you cannot check or save the port group configuration"）。
    """
    lines = text.splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        raw = lines[index]
        temporary = _PORT_GROUP_TEMPORARY.match(raw)
        named = _PORT_GROUP_NAMED.match(raw)
        if not (temporary or named):
            out.append(raw)
            index += 1
            continue

        members = _group_members(temporary.group(1)) if temporary else []
        body: list[str] = []
        index += 1
        while index < len(lines):
            line = lines[index]
            if line and not line[0].isspace():
                break
            member = _GROUP_MEMBER.match(line)
            if member:
                members.extend(_group_members(member.group(1)))
            elif line.strip() and line.strip().lower() not in ("quit", "commit", "return"):
                body.append(line)
            index += 1

        for name in members:
            out.append(f"interface {name}")
            out.extend(body)
    return "\n".join(out)
