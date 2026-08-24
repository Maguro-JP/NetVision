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
    ParsedNat,
    ParsedNatPool,
    ParsedNatRule,
    ParsedQos,
    ParsedQosAction,
    ParsedQosClass,
    ParsedQosPolicy,
    ParsedTransformSet,
)
from app.services.configparse.ifname import expand


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


_INTERFACE = re.compile(r"^interface\s+(\S+)", re.IGNORECASE)


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
