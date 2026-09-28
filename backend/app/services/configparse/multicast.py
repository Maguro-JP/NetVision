"""マルチキャストの配り方を読む。

映像配信や放送、機器の自動探索がここに乗る。動かないときの原因は
たいてい次のどれかで、どれも Config を見れば分かる。

- sparse-mode で配るのに、集合場所（RP）を決めていない
- RP のアドレスが、この機器からは届かないところにある
- IGMP スヌーピングだけ入れて、定期的に聞いて回る役（querier）が居ない
  L3 が居ない帯でこれをやると、数分後に配信が止まる

読めるのは Cisco IOS / NX-OS、Arista、Junos（set 形式）、NEC QX、
Huawei、H3C、Aruba CX の書き方。判断はしない。判断は multicastreview.py の仕事。

出典（2026-09-28 JST に確認）
- Cisco IOS: Command Reference, Cisco IOS XE Gibraltar 16.12.x (Catalyst 9600)
  「IP Multicast Routing Commands」の ip pim rp-address
  （`ip pim [vrf X] rp-address A [access-list] [override] [bidir]`）と、
  IP Multicast Configuration Guide「Configuring Basic IP Multicast」
- Huawei: S300/S500/S2700/S5700/S6700 V200R024C00 Command Reference の
  igmp-snooping querier enable（VLAN ビュー）と static-rp (IPv4)
  （`static-rp A [acl 番号 | acl-name 名前] [preferred]`）
- H3C: IGMP snooping configuration / commands（Comware 7）の
  `igmp-snooping`（全体で入れて囲みに入る）、`enable vlan 2 to 10`、
  VLAN ビューの `igmp-snooping enable` / `igmp-snooping querier`
- NX-OS、Arista、Junos、NEC QX、Aruba CX の書き方は未確認（既存の試験の形のまま）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HINT = re.compile(r"\bpim\b|igmp|multicast", re.IGNORECASE)


@dataclass
class ParsedRp:
    """集合場所（RP）。sparse-mode はここを経由して配信が始まる。"""

    address: str
    #: static / candidate / auto-rp。候補方式は他機が決めるので、片側だけでは断定しない
    kind: str = "static"
    #: 対象の範囲（ACL 名やグループ範囲）。書いていなければ全部
    scope: str | None = None


@dataclass
class ParsedPimInterface:
    name: str
    mode: str  # sparse / dense / sparse-dense


@dataclass
class ParsedSnoopVlan:
    """帯ごとのスヌーピングの状態。"""

    vlan: int
    enabled: bool = True
    querier: bool = False


@dataclass
class ParsedMulticast:
    """1台分のマルチキャスト設定。"""

    #: マルチキャストの転送そのものが有効か（ip multicast-routing など）
    routing: bool = False
    rps: list[ParsedRp] = field(default_factory=list)
    #: 候補方式（BSR / Auto-RP）で RP を決めている。相手が居るので片側では断定しない
    dynamic_rp: bool = False
    pim_interfaces: list[ParsedPimInterface] = field(default_factory=list)
    #: 全体のスヌーピング。書いていなければ None（機種の既定に従う）
    snooping: bool | None = None
    vlans: list[ParsedSnoopVlan] = field(default_factory=list)

    @property
    def has_sparse(self) -> bool:
        return any(item.mode != "dense" for item in self.pim_interfaces)

    @property
    def in_use(self) -> bool:
        return bool(self.routing or self.pim_interfaces or self.rps or self.vlans)


def has_any(text: str) -> bool:
    return bool(_HINT.search(text))


# ---------------------------------------------------------------------------
# Cisco IOS / NX-OS / Arista
# ---------------------------------------------------------------------------

_ROUTING = re.compile(
    r"^\s*(?:ip\s+)?multicast-routing\b|^\s*feature\s+pim\b|^\s*router\s+pim\b",
    re.IGNORECASE,
)
_RP = re.compile(
    r"^\s*ip\s+pim\s+(?:vrf\s+\S+\s+)?rp-address\s+(?P<address>\S+)(?:\s+(?P<scope>.+))?$",
    re.IGNORECASE,
)
#: Arista `ip rp address 10.0.0.9`、AOS-CX `rp-address 10.0.0.9`。
#: どちらも router pim の中に書くので、字下がりの中だけで見る
_RP_BLOCK = re.compile(
    r"^\s+(?:ip\s+rp\s+address|rp-address)\s+(?P<address>\S+)(?:\s+(?P<scope>.+))?$",
    re.IGNORECASE,
)
_CANDIDATE = re.compile(
    r"^\s*ip\s+pim\s+(?:bsr-candidate|rp-candidate|send-rp-announce|autorp)\b", re.IGNORECASE
)
#: `interface Vlan10` と、番号を離して書く流儀（AOS-CX の `interface vlan 10`）
_INTERFACE = re.compile(r"^interface\s+(\S+(?:\s+\d+(?:/\d+)*)?)\s*$", re.IGNORECASE)
#: 帯の中に書く流儀（AOS-CX の `vlan 10` / `ip igmp snooping enable`）
_VLAN_BLOCK = re.compile(r"^vlan\s+(\d+)\s*$", re.IGNORECASE)
#: Arista `router pim sparse`、AOS-CX `router pim`
_PIM_BLOCK = re.compile(r"^router\s+pim\b", re.IGNORECASE)
#: Cisco `ip pim sparse-mode`、Arista `pim ipv4 sparse-mode`、
#: AOS-CX `ip pim-sparse enable`
_PIM_MODE = re.compile(
    r"^\s*(?:ip\s+pim|pim\s+ipv[46])[\s-]"
    r"(sparse-dense-mode|sparse-mode|dense-mode|sparse|dense)\b",
    re.IGNORECASE,
)
_SNOOP_OFF = re.compile(r"^\s*no\s+ip\s+igmp\s+snooping\s*$", re.IGNORECASE)
_SNOOP_ON = re.compile(r"^\s*ip\s+igmp\s+snooping(?:\s+enable)?\s*$", re.IGNORECASE)
_SNOOP_VLAN = re.compile(
    r"^\s*(?P<no>no\s+)?ip\s+igmp\s+snooping\s+vlan\s+(?P<vlan>\d+)(?:\s+(?P<rest>.+))?$",
    re.IGNORECASE,
)
_QUERIER_GLOBAL = re.compile(r"^\s*ip\s+igmp\s+snooping\s+querier\b", re.IGNORECASE)


_SCOPE_WORDS = re.compile(r"^(?:group-list|access-list|acl|prefix-list)\s+", re.IGNORECASE)


def _scope(text: str | None) -> str | None:
    """RP に付いている範囲の指定。語の飾りは落として、対象だけを残す。"""
    if not text:
        return None
    cleaned = _SCOPE_WORDS.sub("", text.strip())
    # override は静的を優先する印、bidir は双方向 PIM の印。どちらも範囲ではない
    cleaned = re.sub(r"\b(?:override|bidir)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = " ".join(cleaned.split())
    return cleaned or None


def _mode(word: str) -> str:
    lowered = word.lower()
    if lowered.startswith("sparse-dense"):
        return "sparse-dense"
    return "dense" if lowered.startswith("dense") else "sparse"


def _vlan(found: ParsedMulticast, number: int) -> ParsedSnoopVlan:
    for item in found.vlans:
        if item.vlan == number:
            return item
    item = ParsedSnoopVlan(vlan=number)
    found.vlans.append(item)
    return item


def parse_cisco(text: str, found: ParsedMulticast) -> None:
    """Cisco IOS / NX-OS / Arista の書き方。"""
    interface: str | None = None
    vlan: int | None = None
    pim_block = False
    global_querier = False
    for raw in text.splitlines():
        line = raw.rstrip()
        head = _INTERFACE.match(line)
        if head:
            interface, vlan, pim_block = head.group(1), None, False
            continue
        in_vlan = _VLAN_BLOCK.match(line)
        if in_vlan:
            # どの帯の話かを覚えるだけ。ここで作ってしまうと、ただの VLAN の
            # 定義がスヌーピングの設定に化ける
            interface, pim_block = None, False
            vlan = int(in_vlan.group(1))
            continue
        if _PIM_BLOCK.match(line):
            interface, vlan, pim_block = None, None, True
            found.routing = True
            continue
        if line and not line.startswith((" ", "\t")):
            interface, vlan, pim_block = None, None, False

        if _ROUTING.match(line):
            found.routing = True
            continue
        rp = _RP.match(line)
        if rp:
            found.rps.append(ParsedRp(address=rp.group("address"), scope=_scope(rp.group("scope"))))
            continue
        in_pim = _RP_BLOCK.match(line) if line.startswith((" ", "\t")) else None
        if in_pim and pim_block:
            found.rps.append(
                ParsedRp(address=in_pim.group("address"), scope=_scope(in_pim.group("scope")))
            )
            continue
        if _CANDIDATE.match(line):
            found.dynamic_rp = True
            continue
        mode = _PIM_MODE.match(line)
        if mode and interface:
            found.pim_interfaces.append(
                ParsedPimInterface(name=interface, mode=_mode(mode.group(1)))
            )
            continue
        if _QUERIER_GLOBAL.match(line):
            global_querier = True
            continue
        snoop_vlan = _SNOOP_VLAN.match(line)
        if snoop_vlan:
            item = _vlan(found, int(snoop_vlan.group("vlan")))
            rest = (snoop_vlan.group("rest") or "").lower()
            if snoop_vlan.group("no"):
                if rest.startswith("querier"):
                    item.querier = False
                else:
                    item.enabled = False
            elif rest.startswith("querier"):
                item.querier = True
            continue
        if _SNOOP_OFF.match(line):
            found.snooping = False
            continue
        if _SNOOP_ON.match(line):
            if vlan is None:
                found.snooping = True
            else:
                _vlan(found, vlan).enabled = True
            continue
        if re.match(r"^\s*ip\s+igmp\s+snooping\s+querier\b", line, re.IGNORECASE) and vlan:
            _vlan(found, vlan).querier = True

    if global_querier:
        for item in found.vlans:
            item.querier = True
        found.snooping = True if found.snooping is None else found.snooping


# ---------------------------------------------------------------------------
# Junos（set 形式）
# ---------------------------------------------------------------------------

_JUNOS_PIM_IF = re.compile(
    r"^set protocols pim interface (\S+)(?:.*\bmode\s+(sparse|dense|sparse-dense))?", re.IGNORECASE
)
_JUNOS_RP = re.compile(r"^set protocols pim rp static address (\S+)", re.IGNORECASE)
_JUNOS_RP_LOCAL = re.compile(r"^set protocols pim rp (?:local|bootstrap|auto-rp)\b", re.IGNORECASE)
_JUNOS_SNOOP = re.compile(r"^set protocols igmp-snooping vlan (\S+)", re.IGNORECASE)


def parse_junos(text: str, found: ParsedMulticast) -> None:
    for raw in text.splitlines():
        line = raw.strip()
        interface = _JUNOS_PIM_IF.match(line)
        if interface:
            found.routing = True
            name, mode = interface.group(1), interface.group(2)
            if not any(item.name == name for item in found.pim_interfaces):
                found.pim_interfaces.append(
                    ParsedPimInterface(name=name, mode=_mode(mode or "sparse"))
                )
            continue
        rp = _JUNOS_RP.match(line)
        if rp:
            found.rps.append(ParsedRp(address=rp.group(1)))
            continue
        if _JUNOS_RP_LOCAL.match(line):
            found.dynamic_rp = True
            continue
        snoop = _JUNOS_SNOOP.match(line)
        if snoop:
            found.snooping = True
            name = snoop.group(1)
            number = re.sub(r"\D", "", name)
            if number:
                _vlan(found, int(number))
            if "querier" in line.lower():
                for item in found.vlans:
                    item.querier = True


# ---------------------------------------------------------------------------
# NEC QX / Aruba CX
# ---------------------------------------------------------------------------

_CW_ROUTING = re.compile(r"^\s*multicast\s+routing\b", re.IGNORECASE)
_CW_PIM = re.compile(r"^\s*pim\s+(sm|dm)\s*$", re.IGNORECASE)
#: `static-rp 10.1.1.1`、`static-rp 10.1.1.1 2001 preferred`、
#: `static-rp 10.1.1.1 acl-name myacl preferred`。後ろは _cw_rp_scope で読む
_CW_STATIC_RP = re.compile(r"^\s*static-rp\s+(\S+)(?:\s+(.+))?$", re.IGNORECASE)
_CW_CANDIDATE = re.compile(r"^\s*c-(?:rp|bsr)\s+\S+", re.IGNORECASE)
_CW_SNOOP = re.compile(r"^\s*igmp-snooping\s+enable\s*$", re.IGNORECASE)
#: H3C / NEC QX は `igmp-snooping querier`、Huawei は `igmp-snooping querier enable`
_CW_QUERIER = re.compile(r"^\s*igmp-snooping\s+querier(?:\s+enable)?\s*$", re.IGNORECASE)
#: H3C の `igmp-snooping` だけの行。全体で入れて、IGMP-snooping の囲みに入る
_CW_SNOOP_VIEW = re.compile(r"^igmp-snooping\s*$", re.IGNORECASE)
#: その囲みの中の `enable vlan 2 to 10`（帯をまとめて入れる）
_CW_ENABLE_VLANS = re.compile(r"^\s+enable\s+vlan\s+(.+)$", re.IGNORECASE)
_CW_VLAN = re.compile(r"^vlan\s+(\d+)\s*$", re.IGNORECASE)
_CW_INTERFACE = re.compile(r"^interface\s+(\S+)\s*$", re.IGNORECASE)
#: 範囲に数えない飾りの語。preferred は静的 RP を優先する印、bidir は双方向 PIM
_CW_RP_FLAGS = {"preferred", "bidir"}


def _cw_rp_scope(rest: str | None) -> str | None:
    """`static-rp` の後ろから、対象の範囲（ACL の番号か名前）だけを取り出す。"""
    words = [word for word in (rest or "").split() if word.lower() not in _CW_RP_FLAGS]
    if words and words[0].lower() == "acl-name":
        words = words[1:]
    return words[0] if words else None


def _vlan_list(text: str) -> list[int]:
    """`2 to 10`、`2 5 to 8` を帯の番号に開く。"""
    numbers: list[int] = []
    words = text.split()
    index = 0
    while index < len(words):
        word = words[index]
        if not word.isdigit():
            break
        ranged = index + 2 < len(words) and words[index + 1].lower() == "to"
        if ranged and words[index + 2].isdigit():
            start, end = int(word), int(words[index + 2])
            if start <= end <= 4094:
                numbers.extend(range(start, end + 1))
            index += 3
            continue
        numbers.append(int(word))
        index += 1
    return numbers


def parse_undoform(text: str, found: ParsedMulticast) -> None:
    """NEC QX と Aruba CX。帯の中に書く流儀を見る。"""
    interface: str | None = None
    vlan: int | None = None
    snoop_view = False
    for raw in text.splitlines():
        line = raw.rstrip()
        head = _CW_INTERFACE.match(line)
        if head:
            interface, vlan, snoop_view = head.group(1), None, False
            continue
        in_vlan = _CW_VLAN.match(line)
        if in_vlan:
            interface, vlan, snoop_view = None, int(in_vlan.group(1)), False
            continue
        if _CW_SNOOP_VIEW.match(line):
            interface, vlan, snoop_view = None, None, True
            found.snooping = True
            continue
        if line and not line.startswith((" ", "\t")):
            snoop_view = False
            if not _CW_ROUTING.match(line) and not line.lower().startswith("pim"):
                interface, vlan = None, None

        many = _CW_ENABLE_VLANS.match(line) if snoop_view else None
        if many:
            for number in _vlan_list(many.group(1)):
                _vlan(found, number).enabled = True
            continue

        if _CW_ROUTING.match(line):
            found.routing = True
            continue
        pim = _CW_PIM.match(line)
        if pim and interface:
            found.pim_interfaces.append(
                ParsedPimInterface(
                    name=interface, mode=_mode("dense" if pim.group(1) == "dm" else "sparse")
                )
            )
            continue
        rp = _CW_STATIC_RP.match(line)
        if rp:
            found.rps.append(ParsedRp(address=rp.group(1), scope=_cw_rp_scope(rp.group(2))))
            continue
        if _CW_CANDIDATE.match(line):
            found.dynamic_rp = True
            continue
        if _CW_SNOOP.match(line):
            if vlan is None:
                found.snooping = True
            else:
                _vlan(found, vlan).enabled = True
            continue
        if _CW_QUERIER.match(line):
            if vlan is None:
                for item in found.vlans:
                    item.querier = True
            else:
                _vlan(found, vlan).querier = True


_PARSERS = {
    "cisco": parse_cisco,
    "arista": parse_cisco,
    "nxos": parse_cisco,
    "juniper": parse_junos,
    "undoform": parse_undoform,
    "nec": parse_undoform,
    "aruba": parse_undoform,
}


def parse(text: str, vendor: str = "cisco") -> ParsedMulticast | None:
    """書いてあれば読む。1行も無ければ None（無いのか読めていないのかを混ぜない）。"""
    if not has_any(text):
        return None
    found = ParsedMulticast()
    handler = _PARSERS.get(vendor, parse_cisco)
    handler(text, found)
    if handler is not parse_cisco and not found.in_use:
        # 書き方が読み手と合わなかったときのために、Cisco 流でも一度見る
        parse_cisco(text, found)
    return found if found.in_use else None
