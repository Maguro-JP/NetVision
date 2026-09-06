"""通信の制限・アドレス変換・アドレス配布・優先制御・暗号化を、中身まで読む。

`features.py` は「その機能が入っているか」を数えるところで止めている。
台帳としてはそれで足りるが、設計を見るには足りない。ACL は上から順に
読まれるので並びが要る。NAT は overload かどうかで外から入れるかが変わる。
IPsec は方式の強さが分かっていないと、古いまま残っているのを言えない。

ここは「書いてあることを、そのままの構造で取り出す」層である。良し悪しの
判断はしない。判断は `policyreview.py` の仕事。分けておかないと、読み方を
変えたいだけのときに判断まで壊れる。

読めなかった行は黙って捨てる。実機の出力には show の見出しや折り返しが
混ざるので、1行読めないだけで全体を諦めるほうが困る。
"""

from __future__ import annotations

import re

from app.services.configparse.base import (
    ParsedAcl,
    ParsedAclEntry,
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
)
from app.services.configparse.ifname import expand, normalize


def parse_cisco_crypto(text: str, parsed: ParsedConfig) -> None:
    """拠点間の暗号化だけを読む。

    ASA は ACL も NAT も書き方が違うが、VPN のところは IOS と同じ書き方に
    なっている。そこだけ切り出して使えるようにしておく。
    """
    parsed.crypto = _crypto(text)


def parse_cisco(text: str, parsed: ParsedConfig) -> None:
    """Cisco IOS / IOS-XE / NX-OS の書き方で読む。"""
    parsed.acls = _acls(text)
    _apply_acls(text, parsed.acls)
    parsed.nat = _nat(text)
    parsed.dhcp = _dhcp(text)
    parsed.qos = _qos(text)
    parsed.crypto = _crypto(text)


# ---------------------------------------------------------------------------
# 見出しと、そこにぶら下がる行
# ---------------------------------------------------------------------------


def _blocks(text: str, head: re.Pattern[str]) -> list[tuple[re.Match[str], list[str]]]:
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


