"""L3（経路・OSPF・BGP・ゲートウェイ冗長）を Config から読む。

インターフェースや VLAN と違い、L3 は「どこと繋がるか」ではなく
「どう届くか」を決めている。ここが読めていないと、機器を1台足すときに
何を書けばいいのかも、いまの構成のどこが危ういのかも言えない。

ベンダーごとに書き方は違うが、意味は同じところへ落ちる。差はこの
モジュールの中で吸収して、上位には同じ形（base.Parsed* ）で渡す。

読めなかったものは黙って捨てる。実機の出力には show の見出しや折り返しが
混ざるため、1行読めないだけで全体を諦めるほうが困る。
"""

from __future__ import annotations

import ipaddress
import re

from app.services.configparse.base import (
    ParsedBgp,
    ParsedBgpNeighbor,
    ParsedConfig,
    ParsedOspf,
    ParsedOspfArea,
    ParsedPolicy,
    ParsedRedundancy,
    ParsedRoute,
    ParsedRoutingProtocol,
)
from app.services.configparse.ifname import expand

# ---------------------------------------------------------------------------
# 共通の道具
# ---------------------------------------------------------------------------


def to_prefix(address: str, mask: str | None = None) -> str | None:
    """`10.0.0.0 255.255.255.0` も `10.0.0.0/24` も `10.0.0.0/24` にそろえる。

    ベンダーごとに書き方が違うだけで意味は同じなので、比べられる形に寄せる。
    ここでそろえておかないと、「同じ経路が両側にあるか」を後で照合できない。
    """
    text = address.strip()
    try:
        if mask:
            mask = mask.strip()
            if mask.startswith("/"):
                return str(ipaddress.ip_network(f"{text}{mask}", strict=False))
            # ワイルドカードマスク（0.0.0.255）は反転してからネットマスクにする
            if _is_wildcard(mask):
                mask = _invert(mask)
            return str(ipaddress.ip_network(f"{text}/{mask}", strict=False))
        if "/" in text:
            return str(ipaddress.ip_network(text, strict=False))
        return str(ipaddress.ip_network(f"{text}/32", strict=False))
    except ValueError:
        return None


def _is_wildcard(mask: str) -> bool:
    """0.0.0.255 のような、先頭が 0 の側から立つ書き方かどうか。"""
    try:
        first = int(mask.split(".")[0])
    except (ValueError, IndexError):
        return False
    return first == 0 and mask != "0.0.0.0"


