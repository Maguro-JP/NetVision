"""VRF の経路の相互流入（route-target）を読む。

VRF は RD で区別し、route-target で「どの VRF の経路を受け取るか」を決める。
片側が export していないものを反対側が import していても、設定は入っているのに
経路は流れない。1台の Config の中では正しいので、機器をまたがないと見つからない。

読めるのは次の書き方。全部を読み解く必要は無いので、この行だけを見る軽い読み手にする。

- Cisco `vrf definition X` / `ip vrf X` の下（`address-family` の下も）の
  `route-target import|export|both A:B`
- Arista `router bgp N` の下の `vrf X` の下の
  `route-target import|export [evpn|vpn-ipv4|vpn-ipv6] A:B`
- Junos `set routing-instances X vrf-target target:A:B`（import / export 付きも）
- NEC QX `ip vpn-instance X` の下の
  `vpn-target A:B import-extcommunity|export-extcommunity|both`

値は `A:B` の形（AS 番号か IP アドレスと番号）のものだけを持つ。Junos の `vrf-target auto` や
Arista の `route-target export evpn route-map 名前` は、ここでは比べられる値ではない。

出典:
- Cisco「MPLS: Layer 3 VPNs Configuration Guide, Cisco IOS XE 17」の「MPLS VPN VRF CLI for IPv4
  and IPv6 VPNs」と、「Cisco IOS IPv6 Command Reference」の route-target
- Arista「EOS User Manual」の「Inter-VRF Local Route Leaking」（4.36.0F）
- Juniper「Junos OS CLI Reference」の vrf-target と、「Configuring Policies for the VRF Table on PE
  Routers in VPNs」
- NEC QX の `vpn-target` は未確認（公式の資料に当たれていない）。表番号はどれも無い
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HEAD = re.compile(
    r"^(?:vrf definition|ip vrf|vrf context|ip vpn-instance)\s+(\S+)\s*$", re.IGNORECASE
)
_CISCO_RT = re.compile(
    r"^\s+route-target\s+(import|export|both)\s+(?:(?:evpn|vpn-ipv4|vpn-ipv6)\s+)?(\S+:\S+)",
    re.IGNORECASE,
)
#: Arista は VRF の route-target を `router bgp` の下の `vrf X` に書く
_BGP_HEAD = re.compile(r"^router bgp\s+\S+", re.IGNORECASE)
_BGP_VRF = re.compile(r"^(\s+)vrf\s+(\S+)\s*$", re.IGNORECASE)
_UNDOFORM_RT = re.compile(
    r"^\s+vpn-target\s+(.+?)\s+(import-extcommunity|export-extcommunity|both)\s*$",
    re.IGNORECASE,
)
_JUNOS_RT = re.compile(
    r"^set routing-instances (\S+) vrf-target(?:\s+(import|export))?\s+(\S+)", re.IGNORECASE
)
_HINT = re.compile(r"route-target|vrf-target|vpn-target", re.IGNORECASE)


@dataclass
class RouteTargets:
    imports: set[str] = field(default_factory=set)
    exports: set[str] = field(default_factory=set)


def has_any(text: str) -> bool:
    return bool(_HINT.search(text))


def parse(text: str) -> dict[str, RouteTargets]:
    out: dict[str, RouteTargets] = {}
    current: str | None = None
    in_bgp = False
    #: `router bgp` の下の `vrf X` の字下げ。これより浅い行に戻ったら VRF を出る
    vrf_indent: int | None = None
    for raw in text.splitlines():
        head = _HEAD.match(raw)
        if head:
            current, in_bgp, vrf_indent = head.group(1), False, None
            out.setdefault(current, RouteTargets())
            continue
        if _BGP_HEAD.match(raw):
            current, in_bgp, vrf_indent = None, True, None
            continue
        if in_bgp:
            if raw and not raw[0].isspace():
                in_bgp, current, vrf_indent = False, None, None
            else:
                bgp_vrf = _BGP_VRF.match(raw)
                if bgp_vrf:
                    current, vrf_indent = bgp_vrf.group(2), len(bgp_vrf.group(1))
                    out.setdefault(current, RouteTargets())
                    continue
                indent = len(raw) - len(raw.lstrip())
                if current is not None and raw.strip() and indent <= (vrf_indent or 0):
                    current = None
        junos = _JUNOS_RT.match(raw.strip())
        if junos:
            targets = out.setdefault(junos.group(1), RouteTargets())
            if ":" in junos.group(3):  # `vrf-target auto` は機器が決める値で、比べられない
                _add(targets, (junos.group(2) or "both").lower(), _bare(junos.group(3)))
            continue
        if current is None:
            continue
        if raw and not raw[0].isspace():
            current = None
            continue
        cisco = _CISCO_RT.match(raw)
        if cisco:
            _add(out[current], cisco.group(1).lower(), _bare(cisco.group(2)))
            continue
        rule = _UNDOFORM_RT.match(raw)
        if rule:
            direction = rule.group(2).lower().replace("-extcommunity", "")
            for value in rule.group(1).split():
                _add(out[current], direction, _bare(value))
    return out


def _bare(value: str) -> str:
    """`target:65000:10` と `65000:10` を同じものとして扱う。"""
    return value.lower().removeprefix("target:")


def _add(targets: RouteTargets, direction: str, value: str) -> None:
    if direction in ("import", "both"):
        targets.imports.add(value)
    if direction in ("export", "both"):
        targets.exports.add(value)
