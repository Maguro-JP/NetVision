"""Ruckus / Brocade FastIron（ICX シリーズ）の config を読む。

見た目は IOS に近いが、VLAN の持ち方が逆になっている。IOS はポートの下に
「この口はどの VLAN」と書くが、FastIron は VLAN の下に「この VLAN はどの口」と
書く。そのまま IOS の読み手に渡すと、VLAN も口の所属も落ちる。

違うのは次の4つで、そこだけこちらで読む。

- 口の名前が種別と番号で分かれている（`interface ethernet 1/1/1`）
- 説明が `port-name`（`description` ではない）
- VLAN が `vlan 10 name USERS by port` で、その下に `tagged` / `untagged`
- 型番と版数は `HW:` / `SW:` の2行

公開されている書式から実装したもので、実機の出力では確かめていない。
"""

from __future__ import annotations

import re

from app.services.configparse import cisco, lldptable
from app.services.configparse.base import (
    ParsedConfig,
    ParsedInterface,
    ParsedRedundancy,
    Vendor,
)

_HW = re.compile(r"^\s*HW:\s*(?:Stackable\s+)?(\S+)", re.IGNORECASE | re.MULTILINE)
_SW = re.compile(r"^\s*SW:\s*Version\s+(\S+)", re.IGNORECASE | re.MULTILINE)
#: FastIron にしか出ない語。IOS 風の書き方だけで名乗らない
_STRONG = (
    r"^\s*vlan\s+\d+\s+name\s+\S+\s+by\s+port\s*$",
    r"^\s*port-name\s+\S",
    r"^\s*(?:tagged|untagged)\s+ethe(?:rnet)?\s+\d",
    r"^\s*HW:\s*Stackable\s+ICX",
    r"\bICX\d{4}\b",
    r"\bFastIron\b",
)

_SPLIT_PORT = re.compile(
    r"^(?P<head>\s*interface\s+)"
    r"(?P<kind>ethernet|ve|loopback|management|lag)\s+(?P<number>[\d/]+)\s*$",
    re.IGNORECASE,
)
_KINDS = {
    "ethernet": "Ethernet",
    "ve": "Ve",
    "loopback": "Loopback",
    "management": "Management",
    "lag": "Lag",
}
#: 範囲でまとめた指定（`interface ethernet 1/1/1 to 1/1/4`）。
#: 出典: RUCKUS FastIron Management Guide「Changing the Gbps fiber negotiation mode」
#: （`device(config)# interface ethernet 1/1/1 to 1/1/4` /
#: プロンプトが `config-mif-1/1/1-1/1/4` になる）
_SPLIT_RANGE = re.compile(
    r"^(?P<head>\s*)interface\s+(?P<kind>ethernet|ve|loopback|management|lag)\s+"
    r"(?P<first>[\d/]+)\s+to\s+(?P<last>[\d/]+)\s*$",
    re.IGNORECASE,
)
_PORT_NAME = re.compile(r"^(?P<indent>\s+)port-name\s+(?P<value>.+?)\s*$", re.IGNORECASE)
#: 行の途中に出てくる口の名前（`ip nat inside source list 1 interface ethernet 1/1/1`）。
#: 種別と番号が離れたままだと、種別だけを名前として拾ってしまう
_INLINE_PORT = re.compile(
    r"\b(?P<kind>ethernet|ve|loopback|management|lag)\s+(?P<number>\d+(?:/\d+)*)\b",
    re.IGNORECASE,
)


def _join_ports(line: str) -> str:
    def rewrite(found: re.Match[str]) -> str:
        kind = _KINDS.get(found.group("kind").lower())
        return f"{kind}{found.group('number')}" if kind else found.group(0)

    return _INLINE_PORT.sub(rewrite, line)


