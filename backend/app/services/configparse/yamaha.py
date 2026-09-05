"""ヤマハ RTX / NVR の config を読む。

日本の拠点ルーターでいちばん見かける機種のひとつなのに、これまで読めて
いなかった。書き方は Cisco とも Junos とも違うが、決めていることは同じ
（どのポートにどのアドレスか、どこへ変換するか、何を配るか、何を通すか）
なので、同じ器へ読み替える。

読める書き方は、`show config` にそのまま出てくる形に絞ってある。

- `ip lan1 address` … LAN 側のポートとアドレス
- `pp select` の塊 … PPPoE で外へ出る側（相手先情報）
- `ip route` … 静的経路（gateway が pp のこともある）
- `nat descriptor` … アドレス変換（masquerade = NAPT）
- `dhcp scope` … 配るアドレス
- `ip filter` と `secure filter` … 通信の制限と、その当て先

トンネル（`tunnel select`）や無線（`wlan`）は、確かめられる設定が手元に
無いので読んでいない。推測で読むと、間違った断定が資料に載る。
"""

from __future__ import annotations

import re

from app.services.configparse.base import (
    ParsedAcl,
    ParsedAclEntry,
    ParsedConfig,
    ParsedCrypto,
    ParsedDhcp,
    ParsedDhcpPool,
    ParsedIkePolicy,
    ParsedInterface,
    ParsedNat,
    ParsedNatRule,
    ParsedRoute,
    ParsedTransformSet,
    ParsedTunnel,
    Vendor,
)