_INTERFACE = re.compile(r"^interface\s+(\S+)\s*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 通信の制限（ACL）
# ---------------------------------------------------------------------------

_NAMED_ACL = re.compile(r"^ip(?:v6)?\s+access-list\s+(standard|extended)?\s*(\S+)", re.IGNORECASE)
_NUMBERED_ACL = re.compile(r"^access-list\s+(\d+)\s+(.+)$", re.IGNORECASE)
_PORT_OPERATOR = {"eq", "neq", "gt", "lt", "range"}
#: 番号だけで種別が決まる。標準 ACL は宛先を書けないので、読み方を変える
_STANDARD_RANGES = ((1, 99), (1300, 1999))


def _acls(text: str) -> list[ParsedAcl]:
    found: dict[str, ParsedAcl] = {}

    for head, body in _blocks(text, _NAMED_ACL):
        kind = (head.group(1) or "named").lower()
        name = head.group(2)
        acl = found.setdefault(name, ParsedAcl(name=name, kind=kind))
        acl.kind = kind
        for line in body:
            entry = _acl_entry(line, standard=kind == "standard")
            if entry is not None:
                acl.entries.append(entry)

    for line in text.splitlines():
        match = _NUMBERED_ACL.match(line.strip())
        if not match:
            continue
        number = match.group(1)
        standard = any(low <= int(number) <= high for low, high in _STANDARD_RANGES)
        acl = found.setdefault(
            number, ParsedAcl(name=number, kind="standard" if standard else "extended")
        )
        entry = _acl_entry(match.group(2), standard=standard)
        if entry is not None:
            acl.entries.append(entry)

    return list(found.values())


def acl_entry(line: str) -> ParsedAclEntry | None:
    """IOS 風の1行（`10 permit tcp any any eq 443`）を読む。他の読み手からも使う。"""
    return _acl_entry(line, standard=False)


def _acl_entry(line: str, *, standard: bool) -> ParsedAclEntry | None:
    """1行を、順番・許否・送信元・宛先に分ける。"""
    raw = line.strip()
    tokens = raw.split()
    if not tokens:
        return None

    seq = None
    heads = {"permit", "deny", "remark"}
    if tokens[0].isdigit() and len(tokens) > 1 and tokens[1].lower() in heads:
        seq, tokens = tokens[0], tokens[1:]

    head = tokens[0].lower()
    if head == "remark":
        return ParsedAclEntry(seq=seq, action="remark", remark=" ".join(tokens[1:]), raw=raw)
    if head not in {"permit", "deny"}:
        return None

    entry = ParsedAclEntry(seq=seq, action=head, raw=raw)
    rest = tokens[1:]
    entry.log = any(token.lower().startswith("log") for token in rest)

    if standard:
        entry.source, _ = _address(rest, 0)
        entry.destination = "any"
        return entry

    if not rest:
        return entry
    entry.protocol = rest[0].lower()
    index = 1
    entry.source, index = _address(rest, index)
    entry.source_port, index = _port(rest, index)
    entry.destination, index = _address(rest, index)
    entry.destination_port, _ = _port(rest, index)
    return entry


def _address(tokens: list[str], index: int) -> tuple[str | None, int]:
    """`any` / `host X` / `X 0.0.0.255` / `object-group NAME` を1つ読む。"""
    if index >= len(tokens):
        return None, index
    token = tokens[index].lower()
    if token == "any":
        return "any", index + 1
    if token in {"host", "object-group", "addrgroup"} and index + 1 < len(tokens):
        return f"{token} {tokens[index + 1]}", index + 2
    if "/" in tokens[index]:
        return tokens[index], index + 1
    if index + 1 < len(tokens) and _looks_like_mask(tokens[index + 1]):
        return f"{tokens[index]} {tokens[index + 1]}", index + 2
    return tokens[index], index + 1


def _looks_like_mask(token: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+\.\d+\.\d+", token))


def _port(tokens: list[str], index: int) -> tuple[str | None, int]:
    if index >= len(tokens) or tokens[index].lower() not in _PORT_OPERATOR:
        return None, index
    operator = tokens[index].lower()
    width = 2 if operator == "range" else 1
    values = tokens[index + 1 : index + 1 + width]
    return " ".join([operator, *values]), index + 1 + width


def _apply_acls(text: str, acls: list[ParsedAcl]) -> None:
    """どこに、どちら向きで当てているかを書き足す。

    当てていない ACL は、書いてあっても何もしていない。逆に、当てている
    のに中身が deny だけなら全部落ちる。どちらも当て先を見ないと言えない。
    """
    by_name = {acl.name: acl for acl in acls}
    for head, body in _blocks(text, _INTERFACE):
        interface = expand(head.group(1))
        for line in body:
            match = re.match(
                r"^(?:ip\s+|ipv6\s+)?(?:access-group|traffic-filter|port\s+access-group)"
                r"\s+(\S+)\s+(in|out)\b",
                line,
                re.IGNORECASE,
            )
            if match and match.group(1) in by_name:
                direction = "入り" if match.group(2).lower() == "in" else "出"
                by_name[match.group(1)].applied_to.append(f"interface {interface} {direction}")

    for line in text.splitlines():
        stripped = line.strip()
        for pattern, label in (
            (r"^ip nat (?:inside|outside) source (?:list|route-map)\s+(\S+)", "NAT"),
            (r"^\s*match address\s+(\S+)", "crypto map"),
            (r"^\s*match access-group(?:\s+name)?\s+(\S+)", "class-map"),
            (r"^\s*match ip address\s+(\d+|\S+)\s*$", "route-map"),
            (r"^access-class\s+(\S+)\s+(?:in|out)", "vty"),
            (r"^\s*ip access-class\s+(\S+)\s+(?:in|out)", "vty"),
            (r"^snmp-server community\s+\S+\s+(?:RO|RW)\s+(\S+)", "SNMP"),
        ):
            match = re.match(pattern, stripped, re.IGNORECASE)
            if match and match.group(1) in by_name:
                acl = by_name[match.group(1)]
                if label not in acl.referenced_by:
                    acl.referenced_by.append(label)
                break


# ---------------------------------------------------------------------------
# アドレス変換（NAT / NAPT）
# ---------------------------------------------------------------------------

_NAT_POOL = re.compile(
    r"^ip nat pool\s+(?P<name>\S+)\s+(?P<start>\S+)\s+(?P<end>\S+)"
    r"(?:\s+netmask\s+(?P<netmask>\S+)|\s+prefix-length\s+(?P<length>\d+))?",
    re.IGNORECASE,
)
_NAT_SOURCE = re.compile(
    r"^ip nat (?P<direction>inside|outside) source\s+(?P<rest>.+)$", re.IGNORECASE
)


def _nat(text: str) -> ParsedNat | None:
    nat = ParsedNat()
    seen = False

    for line in text.splitlines():
        stripped = line.strip()
        pool = _NAT_POOL.match(stripped)
        if pool:
            seen = True
            netmask = pool.group("netmask")
            if netmask is None and pool.group("length"):
                netmask = f"/{pool.group('length')}"
            nat.pools.append(
                ParsedNatPool(
                    name=pool.group("name"),
                    start=pool.group("start"),
                    end=pool.group("end"),
                    netmask=netmask,
                )
            )
            continue
        source = _NAT_SOURCE.match(stripped)
        if source:
            seen = True
            rule = _nat_rule(source.group("direction").lower(), source.group("rest"), stripped)
            if rule is not None:
                nat.rules.append(rule)

    for head, body in _blocks(text, _INTERFACE):
        interface = expand(head.group(1))
        for line in body:
            match = re.match(r"^ip nat (inside|outside)\s*$", line, re.IGNORECASE)
            if not match:
                continue
            seen = True
            if match.group(1).lower() == "inside":
                nat.inside_interfaces.append(interface)
            else:
                nat.outside_interfaces.append(interface)

    return nat if seen else None


def _nat_rule(direction: str, rest: str, raw: str) -> ParsedNatRule | None:
    tokens = rest.split()
    if not tokens:
        return None
    overload = any(token.lower() == "overload" for token in tokens)
    head = tokens[0].lower()

    if head in {"list", "route-map"}:
        rule = ParsedNatRule(
            kind="dynamic", direction=direction, matched=tokens[1] if len(tokens) > 1 else None
        )
        rule.overload = overload
        rule.raw = raw
        for index, token in enumerate(tokens):
            if token.lower() in {"pool", "interface"} and index + 1 < len(tokens):
                rule.translated = tokens[index + 1]
        return rule

    if head == "static":
        body = tokens[1:]
        protocol = None
        if body and body[0].lower() in {"tcp", "udp"}:
            protocol = body[0].lower()
            body = body[1:]
        addresses = [token for token in body if not token.lower().startswith("extendable")]
        if protocol:
            # static tcp 10.0.0.1 80 203.0.113.1 80
            matched = " ".join(addresses[:2]) if len(addresses) >= 2 else None
            translated = " ".join(addresses[2:4]) if len(addresses) >= 4 else None
            kind = "static-port"
        else:
            matched = addresses[0] if addresses else None
            translated = addresses[1] if len(addresses) > 1 else None
            kind = "static"
        return ParsedNatRule(
            kind=kind,
            direction=direction,
            matched=matched,
            translated=translated,
            protocol=protocol,
            raw=raw,
        )
    return None


# ---------------------------------------------------------------------------
# アドレス配布（DHCP）
# ---------------------------------------------------------------------------

_DHCP_POOL = re.compile(r"^ip dhcp pool\s+(\S+)", re.IGNORECASE)
_DHCP_EXCLUDED = re.compile(r"^ip dhcp excluded-address\s+(\S+)(?:\s+(\S+))?", re.IGNORECASE)


def _dhcp(text: str) -> ParsedDhcp | None:
    dhcp = ParsedDhcp()
    seen = False

    for head, body in _blocks(text, _DHCP_POOL):
        seen = True
        pool = ParsedDhcpPool(name=head.group(1))
        for line in body:
            network = re.match(r"^network\s+(\S+)(?:\s+(\S+))?", line, re.IGNORECASE)
            if network:
                mask = network.group(2) or ""
                pool.network = f"{network.group(1)} {mask}".strip()
                continue
            router = re.match(r"^default-router\s+(.+)$", line, re.IGNORECASE)
            if router:
                pool.default_routers = router.group(1).split()
                continue
            dns = re.match(r"^dns-server\s+(.+)$", line, re.IGNORECASE)
            if dns:
                pool.dns_servers = dns.group(1).split()
                continue
            domain = re.match(r"^domain-name\s+(\S+)", line, re.IGNORECASE)
            if domain:
                pool.domain = domain.group(1)
                continue
            lease = re.match(r"^lease\s+(.+)$", line, re.IGNORECASE)
            if lease:
                pool.lease = lease.group(1).strip()
        dhcp.pools.append(pool)

    for line in text.splitlines():
        excluded = _DHCP_EXCLUDED.match(line.strip())
        if excluded:
            seen = True
            dhcp.excluded.append((excluded.group(1), excluded.group(2) or excluded.group(1)))

    for head, body in _blocks(text, _INTERFACE):
        interface = expand(head.group(1))
        for line in body:
            relay = re.match(r"^ip helper-address\s+(\S+)", line, re.IGNORECASE)
            if relay:
                seen = True
                dhcp.relays.append((interface, relay.group(1)))

    return dhcp if seen else None


# ---------------------------------------------------------------------------
# 優先制御（QoS）
# ---------------------------------------------------------------------------

_CLASS_MAP = re.compile(r"^class-map\s+(?:(match-all|match-any)\s+)?(\S+)", re.IGNORECASE)
_POLICY_MAP = re.compile(r"^policy-map\s+(\S+)", re.IGNORECASE)


def _qos(text: str) -> ParsedQos | None:
    qos = ParsedQos()
    seen = False

    for head, body in _blocks(text, _CLASS_MAP):
        seen = True
        entry = ParsedQosClass(
            name=head.group(2), match_type=(head.group(1) or "match-all").lower()
        )
        for line in body:
            if re.match(r"^match\s+", line, re.IGNORECASE):
                entry.matches.append(line.strip())
        qos.classes.append(entry)

    for head, body in _blocks(text, _POLICY_MAP):
        seen = True
        policy = ParsedQosPolicy(name=head.group(1))
        current: ParsedQosAction | None = None
        for line in body:
            match = re.match(r"^class\s+(\S+)", line, re.IGNORECASE)
            if match:
                current = ParsedQosAction(class_name=match.group(1))
                policy.classes.append(current)
                continue
            if current is not None:
                current.actions.append(line.strip())
        qos.policies.append(policy)

    for head, body in _blocks(text, _INTERFACE):
        interface = expand(head.group(1))
        for line in body:
            match = re.match(r"^service-policy\s+(input|output)\s+(\S+)", line, re.IGNORECASE)
            if match:
                seen = True
                qos.applied.append((interface, match.group(1).lower(), match.group(2)))

    return qos if seen else None


# ---------------------------------------------------------------------------
# 拠点間の暗号化（IPsec / VPN）
# ---------------------------------------------------------------------------

_ISAKMP_POLICY = re.compile(r"^crypto isakmp policy\s+(\S+)", re.IGNORECASE)
_IKEV2_PROPOSAL = re.compile(r"^crypto ikev2 proposal\s+(\S+)", re.IGNORECASE)
_TRANSFORM_SET = re.compile(r"^crypto ipsec transform-set\s+(\S+)\s+(.+)$", re.IGNORECASE)
_CRYPTO_MAP = re.compile(
    r"^crypto map\s+(?P<name>\S+)\s+(?P<seq>\d+)\s+(?P<kind>\S+)", re.IGNORECASE
)
_ISAKMP_KEY = re.compile(r"^crypto isakmp key\s+\S+\s+address\s+(\S+)", re.IGNORECASE)


def _crypto(text: str) -> ParsedCrypto | None:
    crypto = ParsedCrypto()
    seen = False

    for head, body in _blocks(text, _ISAKMP_POLICY):
        seen = True
        policy = ParsedIkePolicy(number=head.group(1), version=1)
        for line in body:
            _ike_line(policy, line)
        crypto.ike_policies.append(policy)

    for head, body in _blocks(text, _IKEV2_PROPOSAL):
        seen = True
        policy = ParsedIkePolicy(number=head.group(1), version=2)
        for line in body:
            _ike_line(policy, line)
        crypto.ike_policies.append(policy)

    for head, body in _blocks(text, _TRANSFORM_SET):
        seen = True
        transform = ParsedTransformSet(name=head.group(1), transforms=head.group(2).split())
        for line in body:
            mode = re.match(r"^mode\s+(\S+)", line, re.IGNORECASE)
            if mode:
                transform.mode = mode.group(1).lower()
        crypto.transform_sets.append(transform)

    for head, body in _blocks(text, _CRYPTO_MAP):
        seen = True
        entry = ParsedCryptoMapEntry(
            name=head.group("name"), seq=head.group("seq"), kind=head.group("kind").lower()
        )
        for line in body:
            peer = re.match(r"^set peer\s+(\S+)", line, re.IGNORECASE)
            if peer:
                entry.peers.append(peer.group(1))
                continue
            transform = re.match(r"^set transform-set\s+(.+)$", line, re.IGNORECASE)
            if transform:
                entry.transform_sets = transform.group(1).split()
                continue
            address = re.match(r"^match address\s+(\S+)", line, re.IGNORECASE)
            if address:
                entry.match_address = address.group(1)
                continue
            pfs = re.match(r"^set pfs\s+(\S+)", line, re.IGNORECASE)
            if pfs:
                entry.pfs = pfs.group(1).lower()
                continue
            profile = re.match(
                r"^set (?:ikev2-profile|isakmp-profile)\s+(\S+)", line, re.IGNORECASE
            )
            if profile:
                entry.profile = profile.group(1)
        crypto.maps.append(entry)

    for line in text.splitlines():
        key = _ISAKMP_KEY.match(line.strip())
        if key:
            seen = True
            crypto.keyed_peers.append(key.group(1))

    for head, body in _blocks(text, _INTERFACE):
        interface = expand(head.group(1))
        for line in body:
            match = re.match(r"^crypto map\s+(\S+)\s*$", line, re.IGNORECASE)
            if match:
                seen = True
                crypto.applied.append((interface, match.group(1)))

    return crypto if seen else None


def _ike_line(policy: ParsedIkePolicy, line: str) -> None:
    for pattern, name in (
        (r"^encryption\s+(.+)$", "encryption"),
        (r"^integrity\s+(.+)$", "hash"),
        (r"^hash\s+(.+)$", "hash"),
        (r"^authentication\s+(.+)$", "authentication"),
        (r"^group\s+(.+)$", "group"),
    ):
        match = re.match(pattern, line, re.IGNORECASE)
        if match:
            setattr(policy, name, match.group(1).strip().lower())
            return
    lifetime = re.match(r"^lifetime\s+(\d+)", line, re.IGNORECASE)
    if lifetime:
        policy.lifetime = int(lifetime.group(1))


# ---------------------------------------------------------------------------
# ベンダーごとの、通信の制限
# ---------------------------------------------------------------------------
#
# 書き方はまるで違うが、意味は同じところへ落ちる。「上から順に見て、最初に
# 当たった行で決まる」も同じ。だから同じ器（ParsedAcl）に入れておけば、
# 影の行を見つける仕組みは1つで足りる。

_QX_ACL = re.compile(
    r"^acl\s+(?:number\s+)?(?:(?:advanced|basic|mac|ipv6)\s+)?(?:name\s+)?(\S+)", re.IGNORECASE
)
_QX_RULE = re.compile(r"^rule\s+(?:(\d+)\s+)?(permit|deny)\s*(.*)$", re.IGNORECASE)


def parse_comware(text: str, parsed: ParsedConfig) -> None:
    """NEC QX（H3C Comware 系）の acl / rule を読む。"""
    acls: dict[str, ParsedAcl] = {}
    for head, body in _blocks(text, _QX_ACL):
        name = head.group(1)
        acl = acls.setdefault(name, ParsedAcl(name=name, kind="extended"))
        for line in body:
            rule = _QX_RULE.match(line)
            if rule:
                acl.entries.append(_comware_entry(rule, line))

    for head, body in _blocks(text, _INTERFACE):
        interface = expand(head.group(1))
        for line in body:
            match = re.match(r"^packet-filter\s+(\S+)\s+(inbound|outbound)", line, re.IGNORECASE)
            if match and match.group(1) in acls:
                direction = "入り" if match.group(2).lower() == "inbound" else "出"
                acls[match.group(1)].applied_to.append(f"interface {interface} {direction}")
                continue
            # VRP は向きを先に書く（`traffic-filter inbound acl 3000`）。
            # Comware の並びだけを見ていると、当ててあるのに当たっていないと言う
            match = re.match(
                r"^traffic-filter\s+(inbound|outbound)\s+acl\s+(?:number\s+|name\s+)?(\S+)",
                line,
                re.IGNORECASE,
            )
            if match and match.group(2) in acls:
                direction = "入り" if match.group(1).lower() == "inbound" else "出"
                acls[match.group(2)].applied_to.append(f"interface {interface} {direction}")

    parsed.acls.extend(acls.values())


def _comware_entry(rule: re.Match[str], raw: str) -> ParsedAclEntry:
    """`rule 5 permit tcp source 10.0.0.0 0.0.0.255 destination-port eq 80` を読む。

    Comware は何をどこに書くかを言葉で示すので、位置ではなく言葉で拾う。
    """
    rest = rule.group(3).split()
    entry = ParsedAclEntry(seq=rule.group(1), action=rule.group(2).lower(), raw=raw)
    if rest and not rest[0].lower().startswith(("source", "destination", "logging")):
        entry.protocol = rest[0].lower()
        rest = rest[1:]
    entry.log = any(token.lower() == "logging" for token in rest)

    index = 0
    while index < len(rest):
        keyword = rest[index].lower()
        if keyword in {"source", "destination"}:
            value, index = _address(rest, index + 1)
            setattr(entry, keyword, value)
            continue
        if keyword in {"source-port", "destination-port"}:
            value, index = _port(rest, index + 1)
            setattr(entry, keyword.replace("-", "_"), value)
            continue
        index += 1
    # 何も書かなければ、どこからどこへでも当たる
    entry.source = entry.source or "any"
    entry.destination = entry.destination or "any"
    return entry


_IX_ACL = re.compile(
    r"^ip access-list\s+(?P<name>\S+)\s+(?P<action>permit|deny)\s+(?P<rest>.+)$", re.IGNORECASE
)
_IX_FILTER = re.compile(r"^ip filter\s+(\S+)\s+\d+\s+(in|out)\b", re.IGNORECASE)


def parse_ix(text: str, parsed: ParsedConfig) -> None:
    """NEC IX の ip access-list を読む。1行で1つの規則を書く形。"""
    acls: dict[str, ParsedAcl] = {}
    for raw in text.splitlines():
        match = _IX_ACL.match(raw.strip())
        if not match:
            continue
        acl = acls.setdefault(match.group("name"), ParsedAcl(name=match.group("name")))
        acl.entries.append(_ix_entry(match, raw.strip()))

    for head, body in _blocks(text, _INTERFACE):
        interface = expand(head.group(1))
        for line in body:
            match = _IX_FILTER.match(line)
            if match and match.group(1) in acls:
                direction = "入り" if match.group(2).lower() == "in" else "出"
                acls[match.group(1)].applied_to.append(f"interface {interface} {direction}")

    parsed.acls.extend(acls.values())


def _ix_entry(match: re.Match[str], raw: str) -> ParsedAclEntry:
    """`permit ip src 10.0.0.0/24 dest any` を読む。"""
    entry = ParsedAclEntry(action=match.group("action").lower(), raw=raw)
    rest = match.group("rest").split()
    if rest:
        entry.protocol = rest[0].lower()
        rest = rest[1:]
    index = 0
    while index < len(rest):
        keyword = rest[index].lower()
        if keyword in {"src", "source"} and index + 1 < len(rest):
            entry.source = rest[index + 1]
            index += 2
            continue
        if keyword in {"dest", "destination"} and index + 1 < len(rest):
            entry.destination = rest[index + 1]
            index += 2
            continue
        if keyword in {"sport", "dport"} and index + 1 < len(rest):
            field = "source_port" if keyword == "sport" else "destination_port"
            setattr(entry, field, f"eq {rest[index + 1]}")
            index += 2
            continue
        index += 1
    entry.source = entry.source or "any"
    entry.destination = entry.destination or "any"
    return entry


_JUNOS_TERM = re.compile(
    r"^set firewall(?:\s+family\s+\S+)?\s+filter\s+(?P<name>\S+)\s+term\s+(?P<term>\S+)\s+"
    r"(?P<rest>.+)$",
    re.IGNORECASE,
)
_JUNOS_APPLY = re.compile(
    r"^set interfaces\s+(?P<interface>\S+)(?:\s+unit\s+(?P<unit>\S+))?"
    r".*?\bfilter\s+(?P<direction>input|output)\s+(?P<name>\S+)",
    re.IGNORECASE,
)


def parse_junos(text: str, parsed: ParsedConfig) -> None:
    """Junos の firewall filter を読む。term ごとに1つの規則にまとめる。"""
    acls: dict[str, ParsedAcl] = {}
    terms: dict[tuple[str, str], ParsedAclEntry] = {}

    for raw in text.splitlines():
        match = _JUNOS_TERM.match(raw.strip())
        if not match:
            continue
        name, term = match.group("name"), match.group("term")
        acl = acls.setdefault(name, ParsedAcl(name=name, kind="filter"))
        entry = terms.get((name, term))
        if entry is None:
            entry = ParsedAclEntry(seq=term, action="permit", raw=f"term {term}")
            terms[(name, term)] = entry
            acl.entries.append(entry)
        _junos_term(entry, match.group("rest"))

    for raw in text.splitlines():
        match = _JUNOS_APPLY.match(raw.strip())
        if match and match.group("name") in acls:
            direction = "入り" if match.group("direction").lower() == "input" else "出"
            unit = f".{match.group('unit')}" if match.group("unit") else ""
            acls[match.group("name")].applied_to.append(
                f"interface {match.group('interface')}{unit} {direction}"
            )

    parsed.acls.extend(acls.values())


def _junos_term(entry: ParsedAclEntry, rest: str) -> None:
    """1つの term に、from（条件）と then（結果）が何行かに分かれて書かれる。"""
    tokens = rest.split()
    if not tokens:
        return
    if tokens[0].lower() == "then":
        result = tokens[1].lower() if len(tokens) > 1 else ""
        if result in {"discard", "reject"}:
            entry.action = "deny"
        elif result == "accept":
            entry.action = "permit"
        if any(token.lower().startswith(("log", "syslog")) for token in tokens):
            entry.log = True
        entry.raw = f"{entry.raw} then {result}".strip()
        return
    if tokens[0].lower() != "from" or len(tokens) < 3:
        return
    keyword, value = tokens[1].lower(), tokens[2]
    fields = {
        "source-address": "source",
        "destination-address": "destination",
        "protocol": "protocol",
    }
    if keyword in fields:
        setattr(entry, fields[keyword], value.lower() if keyword == "protocol" else value)
    elif keyword in {"source-port", "destination-port"}:
        setattr(entry, keyword.replace("-", "_"), f"eq {value}")
    entry.raw = f"{entry.raw} from {keyword} {value}"


_FORTI_POLICY = re.compile(r"^config firewall policy\s*$", re.IGNORECASE)
_FORTI_EDIT = re.compile(r"^edit\s+(\S+)", re.IGNORECASE)


def parse_fortios(text: str, parsed: ParsedConfig) -> None:
    """FortiOS の firewall policy を読む。

    ACL とは形が違うが、上から順に見て最初に当たった行で決まるところは同じ。
    同じ器に入れておけば、影の行を見つける仕組みをそのまま使える。

    許否は「どの口から、どの口へ」（srcintf / dstintf）の組み合わせごとに
    並ぶ。PAN-OS のゾーンと同じで、条件に含めておかないと別の口宛ての
    行どうしを「上が下を覆っている」と取り違える。アドレスの名前は
    `firewall address` の中身が分かるときだけ解決する。
    """
    values = _fortios_addresses(text)
    acl = ParsedAcl(name="firewall policy", kind="policy", applied_to=["ポリシー表"])
    current: ParsedAclEntry | None = None
    interfaces: dict[str, tuple[str, str]] = {}
    inside = False

    for raw in text.splitlines():
        line = raw.strip()
        if _FORTI_POLICY.match(line):
            inside = True
            continue
        if not inside:
            continue
        if line == "end":
            break
        edit = _FORTI_EDIT.match(line)
        if edit:
            current = ParsedAclEntry(
                seq=edit.group(1), action="deny", raw=f"policy {edit.group(1)}"
            )
            acl.entries.append(current)
            interfaces[current.seq or ""] = ("any", "any")
            continue
        if current is None:
            continue
        setting = re.match(r"^set\s+(\S+)\s+(.+)$", line, re.IGNORECASE)
        if not setting:
            continue
        name, value = setting.group(1).lower(), setting.group(2)
        if name in ("srcintf", "dstintf"):
            first = value.replace('"', "").split()
            side = first[0] if first else "any"
            source, target = interfaces[current.seq or ""]
            interfaces[current.seq or ""] = (side, target) if name == "srcintf" else (source, side)
            continue
        _fortios_setting(current, name, value)

    for entry in acl.entries:
        source_side, target_side = interfaces.get(entry.seq or "", ("any", "any"))
        entry.source_value = _fortios_resolved(source_side, entry.source, values)
        entry.destination_value = _fortios_resolved(target_side, entry.destination, values)
        entry.source = _fortios_scoped(source_side, entry.source)
        entry.destination = _fortios_scoped(target_side, entry.destination)

    if acl.entries:
        parsed.acls.append(acl)


def _fortios_addresses(text: str) -> dict[str, str]:
    """`firewall address` の中身。subnet の形だけ解決する（範囲や FQDN は名前のまま）。"""
    from app.services.configparse import fortitext

    values: dict[str, str] = {}
    for name, settings in fortitext.blocks(text, "firewall address"):
        subnet = settings.get("subnet")
        if not subnet:
            continue
        parts = subnet.split()
        if len(parts) == 2 and _looks_like_mask(parts[1]):
            from app.services.configparse.routing import to_prefix

            prefix = to_prefix(parts[0], parts[1], wildcard=False)
            if prefix:
                values[name] = prefix
        elif "/" in subnet:
            values[name] = subnet
    return values


def _fortios_many(cleaned: str) -> str:
    """複数書ける欄。1つ目までを読み、残りは数で示す（PAN-OS と同じ）。

    まとめて1つの名前にすると、中身を解決したときに1つ目だけで
    「覆っている」と言ってしまう。2つ以上あるときは解決しない。
    """
    values = cleaned.split()
    if not values:
        return "any"
    head = "any" if values[0].lower() == "all" else values[0]
    if len(values) > 1:
        return f"{head} ほか{len(values) - 1}件"
    return head


def _fortios_scoped(side: str, address: str | None) -> str:
    """口とアドレスを1つの条件にする。口が any のときだけアドレスだけで言える。"""
    value = address or "any"
    return value if side == "any" else f"{side}:{value}"


def _fortios_resolved(side: str, address: str | None, values: dict[str, str]) -> str | None:
    if not address or address == "any":
        return None
    value = values.get(address)
    return None if value is None else _fortios_scoped(side, value)


def _fortios_setting(entry: ParsedAclEntry, name: str, value: str) -> None:
    cleaned = value.replace('"', "").strip()
    first = cleaned.split()[0] if cleaned.split() else cleaned
    if name == "action":
        entry.action = "permit" if first.lower() == "accept" else "deny"
    elif name == "status" and first.lower() == "disable":
        # 止めたままの行。効いていないので、並びの検算からは外す
        entry.action = "disabled"
    elif name == "srcaddr":
        entry.source = _fortios_many(cleaned)
    elif name == "dstaddr":
        entry.destination = _fortios_many(cleaned)
    elif name == "service":
        entry.destination_port = None if first.upper() == "ALL" else f"サービス {first}"
    elif name == "name":
        entry.remark = first
    elif name == "logtraffic" and first.lower() != "disable":
        entry.log = True
    entry.raw = f"{entry.raw} {name} {first}".strip()


# ---------------------------------------------------------------------------
# Junos の security policies（SRX）
# ---------------------------------------------------------------------------
#
# SRX の許否は、ゾーンの組み合わせごとに並ぶ。同じ「上から順に、最初に
# 当たった行」なので、器は ACL と同じものを使う。PAN-OS と同じく、条件には
# ゾーンを含めておく。含めないと、別のゾーン宛ての行どうしを「上が下を
# 覆っている」と取り違える。

_SRX_POLICY = re.compile(
    r"^set\s+security\s+policies\s+from-zone\s+(?P<from>\S+)\s+to-zone\s+(?P<to>\S+)\s+"
    r"policy\s+(?P<name>\S+)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
_SRX_ADDRESS = re.compile(
    r"^set\s+security\s+(?:zones\s+security-zone\s+\S+\s+)?address-book\s+"
    r"(?:global\s+)?address\s+(?P<name>\S+)\s+(?P<value>\S+)",
    re.IGNORECASE,
)
_SRX_ZONE_INTERFACE = re.compile(
    r"^set\s+security\s+zones\s+security-zone\s+(?P<zone>\S+)\s+interfaces\s+(?P<interface>\S+)",
    re.IGNORECASE,
)
_SRX_NAT_SOURCE = re.compile(
    r"^set\s+security\s+nat\s+source\s+rule-set\s+(?P<set>\S+)\s+(?P<rest>.+)$", re.IGNORECASE
)
_SRX_NAT_STATIC = re.compile(
    r"^set\s+security\s+nat\s+static\s+rule-set\s+(?P<set>\S+)\s+rule\s+(?P<rule>\S+)\s+"
    r"(?P<rest>.+)$",
    re.IGNORECASE,
)


def parse_junos_security(text: str, parsed: ParsedConfig) -> None:
    """SRX の許否・アドレス帳・ゾーン・source NAT を読む。"""
    values = _srx_addresses(text)
    _srx_zones(text, parsed)
    _srx_policies(text, parsed, values)
    _srx_nat(text, parsed)


def _srx_addresses(text: str) -> dict[str, str]:
    """アドレス帳。名前のままでは、覆っているかどうかを言えない。"""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        found = _SRX_ADDRESS.match(raw.strip())
        if found:
            values[found.group("name")] = found.group("value")
    return values


def _srx_zones(text: str, parsed: ParsedConfig) -> None:
    """どのポートがどのゾーンかを、ポートの説明に残す。

    ゾーンが分からないと、図の上で「外向きの口」がどれか分からない。
    """
    for raw in text.splitlines():
        found = _SRX_ZONE_INTERFACE.match(raw.strip())
        if not found:
            continue
        # ゾーンは論理の単位（`ge-0/0/1.0`）に付くが、台帳と図で見るのは
        # 物理のポート。同じ名前のポートが既にあれば、そちらに書く
        name = found.group("interface")
        base = name.split(".")[0]
        known = {item.key: item for item in parsed.interfaces}
        interface = known.get(normalize(base)) or parsed.add_interface(ParsedInterface(name=name))
        note = f"zone {found.group('zone')}"
        current = interface.description or ""
        if note not in current:
            interface.description = f"{current} / {note}".strip(" /") if current else note


def _srx_policies(text: str, parsed: ParsedConfig, values: dict[str, str]) -> None:
    rules: dict[tuple[str, str, str], ParsedAclEntry] = {}
    order: list[tuple[str, str, str]] = []
    zones: dict[tuple[str, str, str], tuple[str, str]] = {}

    for raw in text.splitlines():
        found = _SRX_POLICY.match(raw.strip())
        if not found:
            continue
        key = (found.group("from"), found.group("to"), found.group("name"))
        entry = rules.get(key)
        if entry is None:
            entry = ParsedAclEntry(
                seq=str(len(order) + 1),
                action="permit",
                raw=f"policy {found.group('name')}",
            )
            rules[key] = entry
            zones[key] = (found.group("from"), found.group("to"))
            order.append(key)
        _srx_policy_option(entry, found.group("rest"))

    if not order:
        return

    for key in order:
        entry = rules[key]
        source_zone, target_zone = zones[key]
        entry.source = _srx_scoped(source_zone, entry.source)
        entry.destination = _srx_scoped(target_zone, entry.destination)
        entry.source_value = _srx_resolved(source_zone, entry.source, values)
        entry.destination_value = _srx_resolved(target_zone, entry.destination, values)

    parsed.acls.append(
        ParsedAcl(
            name="security policies",
            kind="policy",
            entries=[rules[key] for key in order],
            applied_to=["ポリシー表"],
            stateful=True,  # SRX は戻りを自動で通す。出る側を広く開けるのが普通
        )
    )


def _srx_policy_option(entry: ParsedAclEntry, rest: str) -> None:
    words = rest.split()
    if not words:
        return
    head = words[0].lower()

    if head == "match" and len(words) >= 3:
        what, value = words[1].lower(), words[2]
        if what == "source-address":
            entry.source = _srx_more(entry.source, value)
        elif what == "destination-address":
            entry.destination = _srx_more(entry.destination, value)
        elif what == "application":
            entry.destination_port = f"アプリ {value}"
        elif what == "source-identity":
            entry.remark = f"利用者 {value}"
        return

    if head == "then" and len(words) >= 2:
        action = words[1].lower()
        if action in ("permit", "deny", "reject"):
            entry.action = "permit" if action == "permit" else "deny"
        elif action == "log":
            entry.log = True
        return

    if head == "description":
        entry.remark = " ".join(words[1:])


def _srx_more(current: str | None, value: str) -> str:
    """同じ欄に2行目以降が来たら、1つ目までを読み、残りは数で示す。

    最後の行で上書きすると、先に書いた条件が消える。PAN-OS と同じ形にする。
    """
    if current is None:
        return value
    head, _, rest = current.partition(" ほか")
    count = int(rest.rstrip("件")) if rest else 0
    return f"{head} ほか{count + 1}件"


def _srx_scoped(zone: str, address: str | None) -> str:
    """ゾーンとアドレスを1つの条件にする（PAN-OS と同じ考え方）。"""
    value = address or "any"
    return value if zone == "any" else f"{zone}:{value}"


def _srx_resolved(zone: str, scoped: str, values: dict[str, str]) -> str | None:
    _, _, address = scoped.partition(":") if ":" in scoped else ("", "", scoped)
    address = address or scoped
    if address in ("any", "any-ipv4", ""):
        return None
    value = values.get(address)
    return None if value is None else _srx_scoped(zone, value)


def _srx_nat(text: str, parsed: ParsedConfig) -> None:
    """source NAT（出ていく通信の変換）と static NAT を読む。"""
    nat = parsed.nat or ParsedNat()
    rules: dict[str, ParsedNatRule] = {}
    found_any = False

    for raw in text.splitlines():
        line = raw.strip()

        source = _SRX_NAT_SOURCE.match(line)
        if source:
            rest = source.group("rest").split()
            if len(rest) >= 2 and rest[0].lower() == "to" and rest[1].lower() == "zone":
                nat.outside_interfaces.append(rest[2] if len(rest) > 2 else "")
                continue
            if not rest or rest[0].lower() != "rule":
                continue
            found_any = True
            name = rest[1] if len(rest) > 1 else source.group("set")
            rule = rules.setdefault(
                name,
                ParsedNatRule(
                    kind="dynamic",
                    direction="inside",
                    matched="内側から出る通信",
                    matched_is_name=False,
                    overload=True,
                    raw=line,
                ),
            )
            body = rest[2:]
            matched = len(body) >= 3 and body[0].lower() == "match"
            if matched and body[1].lower() == "source-address":
                rule.matched = body[2]
            elif len(body) >= 2 and body[0].lower() == "then":
                rule.translated = "出口のアドレス" if body[-1].lower() == "interface" else body[-1]
            continue

        static = _SRX_NAT_STATIC.match(line)
        if static:
            found_any = True
            rule = rules.setdefault(
                f"static {static.group('rule')}",
                ParsedNatRule(kind="static", direction="outside", matched_is_name=False, raw=line),
            )
            body = static.group("rest").split()
            if len(body) >= 3 and body[0].lower() == "match":
                rule.matched = body[2]
            elif len(body) >= 2 and body[0].lower() == "then":
                rule.translated = body[-1]

    if not found_any:
        return
    nat.rules.extend(rules.values())
    nat.outside_interfaces = [name for name in nat.outside_interfaces if name]
    parsed.nat = nat