_VLAN_HEAD = re.compile(
    r"^\s*vlan\s+(?P<id>\d+)(?:\s+name\s+(?P<name>\S+))?(?:\s+by\s+port)?\s*$", re.IGNORECASE
)
#: VLAN に載せる口と LAG。出典: RUCKUS Support KB 000015488「Configuring MCT Cluster」
#: （`tagged lag 2`）。口と LAG を1行に並べる形（`tagged ethe 1/1/48 lag 1 lag 11 to 12`）は
#: FastIron 10.0.10 L2 ガイドの MCT の設定例（公式ではない写しで読んだ）
_MEMBERS = re.compile(
    r"^\s+(?P<how>tagged|untagged)\s+(?P<ports>(?:ethe(?:rnet)?|lag)\s+.+?)\s*$", re.IGNORECASE
)
_LAG_IDS = re.compile(r"\blag\s+(\d+)(?:\s+to\s+(\d+))?", re.IGNORECASE)
#: `ethe 1/1/1 to 1/1/4 ethe 1/1/8` のような並び
_RANGE = re.compile(r"(\d+/\d+/\d+)(?:\s+to\s+(\d+/\d+/\d+))?", re.IGNORECASE)


def detect(text: str) -> Vendor | None:
    """FastIron かどうか。この機種にしか出ない語が入っているときだけ言う。"""
    for pattern in _STRONG:
        if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
            return Vendor.FASTIRON
    return None


#: FastIron は経路の見出しに番号を書かない（`router ospf` / `router bgp`）。
#: 自分の AS 番号は中の `local-as` に書く
_ROUTER_HEAD = re.compile(r"^router\s+(ospf|bgp)\s*$", re.IGNORECASE)
_LOCAL_AS = re.compile(r"^\s+local-as\s+(\d+)", re.IGNORECASE | re.MULTILINE)


def _local_as(text: str) -> str | None:
    found = _LOCAL_AS.search(text)
    return found.group(1) if found else None


#: FastIron は参照先を `ntp` の下に一段下げて書く。行だけを見て
#: `server 10.0.0.5` を拾うと、RADIUS の並び（`aaa group server radius`）まで
#: 時刻の参照先として数えてしまう。囲みの中にいるかどうかで決める
_NTP_HEAD = re.compile(r"^ntp\s*$", re.IGNORECASE)
_NTP_SERVER = re.compile(r"^\s+server\s+(\d+\.\d+\.\d+\.\d+)\s*$", re.IGNORECASE)


def _to_ios(text: str) -> str:
    """口の名前と説明を、IOS と同じ書き方に寄せる。"""
    local_as = _local_as(text)
    in_ntp = False
    rows: list[str] = []
    for raw in text.splitlines():
        spread = _SPLIT_RANGE.match(raw)
        if spread:
            kind = _KINDS.get(spread.group("kind").lower())
            if kind:
                # IOS の `interface range` と同じ形にして、口ごとに開かせる。
                # 開かないと、その範囲の口が「何も設定されていない」ことになる
                last = spread.group("last").rsplit("/", maxsplit=1)[-1]
                rows.append(
                    f"{spread.group('head')}interface range {kind}{spread.group('first')} - {last}"
                )
                continue
        found = _SPLIT_PORT.match(raw)
        if found:
            kind = _KINDS.get(found.group("kind").lower())
            if kind:
                rows.append(f"{found.group('head')}{kind}{found.group('number')}")
                continue
        named = _PORT_NAME.match(raw)
        if named:
            rows.append(f"{named.group('indent')}description {named.group('value')}")
            continue
        if _NTP_HEAD.match(raw):
            in_ntp = True
            rows.append(raw)
            continue
        if raw.strip() and not raw.startswith((" ", "\t")):
            in_ntp = False
        if in_ntp:
            server = _NTP_SERVER.match(raw)
            if server:
                rows.append(f"ntp server {server.group(1)}")
                continue
        head = _ROUTER_HEAD.match(raw)
        if head:
            kind = head.group(1).lower()
            # 番号が無いと IOS の読み手が見出しとして拾えない。OSPF は1つだけ
            number = local_as if kind == "bgp" else "1"
            if number:
                rows.append(f"router {kind} {number}")
                continue
        rows.append(_join_ports(raw))
    return "\n".join(rows)