#: 1行目の見出し。`# RTX830 Rev.15.02.28 (Tue Nov 26 18:39:03 2019)`
#: show environment に出るときは頭の `#` が付かないので、無くても読む
_HEADER = re.compile(r"^#?\s*((?:RTX|NVR|FWX|SRT)\S*)\s+Rev\.([\d.]+)", re.MULTILINE)
_SERIAL = re.compile(r"serial=(\S+)", re.IGNORECASE)
#: ヤマハには hostname にあたる命令が無い。現場では `console prompt` に
#: 機器名を入れて見分けている。他に名前の手掛かりが無いので、ここから取る
_PROMPT = re.compile(r"^console\s+prompt\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_LAN_ADDRESS = re.compile(r"^ip\s+(lan\d+)\s+address\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_LAN_SECURE = re.compile(r"^ip\s+(lan\d+)\s+secure\s+filter\s+(in|out)\s+(.+)$", re.IGNORECASE)
_PP_SELECT = re.compile(r"^pp\s+select\s+(\S+)", re.IGNORECASE)
_PP_SECURE = re.compile(r"^\s*ip\s+pp\s+secure\s+filter\s+(in|out)\s+(.+)$", re.IGNORECASE)
_PP_NAT = re.compile(r"^\s*ip\s+pp\s+nat\s+descriptor\s+(\d+)", re.IGNORECASE)
_PPPOE_USE = re.compile(r"^\s*pppoe\s+use\s+(lan\d+)", re.IGNORECASE)
_PP_ADDRESS = re.compile(r"^\s*ip\s+pp\s+address\s+(\S+)", re.IGNORECASE)
_ROUTE = re.compile(
    r"^ip\s+route\s+(default|\S+)\s+gateway\s+(.+)$",
    re.IGNORECASE,
)
_NAT_TYPE = re.compile(r"^nat\s+descriptor\s+type\s+(\d+)\s+(\S+)", re.IGNORECASE)
_NAT_OUTER = re.compile(r"^nat\s+descriptor\s+address\s+outer\s+(\d+)\s+(.+)$", re.IGNORECASE)
_NAT_INNER = re.compile(r"^nat\s+descriptor\s+address\s+inner\s+(\d+)\s+(.+)$", re.IGNORECASE)
_NAT_STATIC = re.compile(
    r"^nat\s+descriptor\s+masquerade\s+static\s+(\d+)\s+\d+\s+(\S+)\s+(.+)$", re.IGNORECASE
)
_DHCP_SCOPE = re.compile(r"^dhcp\s+scope\s+(\d+)\s+(\S+?)-(\S+?)/(\d+)", re.IGNORECASE)
_DHCP_OPTION = re.compile(r"^dhcp\s+scope\s+option\s+(\d+)\s+(.+)$", re.IGNORECASE)
#: `pass` / `reject` / `restrict` に、記録の有無（-log / -nolog）が付く。
#: 長いほうから並べないと、`reject-log` の `-log` が条件の側へ流れ込む
_FILTER = re.compile(
    r"^ip\s+filter\s+(\d+)\s+(pass|reject|restrict)(-log|-nolog)?\s*(.*)$",
    re.IGNORECASE,
)


def detect(text: str) -> Vendor | None:
    """ヤマハかどうか。見出しの機種名か、この機種にしか無い書き方で見る。"""
    if _HEADER.search(text):
        return Vendor.YAMAHA
    for pattern in (
        r"^ip\s+lan\d+\s+address\s",
        r"^nat\s+descriptor\s+type\s",
        r"^pp\s+select\s",
        r"^dhcp\s+scope\s+\d",
    ):
        if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
            return Vendor.YAMAHA
    return None


def parse(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.YAMAHA, os_name="Yamaha RT")

    header = _HEADER.search(text)
    if header:
        parsed.model = header.group(1)
        parsed.os_version = header.group(2)
    serial = _SERIAL.search(text)
    if serial:
        parsed.serial_number = serial.group(1)
    prompt = _PROMPT.search(text)
    if prompt:
        parsed.hostname = prompt.group(1).strip('"')

    _parse_interfaces(text, parsed)
    _parse_routes(text, parsed)
    _parse_filters(text, parsed)
    _parse_nat(text, parsed)
    _parse_dhcp(text, parsed)
    _parse_tunnels(text, parsed)
    return parsed


# ---------------------------------------------------------------------------
# ポート
# ---------------------------------------------------------------------------


def _parse_interfaces(text: str, parsed: ParsedConfig) -> None:
    """LAN と、PPPoE で外へ出る側（pp）をポートとして持つ。

    pp は物理ポートではないが、図と台帳では「外へ出ている口」として
    扱えたほうが分かりやすい。どの LAN を使っているかは説明に残す。
    """
    for name, address in _LAN_ADDRESS.findall(text):
        interface = parsed.add_interface(ParsedInterface(name=name.upper()))
        if address.lower() not in ("dhcp", "auto"):
            interface.ip_address = address

    current: ParsedInterface | None = None
    for raw in text.splitlines():
        head = _PP_SELECT.match(raw.strip())
        if head:
            current = parsed.add_interface(ParsedInterface(name=f"PP{head.group(1)}"))
            current.mode = "routed"
            continue
        if raw and not raw.startswith((" ", "\t")) and not _PP_SELECT.match(raw):
            if not raw.strip().lower().startswith(("pp ", "ppp ", "ip pp", "pppoe")):
                current = None
        if current is None:
            continue
        used = _PPPOE_USE.match(raw)
        if used:
            current.description = f"PPPoE {used.group(1).upper()} 経由"
            continue
        address = _PP_ADDRESS.match(raw)
        if address and address.group(1).lower() not in ("ipcp", "dhcp"):
            current.ip_address = address.group(1)


# ---------------------------------------------------------------------------
# 経路
# ---------------------------------------------------------------------------


def _parse_routes(text: str, parsed: ParsedConfig) -> None:
    for raw in text.splitlines():
        found = _ROUTE.match(raw.strip())
        if not found:
            continue
        destination = "0.0.0.0/0" if found.group(1).lower() == "default" else found.group(1)
        rest = found.group(2).split()
        # `gateway pp 1` のように、次の相手がポートのことがある
        if rest and rest[0].lower() in ("pp", "tunnel", "dhcp"):
            next_hop = f"{rest[0].upper()}{rest[1]}" if len(rest) > 1 else rest[0].upper()
            parsed.routes.append(ParsedRoute(destination=destination, interface=next_hop))
            continue
        parsed.routes.append(
            ParsedRoute(destination=destination, next_hop=rest[0] if rest else None)
        )


# ---------------------------------------------------------------------------
# 通信の制限
# ---------------------------------------------------------------------------


def _parse_filters(text: str, parsed: ParsedConfig) -> None:
    """`ip filter` は番号ごとの1行。当て先は secure filter の並びで決まる。

    Cisco の ACL と違い、番号を並べた順に見る。並びは当て先の側に書いて
    あるので、そこを読んで、番号の集まりを1つの ACL として組み立てる。
    """
    rules: dict[str, ParsedAclEntry] = {}
    for raw in text.splitlines():
        found = _FILTER.match(raw.strip())
        if not found:
            continue
        action = found.group(2).lower()
        logging = (found.group(3) or "").lower()
        parts = found.group(4).split()
        entry = ParsedAclEntry(
            seq=found.group(1),
            # restrict は「通すが、そのために回線を張りには行かない」。
            # 通す側として扱う（落としているわけではない）
            action="deny" if action == "reject" else "permit",
            source=_address(parts[0]) if parts else None,
            destination=_address(parts[1]) if len(parts) > 1 else None,
            protocol=parts[2].lower() if len(parts) > 2 and parts[2] != "*" else None,
            log=logging == "-log",
            raw=raw.strip(),
        )
        if len(parts) > 3 and parts[3] != "*":
            entry.source_port = parts[3]
        if len(parts) > 4 and parts[4] != "*":
            entry.destination_port = parts[4]
        rules[found.group(1)] = entry

    if not rules:
        return

    applied = _applied_filters(text)
    for where, direction, numbers, stateful in applied:
        wanted = [number for number in numbers if number in rules]
        if not wanted:
            continue
        label = "入り" if direction == "in" else "出"
        acl = ParsedAcl(
            name=f"{where} {label}",
            kind="filter",
            entries=[rules[number] for number in wanted],
            applied_to=[f"interface {where} {label}"],
            stateful=stateful,
        )
        parsed.acls.append(acl)

    used = {number for _, _, numbers, _ in applied for number in numbers}
    idle = [number for number in rules if number not in used]
    if idle:
        # 番号で呼べる形にする。「未使用の filter」とだけ出しても、
        # どれを消せばいいのか分からない
        more = f" ほか{len(idle) - 1}件" if len(idle) > 1 else ""
        parsed.acls.append(
            ParsedAcl(
                name=f"ip filter {idle[0]}{more}",
                kind="filter",
                entries=[rules[number] for number in idle],
            )
        )


def _applied_filters(text: str) -> list[tuple[str, str, list[str], bool]]:
    """どの口に、どの向きで、どの番号を並べているか。

    最後の1つは「戻りの通信を動的に通す指定（dynamic）が付いているか」。
    付いていれば、出る側を広く開けるのが普通の書き方になる。
    """
    out: list[tuple[str, str, list[str], bool]] = []
    current = ""
    for raw in text.splitlines():
        head = _PP_SELECT.match(raw.strip())
        if head:
            current = f"PP{head.group(1)}"
            continue
        lan = _LAN_SECURE.match(raw.strip())
        if lan:
            out.append(
                (
                    lan.group(1).upper(),
                    lan.group(2).lower(),
                    _numbers(lan.group(3)),
                    "dynamic" in lan.group(3).lower(),
                )
            )
            continue
        pp = _PP_SECURE.match(raw)
        if pp and current:
            out.append(
                (
                    current,
                    pp.group(1).lower(),
                    _numbers(pp.group(2)),
                    "dynamic" in pp.group(2).lower(),
                )
            )
    return out


def _numbers(rest: str) -> list[str]:
    """並びのうち、静的な番号だけ。`dynamic` の後ろは別の仕組み。"""
    out: list[str] = []
    for token in rest.split():
        if token.lower() == "dynamic":
            break
        if token.isdigit():
            out.append(token)
    return out


def _address(value: str) -> str:
    return "any" if value == "*" else value


# ---------------------------------------------------------------------------
# アドレス変換
# ---------------------------------------------------------------------------


def _parse_nat(text: str, parsed: ParsedConfig) -> None:
    """`nat descriptor` を読む。masquerade がいわゆる NAPT。"""
    nat = ParsedNat()
    kinds: dict[str, str] = {}
    outer: dict[str, str] = {}
    inner: dict[str, str] = {}

    for raw in text.splitlines():
        line = raw.strip()
        found = _NAT_TYPE.match(line)
        if found:
            kinds[found.group(1)] = found.group(2).lower()
            continue
        found = _NAT_OUTER.match(line)
        if found:
            outer[found.group(1)] = found.group(2).strip()
            continue
        found = _NAT_INNER.match(line)
        if found:
            inner[found.group(1)] = found.group(2).strip()
            continue
        found = _NAT_STATIC.match(line)
        if found:
            nat.rules.append(
                ParsedNatRule(
                    kind="static-port",
                    direction="outside",
                    matched=_outer_label(outer.get(found.group(1))),
                    translated=found.group(2),
                    protocol=found.group(3).split()[0].lower() if found.group(3) else None,
                    matched_is_name=False,
                    raw=line,
                )
            )

    for number, kind in kinds.items():
        if kind == "none":
            continue
        nat.rules.append(
            ParsedNatRule(
                kind="dynamic",
                direction="inside",
                matched=inner.get(number, "内側から出る通信"),
                translated=_outer_label(outer.get(number)),
                # masquerade はポートで束ねる。nat（1対1）は束ねない
                overload=kind == "masquerade",
                matched_is_name=False,
                raw=f"nat descriptor type {number} {kind}",
            )
        )

    for where, number in _nat_applied(text):
        if where not in nat.outside_interfaces:
            nat.outside_interfaces.append(where)
        del number

    if nat.rules:
        nat.inside_interfaces = [
            i.name for i in parsed.interfaces if i.name not in nat.outside_interfaces
        ]
        parsed.nat = nat


def _outer_label(value: str | None) -> str:
    if not value:
        return "出口のアドレス"
    if value.lower() in ("ipcp", "dhcp", "primary"):
        return "出口のアドレス（回線から受け取る）"
    return value


def _nat_applied(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    current = ""
    for raw in text.splitlines():
        head = _PP_SELECT.match(raw.strip())
        if head:
            current = f"PP{head.group(1)}"
            continue
        found = _PP_NAT.match(raw)
        if found and current:
            out.append((current, found.group(1)))
    return out


# ---------------------------------------------------------------------------
# アドレス配布
# ---------------------------------------------------------------------------


def _parse_dhcp(text: str, parsed: ParsedConfig) -> None:
    """`dhcp scope` は配る範囲をそのまま書く。"""
    dhcp = ParsedDhcp()
    pools: dict[str, ParsedDhcpPool] = {}

    for raw in text.splitlines():
        line = raw.strip()
        found = _DHCP_SCOPE.match(line)
        if found:
            pool = ParsedDhcpPool(
                name=f"dhcp scope {found.group(1)}",
                network=_network_of(found.group(2), found.group(4)),
                range_start=found.group(2),
                range_end=found.group(3),
            )
            pools[found.group(1)] = pool
            dhcp.pools.append(pool)
            continue
        found = _DHCP_OPTION.match(line)
        if found and found.group(1) in pools:
            _dhcp_option(pools[found.group(1)], found.group(2))

    if dhcp.pools:
        # 既定ゲートウェイを書いていない機種の既定は「配った本人」。
        # 読み取れないものを埋めると嘘になるので、ここでは足さない
        parsed.dhcp = dhcp


def _network_of(address: str, prefix: str) -> str:
    """配る範囲の始まりと長さから、そのセグメントを出す。

    書いてあるのは範囲の始まり（`192.168.100.2-...`）なので、そのまま
    ネットワークとして持つと、既定ゲートウェイの検算がずれる。
    """
    import ipaddress

    try:
        return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))
    except ValueError:
        return f"{address}/{prefix}"


