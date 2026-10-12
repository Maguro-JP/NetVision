"""Junos の config（set 形式 / 波括弧形式）と show 出力を読む。"""

from __future__ import annotations

import re

from app.services.configparse import policy, routing, tunnels, vlanlist
from app.services.configparse.base import (
    NeighborProtocol,
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
    ParsedNeighbor,
    ParsedQos,
    ParsedQosAction,
    ParsedQosClass,
    ParsedQosPolicy,
    ParsedTransformSet,
    ParsedVrf,
    Vendor,
)

_SET_HOSTNAME = re.compile(r"^set system host-name\s+(\S+)", re.MULTILINE)
_BRACE_HOSTNAME = re.compile(r"^\s*host-name\s+(\S+);", re.MULTILINE)
#: show version の1行目。取り込みで機器と突き合わせるのはホスト名なので、
#: ここを読めないと show version だけ貼られた分が結び付かない
_SHOW_HOSTNAME = re.compile(r"^Hostname:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
#: `Junos: 21.4R3-S5.4`、`JUNOS Software Release [21.4R3]`、階層形式の `version ...`、
#: set 形式の `set version ...`。版が読めないと弱点の照合から漏れる
_VERSION = re.compile(
    r"^(?:Junos:|JUNOS Software Release \[|set version |version )\s*([\w.\-]+)", re.MULTILINE
)
_MODEL = re.compile(r"^Model:\s*(\S+)", re.MULTILINE)
# show chassis hardware の1行目。LLDP 表の "Chassis Id" 見出しを拾わないよう、
# シリアルらしい英数字の並びだけを対象にする
_SERIAL = re.compile(r"^Chassis\s+([A-Za-z0-9]{6,})\b", re.MULTILINE)

_SET_DESCRIPTION = re.compile(r"^set interfaces (\S+) description\s+(.+)$", re.MULTILINE)
# Junos では説明を unit 側に書く流儀も普通にある。物理側だけ見ていると
# 「説明が1つも無い」と誤って言ってしまうので、こちらも拾う
_SET_UNIT_DESCRIPTION = re.compile(
    r"^set interfaces (\S+) unit (\d+) description\s+(.+)$", re.MULTILINE
)
_SET_ADDRESS = re.compile(
    r"^set interfaces (\S+) unit (\d+) family inet(?:6)? address\s+(\S+)", re.MULTILINE
)
# `family inet dhcp` は住所を貰う形。書いていないのとは違う
_SET_DHCP = re.compile(r"^set interfaces (\S+) unit (\d+) family inet dhcp\b", re.MULTILINE)
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
#: 副ポート（unit）に付けたタグ。ルーター側ではここが VLAN の所在になる
_SET_UNIT_VLAN = re.compile(r"^set interfaces (\S+) unit (\d+) vlan-id\s+(\d+)", re.MULTILINE)
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


def _flatten(text: str) -> str:
    """囲みで書いた設定を、`set` の並びに直す。

    Junos は同じ設定を2つの形で見せる。囲みの形（`show configuration` の既定）と、
    `set` の形（`show configuration | display set`）である。読み手を2つ持つと
    片方だけ直したときに食い違うので、入口で `set` の形にそろえる。

    値が角括弧の並び（`members [ USERS VOICE ];`）になっている行は、そのまま
    1行として渡す。受け取る側が括弧を開く。

    出典: Juniper のサポート記事 [EX/QFX] Example - Configuring VLAN translation
    by using ELS on Junos OS の `show configuration interfaces` 出力例
    （ge-0/0/0 { unit 0 { family ethernet-switching { interface-mode trunk;
    vlan { members [ 10 220 ]; } } } }）
    """
    if "{" not in text:
        return text
    out: list[str] = []
    stack: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "/*", "*")):
            continue
        # `unit 0 { family inet { address 10.0.0.1/24; } }` のような1行書きは
        # 見ない（Junos の出力では出ない）。素直に段だけ数える
        if line.endswith("{"):
            stack.append(line[:-1].strip())
            continue
        if line in ("}", "};"):
            if stack:
                stack.pop()
            continue
        if line.startswith("}"):
            continue
        out.append("set " + " ".join([*stack, line.rstrip(";").strip()]))
    return "\n".join(out) if out else text