def feature_text(text: str) -> str:
    """機能の拾い出しに渡す文。囲みの中でしか意味が決まらない行をほどく。

    `ntp` の下の `server 10.0.0.5` がそれ。行だけを見ると、RADIUS の並びと
    見分けが付かない。
    """
    return _to_ios(text)


def parse(text: str) -> ParsedConfig:
    parsed = cisco.parse(_to_ios(text), Vendor.CISCO_IOS)
    parsed.vendor = Vendor.FASTIRON
    parsed.os_name = "FastIron"
    parsed.os_version = None
    parsed.model = None

    found = _HW.search(text)
    if found:
        parsed.model = found.group(1)
    found = _SW.search(text)
    if found:
        parsed.os_version = found.group(1)

    _read_vlans(text, parsed)
    _read_redundancy(text, parsed)
    _read_lag(text, parsed)
    lldptable.parse(text, parsed)
    _name_local_ports(parsed)
    return parsed


def _name_local_ports(parsed: ParsedConfig) -> None:
    """表に出てくる `1/1/1` を、config 側と同じ名前にそろえる。

    そろっていないと、隣を見つけていても口に結び付かず、図に線が出ない。
    """
    for neighbor in parsed.neighbors:
        if re.fullmatch(r"\d+(?:/\d+)+", neighbor.local_interface or ""):
            neighbor.local_interface = f"Ethernet{neighbor.local_interface}"


#: `lag "core" dynamic id 1` と、その下の `ports ethernet 1/1/1 to 1/1/2`
_LAG_HEAD = re.compile(
    r"^lag\s+\"?(?P<name>[^\"\s]+)\"?\s+(?P<kind>static|dynamic|keep-alive)"
    r"(?:\s+id\s+(?P<id>\d+))?",
    re.IGNORECASE,
)
_LAG_PORTS = re.compile(r"^\s+ports\s+ethe(?:rnet)?\s+(?P<ports>.+?)\s*$", re.IGNORECASE)


def _lag_members(text: str) -> dict[int, list[str]]:
    """束ねの番号ごとに、束ねた口を並べる。"""
    members: dict[int, list[str]] = {}
    group: int | None = None
    for raw in text.splitlines():
        head = _LAG_HEAD.match(raw)
        if head:
            group = int(head.group("id")) if head.group("id") else None
            continue
        if raw.strip() and not raw.startswith((" ", "\t")):
            group = None
            continue
        ports = _LAG_PORTS.match(raw)
        if group is not None and ports:
            members.setdefault(group, []).extend(_ports(ports.group("ports")))
    return members


def _read_lag(text: str, parsed: ParsedConfig) -> None:
    """束ねを読む。FastIron は束ねの側に口を並べるので、口だけ見ても分からない。

    読めないと、2本で束ねてあるところが1本ずつに見え、落ちる範囲を数えたときに
    実際より軽く出る。
    """
    group: int | None = None
    kind: str | None = None
    for raw in text.splitlines():
        head = _LAG_HEAD.match(raw)
        if head:
            group = int(head.group("id")) if head.group("id") else None
            kind = head.group("kind").lower()
            continue
        if raw.strip() and not raw.startswith((" ", "\t")):
            group, kind = None, None
            continue
        if group is None:
            continue
        ports = _LAG_PORTS.match(raw)
        if not ports:
            continue
        for name in _ports(ports.group("ports")):
            interface = parsed.add_interface(ParsedInterface(name=name))
            interface.lag_group = group
            # dynamic は LACP でやりとりする形、static は決め打ち
            interface.lag_mode = "active" if kind == "dynamic" else "on"


#: `ip vrrp-extended vrid 10` / `ip vrrp vrid 10`。中身は次の行から下げて書く
_VRRP_HEAD = re.compile(r"^(?P<indent>\s+)ip\s+vrrp(?:-extended)?\s+vrid\s+(?P<id>\d+)", re.I)
_VRRP_ADDRESS = re.compile(r"^\s+ip-address\s+(\d+\.\d+\.\d+\.\d+)", re.IGNORECASE)
_VRRP_PRIORITY = re.compile(r"^\s+(?:backup\s+)?priority\s+(\d+)", re.IGNORECASE)
_INTERFACE_HEAD = re.compile(r"^interface\s+(\S+)\s*$", re.IGNORECASE)