def _dhcp_option(pool: ParsedDhcpPool, rest: str) -> None:
    for token in rest.split():
        key, _, value = token.partition("=")
        if not value:
            continue
        if key.lower() == "router":
            pool.default_routers.extend(value.split(","))
        elif key.lower() in ("dns", "dns_server", "dns-server"):
            pool.dns_servers.extend(value.split(","))
        elif key.lower() == "domain":
            pool.domain = value


# ---------------------------------------------------------------------------
# トンネル（IPsec / L2TP）
# ---------------------------------------------------------------------------

_TUNNEL_SELECT = re.compile(r"^tunnel\s+select\s+(\S+)\s*$", re.IGNORECASE)
_TUNNEL_ENABLE = re.compile(r"^tunnel\s+enable\s+(\S+)\s*$", re.IGNORECASE)
_IKE_LOCAL = re.compile(r"^ipsec\s+ike\s+local\s+address\s+(\d+)\s+(\S+)", re.IGNORECASE)
_IKE_REMOTE = re.compile(r"^ipsec\s+ike\s+remote\s+address\s+(\d+)\s+(\S+)", re.IGNORECASE)
_IKE_ENCRYPTION = re.compile(r"^ipsec\s+ike\s+encryption\s+(\d+)\s+(\S+)", re.IGNORECASE)
_IKE_HASH = re.compile(r"^ipsec\s+ike\s+hash\s+(\d+)\s+(\S+)", re.IGNORECASE)
_IKE_GROUP = re.compile(r"^ipsec\s+ike\s+group\s+(\d+)\s+(\S+)", re.IGNORECASE)
_IKE_PSK = re.compile(r"^ipsec\s+ike\s+pre-shared-key\s+(\d+)\s+(\S+)", re.IGNORECASE)
_SA_POLICY = re.compile(r"^ipsec\s+sa\s+policy\s+(\d+)\s+(\d+)\s+(esp|ah)\s+(.+)$", re.IGNORECASE)
_L2TP = re.compile(r"^tunnel\s+encapsulation\s+(\S+)", re.IGNORECASE)
_TUNNEL_DESCRIPTION = re.compile(r"^description\s+tunnel\s+(.+)$", re.IGNORECASE)
#: 鍵の作り方。ヤマハは長さで書くので、判断の側と同じ群の番号に直しておく
_DH_GROUPS = {"modp768": "1", "modp1024": "2", "modp1536": "5", "modp2048": "14"}


