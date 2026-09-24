"""Dell EMC の OS10（SmartFabric OS10）の config を読む。

見た目は IOS に近いが、そのまま IOS として読むと取りこぼすところがある。

- 口の名前が `ethernet 1/1/1`（種別と番号を空けて書く）
- 住所は長さ付き（`ip address 10.0.0.5/24`）
- トランクは `switchport trunk allowed vlan 10,30`、タグ無しは
  `switchport access vlan 999`（トランクの口に書いてある場合は native の意味）
- VLAN の口は `interface vlan 30`
- 束ねるのは `interface port-channel 1` と `channel-group 1 mode active`

VLAN の定義や経路は IOS の読み手でそのまま読めるので、口まわりだけ直す。
"""

from __future__ import annotations

import copy
import re

from app.services.configparse import cisco, lldptable
from app.services.configparse.base import ParsedConfig, ParsedInterface, Vendor

#: OS10 にしか出てこない書き方
_STRONG = (
    re.compile(r"^interface\s+ethernet\s+\d+/\d+/\d+", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^interface\s+breakout\s", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bDell\s+EMC\s+Networking\s+OS10\b|\bOS10\s+Enterprise\b", re.IGNORECASE),
    re.compile(r"^\s*system-user\s+linuxadmin\b", re.IGNORECASE | re.MULTILINE),
)

