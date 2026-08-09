"""ポート名を図に載せる形に整える。

構成図でポート名が読めない原因は2つある。

1. **長い**。`TenGigabitEthernet1/1/1` をそのまま出すと線より長くなる
2. **どちら側のポートか分からない**。1本の線に2つのポート名を並べると、
   「左が上位」のような規則を覚える必要が出る。図の向きが変われば
   その規則は成り立たなくなり、逆に読んでしまう

2 はラベルの置き方（両端に寄せる）で解く。ここは 1 を担当する。

種別（速度）は**色**で表せるので、文字は番号だけに削れる。
`Te1/1/1` の `Te` を色に移せば、残るのは `1/1/1` だけになる。
"""

from __future__ import annotations

import enum
import re


class Media(enum.StrEnum):
    """ポートの種別。線やラベルの色分けに使う。

    速度そのものではなく「見た目を分ける単位」。100M と 1G を混ぜないのは、
    古い機器が残っているところを図で見つけたいため。
    """

    FAST = "fast"  # 100M
    GIGA = "giga"  # 1G
    TEN = "ten"  # 10G
    FAST_40 = "forty"  # 25G / 40G
    HUNDRED = "hundred"  # 100G 以上
    LAG = "lag"  # 論理集約（Port-channel / ae / Bridge-Aggregation）
    VIRTUAL = "virtual"  # Vlan / Loopback / Tunnel
    OTHER = "other"


MEDIA_LABEL: dict[Media, str] = {
    Media.FAST: "100M",
    Media.GIGA: "1G",
    Media.TEN: "10G",
    Media.FAST_40: "25G/40G",
    Media.HUNDRED: "100G",
    Media.LAG: "論理集約",
    Media.VIRTUAL: "仮想",
    Media.OTHER: "その他",
}

# 白地に載せて読める濃さで、隣り合う種別が見分けられるように選ぶ
MEDIA_COLOR: dict[Media, str] = {
    Media.FAST: "#8a8a8a",
    Media.GIGA: "#2f6fb5",
    Media.TEN: "#2d8a4e",
    Media.FAST_40: "#b06a1f",
    Media.HUNDRED: "#8d3fbf",
    Media.LAG: "#c0392b",
    Media.VIRTUAL: "#6b6b6b",
    Media.OTHER: "#5a6270",
}

# 接頭辞 → (略記, 種別)。長いものから順に見る
_PREFIXES: tuple[tuple[str, str, Media], ...] = (
    ("tengigabitethernet", "Te", Media.TEN),
    ("ten-gigabitethernet", "Te", Media.TEN),
    ("twentyfivegige", "Twe", Media.FAST_40),
    ("fortygigabitethernet", "Fo", Media.FAST_40),
    ("hundredgige", "Hu", Media.HUNDRED),
    ("gigabitethernet", "Gi", Media.GIGA),
    # NEC IX は GigaEthernet（bit が無い）。Cisco と紛らわしいので別に持つ
    ("gigaethernet", "GE", Media.GIGA),
    ("fastethernet", "Fa", Media.FAST),
    ("bridge-aggregation", "BAGG", Media.LAG),
    ("route-aggregation", "RAGG", Media.LAG),
    ("port-channel", "Po", Media.LAG),
    ("m-gigabitethernet", "MGi", Media.GIGA),
    ("management", "Mgmt", Media.OTHER),
    ("loopback", "Lo", Media.VIRTUAL),
    ("tunnel", "Tu", Media.VIRTUAL),
    ("vlan-interface", "Vlan", Media.VIRTUAL),
    ("vlan", "Vlan", Media.VIRTUAL),
    ("xgigabitethernet", "XGE", Media.TEN),
    ("ethernet", "Eth", Media.OTHER),
)

# Juniper 系は接頭辞そのものが種別を表す
_JUNIPER: dict[str, tuple[str, Media]] = {
    "fe": ("fe", Media.FAST),
    "ge": ("ge", Media.GIGA),
    "xe": ("xe", Media.TEN),
    "et": ("et", Media.HUNDRED),
    "ae": ("ae", Media.LAG),
    "irb": ("irb", Media.VIRTUAL),
    "lo": ("lo", Media.VIRTUAL),
    "em": ("em", Media.OTHER),
    "fxp": ("fxp", Media.OTHER),
}

_SPLIT = re.compile(r"^([A-Za-z][A-Za-z\-]*?)\s*([\d/\.:].*)?$")
_JUNIPER_SPLIT = re.compile(r"^([a-z]+)-(\d.*)$")


def split(name: str) -> tuple[str, str, Media]:
    """ポート名を（略記, 番号, 種別）に分ける。

    読めない名前は略記に丸ごと入れて返す。勝手に削ると別のポートに見えるため。
    """
    text = (name or "").strip()
    if not text:
        return "", "", Media.OTHER

    juniper = _JUNIPER_SPLIT.match(text)
    if juniper and juniper.group(1).lower() in _JUNIPER:
        short, media = _JUNIPER[juniper.group(1).lower()]
        return short, juniper.group(2), media

    match = _SPLIT.match(text.replace(" ", ""))
    if not match:
        return text, "", Media.OTHER

    prefix = match.group(1).lower()
    number = match.group(2) or ""
    for candidate, short, media in _PREFIXES:
        if prefix == candidate:
            return short, number, media

    # 既に略記で書かれている場合（Gi1/0/1 など）
    for _, short, media in _PREFIXES:
        if prefix == short.lower():
            return short, number, media

    # Juniper はハイフン無しで書かれることもある（ae0 / lo0）。
    # 小文字のときだけ見る（Cisco の Lo0 などを取り違えないため）
    if match.group(1).islower() and prefix in _JUNIPER:
        short, media = _JUNIPER[prefix]
        return short, number, media

    return match.group(1), number, Media.OTHER


def media_of(name: str) -> Media:
    return split(name)[2]


def format(name: str, style: str = "short") -> str:
    """図に載せる文字列にする。

    - `full`   … そのまま（`TenGigabitEthernet1/1/1`）
    - `short`  … 略記（`Te1/1/1`）
    - `number` … 番号だけ（`1/1/1`）。種別は色で表す前提

    **種別が判別できた名前でしか番号だけにしない。** `port1` の `port` を
    削って `1` にすると、色にも情報が乗らないまま元の名前が消える。
    削るのは「色に移した分だけ」でなければならない。
    """
    if not name:
        return ""
    if style == "full":
        return name

    short, number, media = split(name)
    if style == "number" and number and media is not Media.OTHER:
        return number
    return f"{short}{number}" if number else short or name