def _parse_tunnels(text: str, parsed: ParsedConfig) -> None:
    """`tunnel select N` の下を読む。

    ヤマハは1つのトンネルの設定が、`tunnel select` の下と、その外の
    `ipsec ike ...` の両方に散らばる。番号（ゲートウェイ番号）でつながって
    いるので、番号を鍵にして拾い直す。

    暗号の強さは、ここで crypto の形に移しておく。そうすると DES や MD5 が
    残っているときの指摘が、Cisco と同じ判断で出る。
    """
    crypto = parsed.crypto or ParsedCrypto()
    current: ParsedTunnel | None = None
    #: `tunnel select` の中で使っているゲートウェイ番号 → トンネル
    by_gateway: dict[str, ParsedTunnel] = {}
    policies: dict[str, ParsedIkePolicy] = {}
    keyed_gateways: list[str] = []
    found_any = False

    for raw in text.splitlines():
        body = raw.strip()
        if not body:
            continue

        select = _TUNNEL_SELECT.match(body)
        if select:
            current = parsed.add_tunnel(ParsedTunnel(name=f"tunnel{select.group(1)}"))
            continue
        if _TUNNEL_ENABLE.match(body):
            current = None
            continue

        encapsulation = _L2TP.match(body)
        if encapsulation and current is not None:
            current.kind = encapsulation.group(1).lower()
            continue

        described = _TUNNEL_DESCRIPTION.match(body)
        if described and current is not None:
            current.description = described.group(1).strip()
            continue

        for pattern, field_name in ((_IKE_LOCAL, "source"), (_IKE_REMOTE, "destination")):
            match = pattern.match(body)
            if not match:
                continue
            found_any = True
            tunnel = current or by_gateway.get(match.group(1))
            if tunnel is None:
                continue
            by_gateway[match.group(1)] = tunnel
            tunnel.kind = tunnel.kind or "ipsec"
            setattr(tunnel, field_name, match.group(2))

        for pattern, field_name in (
            (_IKE_ENCRYPTION, "encryption"),
            (_IKE_HASH, "hash"),
            (_IKE_GROUP, "group"),
        ):
            match = pattern.match(body)
            if match:
                found_any = True
                policy = policies.setdefault(match.group(1), ParsedIkePolicy(number=match.group(1)))
                value = match.group(2)
                if field_name == "group":
                    value = _DH_GROUPS.get(value.lower(), value)
                setattr(policy, field_name, value)

        psk = _IKE_PSK.match(body)
        if psk:
            found_any = True
            # 合言葉そのものは持たない。どのゲートウェイに置いてあるかだけ覚えて、
            # 相手のアドレスは読み終わってから当てる（書く順が前後するため）
            keyed_gateways.append(psk.group(1))
            if current is not None:
                by_gateway.setdefault(psk.group(1), current)

        sa = _SA_POLICY.match(body)
        if sa:
            found_any = True
            prefix = sa.group(3).lower()
            transforms = [f"{prefix}-{value.lower()}" for value in sa.group(4).split()]
            crypto.transform_sets.append(
                ParsedTransformSet(name=f"sa policy {sa.group(1)}", transforms=transforms)
            )

    if not found_any:
        return
    for gateway in keyed_gateways:
        tunnel = by_gateway.get(gateway)
        peer = tunnel.destination if tunnel else None
        crypto.keyed_peers.append(peer or f"gateway {gateway}")
    crypto.ike_policies.extend(policies.values())
    parsed.crypto = crypto
