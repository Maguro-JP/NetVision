"""Palo Alto（PAN-OS）の config を読む。

現場で貼られるのは `set` 形式が多い（`configure` してから `show` した形）。
1行が1つの設定で、階層をそのまま空白で並べる。ここではその形を読む。
XML 形式は別物なので読んでいない。

PAN-OS のポリシーも、上から順に見て最初に当たった行で決まる。そこは ACL と
同じなので、同じ器へ入れる。影の行を見つける仕組みがそのまま使える。

読める書き方:

- `set network interface ethernet <名前> layer3 ip <アドレス>`
- `set network virtual-router <名前> routing-table ip static-route ...`
- `set zone <名前> network layer3 <ポート>`
- `set rulebase security rules <名前> ...`（from / to / source / destination /
  service / application / action / disabled / log-end）
- `set rulebase nat rules <名前> ...`（source-translation / destination-translation）
"""

from __future__ import annotations

import re

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
from app.services.configparse.ifname import normalize

_SET = re.compile(r"^set\s+(.+)$", re.IGNORECASE)
_HOSTNAME = re.compile(
    r"^set\s+deviceconfig\s+system\s+hostname\s+(\S+)", re.IGNORECASE | re.MULTILINE
)
_MODEL = re.compile(r"^model:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_VERSION = re.compile(r"^sw-version:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_SERIAL = re.compile(r"^serial:\s*(\S+)", re.IGNORECASE | re.MULTILINE)


def detect(text: str) -> Vendor | None:
    """PAN-OS かどうか。この機種にしか無い並びで見る。"""
    for pattern in (
        r"^set\s+rulebase\s+security\s+rules\s",
        r"^set\s+network\s+interface\s+ethernet\s+\S+\s+layer3\s",
        r"^set\s+zone\s+\S+\s+network\s+layer3\s",
        r"^set\s+deviceconfig\s+system\s+hostname\s",
        r"^set\s+vsys\s+\S+\s+rulebase\s",
    ):
        if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
            return Vendor.PALO_ALTO
    return None


def parse(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.PALO_ALTO, os_name="PAN-OS")

    found = _HOSTNAME.search(text)
    if found:
        parsed.hostname = found.group(1)
    for pattern, name in ((_MODEL, "model"), (_VERSION, "os_version"), (_SERIAL, "serial_number")):
        found = pattern.search(text)
        if found:
            setattr(parsed, name, found.group(1))

    lines = _lines(text)
    values = _address_values(lines)
    _parse_interfaces(lines, parsed)
    _parse_zones(lines, parsed)
    _parse_routes(lines, parsed)
    _parse_rules(lines, parsed, values)
    _parse_nat(lines, parsed)
    _parse_objects(lines, parsed)
    return parsed


def _lines(text: str) -> list[list[str]]:
    """`set ...` の行を、語に分けて返す。

    vsys（仮想的な区切り）が頭に付く形もある。どの vsys のものかは、いまは
    見ていない（1台に1つの構成が大半のため）ので、頭を落として同じに扱う。
    """
    out: list[list[str]] = []
    for raw in text.splitlines():
        found = _SET.match(raw.strip())
        if not found:
            continue
        words = _split(found.group(1))
        if len(words) >= 2 and words[0].lower() == "vsys":
            words = words[2:]
        if words:
            out.append(words)
    return out


def _split(rest: str) -> list[str]:
    """語に分ける。`[ a b ]` のまとまりは1つの語にはせず、中身だけを並べる。"""
    return [word for word in rest.replace("[", " ").replace("]", " ").split() if word]


def _after(words: list[str], *keys: str) -> list[str]:
    """`keys` の並びで始まっていれば、その後ろを返す。違えば空。"""
    if len(words) < len(keys):
        return []
    for index, key in enumerate(keys):
        if words[index].lower() != key:
            return []
    return words[len(keys) :]


# ---------------------------------------------------------------------------
# ポートとゾーン
# ---------------------------------------------------------------------------


def _parse_interfaces(lines: list[list[str]], parsed: ParsedConfig) -> None:
    for words in lines:
        rest = _after(words, "network", "interface", "ethernet")
        if not rest:
            continue
        name = rest[0]
        interface = parsed.add_interface(ParsedInterface(name=name, mode="routed"))
        body = rest[1:]
        if not body:
            continue
        if body[0].lower() == "layer3":
            body = body[1:]
        if not body:
            continue
        key = body[0].lower()
        if key == "ip" and len(body) > 1:
            interface.ip_address = body[1]
        elif key == "comment" and len(body) > 1:
            interface.description = " ".join(body[1:]).strip('"')
        elif key == "units" and len(body) > 2:
            # ethernet1/1.100 のような子の口。番号を VLAN として持つ
            child = parsed.add_interface(ParsedInterface(name=body[1], mode="routed"))
            tagged = _after(body[2:], "tag")
            if tagged:
                child.vlan = int(tagged[0]) if tagged[0].isdigit() else None
            addressed = _after(body[2:], "ip")
            if addressed:
                child.ip_address = addressed[0]


def _parse_zones(lines: list[list[str]], parsed: ParsedConfig) -> None:
    """ゾーンは、ポートの説明に残す。

    PAN-OS の許否はゾーンの組み合わせで決まるので、どのポートがどのゾーンかが
    分からないと、ポリシーを読んでも現場の口と結び付かない。
    """
    for words in lines:
        rest = _after(words, "zone")
        if len(rest) < 4:
            continue
        zone = rest[0]
        if [w.lower() for w in rest[1:4]] != ["network", "layer3"] and (
            [w.lower() for w in rest[1:3]] != ["network", "layer3"]
        ):
            continue
        for name in rest[3:]:
            for interface in parsed.interfaces:
                # 取り込むときに表記をそろえているので、比べるときも同じ形にする
                if normalize(interface.name) == normalize(name):
                    interface.description = (
                        f"{interface.description} / zone {zone}"
                        if interface.description
                        else f"zone {zone}"
                    )


def zone_of(interface: ParsedInterface) -> str | None:
    if not interface.description:
        return None
    found = re.search(r"zone\s+(\S+)", interface.description)
    return found.group(1) if found else None


# ---------------------------------------------------------------------------
# 経路
# ---------------------------------------------------------------------------


def _parse_routes(lines: list[list[str]], parsed: ParsedConfig) -> None:
    routes: dict[str, ParsedRoute] = {}
    for words in lines:
        rest = _after(words, "network", "virtual-router")
        if len(rest) < 5:
            continue
        if [w.lower() for w in rest[1:4]] != ["routing-table", "ip", "static-route"]:
            continue
        name = rest[4]
        route = routes.setdefault(name, ParsedRoute(destination=""))
        body = rest[5:]
        if not body:
            continue
        key = body[0].lower()
        if key == "destination" and len(body) > 1:
            route.destination = body[1]
        elif key == "nexthop" and len(body) > 2 and body[1].lower() == "ip-address":
            route.next_hop = body[2]
        elif key == "interface" and len(body) > 1:
            route.interface = body[1]
        elif key == "metric" and len(body) > 1 and body[1].isdigit():
            route.distance = int(body[1])

    parsed.routes.extend(route for route in routes.values() if route.destination)


# ---------------------------------------------------------------------------
# セキュリティポリシー
# ---------------------------------------------------------------------------


def _address_values(lines: list[list[str]]) -> dict[str, str]:
    """名前で書かれたアドレスの中身。

    表には名前を出したいが、上の行が下の行を覆っているかどうかは、
    中身で比べないと言えない。両方持てるように、ここで対応表を作る。
    グループ（address-group）は、1つだけを指しているときだけ解く。
    """
    values: dict[str, str] = {}
    groups: dict[str, list[str]] = {}
    for words in lines:
        rest = _after(words, "address")
        if len(rest) >= 3 and rest[1].lower() in ("ip-netmask", "ip-range", "fqdn"):
            if rest[1].lower() == "ip-netmask":
                values[rest[0]] = rest[2]
            continue
        rest = _after(words, "address-group")
        if len(rest) >= 3 and rest[1].lower() == "static":
            groups[rest[0]] = rest[2:]

    for name, members in groups.items():
        if len(members) == 1 and members[0] in values:
            values[name] = values[members[0]]
    return values


def _parse_rules(lines: list[list[str]], parsed: ParsedConfig, values: dict[str, str]) -> None:
    """上から順に見て、最初に当たった行で決まる。ACL と同じ器へ入れる。"""
    rules: dict[str, ParsedAclEntry] = {}
    order: list[str] = []
    zones: dict[str, tuple[list[str], list[str]]] = {}

    for words in lines:
        rest = _after(words, "rulebase", "security", "rules")
        if len(rest) < 2:
            continue
        name = rest[0]
        if name not in rules:
            rules[name] = ParsedAclEntry(
                seq=str(len(order) + 1), action="permit", remark=name, raw=f"rule {name}"
            )
            order.append(name)
            zones[name] = ([], [])
        _rule_option(rules[name], zones[name], rest[1:])

    if not rules:
        return

    for name, entry in rules.items():
        source_zones, target_zones = zones[name]
        source_label = " ".join(source_zones) or "any"
        target_label = " ".join(target_zones) or "any"
        where = f"{source_label} → {target_label}"
        entry.raw = f"rule {name}（{where}）{entry.raw.removeprefix(f'rule {name}')}".strip()
        # ゾーンは条件の一部。ここを落とすと、別のゾーン宛ての行どうしを
        # 「上が下を覆っている」と読み違える。アドレスと一緒に持つ
        entry.source = _scoped(source_label, entry.source)
        entry.destination = _scoped(target_label, entry.destination)
        entry.source_value = _resolved(source_label, entry.source, values)
        entry.destination_value = _resolved(target_label, entry.destination, values)

    parsed.acls.append(
        ParsedAcl(
            name="security rules",
            kind="policy",
            entries=[rules[name] for name in order],
            applied_to=["ポリシー表"],
        )
    )


def _rule_option(
    entry: ParsedAclEntry, zones: tuple[list[str], list[str]], body: list[str]
) -> None:
    if not body:
        return
    key = body[0].lower()
    values = body[1:]
    if key == "from":
        zones[0].extend(values)
    elif key == "to":
        zones[1].extend(values)
    elif key == "source":
        entry.source = _one(values, entry.source)
    elif key == "destination":
        entry.destination = _one(values, entry.destination)
    elif key == "service":
        if values and values[0].lower() not in ("any", "application-default"):
            entry.destination_port = f"サービス {values[0]}"
    elif key == "application":
        if values and values[0].lower() != "any":
            entry.protocol = values[0].lower()
    elif key == "action":
        if values:
            # allow 以外（deny / drop / reset-both）は落とす側
            entry.action = "permit" if values[0].lower() == "allow" else "deny"
    elif key == "disabled":
        if values and values[0].lower() == "yes":
            # 止めてある行。並びの検算からは外す
            entry.action = "disabled"
    elif key in ("log-end", "log-start"):
        if values and values[0].lower() == "yes":
            entry.log = True


def _scoped(zone: str, address: str | None) -> str:
    """ゾーンとアドレスを1つの条件にする。

    どちらも any のときだけ「すべて」と言える。ゾーンが限られていれば、
    見た目が any でも、そのゾーンから来た通信にしか当たらない。
    """
    value = address or "any"
    if zone == "any":
        return value
    return f"{zone}:{value}"


def _resolved(zone: str, scoped: str, values: dict[str, str]) -> str | None:
    """名前の部分を中身に置き換えた形。分からなければ None。"""
    _, _, address = scoped.partition(":") if ":" in scoped else ("", "", scoped)
    address = address or scoped
    if address in ("any", ""):
        return None
    value = values.get(address)
    if value is None:
        return None
    return _scoped(zone, value)


def _one(values: list[str], current: str | None) -> str | None:
    """複数書ける欄。読めるのは1つ目までにして、残りは数で示す。"""
    if not values:
        return current
    head = "any" if values[0].lower() == "any" else values[0]
    if len(values) > 1:
        return f"{head} ほか{len(values) - 1}件"
    return head


# ---------------------------------------------------------------------------
# アドレス変換
# ---------------------------------------------------------------------------


def _parse_nat(lines: list[list[str]], parsed: ParsedConfig) -> None:
    nat = ParsedNat()
    rules: dict[str, ParsedNatRule] = {}
    order: list[str] = []
    #: 宛先変換のもとになるアドレス（外から見えているほう）
    targets: dict[str, str | None] = {}

    for words in lines:
        rest = _after(words, "rulebase", "nat", "rules")
        if len(rest) < 2:
            continue
        name = rest[0]
        if name not in rules:
            rules[name] = ParsedNatRule(
                kind="dynamic",
                direction="inside",
                matched="内側から出る通信",
                matched_is_name=False,
                raw=f"nat rule {name}",
            )
            order.append(name)
            targets[name] = None
        _nat_option(nat, rules[name], rest[1:], name, targets)

    if not rules:
        return
    nat.rules.extend(rules[name] for name in order)
    parsed.nat = nat


def _nat_option(
    nat: ParsedNat,
    rule: ParsedNatRule,
    body: list[str],
    name: str,
    targets: dict[str, str | None],
) -> None:
    if not body:
        return
    key = body[0].lower()
    values = body[1:]
    if key == "from":
        rule.raw = f"{rule.raw} from {' '.join(values)}"
    elif key == "to":
        rule.raw = f"{rule.raw} to {' '.join(values)}"
    elif key == "source" and values:
        if values[0].lower() != "any":
            rule.matched = _one(values, rule.matched)
    elif key == "destination" and values:
        targets[name] = _one(values, None)
    elif key == "source-translation":
        rule.kind = "dynamic"
        # 出口のアドレスに束ねる形（dynamic-ip-and-port）が、いわゆる NAPT
        rule.overload = "dynamic-ip-and-port" in [w.lower() for w in values]
        translated = _after(values, "dynamic-ip-and-port", "interface-address", "interface")
        if translated:
            rule.translated = f"出口のアドレス（{translated[0]}）"
        else:
            pool = [w for w in values if re.match(r"^\d+\.\d+\.\d+\.\d+", w)]
            if pool:
                rule.translated = pool[0]
    elif key == "destination-translation":
        rule.kind = "static"
        rule.direction = "outside"
        rule.overload = False
        # 外から見えているアドレスが、変換のもとになる
        if targets.get(name):
            rule.matched = targets[name]
        translated = _after(values, "translated-address")
        if translated:
            rule.translated = translated[0]
    elif key == "service" and values:
        rule.protocol = values[0]

    # 内側と外側は、出る側の変換（source-translation）を持つ規則から取る。
    # 入る側の規則の from を内側に混ぜると、どちらが内か分からなくなる
    if rule.kind == "dynamic" and rule.direction == "inside":
        _zones(nat.inside_interfaces, rule.raw, "from")
        _zones(nat.outside_interfaces, rule.raw, "to")


# ---------------------------------------------------------------------------
# 名前で書かれたもの
# ---------------------------------------------------------------------------


def _zones(into: list[str], raw: str, key: str) -> None:
    found = re.search(rf"\b{key}\s+([^\s]+)", raw)
    if found and found.group(1) not in into:
        into.append(found.group(1))


def _parse_objects(lines: list[list[str]], parsed: ParsedConfig) -> None:
    """`address` と `address-group` と `service` の名前を控える。

    中身までは持たない。ポリシーが指している名前が存在するかどうかを
    言うために、名前だけあればよい。
    """
    for words in lines:
        for head in (("address",), ("address-group",), ("service",), ("service-group",)):
            rest = _after(words, *head)
            if rest and rest[0] not in parsed.address_objects:
                parsed.address_objects.append(rest[0])
    for word in ("any", "application-default"):
        parsed.address_objects.append(word)
