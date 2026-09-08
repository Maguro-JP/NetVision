"""ファーウェイ VRP（S / CE / AR シリーズ）の config を読む。

`sysname`・`vlan`・`port link-type`・`ip route-static` の書き方は NEC QX と
一致する箇所が多く、いまは NEC QX の読み手を通してから VRP だけの書き方
（`Vlanif10`、`allow-pass`、VLAN 名を `description` に書く形）を上から直している。

だが別の製品である。似ているだけで同じ扱いにしない。H3C と同じく、
読み手はこの機種のものとして独立させる（次の変更で行う）。メーカーの取り違えは、
別のメーカーの保守期限と脆弱性で判定することになり、しかも黙って通る。
"""

from __future__ import annotations

import re

from app.services.configparse import lldptable, nec
from app.services.configparse.base import ParsedConfig, Vendor

#: VRP の名乗り。NEC QX と見分けが付くのはここだけ
_BANNER = re.compile(
    r"Huawei Versatile Routing Platform|VRP\s*\(R\)\s*software|\bHUAWEI\b", re.IGNORECASE
)
_VERSION = re.compile(r"^\s*VRP\s*\(R\).*?Version\s+([\w.]+)", re.IGNORECASE | re.MULTILINE)
#: `Version 8.180 (CE6850 V200R005C10SPC800)` の括弧に型番が入る
_MODEL_IN_VERSION = re.compile(r"\(\s*((?:CE|S|AR|NE|USG)\d[\w-]*)\s", re.IGNORECASE)
_DISPLAY_MODEL = re.compile(
    r"^\s*(?:HUAWEI\s+)?((?:CE|S|AR|NE|USG)\d[\w-]*)\s+(?:Router|Switch|uptime)",
    re.IGNORECASE | re.MULTILINE,
)


def detect(text: str) -> Vendor | None:
    """VRP かどうか。名乗りが出ているときだけ言う。

    NEC QX と書き方が重なる箇所があるので、名乗りが無いものを VRP だと決めない。
    決め打つと、NEC QX の config をファーウェイとして読むことになる。
    """
    return Vendor.HUAWEI if _BANNER.search(text) else None


#: VRP は VLAN の名前を `description` に書くことが多い（NEC QX は `name`）
_VLAN_HEAD = re.compile(r"^\s*vlan\s+(\d+)\s*$", re.IGNORECASE)
_VLAN_DESCRIPTION = re.compile(r"^\s+description\s+(.+?)\s*$", re.IGNORECASE)


def _vlan_names(text: str) -> dict[int, str]:
    found: dict[int, str] = {}
    current: int | None = None
    for raw in text.splitlines():
        head = _VLAN_HEAD.match(raw)
        if head:
            current = int(head.group(1))
            continue
        if current is None:
            continue
        described = _VLAN_DESCRIPTION.match(raw)
        if described:
            found[current] = described.group(1)
        if not raw.startswith((" ", "\t")):
            current = None
    return found


def parse(text: str) -> ParsedConfig:
    """いまは NEC QX の読み手を通してから、VRP だけの書き方を上から直す。"""
    parsed = nec.parse(text, Vendor.NEC_QX)
    parsed.vendor = Vendor.HUAWEI
    parsed.os_name = "VRP"
    parsed.os_version = None
    parsed.model = None

    found = _VERSION.search(text)
    if found:
        parsed.os_version = found.group(1)
    found = _MODEL_IN_VERSION.search(text) or _DISPLAY_MODEL.search(text)
    if found:
        parsed.model = found.group(1)

    lldptable.parse(text, parsed)
    for vlan_id, name in _vlan_names(text).items():
        if not parsed.vlans.get(vlan_id):
            parsed.vlans[vlan_id] = name
    return parsed