def _read_redundancy(text: str, parsed: ParsedConfig) -> None:
    """VRRP を読む。中身を口の下に一段下げて書くので、行だけでは拾えない。

    読めないと、二重にしてある入口が「1台だけ」に見える。落ちる範囲を
    数えるときに、いちばん効くところ。
    """
    port: str | None = None
    entry: ParsedRedundancy | None = None
    for raw in _to_ios(text).splitlines():
        head = _INTERFACE_HEAD.match(raw)
        if head:
            port, entry = head.group(1), None
            continue
        if raw.strip() and not raw.startswith((" ", "\t")):
            port, entry = None, None
            continue
        if port is None:
            continue
        found = _VRRP_HEAD.match(raw)
        if found:
            entry = ParsedRedundancy(interface=port, group=found.group("id"), protocol="vrrp")
            parsed.redundancy.append(entry)
            continue
        if entry is None:
            continue
        address = _VRRP_ADDRESS.match(raw)
        if address:
            entry.virtual_ip = address.group(1)
            continue
        priority = _VRRP_PRIORITY.match(raw)
        if priority:
            entry.priority = int(priority.group(1))


def _ports(text: str) -> list[str]:
    """`1/1/1 to 1/1/4` のような並びを、1つずつの名前にほどく。"""
    names: list[str] = []
    for start, end in _RANGE.findall(text):
        if not end:
            names.append(f"Ethernet{start}")
            continue
        head, unit, first = start.rsplit("/", 2)[0], start.split("/")[1], int(start.split("/")[2])
        last = int(end.split("/")[2])
        if last < first:
            names.append(f"Ethernet{start}")
            continue
        names.extend(f"Ethernet{head}/{unit}/{number}" for number in range(first, last + 1))
    return names


def _read_vlans(text: str, parsed: ParsedConfig) -> None:
    """VLAN の下に並ぶ口を読んで、ポート側の所属に直す。

    IOS と向きが逆なので、ここを読まないと VLAN も所属も空のままになる。
    """
    current: int | None = None
    lags = _lag_members(text)
    #: タグ無しで載せた VLAN。タグ付きの行が後から来ることがあるので、
    #: どちらの意味になるかは読み終えてから決める
    untagged: dict[str, int] = {}
    for raw in text.splitlines():
        head = _VLAN_HEAD.match(raw)
        if head and not raw.startswith((" ", "\t")):
            current = int(head.group("id"))
            parsed.vlans.setdefault(current, head.group("name") or "")
            if head.group("name"):
                parsed.vlans[current] = head.group("name")
            continue
        if current is None:
            continue
        if raw.strip() and not raw.startswith((" ", "\t")):
            current = None
            continue
        members = _MEMBERS.match(raw)
        if not members:
            continue
        tagged = members.group("how").lower() == "tagged"
        written = members.group("ports")
        # LAG を載せたときは、束ねた口それぞれに載せたのと同じ
        from_lags: list[str] = []
        for first, last in _LAG_IDS.findall(written):
            for number in range(int(first), int(last or first) + 1):
                from_lags.extend(lags.get(number, []))
        for name in [*_ports(_LAG_IDS.sub(" ", written)), *from_lags]:
            interface = parsed.add_interface(ParsedInterface(name=name))
            if tagged:
                interface.mode = "trunk"
                if current not in interface.trunk_vlans:
                    interface.trunk_vlans.append(current)
            else:
                untagged.setdefault(name, current)

    # タグ付きと一緒に載っているタグ無しは、トランクのタグ無し VLAN（native）である。
    # アクセス VLAN として持つと、両端で食い違っていても比べられない。
    # タグ無しだけなら、それは端末を挿す口
    for name, vlan_id in untagged.items():
        interface = parsed.add_interface(ParsedInterface(name=name))
        if interface.trunk_vlans:
            interface.mode = "trunk"
            interface.native_vlan = interface.native_vlan or vlan_id
        else:
            interface.mode = interface.mode or "access"
            interface.vlan = interface.vlan or vlan_id