#: 論理システムの中の行。`set logical-systems NAME ` を外すと、全体と同じ書き方になる
_LOGICAL_SYSTEM_LINE = re.compile(r"^set\s+logical-systems\s+(\S+)\s+(.+)$", re.IGNORECASE)


def split_logical_systems(text: str) -> tuple[str, dict[str, str]]:
    """全体の `set` 行と、論理システムごとの `set` 行に分ける。

    論理システムは自分の口・経路表・ポリシーを持つ別の機器として動く（出典: Juniper
    「Understanding Logical Systems for Routers and Switches」）。全体に混ぜて読むと、
    口の帯を知らないまま経路を判断して誤った指摘になる（docs/knowledge/decisions.md）。
    分けた行は `logical-systems NAME` を外してあり、`parse` にそのまま渡せる
    （書き方は「Routing, Interfaces, and NAT for User Logical Systems」の set の設定例）。
    囲みの形は先に平らにする。まだ取り込みにはつないでいない（読む入口は別に足す）。
    """
    main: list[str] = []
    systems: dict[str, list[str]] = {}
    for line in _flatten(text).splitlines():
        match = _LOGICAL_SYSTEM_LINE.match(line.strip())
        if match:
            systems.setdefault(match.group(1), []).append(f"set {match.group(2)}")
        else:
            main.append(line)
    return "\n".join(main), {name: "\n".join(lines) for name, lines in systems.items()}


def parse(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.JUNIPER, os_name="Junos")
    # 囲みの形で貼られたときは、先に `set` の形へそろえる。名乗り（show version）は
    # 囲みの外に出るので、元の字も残して両方から読む
    flattened = _flatten(text)
    if flattened is not text:
        text = f"{text}\n{flattened}"
    # 範囲でまとめて書かれた口を、1つずつに開く。開かないと、その口が
    # 「何も設定されていない」ことになる
    expanded = _expand_interface_ranges(text)
    if expanded:
        text = f"{text}\n{expanded}"

    match = (
        _SET_HOSTNAME.search(text) or _BRACE_HOSTNAME.search(text) or _SHOW_HOSTNAME.search(text)
    )
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
    policy.parse_junos(text, parsed)
    policy.parse_junos_security(text, parsed)
    _parse_nat(text, parsed)
    _parse_dhcp(text, parsed)
    _parse_ipsec(text, parsed)
    _parse_cos(text, parsed)
    return parsed


# ---------------------------------------------------------------------------
# アドレス変換 / アドレス配布 / 拠点間の暗号化
#
# Junos は書き方が Cisco と大きく違うが、決めていることは同じである。
# 同じ器（ParsedNat / ParsedDhcp / ParsedCrypto）へ入れておけば、粗を見る
# ところは1つで足りる。ここでやるのは読み替えだけで、判断はしない。
# ---------------------------------------------------------------------------

_NAT_RULE = re.compile(
    r"^set security nat (?P<kind>source|destination|static) rule-set (?P<set>\S+)\s+"
    r"(?:rule (?P<rule>\S+)\s+)?(?P<rest>.+)$",
    re.IGNORECASE,
)
_NAT_POOL = re.compile(
    r"^set security nat (?:source|destination) pool (?P<name>\S+)\s+"
    r"address (?P<address>\S+?)(?:\s+to\s+address\s+(?P<end>\S+))?\s*$",
    re.IGNORECASE,
)


def _parse_nat(text: str, parsed: ParsedConfig) -> None:
    nat = ParsedNat()
    rules: dict[str, ParsedNatRule] = {}

    for raw in text.splitlines():
        line = raw.strip()
        pool = _NAT_POOL.match(line)
        if pool:
            nat.pools.append(
                ParsedNatPool(
                    name=pool.group("name"),
                    start=pool.group("address"),
                    end=pool.group("end"),
                )
            )
            continue

        found = _NAT_RULE.match(line)
        if not found:
            continue
        kind, rule_set, rule = found.group("kind"), found.group("set"), found.group("rule")
        rest = found.group("rest")
        if rule is None:
            # rule-set そのものへの指定（from zone / to zone）
            _nat_zone(nat, rest)
            continue
        key = f"{rule_set}/{rule}"
        entry = rules.get(key)
        if entry is None:
            entry = ParsedNatRule(
                kind="dynamic" if kind == "source" else "static",
                direction="inside" if kind == "source" else "outside",
                raw=f"nat {kind} rule-set {rule_set} rule {rule}",
            )
            rules[key] = entry
            nat.rules.append(entry)
        _nat_detail(entry, rest)

    if nat.rules or nat.pools:
        parsed.nat = nat


