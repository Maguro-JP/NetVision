"""Cisco ASA / FTD（ファイアウォール）の config を読む。

同じ Cisco でも、スイッチやルーターとは別物として扱う必要がある。ASA は
「どちら側が内側か」を security-level という数字で決めていて、通信の許否も
その数字と ACL の組み合わせで決まる。IOS と同じ器に入れてしまうと、
そこが落ちる。

読める書き方は、`show running-config` にそのまま出てくる形に絞ってある。

- `interface` の中の `nameif` / `security-level` / `ip address`
- `access-list` と `access-group`（当て先と向き）
- `object network` / `object-group` … ACL が名前で指しているものの実体
- NAT（object の中の `nat`、および行で書く twice NAT）

VPN（`crypto`）は IOS と同じ書き方なので、そちらの読み手をそのまま使う。
"""

from __future__ import annotations

import re

from app.services.configparse import policy, tunnels
from app.services.configparse.base import (
    ParsedAcl,
    ParsedAclEntry,
    ParsedConfig,
    ParsedInterface,
    ParsedNat,
    ParsedNatRule,
    ParsedRoute,
    Vendor,
)
from app.services.configparse.routing import to_prefix

_HOSTNAME = re.compile(r"^hostname\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_VERSION = re.compile(r"^ASA Version\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_MODEL = re.compile(r"^Hardware:\s+(\S+?),", re.IGNORECASE | re.MULTILINE)
_SERIAL = re.compile(r"^Serial Number:\s+(\S+)", re.IGNORECASE | re.MULTILINE)

_INTERFACE = re.compile(r"^interface\s+(\S+)", re.IGNORECASE)
_ACCESS_LIST = re.compile(r"^access-list\s+(\S+)\s+(.+)$", re.IGNORECASE)
_ACCESS_GROUP = re.compile(
    r"^access-group\s+(\S+)\s+(in|out)\s+interface\s+(\S+)",
    re.IGNORECASE | re.MULTILINE,
)
_OBJECT = re.compile(r"^object\s+(network|service)\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_OBJECT_GROUP = re.compile(r"^object-group\s+(\S+)\s+(\S+)", re.IGNORECASE | re.MULTILINE)
#: ASA の静的経路。`route <nameif> <宛先> <マスク> <次の相手> [距離]`
_ROUTE = re.compile(
    r"^route\s+(\S+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)"
    r"\s+(\S+)(?:\s+(\d+))?",
    re.IGNORECASE | re.MULTILINE,
)
_NAT_LINE = re.compile(r"^nat\s+\((\S+),(\S+)\)\s+(.+)$", re.IGNORECASE)
_OBJECT_NAT = re.compile(r"^\s+nat\s+\((\S+),(\S+)\)\s+(.+)$", re.IGNORECASE)


def detect(text: str) -> Vendor | None:
    """ASA かどうか。IOS と見分けが付く手掛かりだけで見る。

    `hostname` はどちらにもあるので使えない。ASA にしか無い書き方
    （nameif・security-level・ASA Version）で判断する。
    """
    if _VERSION.search(text):
        return Vendor.CISCO_ASA
    for pattern in (
        r"^\s*nameif\s+\S+",
        r"^\s*security-level\s+\d+",
        r"^access-group\s+\S+\s+(?:in|out)\s+interface\s",
        r"^same-security-traffic\s",
    ):
        if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
            return Vendor.CISCO_ASA
    return None


def parse(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.CISCO_ASA, os_name="ASA")

    found = _HOSTNAME.search(text)
    if found:
        parsed.hostname = found.group(1).strip('"')
    found = _VERSION.search(text)
    if found:
        parsed.os_version = found.group(1)
    found = _MODEL.search(text)
    if found:
        parsed.model = found.group(1)
    found = _SERIAL.search(text)
    if found:
        parsed.serial_number = found.group(1)

    _parse_interfaces(text, parsed)
    _parse_objects(text, parsed)
    _parse_acls(text, parsed)
    _parse_nat(text, parsed)
    _parse_routes(text, parsed)
    # トンネルと VPN は IOS と同じ書き方なので、そのまま任せる
    tunnels.parse_block_style(text, parsed)
    policy.parse_cisco_crypto(text, parsed)
    return parsed


# ---------------------------------------------------------------------------
# ポート
# ---------------------------------------------------------------------------


def _parse_interfaces(text: str, parsed: ParsedConfig) -> None:
    """ポートは名前（nameif）と、内側らしさ（security-level）で意味が決まる。

    `outside` と名付けても数字が 100 なら、内側と同じ扱いになる。名前では
    なく数字が効くので、両方を持っておく。
    """
    current: ParsedInterface | None = None
    for raw in text.splitlines():
        head = _INTERFACE.match(raw)
        if head:
            current = parsed.add_interface(ParsedInterface(name=head.group(1), mode="routed"))
            continue
        if raw and not raw.startswith((" ", "\t")):
            current = None
            continue
        if current is None:
            continue
        body = raw.strip()
        found = re.match(r"^nameif\s+(\S+)", body, re.IGNORECASE)
        if found:
            current.description = f"nameif {found.group(1)}"
            continue
        found = re.match(r"^security-level\s+(\d+)", body, re.IGNORECASE)
        if found:
            level = found.group(1)
            current.description = (
                f"{current.description} / security-level {level}"
                if current.description
                else f"security-level {level}"
            )
            continue
        found = re.match(r"^ip address\s+(\S+)\s+(\S+)", body, re.IGNORECASE)
        if found:
            current.ip_address = f"{found.group(1)} {found.group(2)}"
            continue
        found = re.match(r"^vlan\s+(\d+)", body, re.IGNORECASE)
        if found:
            current.vlan = int(found.group(1))
            continue
        if body == "shutdown":
            current.enabled = False


def _parse_routes(text: str, parsed: ParsedConfig) -> None:
    """静的経路。ASA は `ip route` ではなく、口の名前から書く。"""
    for where, destination, mask, next_hop, distance in _ROUTE.findall(text):
        # 宛先の書き方は他ベンダーとそろえる（10.20.0.0/16 の形）。
        # 揃えておかないと、経路どうしの突き合わせでだけ食い違う
        network = to_prefix(destination, mask)
        if network is None:
            continue
        parsed.routes.append(
            ParsedRoute(
                destination=network,
                next_hop=next_hop,
                interface=where,
                distance=int(distance) if distance else None,
            )
        )


def nameif_of(interface: ParsedInterface) -> str | None:
    """そのポートの nameif（説明に残してある）。"""
    if not interface.description:
        return None
    found = re.search(r"nameif\s+(\S+)", interface.description)
    return found.group(1) if found else None


def security_level_of(interface: ParsedInterface) -> int | None:
    if not interface.description:
        return None
    found = re.search(r"security-level\s+(\d+)", interface.description)
    return int(found.group(1)) if found else None


# ---------------------------------------------------------------------------
# 名前で書かれたもの
# ---------------------------------------------------------------------------


def _parse_objects(text: str, parsed: ParsedConfig) -> None:
    """`object` と `object-group` の名前を控える。

    中身までは持たない。ACL が指している名前が存在するかどうかを言うために、
    名前だけあればよい。中身の評価は、そこが読めていないと嘘になる。
    """
    for _, name in _OBJECT.findall(text):
        if name not in parsed.address_objects:
            parsed.address_objects.append(name)
    for _, name in _OBJECT_GROUP.findall(text):
        if name not in parsed.address_objects:
            parsed.address_objects.append(name)
    for word in ("any", "any4", "any6"):
        parsed.address_objects.append(word)


# ---------------------------------------------------------------------------
# 通信の制限
# ---------------------------------------------------------------------------


def _parse_acls(text: str, parsed: ParsedConfig) -> None:
    """ASA の access-list は1行ずつ。当て先は access-group で決まる。"""
    acls: dict[str, ParsedAcl] = {}
    order: dict[str, int] = {}

    for raw in text.splitlines():
        found = _ACCESS_LIST.match(raw.strip())
        if not found:
            continue
        name, rest = found.group(1), found.group(2)
        acl = acls.setdefault(name, ParsedAcl(name=name, kind="extended"))
        if rest.lower().startswith("remark"):
            continue
        order[name] = order.get(name, 0) + 1
        entry = _entry(rest, seq=str(order[name]), raw=raw.strip())
        if entry is not None:
            acl.entries.append(entry)

    for name, direction, where in _ACCESS_GROUP.findall(text):
        acl = acls.get(name)
        if acl is None:
            continue
        label = "入り" if direction.lower() == "in" else "出"
        acl.applied_to.append(f"interface {where} {label}")

    parsed.acls.extend(acls.values())


def _entry(rest: str, *, seq: str, raw: str) -> ParsedAclEntry | None:
    """`extended permit tcp any object WEB eq 443` のような1行を読む。

    アドレスは `host x.x.x.x` / `x.x.x.x 255.255.255.0` / `object NAME` /
    `object-group NAME` / `any` の5通りで書かれる。どれも1つの語として扱う。
    """
    tokens = rest.split()
    if not tokens:
        return None
    index = 0
    if tokens[index].lower() in ("extended", "standard"):
        index += 1
    if index >= len(tokens) or tokens[index].lower() not in ("permit", "deny"):
        return None
    entry = ParsedAclEntry(seq=seq, action=tokens[index].lower(), raw=raw)
    index += 1

    if index < len(tokens):
        protocol = tokens[index]
        if protocol.lower() in ("object-group", "object"):
            index += 1
            protocol = tokens[index] if index < len(tokens) else "ip"
        entry.protocol = None if protocol.lower() == "ip" else protocol.lower()
        index += 1

    entry.source, index = _address(tokens, index)
    # 送信元の後ろに来る object-group は、宛先のアドレスであってポートではない。
    # ポートとして食べてしまうと、宛先が1つずれる
    entry.source_port, index = _port(tokens, index, allow_object=False)
    entry.destination, index = _address(tokens, index)
    entry.destination_port, index = _port(tokens, index, allow_object=True)
    if "log" in [token.lower() for token in tokens[index:]]:
        entry.log = True
    return entry


def _address(tokens: list[str], index: int) -> tuple[str | None, int]:
    if index >= len(tokens):
        return None, index
    token = tokens[index].lower()
    if token in ("any", "any4", "any6"):
        return "any", index + 1
    if token in ("host", "object", "object-group", "interface"):
        value = tokens[index + 1] if index + 1 < len(tokens) else None
        return value, index + 2
    # `10.0.0.0 255.255.255.0` の形
    if index + 1 < len(tokens) and re.match(r"^\d+\.\d+\.\d+\.\d+$", tokens[index + 1]):
        return f"{tokens[index]} {tokens[index + 1]}", index + 2
    return tokens[index], index + 1


def _port(tokens: list[str], index: int, *, allow_object: bool) -> tuple[str | None, int]:
    if index >= len(tokens):
        return None, index
    token = tokens[index].lower()
    if token in ("eq", "neq", "lt", "gt"):
        value = tokens[index + 1] if index + 1 < len(tokens) else ""
        return f"{token} {value}".strip(), index + 2
    if token == "range":
        low = tokens[index + 1] if index + 1 < len(tokens) else ""
        high = tokens[index + 2] if index + 2 < len(tokens) else ""
        return f"range {low} {high}".strip(), index + 3
    if allow_object and token in ("object-group", "object"):
        value = tokens[index + 1] if index + 1 < len(tokens) else ""
        return f"サービス {value}", index + 2
    return None, index


# ---------------------------------------------------------------------------
# アドレス変換
# ---------------------------------------------------------------------------


def _parse_nat(text: str, parsed: ParsedConfig) -> None:
    """ASA の NAT は2通りの書き方がある。

    - object の中に書く（その object のアドレスを変換する）
    - 行で書く（twice NAT。送信元と宛先の組み合わせで変換する）

    どちらも「何が何に変わるか」は同じなので、同じ器へ入れる。
    """
    nat = ParsedNat()
    current_object: str | None = None

    for raw in text.splitlines():
        head = _OBJECT.match(raw)
        if head:
            current_object = head.group(2)
            continue
        if raw and not raw.startswith((" ", "\t")):
            current_object = None

        inside = _OBJECT_NAT.match(raw)
        if inside and current_object:
            nat.rules.append(_nat_rule(inside, matched=current_object, matched_is_name=True))
            _remember(nat, inside.group(1), inside.group(2))
            continue

        line = _NAT_LINE.match(raw.strip())
        if line:
            rest = line.group(3).split()
            matched = rest[1] if len(rest) > 1 and rest[0].lower() == "source" else None
            nat.rules.append(
                _nat_rule(
                    line, matched=matched or "内側から出る通信", matched_is_name=bool(matched)
                )
            )
            _remember(nat, line.group(1), line.group(2))

    if nat.rules:
        parsed.nat = nat


def _nat_rule(found: re.Match[str], *, matched: str, matched_is_name: bool) -> ParsedNatRule:
    rest = found.group(3)
    words = rest.split()
    kind = "dynamic" if "dynamic" in [w.lower() for w in words] else "static"
    translated = None
    for keyword in ("dynamic", "static"):
        if keyword in [w.lower() for w in words]:
            at = [w.lower() for w in words].index(keyword)
            if at + 1 < len(words):
                translated = words[at + 1]
            break
    # `interface` へ変換する形が、出口のアドレスに束ねる指定
    overload = kind == "dynamic"
    if translated and translated.lower() == "interface":
        translated = "出口のアドレス（interface）"
    return ParsedNatRule(
        kind=kind,
        direction="inside",
        matched=matched,
        translated=translated,
        overload=overload,
        matched_is_name=matched_is_name,
        raw=f"nat ({found.group(1)},{found.group(2)}) {rest}".strip(),
    )


def _remember(nat: ParsedNat, inside: str, outside: str) -> None:
    if inside not in nat.inside_interfaces:
        nat.inside_interfaces.append(inside)
    if outside not in nat.outside_interfaces:
        nat.outside_interfaces.append(outside)
