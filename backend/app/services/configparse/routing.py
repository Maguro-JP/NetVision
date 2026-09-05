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
    ParsedPolicyEntry,
    ParsedRedundancy,
    ParsedRoute,
    ParsedRoutingProtocol,
)
from app.services.configparse.ifname import expand

# ---------------------------------------------------------------------------
# 共通の道具
# ---------------------------------------------------------------------------


def to_prefix(address: str, mask: str | None = None, *, wildcard: bool | None = None) -> str | None:
    """`10.0.0.0 255.255.255.0` も `10.0.0.0/24` も `10.0.0.0/24` にそろえる。

    ベンダーごとに書き方が違うだけで意味は同じなので、比べられる形に寄せる。
    ここでそろえておかないと、「同じ経路が両側にあるか」を後で照合できない。

    2つ目の値がネットマスク（255.255.255.0）かワイルドカード（0.0.0.255）かは、
    書いてある場所で決まる。同じ `0.0.0.0` でも、経路の行では「全部」、
    OSPF の network 行では「この1台だけ」を指す。取り違えると意味が正反対に
    なるので、呼ぶ側が `wildcard` で伝える。伝えられないときだけ、
    先頭の桁から推し量る。

      wildcard=True   … 必ずワイルドカードとして読む（OSPF の network、ACL）
      wildcard=False  … 必ずネットマスクとして読む（経路、BGP の network）
      wildcard=None   … 分からないので推し量る
    """
    text = address.strip()
    if mask is None and len(text.split()) == 2:
        # `10.0.0.1 255.255.255.0` と1つにまとめて渡されることがある
        text, mask = text.split()
    try:
        if mask:
            mask = mask.strip()
            if mask.startswith("/"):
                return str(ipaddress.ip_network(f"{text}{mask}", strict=False))
            if wildcard is True or (wildcard is None and _is_wildcard(mask)):
                mask = _invert(mask)
            return str(ipaddress.ip_network(f"{text}/{mask}", strict=False))
        if "/" in text:
            return str(ipaddress.ip_network(text, strict=False))
        # 長さを書いていない＝その1つを指している。長さは種類で決まる
        return str(ipaddress.ip_network(f"{text}/{_host_length(text)}", strict=False))
    except ValueError:
        return None


def _host_length(text: str) -> int:
    """長さを書いていないときの、1つぶんの長さ。IPv6 は 128。"""
    try:
        return ipaddress.ip_address(text).max_prefixlen
    except ValueError:
        return 32