def _nat_zone(nat: ParsedNat, rest: str) -> None:
    """どちら向きの変換かは、from zone / to zone で決まる。"""
    found = re.match(r"^(from|to) (?:zone|interface)\s+(\S+)", rest, re.IGNORECASE)
    if not found:
        return
    where = nat.inside_interfaces if found.group(1).lower() == "from" else nat.outside_interfaces
    if found.group(2) not in where:
        where.append(found.group(2))


def _nat_detail(entry: ParsedNatRule, rest: str) -> None:
    lowered = rest.lower()
    match_from = re.search(r"match source-address\s+(\S+)", lowered)
    if match_from:
        entry.matched = match_from.group(1)
        entry.matched_is_name = False
    match_to = re.search(r"match destination-address\s+(\S+)", lowered)
    if match_to and entry.direction == "outside":
        entry.matched = match_to.group(1)
        entry.matched_is_name = False
    if "then source-nat interface" in lowered:
        # 出口のアドレスに束ねる。Cisco の overload と同じ
        entry.translated = "出口のアドレス"
        entry.overload = True
    pool = re.search(r"then (?:source-nat|destination-nat) pool\s+(\S+)", lowered)
    if pool:
        entry.translated = pool.group(1)
        entry.overload = entry.direction == "inside"
    if "then source-nat off" in lowered:
        entry.kind = "static"
        entry.translated = "変換しない"
    entry.raw = f"{entry.raw} {rest}".strip()


_DHCP_POOL = re.compile(
    r"^set access address-assignment pool (?P<name>\S+) family inet (?P<rest>.+)$",
    re.IGNORECASE,
)


def _parse_dhcp(text: str, parsed: ParsedConfig) -> None:
    """配るアドレスを読む。Junos は範囲をそのまま書く。"""
    dhcp = ParsedDhcp()
    pools: dict[str, ParsedDhcpPool] = {}

    for raw in text.splitlines():
        found = _DHCP_POOL.match(raw.strip())
        if not found:
            continue
        name = found.group("name")
        pool = pools.get(name)
        if pool is None:
            pool = ParsedDhcpPool(name=f"pool {name}")
            pools[name] = pool
            dhcp.pools.append(pool)
        _dhcp_detail(pool, found.group("rest"))

    _parse_legacy_dhcp(text, parsed, dhcp)
    if dhcp.pools:
        parsed.dhcp = dhcp


_LEGACY_DHCP = re.compile(
    r"^set system services dhcp (?:pool (?P<pool>\S+) )?(?P<key>\S+)\s+(?P<value>.+)$",
    re.IGNORECASE,
)