_VERSION = re.compile(r"OS10\s+Enterprise.*?Version\s+([\d.]+\S*)", re.IGNORECASE | re.DOTALL)
_OS_NAME = re.compile(r"\bDell\s+EMC\s+Networking\s+OS10\b|\bOS10\s+Enterprise\b", re.IGNORECASE)
_MODEL = re.compile(r"^\s*!\s*Model:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_INTERFACE = re.compile(r"^interface\s+(?P<kind>[A-Za-z-]+)\s+(?P<number>[\d/:.]+)\s*$", re.I)
#: 範囲でまとめた指定（`interface range ethernet 1/1/1-1/1/4`）。
#: カンマ区切りで最大6範囲まで書ける
#: 出典: Dell SmartFabric OS10 User Guide「interface range」
_INTERFACE_RANGE = re.compile(
    r"^interface\s+range\s+(?P<kind>[A-Za-z-]+)\s+(?P<list>\S+)\s*$", re.IGNORECASE
)
_DESCRIPTION = re.compile(r"^\s+description\s+(.+?)\s*$", re.IGNORECASE)
_IP = re.compile(r"^\s+ip\s+address\s+(?P<ip>\d+\.\d+\.\d+\.\d+)/(?P<len>\d+)", re.IGNORECASE)
_SHUTDOWN = re.compile(r"^\s+shutdown\s*$", re.IGNORECASE)
_NO_SHUTDOWN = re.compile(r"^\s+no\s+shutdown\s*$", re.IGNORECASE)
_MODE = re.compile(r"^\s+switchport\s+mode\s+(access|trunk)\s*$", re.IGNORECASE)
_ACCESS = re.compile(r"^\s+switchport\s+access\s+vlan\s+(\d+)", re.IGNORECASE)
_TRUNK = re.compile(r"^\s+switchport\s+trunk\s+allowed\s+vlan\s+(.+?)\s*$", re.IGNORECASE)
_CHANNEL = re.compile(
    r"^\s+channel-group\s+(?P<group>\d+)(?:\s+mode\s+(?P<mode>active|passive|on))?", re.IGNORECASE
)


#: FastIron も `interface ethernet 1/1/1` と書く。こちらの目印が出ていたら
#: OS10 ではない（VLAN の下に口を並べる書き方は OS10 に無い）
_FASTIRON = re.compile(r"\bby\s+port\b|\bport-name\b|\b(?:un)?tagged\s+ethe", re.IGNORECASE)


def detect(text: str) -> Vendor | None:
    if _FASTIRON.search(text):
        return None
    for pattern in _STRONG:
        if pattern.search(text):
            return Vendor.DELL_OS10
    return None


def parse(text: str) -> ParsedConfig:
    """IOS の読み手に任せてから、OS10 の書き方で口を入れ直す。"""
    parsed = cisco.parse(text, Vendor.CISCO_IOS)
    parsed.vendor = Vendor.DELL_OS10
    # OS の名前は機器の印字（`Dell EMC Networking OS10` / `OS10 Enterprise`）から読む
    parsed.os_name = "OS10" if _OS_NAME.search(text) else None
    parsed.model = None
    parsed.interfaces = []

    found = _VERSION.search(text)
    if found:
        parsed.os_version = found.group(1)
    found = _MODEL.search(text)
    if found:
        parsed.model = found.group(1)

    _read_ports(text, parsed)
    # 隣は `show lldp neighbors` の表から。見出しは OS10 独自の並び
    # （出典: Dell SmartFabric OS10 User Guide「show lldp neighbors」）
    lldptable.parse(text, parsed)
    return parsed


def _read_ports(text: str, parsed: ParsedConfig) -> None:
    from app.services.configparse import vlanlist

    current: ParsedInterface | None = None
    #: 範囲でまとめて書かれたときの、2つ目以降の口の名前
    siblings: list[str] = []

    def close() -> None:
        """範囲に書いた内容を、2つ目以降の口へ写して閉じる。"""
        nonlocal current, siblings
        if current is not None:
            for name in siblings:
                other = copy.deepcopy(current)
                other.name = name
                parsed.add_interface(other)
        current, siblings = None, []

    for raw in text.splitlines():
        spread = _INTERFACE_RANGE.match(raw)
        if spread and not raw.startswith((" ", "\t")):
            close()
            names = _range_ports(spread.group("kind"), spread.group("list"))
            if names:
                current = parsed.add_interface(ParsedInterface(name=names[0]))
                siblings = names[1:]
            continue
        head = _INTERFACE.match(raw)
        if head and not raw.startswith((" ", "\t")):
            close()
            current = parsed.add_interface(ParsedInterface(name=_name(head)))
            continue
        if current is None:
            continue
        if raw.strip() and not raw.startswith((" ", "\t")):
            close()
            continue

        found = _DESCRIPTION.match(raw)
        if found:
            current.description = found.group(1)
            continue
        found = _IP.match(raw)
        if found:
            current.ip_address = f"{found.group('ip')}/{found.group('len')}"
            continue
        if _NO_SHUTDOWN.match(raw):
            current.enabled = True
            continue
        if _SHUTDOWN.match(raw):
            current.enabled = False
            continue
        found = _MODE.match(raw)
        if found:
            current.mode = found.group(1).lower()
            continue
        found = _TRUNK.match(raw)
        if found:
            current.mode = "trunk"
            if vlanlist.is_all(found.group(1)):
                current.trunk_all = True
            else:
                current.trunk_vlans = sorted(
                    set(current.trunk_vlans) | set(vlanlist.parse_ranges(found.group(1)))
                )
            continue
        found = _ACCESS.match(raw)
        if found:
            number = int(found.group(1))
            # トランクの口に書いてあれば、それはタグ無しで渡す VLAN の意味。
            # アクセス VLAN として持つと、両端で食い違っていても比べられない
            if current.mode == "trunk" or current.trunk_vlans:
                current.native_vlan = number
            else:
                current.vlan = number
            continue
        found = _CHANNEL.match(raw)
        if found:
            current.lag_group = int(found.group("group"))
            current.lag_mode = (found.group("mode") or "on").lower()

    close()


def _name(head: re.Match[str]) -> str:
    """`ethernet 1/1/1` を、他ベンダーと並べても読める形にする。"""
    kind = head.group("kind").lower()
    number = head.group("number")
    if kind.startswith("eth"):
        return f"Ethernet{number}"
    if kind.startswith("port-channel"):
        return f"Port-channel{number}"
    if kind.startswith("vlan"):
        return f"Vlan{number}"
    if kind.startswith("mgmt"):
        return f"Management{number}"
    return f"{kind}{number}"


def _range_ports(kind: str, spec: str) -> list[str]:
    """`ethernet 1/1/1-1/1/4` を口ごとに開く。

    開かないと、その範囲に書いた設定がどの口にも入らない。カンマ区切りで
    複数の範囲を書ける（出典: Dell SmartFabric OS10 User Guide「interface range」
    "Enter up to six comma-separated interface ranges without spaces between
    commas."）。番号の形が食い違う書き方は開かない。推測で口を作らない。
    """
    out: list[str] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" not in part:
            out.append(_named(kind, part))
            continue
        first, _, last = part.partition("-")
        out.extend(_named(kind, number) for number in _between(first, last))
    seen: set[str] = set()
    unique: list[str] = []
    for name in out:
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        unique.append(name)
    return unique


def _named(kind: str, number: str) -> str:
    """`ethernet` と `1/1/1` を、口の名前にそろえる。"""
    return _name(re.match(_INTERFACE, f"interface {kind} {number}"))  # type: ignore[arg-type]


def _between(first: str, last: str) -> list[str]:
    """`1/1/1` と `1/1/4` の間を埋める。末尾の番号だけを動かす。"""
    left = first.strip().rsplit("/", maxsplit=1)
    right = last.strip().rsplit("/", maxsplit=1)
    if len(left) != 2 or len(right) != 2 or left[0] != right[0]:
        return [first.strip(), last.strip()]
    if not left[1].isdigit() or not right[1].isdigit():
        return [first.strip(), last.strip()]
    start, end = int(left[1]), int(right[1])
    if end < start or end - start > 255:
        return [first.strip(), last.strip()]
    return [f"{left[0]}/{number}" for number in range(start, end + 1)]
