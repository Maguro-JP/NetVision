"""VRF の経路の相互流入（route-target）を読む。

VRF は RD で区別し、route-target で「どの VRF の経路を受け取るか」を決める。
片側が export していないものを反対側が import していても、設定は入っているのに
経路は流れない。1台の Config の中では正しいので、機器をまたがないと見つからない。

読めるのは次の書き方。全部を読み解く必要は無いので、この行だけを見る軽い読み手にする。

- Cisco / Arista `vrf definition X` / `ip vrf X` の下の `route-target import|export|both A:B`
- Junos `set routing-instances X vrf-target target:A:B`（import / export 付きも）
- Comware（NEC QX）`ip vpn-instance X` の下の
  `vpn-target A:B import-extcommunity|export-extcommunity|both`
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HEAD = re.compile(
    r"^(?:vrf definition|ip vrf|vrf context|ip vpn-instance)\s+(\S+)\s*$", re.IGNORECASE
)
_CISCO_RT = re.compile(r"^\s+route-target\s+(import|export|both)\s+(\S+)", re.IGNORECASE)
_COMWARE_RT = re.compile(
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
    for raw in text.splitlines():
        head = _HEAD.match(raw)
        if head:
            current = head.group(1)
            out.setdefault(current, RouteTargets())
            continue
        junos = _JUNOS_RT.match(raw.strip())
        if junos:
            targets = out.setdefault(junos.group(1), RouteTargets())
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
        comware = _COMWARE_RT.match(raw)
        if comware:
            direction = comware.group(2).lower().replace("-extcommunity", "")
            for value in comware.group(1).split():
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