def _parse_legacy_dhcp(text: str, parsed: ParsedConfig, dhcp: ParsedDhcp) -> None:
    """古い形の DHCP（`system services dhcp`）を読む。

    全体（`system services dhcp`）に書いた値は、プールで書かなければ受け継ぐ。
    範囲を書かなければ区画の全部を配るが、口自身のアドレスは配らない。
    出典: Juniper「Legacy DHCP and Extended DHCP」（Table 5 と、下の階層が上の階層に勝つ
    という注記）、「DHCP Server Options」の Configuring Address Pools。
    """
    shared: dict[str, list[str]] = {}
    pools: dict[str, dict[str, list[str]]] = {}
    for raw in text.splitlines():
        found = _LEGACY_DHCP.match(raw.strip())
        if not found:
            continue
        values = found.group("value").split()
        pool_name = found.group("pool")
        target = shared if pool_name is None else pools.setdefault(pool_name, {})
        target.setdefault(found.group("key").lower(), []).extend(values)
    own = {port.ip_address.split("/")[0] for port in parsed.interfaces if port.ip_address}

    for network, settings in pools.items():

        def pick(key: str, settings: dict[str, list[str]] = settings) -> list[str]:
            return settings.get(key) or shared.get(key) or []

        span = settings.get("address-range", [])
        low = span[span.index("low") + 1] if "low" in span[:-1] else None
        high = span[span.index("high") + 1] if "high" in span[:-1] else None
        routers = pick("router")
        pool = ParsedDhcpPool(
            name=f"pool {network}",
            network=network,
            range_start=low,
            range_end=high,
            default_routers=routers,
            dns_servers=pick("name-server"),
            domain=(pick("domain-name") or [None])[0],
            lease=(pick("maximum-lease-time") or pick("default-lease-time") or [None])[0],
            imports_options=bool(pick("propagate-settings")),
            gateway_reserved=bool(routers) and all(router in own for router in routers),
        )
        dhcp.pools.append(pool)
        excluded = settings.get("exclude-address", [])
        dhcp.excluded.extend((address, address) for address in excluded)


def _dhcp_detail(pool: ParsedDhcpPool, rest: str) -> None:
    network = re.match(r"^network\s+(\S+)", rest, re.IGNORECASE)
    if network:
        pool.network = network.group(1)
        return
    span = re.match(r"^range\s+\S+\s+(low|high)\s+(\S+)", rest, re.IGNORECASE)
    if span:
        if span.group(1).lower() == "low":
            pool.range_start = span.group(2)
        else:
            pool.range_end = span.group(2)
        return
    router = re.match(r"^dhcp-attributes router\s+(\S+)", rest, re.IGNORECASE)
    if router:
        pool.default_routers.append(router.group(1))
        return
    dns = re.match(r"^dhcp-attributes name-server\s+(\S+)", rest, re.IGNORECASE)
    if dns:
        pool.dns_servers.append(dns.group(1))
        return
    if re.match(r"^dhcp-attributes propagate-settings\s", rest, re.IGNORECASE):
        # 上流から受け取った DNS などを配る（DHCP Attributes for Address-Assignment Pools）
        pool.imports_options = True
        return
    lease = re.match(r"^dhcp-attributes maximum-lease-time\s+(\S+)", rest, re.IGNORECASE)
    if lease:
        pool.lease = lease.group(1)


def _parse_cos(text: str, parsed: ParsedConfig) -> None:
    """優先制御（CoS）を読む。

    Junos は classifier（どう仕分けるか）・scheduler（どう出すか）・
    scheduler-map（仕分けと出し方の対応）・interfaces（当て先）に分かれる。
    Cisco の class-map / policy-map / service-policy と役割が同じなので、
    そこへ読み替える。当て先の無い scheduler-map が「書いてあるだけ」になる。
    """
    qos = ParsedQos()
    schedulers: dict[str, list[str]] = {}
    maps: dict[str, ParsedQosPolicy] = {}
    classifiers: dict[str, ParsedQosClass] = {}

    for raw in text.splitlines():
        line = raw.strip()

        found = re.match(
            r"^set class-of-service classifiers \S+ (\S+) forwarding-class (\S+)\s*(.*)$",
            line,
            re.IGNORECASE,
        )
        if found:
            entry = classifiers.setdefault(found.group(1), ParsedQosClass(name=found.group(1)))
            detail = f"{found.group(2)} {found.group(3)}".strip()
            if detail not in entry.matches:
                entry.matches.append(detail)
            continue

        found = re.match(r"^set class-of-service schedulers (\S+)\s+(.+)$", line, re.IGNORECASE)
        if found:
            schedulers.setdefault(found.group(1), []).append(found.group(2))
            continue

        found = re.match(
            r"^set class-of-service scheduler-maps (\S+) forwarding-class (\S+) scheduler (\S+)",
            line,
            re.IGNORECASE,
        )
        if found:
            policy = maps.setdefault(found.group(1), ParsedQosPolicy(name=found.group(1)))
            policy.classes.append(
                ParsedQosAction(class_name=found.group(2), actions=[f"scheduler {found.group(3)}"])
            )
            continue

        found = re.match(
            r"^set class-of-service interfaces (\S+)(?:\s+unit \S+)? scheduler-map (\S+)",
            line,
            re.IGNORECASE,
        )
        if found:
            # Junos の scheduler-map は出る側に当てる
            qos.applied.append((found.group(1), "output", found.group(2)))
            continue

        found = re.match(
            r"^set class-of-service interfaces (\S+)(?:\s+unit \S+)? classifiers \S+ (\S+)",
            line,
            re.IGNORECASE,
        )
        if found:
            qos.applied.append((found.group(1), "input", found.group(2)))

    # 仕分けの名前（forwarding-class）は、書かなくても最初からある4つを含む。
    # 定義が無い＝間違い、ではないので、既定の名前も一覧に入れておく
    known = {"best-effort", "expedited-forwarding", "assured-forwarding", "network-control"}
    for raw in text.splitlines():
        found = re.match(
            r"^set class-of-service forwarding-classes (?:queue \S+|class) (\S+)",
            raw.strip(),
            re.IGNORECASE,
        )
        if found:
            known.add(found.group(1))
    used = {action.class_name for policy in maps.values() for action in policy.classes}
    for name in sorted(known & used):
        classifiers.setdefault(name, ParsedQosClass(name=name, matches=["forwarding-class"]))

    for policy in maps.values():
        for action in policy.classes:
            name = action.actions[0].split()[-1]
            action.actions.extend(schedulers.get(name, []))
    qos.policies.extend(maps.values())
    qos.classes.extend(classifiers.values())

    if qos.policies or qos.classes:
        parsed.qos = qos