def _invert(mask: str) -> str:
    parts = [255 - int(octet) for octet in mask.split(".")]
    return ".".join(str(p) for p in parts)


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _blocks(text: str, head: re.Pattern[str]) -> list[tuple[re.Match[str], list[str]]]:
    """`router ospf 1` のような見出しと、そこにぶら下がる字下げ行を取り出す。"""
    out: list[tuple[re.Match[str], list[str]]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        match = head.match(line)
        if match:
            current = []
            out.append((match, current))
            continue
        if current is None:
            continue
        if line.strip() == "" or line.startswith((" ", "\t")):
            if line.strip():
                current.append(line.strip())
            continue
        current = None
    return out


# ---------------------------------------------------------------------------
# Cisco IOS / IOS-XE / NX-OS
# ---------------------------------------------------------------------------

_IOS_ROUTE = re.compile(
    r"^ip route\s+(?:vrf\s+(?P<vrf>\S+)\s+)?"
    r"(?P<dest>\d+\.\d+\.\d+\.\d+)\s+(?P<mask>\d+\.\d+\.\d+\.\d+)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
_IOS_ROUTER_OSPF = re.compile(r"^router ospf\s+(\S+)", re.IGNORECASE)
_IOS_ROUTER_BGP = re.compile(r"^router bgp\s+(\S+)", re.IGNORECASE)
_IOS_INTERFACE = re.compile(r"^interface\s+(\S+)", re.IGNORECASE)


def parse_cisco(text: str, parsed: ParsedConfig) -> None:
    _cisco_routes(text, parsed)
    _cisco_ospf(text, parsed)
    _cisco_bgp(text, parsed)
    _cisco_fhrp(text, parsed)
    parse_other_protocols(text, parsed)
    parse_policies(text, parsed)


def _cisco_routes(text: str, parsed: ParsedConfig) -> None:
    for line in text.splitlines():
        match = _IOS_ROUTE.match(line.strip())
        if not match:
            continue
        destination = to_prefix(match.group("dest"), match.group("mask"))
        if destination is None:
            continue
        route = ParsedRoute(destination=destination, vrf=match.group("vrf"))
        for token in match.group("rest").split():
            if _is_ip(token):
                route.next_hop = token
            elif token.isdigit():
                route.distance = int(token)
            elif re.match(r"^[A-Za-z]", token) and token.lower() not in {"name", "tag", "track"}:
                route.interface = expand(token)
        parsed.routes.append(route)


def _cisco_ospf(text: str, parsed: ParsedConfig) -> None:
    for head, body in _blocks(text, _IOS_ROUTER_OSPF):
        ospf = parsed.ospf or ParsedOspf()
        ospf.process_id = ospf.process_id or head.group(1)
        for line in body:
            router_id = re.match(r"^router-id\s+(\S+)", line, re.IGNORECASE)
            if router_id:
                ospf.router_id = router_id.group(1)
                continue
            network = re.match(r"^network\s+(\S+)\s+(\S+)\s+area\s+(\S+)", line, re.IGNORECASE)
            if network:
                prefix = to_prefix(network.group(1), network.group(2))
                if prefix:
                    _add_area(ospf, network.group(3), prefix)
                continue
            if re.match(r"^passive-interface\s+default", line, re.IGNORECASE):
                ospf.passive_default = True
                continue
            passive = re.match(r"^(?:no\s+)?passive-interface\s+(\S+)", line, re.IGNORECASE)
            if passive:
                ospf.passive_interfaces.append(expand(passive.group(1)))
                continue
            redistribute = re.match(r"^redistribute\s+(\S+)", line, re.IGNORECASE)
            if redistribute:
                ospf.redistributes.append(redistribute.group(1))
        parsed.ospf = ospf

    # NX-OS と IOS-XE は、エリアをインターフェース側に書く
    for head, body in _blocks(text, _IOS_INTERFACE):
        for line in body:
            match = re.match(r"^ip (?:router )?ospf\s+(\S+)\s+area\s+(\S+)", line, re.IGNORECASE)
            if not match:
                continue
            ospf = parsed.ospf or ParsedOspf()
            ospf.process_id = ospf.process_id or match.group(1)
            _add_area(ospf, match.group(2), f"interface {expand(head.group(1))}")
            parsed.ospf = ospf


def _add_area(ospf: ParsedOspf, area: str, network: str) -> None:
    for existing in ospf.areas:
        if existing.area == area:
            if network not in existing.networks:
                existing.networks.append(network)
            return
    ospf.areas.append(ParsedOspfArea(area=area, networks=[network]))


def _cisco_bgp(text: str, parsed: ParsedConfig) -> None:
    for head, body in _blocks(text, _IOS_ROUTER_BGP):
        bgp = parsed.bgp or ParsedBgp()
        bgp.local_as = bgp.local_as or head.group(1)
        for line in body:
            router_id = re.match(r"^bgp router-id\s+(\S+)", line, re.IGNORECASE)
            if router_id:
                bgp.router_id = router_id.group(1)
                continue
            neighbor = re.match(r"^neighbor\s+(\S+)\s+(.+)$", line, re.IGNORECASE)
            if neighbor:
                _add_neighbor(bgp, neighbor.group(1), neighbor.group(2))
                continue
            network = re.match(r"^network\s+(\S+)(?:\s+mask\s+(\S+))?", line, re.IGNORECASE)
            if network:
                prefix = to_prefix(network.group(1), network.group(2))
                if prefix and prefix not in bgp.networks:
                    bgp.networks.append(prefix)
        parsed.bgp = bgp


def _add_neighbor(bgp: ParsedBgp, address: str, rest: str) -> None:
    found = next((n for n in bgp.neighbors if n.address == address), None)
    if found is None:
        found = ParsedBgpNeighbor(address=address)
        bgp.neighbors.append(found)
    remote = re.match(r"^remote-as\s+(\S+)", rest, re.IGNORECASE)
    if remote:
        found.remote_as = remote.group(1)
    description = re.match(r"^description\s+(.+)$", rest, re.IGNORECASE)
    if description:
        found.description = description.group(1).strip()


def _cisco_fhrp(text: str, parsed: ParsedConfig) -> None:
    for head, body in _blocks(text, _IOS_INTERFACE):
        interface = expand(head.group(1))
        groups: dict[tuple[str, str | None], ParsedRedundancy] = {}
        for line in body:
            match = re.match(
                r"^(standby|vrrp)\s+(?:(\d+)\s+)?(ip|priority|address)\s+(\S+)",
                line,
                re.IGNORECASE,
            )
            if not match:
                continue
            protocol = "hsrp" if match.group(1).lower() == "standby" else "vrrp"
            key = (protocol, match.group(2))
            entry = groups.get(key)
            if entry is None:
                entry = ParsedRedundancy(
                    interface=interface, group=match.group(2), protocol=protocol
                )
                groups[key] = entry
            keyword, value = match.group(3).lower(), match.group(4)
            if keyword in {"ip", "address"}:
                entry.virtual_ip = value
            elif keyword == "priority" and value.isdigit():
                entry.priority = int(value)
        parsed.redundancy.extend(groups.values())


# ---------------------------------------------------------------------------
# NEC QX（H3C Comware 系）
# ---------------------------------------------------------------------------

_QX_ROUTE = re.compile(
    r"^ip route-static\s+(?:vpn-instance\s+(?P<vrf>\S+)\s+)?"
    r"(?P<dest>\d+\.\d+\.\d+\.\d+)\s+(?P<mask>[\d.]+)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
_QX_OSPF = re.compile(r"^ospf(?:\s+(\d+))?(?:\s+router-id\s+(\S+))?\s*$", re.IGNORECASE)
_QX_BGP = re.compile(r"^bgp\s+(\S+)\s*$", re.IGNORECASE)
_QX_INTERFACE = re.compile(r"^interface\s+(\S+)", re.IGNORECASE)


def parse_comware(text: str, parsed: ParsedConfig) -> None:
    parse_other_protocols(text, parsed)
    parse_policies(text, parsed)
    for line in text.splitlines():
        match = _QX_ROUTE.match(line.strip())
        if not match:
            continue
        destination = to_prefix(match.group("dest"), match.group("mask"))
        if destination is None:
            continue
        route = ParsedRoute(destination=destination, vrf=match.group("vrf"))
        for token in match.group("rest").split():
            if _is_ip(token):
                route.next_hop = token
            elif token.isdigit():
                route.distance = int(token)
            elif re.match(r"^[A-Za-z]", token) and token.lower() not in {"preference"}:
                route.interface = expand(token)
        parsed.routes.append(route)

    # Comware はエリアが入れ子（ospf 1 → area 0.0.0.0 → network ...）
    for head, body in _blocks(text, _QX_OSPF):
        ospf = parsed.ospf or ParsedOspf()
        ospf.process_id = ospf.process_id or (head.group(1) or "1")
        ospf.router_id = ospf.router_id or head.group(2)
        area = None
        for line in body:
            router_id = re.match(r"^router-id\s+(\S+)", line, re.IGNORECASE)
            if router_id:
                ospf.router_id = router_id.group(1)
                continue
            found = re.match(r"^area\s+(\S+)", line, re.IGNORECASE)
            if found:
                area = found.group(1)
                _add_area(ospf, area, "")
                continue
            network = re.match(r"^network\s+(\S+)\s+(\S+)", line, re.IGNORECASE)
            if network and area is not None:
                prefix = to_prefix(network.group(1), network.group(2))
                if prefix:
                    _add_area(ospf, area, prefix)
        for entry in ospf.areas:  # 見出しだけの空欄を落とす
            entry.networks = [n for n in entry.networks if n]
        parsed.ospf = ospf

    for head, body in _blocks(text, _QX_BGP):
        bgp = parsed.bgp or ParsedBgp()
        bgp.local_as = bgp.local_as or head.group(1)
        for line in body:
            router_id = re.match(r"^router-id\s+(\S+)", line, re.IGNORECASE)
            if router_id:
                bgp.router_id = router_id.group(1)
                continue
            peer = re.match(r"^peer\s+(\S+)\s+(.+)$", line, re.IGNORECASE)
            if peer:
                rest = peer.group(2)
                as_number = re.match(r"^as-number\s+(\S+)", rest, re.IGNORECASE)
                _add_neighbor(
                    bgp,
                    peer.group(1),
                    f"remote-as {as_number.group(1)}" if as_number else rest,
                )
        parsed.bgp = bgp

    for head, body in _blocks(text, _QX_INTERFACE):
        interface = expand(head.group(1))
        groups: dict[str | None, ParsedRedundancy] = {}
        for line in body:
            match = re.match(
                r"^vrrp vrid\s+(\d+)\s+(virtual-ip|priority)\s+(\S+)", line, re.IGNORECASE
            )
            if not match:
                continue
            entry = groups.setdefault(
                match.group(1),
                ParsedRedundancy(interface=interface, group=match.group(1), protocol="vrrp"),
            )
            if match.group(2).lower() == "virtual-ip":
                entry.virtual_ip = match.group(3)
            elif match.group(3).isdigit():
                entry.priority = int(match.group(3))
        parsed.redundancy.extend(groups.values())


# ---------------------------------------------------------------------------
# NEC IX
# ---------------------------------------------------------------------------

_IX_ROUTE = re.compile(
    r"^ip route\s+(?P<dest>\d+\.\d+\.\d+\.\d+)/(?P<length>\d+)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)


def parse_ix(text: str, parsed: ParsedConfig) -> None:
    for line in text.splitlines():
        match = _IX_ROUTE.match(line.strip())
        if not match:
            continue
        destination = to_prefix(f"{match.group('dest')}/{match.group('length')}")
        if destination is None:
            continue
        route = ParsedRoute(destination=destination)
        for token in match.group("rest").split():
            if _is_ip(token):
                route.next_hop = token
            elif token.isdigit():
                route.distance = int(token)
            elif re.match(r"^[A-Za-z]", token):
                route.interface = expand(token)
        parsed.routes.append(route)


# ---------------------------------------------------------------------------
# Juniper Junos
# ---------------------------------------------------------------------------


def parse_junos(text: str, parsed: ParsedConfig) -> None:
    # set 形式（現場で貼られるのはたいていこちら）
    for line in text.splitlines():
        stripped = line.strip()
        route = re.match(
            r"^set routing-options static route\s+(\S+)\s+next-hop\s+(\S+)",
            stripped,
            re.IGNORECASE,
        )
        if route:
            destination = to_prefix(route.group(1))
            if destination:
                parsed.routes.append(ParsedRoute(destination=destination, next_hop=route.group(2)))
            continue
        area = re.match(
            r"^set protocols ospf area\s+(\S+)\s+interface\s+(\S+)", stripped, re.IGNORECASE
        )
        if area:
            ospf = parsed.ospf or ParsedOspf()
            _add_area(ospf, area.group(1), f"interface {area.group(2)}")
            parsed.ospf = ospf
            continue
        local_as = re.match(r"^set routing-options autonomous-system\s+(\S+)", stripped, re.I)
        if local_as:
            bgp = parsed.bgp or ParsedBgp()
            bgp.local_as = local_as.group(1)
            parsed.bgp = bgp
            continue
        neighbor = re.match(
            r"^set protocols bgp group \S+ neighbor\s+(\S+)(?:\s+peer-as\s+(\S+))?",
            stripped,
            re.IGNORECASE,
        )
        if neighbor:
            bgp = parsed.bgp or ParsedBgp()
            _add_neighbor(
                bgp,
                neighbor.group(1),
                f"remote-as {neighbor.group(2)}" if neighbor.group(2) else "",
            )
            parsed.bgp = bgp


# ---------------------------------------------------------------------------
# Fortinet FortiOS
# ---------------------------------------------------------------------------


def parse_fortios(text: str, parsed: ParsedConfig) -> None:
    """`config router static` の edit ブロックを読む。"""
    in_static = False
    current: ParsedRoute | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if re.match(r"^config router static$", line, re.IGNORECASE):
            in_static = True
            continue
        if not in_static:
            continue
        if re.match(r"^end$", line, re.IGNORECASE):
            in_static = False
            current = None
            continue
        if re.match(r"^edit\s+\d+", line, re.IGNORECASE):
            current = ParsedRoute(destination="0.0.0.0/0")
            continue
        if current is None:
            continue
        destination = re.match(r'^set dst\s+"?([\d.]+)"?\s+"?([\d.]+)"?', line, re.IGNORECASE)
        if destination:
            prefix = to_prefix(destination.group(1), destination.group(2))
            if prefix:
                current.destination = prefix
            continue
        gateway = re.match(r'^set gateway\s+"?([\d.]+)"?', line, re.IGNORECASE)
        if gateway:
            current.next_hop = gateway.group(1)
            continue
        device = re.match(r'^set device\s+"?([^"\s]+)"?', line, re.IGNORECASE)
        if device:
            current.interface = device.group(1)
            continue
        distance = re.match(r"^set distance\s+(\d+)", line, re.IGNORECASE)
        if distance:
            current.distance = int(distance.group(1))
            continue
        if re.match(r"^next$", line, re.IGNORECASE):
            parsed.routes.append(current)
            current = None


# ---------------------------------------------------------------------------
# OSPF / BGP 以外の経路プロトコル
# ---------------------------------------------------------------------------

_IOS_EIGRP = re.compile(r"^router eigrp\s+(\S+)", re.IGNORECASE)
_IOS_RIP = re.compile(r"^router rip\s*$", re.IGNORECASE)
_IOS_ISIS = re.compile(r"^router isis(?:\s+(\S+))?", re.IGNORECASE)
_QX_RIP = re.compile(r"^rip(?:\s+(\d+))?\s*$", re.IGNORECASE)
_QX_ISIS = re.compile(r"^isis(?:\s+(\d+))?\s*$", re.IGNORECASE)


def parse_other_protocols(text: str, parsed: ParsedConfig) -> None:
    """EIGRP / RIP / IS-IS を読む。

    現場ではまだ動いている。読めないと、その機器の経路がどこから来ている
    のか説明できず、更改のときに同じものを再現できない。
    """
    for head, body in _blocks(text, _IOS_EIGRP):
        parsed.protocols.append(_protocol("eigrp", head.group(1), body))
    for _head, body in _blocks(text, _IOS_RIP):
        parsed.protocols.append(_protocol("rip", None, body))
    for head, body in _blocks(text, _IOS_ISIS):
        parsed.protocols.append(_protocol("isis", head.group(1), body))
    for head, body in _blocks(text, _QX_RIP):
        parsed.protocols.append(_protocol("rip", head.group(1), body))
    for head, body in _blocks(text, _QX_ISIS):
        parsed.protocols.append(_protocol("isis", head.group(1), body))


def _classful(address: str, mask: str | None) -> str | None:
    """マスクを書かない流儀（RIP や EIGRP）を、昔ながらの区切りで補う。

    書いていないものを /32 と読むと、1台ぶんの意味になってしまう。
    実際には「その帯ぜんぶ」を指しているので、先頭の数字から区切りを決める。
    """
    if mask:
        return to_prefix(address, mask)
    try:
        first = int(address.split(".")[0])
    except (ValueError, IndexError):
        return to_prefix(address)
    length = 8 if first < 128 else (16 if first < 192 else 24)
    return to_prefix(address, f"/{length}")


def _protocol(name: str, identifier: str | None, body: list[str]) -> ParsedRoutingProtocol:
    protocol = ParsedRoutingProtocol(name=name, identifier=identifier)
    for line in body:
        router_id = re.match(r"^(?:router-id|net)\s+(\S+)", line, re.IGNORECASE)
        if router_id:
            protocol.router_id = router_id.group(1)
            continue
        network = re.match(r"^network\s+(\S+)(?:\s+(\S+))?", line, re.IGNORECASE)
        if network:
            prefix = _classful(network.group(1), network.group(2))
            if prefix and prefix not in protocol.networks:
                protocol.networks.append(prefix)
            continue
        passive = re.match(r"^passive-interface\s+(\S+)", line, re.IGNORECASE)
        if passive:
            protocol.passive_interfaces.append(expand(passive.group(1)))
            continue
        redistribute = re.match(r"^(?:redistribute|import-route)\s+(\S+)", line, re.IGNORECASE)
        if redistribute:
            protocol.redistributes.append(redistribute.group(1))
    return protocol


# ---------------------------------------------------------------------------
# 経路の選び方・配り方を決める道具
# ---------------------------------------------------------------------------

_ROUTE_MAP = re.compile(r"^route-map\s+(\S+)(?:\s+(permit|deny))?(?:\s+(\d+))?", re.IGNORECASE)
_PREFIX_LIST = re.compile(r"^ip prefix-list\s+(\S+)\s", re.IGNORECASE)
_QX_ROUTE_POLICY = re.compile(r"^route-policy\s+(\S+)\s+(permit|deny)\s+node\s+(\d+)", re.I)
_POLICY_APPLY = re.compile(r"^\s*ip policy route-map\s+(\S+)", re.IGNORECASE)


def parse_policies(text: str, parsed: ParsedConfig) -> None:
    """route-map / prefix-list と、その使われ方を拾う。

    「広告しているはずなのに相手に届かない」の原因はたいていここにある。
    中身の評価まではしないが、名前と、どこで使われているかは持っておく。
    """
    counts: dict[tuple[str, str], int] = {}
    for line in text.splitlines():
        stripped = line.strip()
        for pattern, kind in (
            (_ROUTE_MAP, "route-map"),
            (_PREFIX_LIST, "prefix-list"),
            (_QX_ROUTE_POLICY, "route-map"),
        ):
            match = pattern.match(stripped)
            if match:
                key = (kind, match.group(1))
                counts[key] = counts.get(key, 0) + 1
                break

    for (kind, name), count in counts.items():
        parsed.policies.append(ParsedPolicy(kind=kind, name=name, detail=f"{count} 行"))

    _mark_usage(text, parsed)


def _mark_usage(text: str, parsed: ParsedConfig) -> None:
    """どこで使われているかを、名前ごとに書き足す。

    使われていない道具が残っていること自体は珍しくないが、それが
    「消し忘れ」なのか「これから使う」のかは、使われ方を見ないと言えない。
    """
    by_name = {policy.name: policy for policy in parsed.policies}
    interface = None
    route_map = None
    for raw in text.splitlines():
        line = raw.rstrip()
        head = re.match(r"^interface\s+(\S+)", line, re.IGNORECASE)
        if head:
            interface, route_map = expand(head.group(1)), None
            continue
        in_map = _ROUTE_MAP.match(line.strip()) if not line.startswith((" ", "\t")) else None
        if in_map:
            interface, route_map = None, in_map.group(1)
            continue
        if not line.startswith((" ", "\t")) and line.strip():
            interface, route_map = None, None

        # route-map の中で参照されている道具にも、使われ方を書き足す
        referenced = re.match(
            r"^\s*match\s+ip\s+address\s+prefix-list\s+(\S+)", line, re.IGNORECASE
        )
        if referenced and route_map and referenced.group(1) in by_name:
            where = f"route-map {route_map}"
            if where not in by_name[referenced.group(1)].used_by:
                by_name[referenced.group(1)].used_by.append(where)
            continue

        applied = _POLICY_APPLY.match(line)
        if applied and applied.group(1) in by_name:
            where = f"interface {interface}" if interface else "policy"
            by_name[applied.group(1)].used_by.append(where)
            by_name[applied.group(1)].kind = "policy-based-routing"
            continue

        for pattern, label in (
            (r"^\s*(?:neighbor\s+\S+\s+)?route-map\s+(\S+)\s+(?:in|out)\b", "BGP"),
            (r"^\s*redistribute\s+\S+.*?route-map\s+(\S+)", "再配布"),
            (r"^\s*(?:neighbor\s+\S+\s+)?prefix-list\s+(\S+)\s+(?:in|out)\b", "BGP"),
        ):
            found = re.match(pattern, line, re.IGNORECASE)
            if found and found.group(1) in by_name:
                policy = by_name[found.group(1)]
                if label not in policy.used_by:
                    policy.used_by.append(label)
                break