def _is_wildcard(mask: str) -> bool:
    """0.0.0.255 のような、先頭が 0 の側から立つ書き方かどうか。

    `0.0.0.0` と `255.255.255.255` は、どちらの読み方でも形が通ってしまう。
    ここでは推し量らない（呼ぶ側が `wildcard` で決める）。
    """
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
_IOS_ROUTE6 = re.compile(
    r"^ipv6 route\s+(?:vrf\s+(?P<vrf>\S+)\s+)?"
    r"(?P<dest>[0-9A-Fa-f:]+/\d+)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
_IOS_ROUTER_OSPF = re.compile(r"^router ospf\s+(\S+)", re.IGNORECASE)
_IOS_ROUTER_BGP = re.compile(r"^router bgp\s+(\S+)", re.IGNORECASE)
_IOS_INTERFACE = re.compile(r"^interface\s+(\S+)\s*$", re.IGNORECASE)


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
        destination = to_prefix(match.group("dest"), match.group("mask"), wildcard=False)
        if destination is None:
            continue
        route = ParsedRoute(destination=destination, vrf=match.group("vrf"))
        _read_route_options(match.group("rest"), route)
        parsed.routes.append(route)

    for line in text.splitlines():
        match = _IOS_ROUTE6.match(line.strip())
        if not match:
            continue
        destination = to_prefix(match.group("dest"))
        if destination is None:
            continue
        route = ParsedRoute(destination=destination, vrf=match.group("vrf"))
        _read_route_options(match.group("rest"), route)
        parsed.routes.append(route)


# name / tag / track は後ろに値を連れている。値まで読み飛ばさないと、
# その数字をアドミニストレイティブディスタンスと取り違える
_ROUTE_KEYWORDS_WITH_VALUE = {"name", "tag", "track", "metric"}
_ROUTE_KEYWORDS = {"permanent", "global", "multicast", "unicast", "dhcp"}


def _read_route_options(rest: str, route: ParsedRoute) -> None:
    tokens = rest.split()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()
        index += 1
        if lowered in _ROUTE_KEYWORDS_WITH_VALUE:
            index += 1
            continue
        if lowered in _ROUTE_KEYWORDS:
            continue
        if _is_ip(token):
            route.next_hop = token
        elif token.isdigit():
            route.distance = int(token)
        elif re.match(r"^[A-Za-z]", token):
            route.interface = expand(token)


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
                prefix = to_prefix(network.group(1), network.group(2), wildcard=True)
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
        family: str | None = None
        for line in body:
            found = re.match(r"^address-family\s+(.+)$", line, re.IGNORECASE)
            if found:
                family = found.group(1).strip().lower()
                continue
            if re.match(r"^exit-address-family\b", line, re.IGNORECASE):
                family = None
                continue
            router_id = re.match(r"^bgp router-id\s+(\S+)", line, re.IGNORECASE)
            if router_id:
                bgp.router_id = router_id.group(1)
                continue
            if _bgp_option(bgp, line):
                continue
            neighbor = re.match(r"^neighbor\s+(\S+)\s+(.+)$", line, re.IGNORECASE)
            if neighbor:
                _add_neighbor(bgp, neighbor.group(1), neighbor.group(2), family)
                continue
            network = re.match(r"^network\s+(\S+)(?:\s+mask\s+(\S+))?", line, re.IGNORECASE)
            if network:
                prefix = to_prefix(network.group(1), network.group(2), wildcard=False)
                if prefix and prefix not in bgp.networks:
                    bgp.networks.append(prefix)
        _inherit_peer_groups(bgp)
        parsed.bgp = bgp


#: 雛形から相手へ引き継がれる設定。相手側に書いてあれば、そちらが勝つ
_PEER_GROUP_INHERITED = (
    "remote_as",
    "update_source",
    "route_map_in",
    "route_map_out",
    "prefix_list_in",
    "prefix_list_out",
    "filter_list_in",
    "filter_list_out",
    "ebgp_multihop",
    "maximum_prefix",
)


def _inherit_peer_groups(bgp: ParsedBgp) -> None:
    """peer-group に書いた内容を、そこに属する相手へ写す。

    IOS は雛形に1回書けば、属する相手すべてに効く。写しておかないと
    「この相手だけ remote-as が無い」と、無いものを有ると言ってしまう。
    """
    templates = {n.address: n for n in bgp.neighbors if n.is_peer_group}
    if not templates:
        return
    for neighbor in bgp.neighbors:
        template = templates.get(neighbor.peer_group or "")
        if template is None or template is neighbor:
            continue
        for name in _PEER_GROUP_INHERITED:
            if getattr(neighbor, name) is None:
                setattr(neighbor, name, getattr(template, name))
        for name in ("next_hop_self", "password"):
            if getattr(template, name):
                setattr(neighbor, name, True)


def _bgp_option(bgp: ParsedBgp, line: str) -> bool:
    """相手ごとではない、BGP 全体の決めごと。"""
    redistribute = re.match(r"^redistribute\s+(\S+)", line, re.IGNORECASE)
    if redistribute:
        if redistribute.group(1) not in bgp.redistributes:
            bgp.redistributes.append(redistribute.group(1))
        return True
    aggregate = re.match(r"^aggregate-address\s+(\S+)(?:\s+(\d+\.\d+\.\d+\.\d+))?", line, re.I)
    if aggregate:
        prefix = to_prefix(aggregate.group(1), aggregate.group(2), wildcard=False)
        if prefix and prefix not in bgp.aggregates:
            bgp.aggregates.append(prefix)
        return True
    timers = re.match(r"^timers bgp\s+(.+)$", line, re.IGNORECASE)
    if timers:
        bgp.timers = timers.group(1).strip()
        return True
    paths = re.match(r"^maximum-paths\s+(\d+)", line, re.IGNORECASE)
    if paths:
        bgp.maximum_paths = int(paths.group(1))
        return True
    if re.match(r"^bgp log-neighbor-changes\b", line, re.IGNORECASE):
        bgp.log_neighbor_changes = True
        return True
    if re.match(r"^bgp always-compare-med\b", line, re.IGNORECASE):
        bgp.always_compare_med = True
        return True
    return False


#: 相手1台に対して書ける指定。BGP は相手ごとに設定が違うのが普通なので、
#: 「1台だけ抜けている」を見つけられるよう、相手単位で持つ
_NEIGHBOR_FLAGS = (
    (r"^remote-as\s+(\S+)", "remote_as"),
    (r"^description\s+(.+)$", "description"),
    (r"^update-source\s+(\S+)", "update_source"),
    (r"^peer-group\s+(\S+)", "peer_group"),
)


def _add_neighbor(bgp: ParsedBgp, address: str, rest: str, family: str | None = None) -> None:
    found = next((n for n in bgp.neighbors if n.address == address), None)
    if found is None:
        found = ParsedBgpNeighbor(address=address)
        bgp.neighbors.append(found)
    if family and family not in found.address_families:
        found.address_families.append(family)

    if re.match(r"^peer-group\s*$", rest, re.IGNORECASE):
        found.is_peer_group = True
        return

    for pattern, name in _NEIGHBOR_FLAGS:
        match = re.match(pattern, rest, re.IGNORECASE)
        if match:
            setattr(found, name, match.group(1).strip())
            return

    # 掛けている道具は、in と out で別物。まとめると片側の抜けが見えなくなる
    tool = re.match(
        r"^(route-map|prefix-list|filter-list)\s+(\S+)\s+(in|out)\b", rest, re.IGNORECASE
    )
    if tool:
        kind = tool.group(1).lower().replace("-", "_")
        setattr(found, f"{kind}_{tool.group(3).lower()}", tool.group(2))
        return

    if re.match(r"^password\b", rest, re.IGNORECASE):
        found.password = True
        return
    if re.match(r"^next-hop-self\b", rest, re.IGNORECASE):
        found.next_hop_self = True
        return
    if re.match(r"^shutdown\s*$", rest, re.IGNORECASE):
        found.shutdown = True
        return
    if re.match(r"^activate\s*$", rest, re.IGNORECASE):
        found.activated = True
        return
    multihop = re.match(r"^ebgp-multihop(?:\s+(\d+))?", rest, re.IGNORECASE)
    if multihop:
        found.ebgp_multihop = int(multihop.group(1)) if multihop.group(1) else 255
        return
    maximum = re.match(r"^maximum-prefix\s+(\d+)", rest, re.IGNORECASE)
    if maximum:
        found.maximum_prefix = int(maximum.group(1))


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

        # NX-OS は入れ子で書く。`hsrp 10` のあとに ip / priority が字下げで続く
        nested: ParsedRedundancy | None = None
        for line in body:
            head_line = re.match(r"^(hsrp|vrrp)\s+(\d+)\s*$", line.strip(), re.IGNORECASE)
            if head_line:
                protocol = head_line.group(1).lower()
                protocol = "hsrp" if protocol == "hsrp" else "vrrp"
                key = (protocol, head_line.group(2))
                nested = groups.get(key)
                if nested is None:
                    nested = ParsedRedundancy(
                        interface=interface, group=head_line.group(2), protocol=protocol
                    )
                    groups[key] = nested
                continue
            if nested is None:
                continue
            value_line = re.match(r"^(ip|address|priority)\s+(\S+)", line.strip(), re.IGNORECASE)
            if value_line is None:
                # 字下げの外に出たら、その塊はおしまい
                if line.strip() and not line.startswith((" ", "\t")):
                    nested = None
                continue
            keyword, value = value_line.group(1).lower(), value_line.group(2)
            if keyword in {"ip", "address"}:
                nested.virtual_ip = value
            elif value.isdigit():
                nested.priority = int(value)

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
_QX_INTERFACE = re.compile(r"^interface\s+(\S+)\s*$", re.IGNORECASE)


def parse_comware(text: str, parsed: ParsedConfig) -> None:
    parse_other_protocols(text, parsed)
    parse_policies(text, parsed)
    for line in text.splitlines():
        match = _QX_ROUTE.match(line.strip())
        if not match:
            continue
        destination = to_prefix(match.group("dest"), match.group("mask"), wildcard=False)
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
                prefix = to_prefix(network.group(1), network.group(2), wildcard=True)
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
    parse_policies(text, parsed)
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
    #: まとまり（group）側に書いた相手の AS。相手の行より先に出てくる
    group_peer_as: dict[str, str] = {}
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
        group_as = re.match(
            r"^set protocols bgp group (\S+) peer-as\s+(\S+)", stripped, re.IGNORECASE
        )
        if group_as:
            # Junos は相手の AS をまとまり（group）側に書ける。相手の行にしか
            # 書いていないものとして読むと、外の AS だと気づけない
            group_peer_as[group_as.group(1)] = group_as.group(2)
            continue
        neighbor = re.match(
            r"^set protocols bgp group (\S+) neighbor\s+(\S+)(?:\s+peer-as\s+(\S+))?",
            stripped,
            re.IGNORECASE,
        )
        if neighbor:
            bgp = parsed.bgp or ParsedBgp()
            remote = neighbor.group(3) or group_peer_as.get(neighbor.group(1))
            _add_neighbor(bgp, neighbor.group(2), f"remote-as {remote}" if remote else "")
            parsed.bgp = bgp


# ---------------------------------------------------------------------------
# Fortinet FortiOS
# ---------------------------------------------------------------------------


_FORTIOS_PBR_SETS = ("gateway", "output-device")
_FORTIOS_PBR_MATCHES = ("input-device", "src", "srcaddr", "dst", "dstaddr", "protocol", "action")


def parse_fortios_policy_routes(text: str, parsed: ParsedConfig) -> None:
    """`config router policy`（ポリシールーティング）を読む。

    どの口から入った・どこ宛ての通信を、どの相手（gateway）へ向けるか。
    普通の経路表より先に見られるので、ここに古い相手が残っていると
    経路表を直しても通信は戻らない。1つの edit を1行として持つ。
    """
    from app.services.configparse.fortitext import blocks

    for name, values in blocks(text, "router policy"):
        if values.get("status", "enable").lower() == "disable":
            continue
        entry = ParsedPolicyEntry(seq=name, action="permit", raw=f"router policy {name}")
        if values.get("action", "").lower() == "deny":
            entry.action = "deny"
        for key in _FORTIOS_PBR_MATCHES:
            if key in values:
                entry.matches.append(f"{key} {values[key]}")
        for key in _FORTIOS_PBR_SETS:
            if key in values:
                entry.sets.append(f"set {key} {values[key]}")
        policy = ParsedPolicy(
            kind="policy-based-routing",
            name=f"router policy {name}",
            detail="1 行",
            entries=[entry],
        )
        input_device = values.get("input-device")
        if input_device:
            policy.used_by.append(f"interface {input_device}")
        parsed.policies.append(policy)


def parse_fortios(text: str, parsed: ParsedConfig) -> None:
    """`config router static` の edit ブロックを読む。"""
    parse_fortios_policy_routes(text, parsed)
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
            prefix = to_prefix(destination.group(1), destination.group(2), wildcard=False)
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
_QX_ROUTE_POLICY = re.compile(r"^route-policy\s+(\S+)\s+(permit|deny)\s+node\s+(\d+)", re.I)
_POLICY_APPLY = re.compile(r"^\s*ip policy route-map\s+(\S+)", re.IGNORECASE)
_PREFIX_LIST_ENTRY = re.compile(
    r"^ip(?:v6)? prefix-list\s+(?P<name>\S+)(?:\s+seq\s+(?P<seq>\d+))?"
    r"\s+(?P<action>permit|deny)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)


def parse_policies(text: str, parsed: ParsedConfig) -> None:
    """route-map / prefix-list を、中身の並びまで拾う。

    「広告しているはずなのに相手に届かない」の原因はたいていここにある。
    どちらも上から順に見て、最初に当たったところで止まる。並びを持たないと、
    「掛かってはいるが、その手前で落ちている」が見えない。
    """
    policies: dict[tuple[str, str], ParsedPolicy] = {}

    def get(kind: str, name: str) -> ParsedPolicy:
        key = (kind, name)
        found = policies.get(key)
        if found is None:
            found = ParsedPolicy(kind=kind, name=name)
            policies[key] = found
        return found

    current: ParsedPolicyEntry | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        prefix = _PREFIX_LIST_ENTRY.match(line)
        if prefix:
            entry = ParsedPolicyEntry(
                seq=prefix.group("seq"),
                action=prefix.group("action").lower(),
                matches=[prefix.group("rest").strip()],
                raw=line,
            )
            get("prefix-list", prefix.group("name")).entries.append(entry)
            current = None
            continue

        head = _ROUTE_MAP.match(line) or _QX_ROUTE_POLICY.match(line)
        if head and not raw.startswith((" ", "\t")):
            groups = head.groups()
            action = (groups[1] or "permit").lower()
            current = ParsedPolicyEntry(seq=groups[2], action=action, raw=line)
            get("route-map", groups[0]).entries.append(current)
            continue

        if current is None:
            continue
        if not raw.startswith((" ", "\t")):
            current = None
            continue
        if re.match(r"^(?:match|if-match)\s+", line, re.IGNORECASE):
            current.matches.append(line)
        elif re.match(r"^(?:set|apply)\s+", line, re.IGNORECASE):
            current.sets.append(line)

    for policy in policies.values():
        policy.detail = f"{len(policy.entries)} 行"
        parsed.policies.append(policy)

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