def _parse_ipsec(text: str, parsed: ParsedConfig) -> None:
    """拠点間の暗号化を読む。

    Junos は proposal（方式）・policy（束ね）・gateway / vpn（相手と範囲）に
    分かれる。Cisco の isakmp policy / transform-set / crypto map と役割が
    同じなので、そこへ読み替える。
    """
    crypto = ParsedCrypto()
    ike: dict[str, ParsedIkePolicy] = {}
    proposals: dict[str, ParsedTransformSet] = {}
    gateways: dict[str, str] = {}
    #: gateway ごとの出口ポート。VPN と結び直してから当て先として持つ
    bound: dict[str, str] = {}
    vpns: dict[str, ParsedCryptoMapEntry] = {}
    ike_policy_proposals: dict[str, list[str]] = {}

    for raw in text.splitlines():
        line = raw.strip()

        found = re.match(r"^set security ike proposal (\S+)\s+(.+)$", line, re.IGNORECASE)
        if found:
            entry = ike.setdefault(
                found.group(1), ParsedIkePolicy(number=found.group(1), version=2)
            )
            _ike_detail(entry, found.group(2))
            continue

        found = re.match(r"^set security ike policy (\S+) proposals?\s+(\S+)", line, re.IGNORECASE)
        if found:
            ike_policy_proposals.setdefault(found.group(1), []).append(found.group(2))
            continue

        # 相手のアドレスを書かない gateway
        # （`dynamic hostname ...` / `dynamic user-at-hostname ...`）。
        # 出典: Juniper「Example: Configuring Dynamic VPN」、Juniper Support「[SRX] Example - How to
        # configure a dialup IPSec VPN with group IKE ID」。空にすると「相手が無い」に見える
        found = re.match(r"^set security ike gateway (\S+) dynamic\s", line, re.IGNORECASE)
        if found:
            gateways.setdefault(found.group(1), "dynamic")
            continue

        found = re.match(
            r"^set security ike gateway (\S+) (?:address|ike-policy|external-interface)\s+(\S+)",
            line,
            re.IGNORECASE,
        )
        if found:
            name, value = found.group(1), found.group(2)
            if " address " in line:
                gateways[name] = value
            elif " external-interface " in line:
                bound[name] = value
            continue

        found = re.match(r"^set security ipsec proposal (\S+)\s+(.+)$", line, re.IGNORECASE)
        if found:
            entry = proposals.setdefault(found.group(1), ParsedTransformSet(name=found.group(1)))
            value = found.group(2).split()
            if len(value) >= 2 and value[0] in ("encryption-algorithm", "authentication-algorithm"):
                entry.transforms.append(value[1])
            continue

        found = re.match(r"^set security ipsec vpn (\S+)\s+(.+)$", line, re.IGNORECASE)
        if found:
            name = found.group(1)
            entry = vpns.setdefault(name, ParsedCryptoMapEntry(name=name, kind="ipsec-vpn"))
            _vpn_detail(entry, found.group(2), gateways)
            continue

    for entry in vpns.values():
        # 相手も出口も gateway の側に書いてある。VPN の名前で引けるよう結び直す
        if not entry.peers and entry.profile in gateways:
            entry.peers = [gateways[entry.profile]]
        if entry.profile in bound:
            crypto.applied.append((bound[entry.profile], entry.name))
        crypto.maps.append(entry)
    crypto.ike_policies.extend(ike.values())
    crypto.transform_sets.extend(proposals.values())
    for name, wanted in ike_policy_proposals.items():
        for proposal in wanted:
            if proposal not in ike:
                crypto.ike_policies.append(ParsedIkePolicy(number=f"{name} → {proposal}"))

    if crypto.maps or crypto.ike_policies:
        parsed.crypto = crypto


