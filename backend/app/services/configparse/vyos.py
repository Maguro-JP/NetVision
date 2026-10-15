"""VyOS / EdgeOS（Vyatta 系）の config を読む。

書き方は2つある。どちらも現場から届くので、両方読む。

- `set interfaces ethernet eth0 address '10.0.0.1/24'` の並び（コマンド形式）
- 同じ内容を波括弧で入れ子にした保存形式

Junos も `set` で始まるので、そこだけで決めると取り違える。口の名前が
`eth0` / `switch0` の形であること、`set interfaces ethernet` と書くことで見分ける。

出典: LAG の子（`set interfaces bonding bond0 member interface eth0`）は VyOS 1.5.x LTS
Documentation「Bond / link aggregation」で、口の address（dhcp・dhcpv6 を含む）・description・
disable と vif の行は「Ethernet」で、経路（next-hop・interface・dhcp-interface）は「Static」で
確かめた。bond-group は既存の試験の形のまま（未確認）。
"""

from __future__ import annotations

import re

from app.services.configparse.base import (
    ParsedConfig,
    ParsedInterface,
    ParsedRoute,
    Vendor,
)

#: Vyatta 系にしか出てこない書き方
_STRONG = (
    re.compile(r"^\s*set\s+interfaces\s+ethernet\s+eth\d", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*interfaces\s*\{", re.MULTILINE),
    re.compile(r"^\s*set\s+service\s+ssh\s+port\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*set\s+protocols\s+static\s+route\s+", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bVyOS\b|\bEdgeOS\b|\bVyatta\b", re.IGNORECASE),
)
#: Junos も set で書く。口の名前で見分ける（Junos は ge-0/0/0 の形）。
#: 囲みで書いた Junos（`show configuration` の形）も、`interfaces {` が
#: VyOS と同じなので取り違える。Junos にしか出ない並びで先に外す
#: （出典: Juniper「Interfaces User Guide for Switches」および Juniper の
#: サポート記事 [EX/QFX] Example - Configuring VLAN translation by using ELS の
#: 出力例: ge-0/0/0 { unit 0 { family ethernet-switching { interface-mode
#: trunk; … } } }、行末に `;` が付く）
_JUNOS = re.compile(
    r"^\s*set\s+interfaces\s+(?:ge|xe|et|ae|irb|lo)-?\d"
    r"|^\s*(?:ge|xe|et|ae|fte|reth|fxp|em)-\d+/\d+/\d+[\d.:]*\s*\{"
    r"|^\s*family\s+ethernet-switching\s*\{"
    r"|^\s*host-name\s+\S+;",
    re.IGNORECASE | re.MULTILINE,
)

_HOSTNAME = re.compile(
    r"^\s*set\s+system\s+host-name\s+'?([^'\s]+)'?", re.IGNORECASE | re.MULTILINE
)
#: 名乗り。設定の先頭に小文字で `vyos version: 1.4` と印字されることがある。
#: 同じ語なので大文字小文字は区別せず、持つときはその製品自身の綴りにする
_OS_NAME = re.compile(r"\b(vyos|edgeos)\b", re.IGNORECASE)
_OS_SPELLING = {"vyos": "VyOS", "edgeos": "EdgeOS"}
_VERSION = re.compile(
    r"^\s*//\s*(?:vyos|Release)\s*version\s*:?\s*(\S+)", re.IGNORECASE | re.MULTILINE
)
_ADDRESS = re.compile(
    r"^\s*set\s+interfaces\s+(?P<kind>\S+)\s+(?P<name>\S+)"
    r"(?:\s+vif\s+(?P<vif>\d+))?\s+address\s+'?(?P<address>[^'\s]+)'?",
    re.IGNORECASE,
)
_DESCRIPTION = re.compile(
    r"^\s*set\s+interfaces\s+(?P<kind>\S+)\s+(?P<name>\S+)"
    r"(?:\s+vif\s+(?P<vif>\d+))?\s+description\s+'?(?P<text>[^']+?)'?\s*$",
    re.IGNORECASE,
)
_DISABLE = re.compile(
    r"^\s*set\s+interfaces\s+(?P<kind>\S+)\s+(?P<name>\S+)"
    r"(?:\s+vif\s+(?P<vif>\d+))?\s+disable\s*$",
    re.IGNORECASE,
)
_ROUTE = re.compile(
    r"^\s*set\s+protocols\s+static\s+route6?\s+'?(?P<dest>[\d.:/a-fA-F]+)'?"
    r"\s+next-hop\s+'?(?P<hop>[^'\s]+)'?",
    re.IGNORECASE,
)
#: 次の宛先を口で書く経路。出典: VyOS Documentation「Static」
#: （set protocols static route <subnet> interface <interface> / dhcp-interface <interface>）
_ROUTE_INTERFACE = re.compile(
    r"^\s*set\s+protocols\s+static\s+route6?\s+'?(?P<dest>[\d.:/a-fA-F]+)'?"
    r"\s+(?:dhcp-)?interface\s+'?(?P<name>[^'\s]+)'?\s*$",
    re.IGNORECASE,
)
_BOND = re.compile(
    r"^\s*set\s+interfaces\s+ethernet\s+(?P<name>\S+)\s+bond-group\s+'?(?P<group>\S+)'?",
    re.IGNORECASE,
)

#: VyOS 1.3 以降は束ねる口の側に子を並べる。出典: VyOS 1.5.x (circinus) LTS Documentation
#: 「Bond / link aggregation」（`set interfaces bonding bond0 member interface eth0`）。
#: 上の `bond-group` は子の口の側に書く旧来の形（EdgeOS もこちら）
_BOND_MEMBER = re.compile(
    r"^\s*set\s+interfaces\s+bonding\s+(?P<group>\S+)\s+member\s+interface\s+'?(?P<name>[^'\s]+)'?",
    re.IGNORECASE,
)


def detect(text: str) -> Vendor | None:
    if _JUNOS.search(text):
        return None
    for pattern in _STRONG:
        if pattern.search(text):
            return Vendor.VYOS
    return None


def parse(text: str) -> ParsedConfig:
    # VyOS と EdgeOS は別の製品。どちらかは機器の印字（名乗り）で決め、無ければ空にする。
    # 2つを混ぜた名前を入れると、どちらでもない名前が台帳に載る
    parsed = ParsedConfig(vendor=Vendor.VYOS)
    named = _OS_NAME.search(text)
    if named:
        parsed.os_name = _OS_SPELLING[named.group(1).lower()]
    text = _flatten(text)

    found = _HOSTNAME.search(text)
    if found:
        parsed.hostname = found.group(1)
    found = _VERSION.search(text)
    if found:
        parsed.os_version = found.group(1)

    for raw in text.splitlines():
        found = _ADDRESS.match(raw)
        if found:
            port = _add(parsed, _name(found))
            value = found.group("address")
            if value.lower() in ("dhcp", "dhcpv6"):
                # 貰う設定は「無い」ではない。空欄にすると付け忘れと区別が付かない
                port.address_source = "dhcp"
            elif port.ip_address is None:
                port.ip_address = value
            else:
                port.extra_addresses.append(value)
            _set_vif(port, found)
            continue
        found = _DESCRIPTION.match(raw)
        if found:
            port = _add(parsed, _name(found))
            port.description = found.group("text").strip('"')
            _set_vif(port, found)
            continue
        found = _DISABLE.match(raw)
        if found:
            port = _add(parsed, _name(found))
            port.enabled = False
            _set_vif(port, found)
            continue
        found = _BOND.match(raw) or _BOND_MEMBER.match(raw)
        if found:
            port = _add(parsed, found.group("name"))
            port.lag_group = int(re.sub(r"\D", "", found.group("group")) or 0)
            continue
        found = _ROUTE.match(raw)
        if found:
            parsed.routes.append(
                ParsedRoute(destination=found.group("dest"), next_hop=found.group("hop"))
            )
            continue
        found = _ROUTE_INTERFACE.match(raw)
        if found:
            parsed.routes.append(
                ParsedRoute(destination=found.group("dest"), interface=found.group("name"))
            )

    # VLAN の口（vif）から、その機器が持つ VLAN を起こす。
    # VLAN の定義を別に書かない書き方なので、ここからしか分からない
    for port in parsed.interfaces:
        if port.vlan is not None:
            parsed.vlans.setdefault(port.vlan, "")
    return parsed


def _add(parsed: ParsedConfig, name: str) -> ParsedInterface:
    """口を足す。名前は機器が名乗るとおり（eth0）のままにする。

    共通の読み手は `eth` を `Ethernet` に開くが、Vyatta 系ではそれが本名で、
    開くと機器の画面と食い違う。突き合わせは正規化した鍵で行うので、
    見せる名前は元に戻してよい。
    """
    port = parsed.add_interface(ParsedInterface(name=name))
    port.name = name
    return port


def _set_vif(port: ParsedInterface, found: re.Match[str]) -> None:
    """vif の行なら、その口の VLAN 番号を入れる。

    address・description・disable はどれも単独で書ける（出典: VyOS Documentation「Ethernet」の
    VLAN の項）。アドレスの無い vif も VLAN として数えるため、どの行からでも入れる。
    """
    if found.group("vif"):
        port.vlan = int(found.group("vif"))


def _name(found: re.Match[str]) -> str:
    """`eth0` と、その上の VLAN の口（`eth0.10`）を分けて名前にする。"""
    name = found.group("name")
    vif = found.groupdict().get("vif")
    return f"{name}.{vif}" if vif else name


def _flatten(text: str) -> str:
    """波括弧の保存形式を、`set` の並びに直す。

    2つの書き方で読み手を分けると、片方だけ直したときに食い違う。
    """
    if "set " in text and "{" not in text:
        return text

    out: list[str] = []
    stack: list[str] = []
    #: その段で葉を1つでも書いたか。中身の無い囲みは、それ自体が1つの設定
    #: （`route 0.0.0.0/0 { next-hop 192.0.2.254 { } }` は
    #: `set protocols static route 0.0.0.0/0 next-hop 192.0.2.254` と同じ）
    filled: list[bool] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("//", "/*", "#")):
            continue
        if line.endswith("{"):
            stack.append(line[:-1].strip())
            filled.append(False)
            continue
        if line == "}":
            if stack:
                path = list(stack)
                had_leaf = filled.pop()
                stack.pop()
                if not had_leaf:
                    out.append("set " + " ".join(path))
                if filled:
                    filled[-1] = True
            continue
        # `address 10.0.0.1/24` のような葉。ここまでの入れ子と繋げて1行にする
        out.append("set " + " ".join([*stack, line.rstrip(";")]))
        if filled:
            filled[-1] = True
    return "\n".join(out) if out else text
