"""MikroTik RouterOS の config（`/export`）を読む。

他社と書き方がまるで違う。`/` で始まる区画に入り、その中で `add` / `set` を
並べ、値は `名前=値` で書く。IOS の目で読むと何も取れない。

```
/interface bridge
add name=bridge1
/interface vlan
add interface=bridge1 name=vlan10 vlan-id=10
/ip address
add address=10.0.0.1/24 interface=vlan10
/ip route
add dst-address=0.0.0.0/0 gateway=10.0.0.254
```

出典: MikroTik RouterOS マニュアル（help.mikrotik.com）の「VLAN」（`/interface vlan` の name・
vlan-id・interface）、「IP Routing」（`/ip route` の dst-address・gateway・distance、
`/ip address` の address・interface）。2026-09-28 に確認。
bridge の vlan-filtering、bridge port の pvid（既定 1、vlan-filtering=yes のときだけ効く）、
bridge vlan の tagged・vlan-ids は「Bridging and Switching」で確かめた（2026-10-16 に確認）。

行が長いと `\\` で折り返される。折り返しを繋いでから読まないと、
後ろ半分の値がまるごと落ちる。
"""

from __future__ import annotations

import re

from app.services.configparse import vlanlist
from app.services.configparse.base import (
    ParsedConfig,
    ParsedInterface,
    ParsedRoute,
    Vendor,
)