def _ike_detail(entry: ParsedIkePolicy, rest: str) -> None:
    parts = rest.split()
    if len(parts) < 2:
        return
    key, value = parts[0].lower(), parts[1]
    if key == "encryption-algorithm":
        entry.encryption = value
    elif key == "authentication-algorithm":
        entry.hash = value
    elif key == "dh-group":
        entry.group = value.replace("group", "")
    elif key == "authentication-method":
        entry.authentication = value
    elif key == "lifetime-seconds" and value.isdigit():
        entry.lifetime = int(value)


def _vpn_detail(entry: ParsedCryptoMapEntry, rest: str, gateways: dict[str, str]) -> None:
    lowered = rest.lower()
    gateway = re.match(r"^ike gateway\s+(\S+)", rest, re.IGNORECASE)
    if gateway:
        entry.profile = gateway.group(1)
        if gateway.group(1) in gateways:
            entry.peers = [gateways[gateway.group(1)]]
        return
    proposal = re.match(r"^ike ipsec-policy\s+(\S+)", rest, re.IGNORECASE)
    if proposal:
        entry.transform_sets.append(proposal.group(1))
        return
    traffic = re.match(r"^traffic-selector \S+ (local|remote)-ip\s+(\S+)", rest, re.IGNORECASE)
    if traffic:
        side = "local" if traffic.group(1).lower() == "local" else "remote"
        current = entry.match_address or ""
        entry.match_address = (
            f"{traffic.group(2)} → {current.split(' → ')[-1] if ' → ' in current else 'any'}"
            if side == "local"
            else f"{current.split(' → ')[0] if ' → ' in current else 'any'} → {traffic.group(2)}"
        )
        entry.match_is_acl = False
        return
    if "bind-interface" in lowered:
        entry.match_address = entry.match_address or "経路で決まる（bind-interface）"
        entry.match_is_acl = False


def _parse_set_interfaces(text: str, parsed: ParsedConfig) -> None:
    for name, description in _SET_DESCRIPTION.findall(text):
        parsed.add_interface(ParsedInterface(name=name, description=description.strip().strip('"')))
    for name, unit, description in _SET_UNIT_DESCRIPTION.findall(text):
        port = name if unit == "0" else f"{name}.{unit}"
        entry = parsed.add_interface(ParsedInterface(name=port))
        if not entry.description:
            entry.description = description.strip().strip('"')
    for name, unit in _SET_DHCP.findall(text):
        port = name if unit == "0" else f"{name}.{unit}"
        parsed.add_interface(ParsedInterface(name=port)).address_source = "dhcp"
    for name, unit, address in _SET_ADDRESS.findall(text):
        # unit 0 は物理の口そのものとして呼ばれる（隣接情報もその名前で来る）。
        # 0 以外は副ポートなので、ge-0/0/1.10 という呼び名で持つ
        port = name if unit == "0" else f"{name}.{unit}"
        parsed.add_interface(ParsedInterface(name=port, ip_address=address))
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
            elif re.fullmatch(r"\d+-\d+", token):
                # 番号は範囲でも書ける（Juniper「members」: 10-20 23 27-30）
                values.extend(vlanlist.parse_ranges(token))
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

    # 副ポートに付けたタグ。アドレスは unit 単位で持っているので、名前も合わせる
    for name, unit, vlan in _SET_UNIT_VLAN.findall(text):
        port = parsed.add_interface(ParsedInterface(name=f"{name}.{unit}"))
        port.vlan = int(vlan)

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