#: RouterOS にしか出てこない書き方
_STRONG = (
    re.compile(r"^/interface\s+(?:bridge|vlan|ethernet)\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^/ip\s+(?:address|route|firewall)\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*#\s*\w+.*by RouterOS", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^/system\s+identity\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bMikroTik\b|\bRouterOS\b", re.IGNORECASE),
)

_VERSION = re.compile(r"by RouterOS\s+(\S+)", re.IGNORECASE)
_MODEL = re.compile(r"^\s*#\s*model\s*=\s*(\S+)", re.IGNORECASE | re.MULTILINE)


def detect(text: str) -> Vendor | None:
    for pattern in _STRONG:
        if pattern.search(text):
            return Vendor.ROUTEROS
    return None


def parse(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.ROUTEROS, os_name="RouterOS")

    found = _VERSION.search(text)
    if found:
        parsed.os_version = found.group(1)
    found = _MODEL.search(text)
    if found:
        parsed.model = found.group(1)

    # 橋ごとに、VLAN を見て振り分ける設定になっているか。
    # 入っていない橋では pvid は効かないので、タグ無し VLAN として持たない
    filtering: set[str] = set()

    section = ""
    for line in _joined(text):
        if line.startswith("/"):
            section = " ".join(line.split()).lower()
            continue
        if not section:
            continue
        verb, values = _values(line)
        if verb is None:
            continue

        if section == "/system identity" and "name" in values:
            parsed.hostname = values["name"]
        elif section == "/interface vlan":
            _vlan_port(parsed, values)
        elif section in ("/interface bridge", "/interface bonding"):
            if "name" in values:
                _port(parsed, values["name"], values)
                if values.get("vlan-filtering") == "yes":
                    filtering.add(values["name"])
        elif section == "/interface ethernet":
            _ethernet(parsed, values)
        elif section == "/ip address":
            _address(parsed, values)
        elif section == "/ip route":
            _route(parsed, values)
        elif section == "/interface bridge port":
            _bridge_port(parsed, values, filtering)
        elif section == "/interface bridge vlan":
            _bridge_vlan(parsed, values, filtering)

    return parsed


def _bridge_vlan(parsed: ParsedConfig, values: dict[str, str], filtering: set[str]) -> None:
    """橋の VLAN の表。`tagged` に並んだ口は、`vlan-ids` の VLAN をトランクで通す。

    出典: MikroTik RouterOS マニュアル「Bridging and Switching」の Bridge VLAN Filtering
    （tagged はカンマ区切りの口、vlan-ids は範囲とカンマ区切り）。pvid と同じく、
    `vlan-filtering=yes` の橋でしか効かないので、それ以外の橋の行は読まない。
    橋そのもの（`tagged=bridge1`）は CPU 側の口なので、トランクの口として数えない
    """
    bridge = values.get("bridge")
    if bridge not in filtering:
        return
    vlans = vlanlist.parse_ranges(values.get("vlan-ids", ""))
    if not vlans:
        return
    for name in values.get("tagged", "").split(","):
        name = name.strip()
        if not name or name == bridge:
            continue
        port = _port(parsed, name, {})
        port.mode = "trunk"
        port.trunk_vlans = sorted(set(port.trunk_vlans) | set(vlans))


def _vlan_port(parsed: ParsedConfig, values: dict[str, str]) -> None:
    """`/interface vlan` の1行が、VLAN の定義とその口を兼ねている。"""
    name = values.get("name")
    tag = values.get("vlan-id")
    if not name or not tag or not tag.isdigit():
        return
    number = int(tag)
    parsed.vlans.setdefault(number, name)
    port = _port(parsed, name, values)
    port.vlan = number
    port.mode = "routed"


def _ethernet(parsed: ParsedConfig, values: dict[str, str]) -> None:
    name = values.get("name") or values.get("default-name")
    if not name:
        return
    _port(parsed, name, values)


def _bridge_port(parsed: ParsedConfig, values: dict[str, str], filtering: set[str]) -> None:
    """橋に足した口。タグ無しで渡す VLAN は `pvid` で書く。

    ただし pvid が効くのは、その橋が VLAN を見て振り分ける設定
    （`vlan-filtering=yes`）になっているときだけ。入っていない橋の pvid を
    タグ無し VLAN として持つと、効いていない値を根拠に食い違いを言うことになる。
    """
    name = values.get("interface")
    if not name:
        return
    port = _port(parsed, name, values)
    pvid = values.get("pvid")
    if pvid and pvid.isdigit() and values.get("bridge") in filtering:
        port.native_vlan = int(pvid)
        port.mode = port.mode or "trunk"


def _address(parsed: ParsedConfig, values: dict[str, str]) -> None:
    """住所は口とは別の区画に書く。口の側に付け直さないと、どこにも出ない。"""
    address = values.get("address")
    name = values.get("interface")
    if not address or not name:
        return
    port = _port(parsed, name, values)
    if port.ip_address is None:
        port.ip_address = address
    else:
        port.extra_addresses.append(address)


def _route(parsed: ParsedConfig, values: dict[str, str]) -> None:
    # 既定経路は dst-address を 0.0.0.0/0 にするか、書かずに済ませる
    # （MikroTik RouterOS マニュアル「IP Routing」の Default Route と、
    # 作例 `/ip route add gateway=...`）。
    # 書かない形を読み飛ばすと、いちばん大事な既定経路が抜ける
    destination = values.get("dst-address") or ("0.0.0.0/0" if values.get("gateway") else None)
    if not destination:
        return
    parsed.routes.append(
        ParsedRoute(
            destination=destination,
            next_hop=values.get("gateway"),
            distance=int(values["distance"]) if values.get("distance", "").isdigit() else None,
        )
    )


def _port(parsed: ParsedConfig, name: str, values: dict[str, str]) -> ParsedInterface:
    """口を足す。名前は機器が名乗るとおり（ether1 / bridge1）のままにする。"""
    port = parsed.add_interface(ParsedInterface(name=name))
    port.name = name
    if values.get("comment"):
        port.description = port.description or values["comment"]
    if values.get("disabled") == "yes":
        # RouterOS は消さずに止める。止めてある口を生きていると数えない
        port.enabled = False
    return port


def _joined(text: str) -> list[str]:
    r"""行末の `\` で折り返された行を繋ぐ。

    繋がないと、後ろ半分の値がまるごと落ちる。`address=` だけ読めて
    `interface=` が落ちれば、その住所はどこにも付かない。
    """
    lines: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1].strip() + " "
            continue
        lines.append((buffer + stripped.strip()).strip())
        buffer = ""
    if buffer:
        lines.append(buffer.strip())
    return [line for line in lines if line]


#: `name=vlan10` / `comment="to core"` の並び
_PAIR = re.compile(r'(?P<key>[\w.-]+)=(?:"(?P<quoted>[^"]*)"|(?P<plain>\S+))')


def _values(line: str) -> tuple[str | None, dict[str, str]]:
    """`add name=x vlan-id=10` を、動詞と値の組にほどく。"""
    head = line.split(None, 1)
    if not head:
        return None, {}
    verb = head[0].lower()
    if verb not in ("add", "set"):
        return None, {}
    values = {
        found.group("key"): (found.group("quoted") or found.group("plain") or "")
        for found in _PAIR.finditer(line)
    }
    return verb, values