#: 表の1列目に出てくるポート名。`ge-0/0/1` `xe-1/0/0` `ae0` `em0` `irb`
_LLDP_PORT = re.compile(
    r"^(?:[a-z]{2,3}-\d+/\d+/\d+(?:\.\d+)?|ae\d+|em\d+|irb(?:\.\d+)?)$", re.IGNORECASE
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
            if not _LLDP_PORT.match(columns[0]):
                # 表の下にそのまま config が続いていることがある。
                # 空行で終わるとは限らないので、ポート名に見えなくなったら止める
                break
            parsed.add_neighbor(
                ParsedNeighbor(
                    local_interface=columns[0],
                    remote_hostname=columns[-1],
                    remote_interface=columns[-2],
                    protocol=NeighborProtocol.LLDP,
                )
            )


#: `set interfaces interface-range NAME member ge-0/0/9`
_RANGE_MEMBER = re.compile(
    r"^set\s+interfaces\s+interface-range\s+(?P<name>\S+)\s+member\s+(?P<port>\S+)\s*$",
    re.IGNORECASE,
)
#: `set interfaces interface-range NAME member-range ge-0/0/0 to ge-0/0/10`
_RANGE_SPAN = re.compile(
    r"^set\s+interfaces\s+interface-range\s+(?P<name>\S+)\s+member-range\s+"
    r"(?P<start>\S+)\s+to\s+(?P<end>\S+)\s*$",
    re.IGNORECASE,
)
#: 範囲そのものに書いた共通の設定
_RANGE_BODY = re.compile(
    r"^set\s+interfaces\s+interface-range\s+(?P<name>\S+)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
#: `ge-0/0/10` のような、末尾の番号だけが動く名前
_PORT_TAIL = re.compile(r"^(?P<head>.*?)(?P<number>\d+)$")


def _expand_interface_ranges(text: str) -> str:
    """`interface-range` を、メンバーの口ごとの `set` に開く。

    範囲に書いた共通の設定は、メンバー全部に写される
    （出典: Junos OS「Interface Ranges for Physical Interfaces」
    "The operating system copies the common configuration to all the
    interface range's member interfaces."）。開かないと、その口は台帳で
    「何も設定されていない」ことになる。

    メンバーの指定には正規表現・ワイルドカードも使えるが、そちらは
    読んでいない（当てずっぽうで口を作るより、読めないほうが害が小さい）。
    """
    members: dict[str, list[str]] = {}
    bodies: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        span = _RANGE_SPAN.match(line)
        if span:
            members.setdefault(span.group("name"), []).extend(
                _ports_between(span.group("start"), span.group("end"))
            )
            continue
        one = _RANGE_MEMBER.match(line)
        if one:
            members.setdefault(one.group("name"), []).append(one.group("port"))
            continue
        body = _RANGE_BODY.match(line)
        if body:
            bodies.append((body.group("name"), body.group("rest")))

    out: list[str] = []
    for name, rest in bodies:
        for port in members.get(name, []):
            out.append(f"set interfaces {port} {rest}")
    return "\n".join(out)


def _ports_between(start: str, end: str) -> list[str]:
    """`ge-0/0/0 to ge-0/0/2` を1つずつに開く。末尾の番号だけを動かす。"""
    left = _PORT_TAIL.match(start)
    right = _PORT_TAIL.match(end)
    if not left or not right or left.group("head") != right.group("head"):
        # 名前の付け方が違うものは、開かずに両端だけを残す。
        # 推測で間の口を作らない
        return [start, end]
    first, last = int(left.group("number")), int(right.group("number"))
    if last < first:
        first, last = last, first
    return [f"{left.group('head')}{number}" for number in range(first, last + 1)]
