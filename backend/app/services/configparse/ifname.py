"""インターフェース名の正規化。

同じポートが `Gi0/1` `Gig 0/1` `GigabitEthernet0/1` と書かれるため、
突き合わせる前に正式名へ寄せる。表示は元の表記を残し、比較にだけ正規化名を使う。
"""

from __future__ import annotations

import re

# 略記 → 正式名。長い接頭辞から順に判定する。
ABBREVIATIONS: tuple[tuple[str, str], ...] = (
    ("ten-gigabitethernet", "TenGigabitEthernet"),
    ("tengigabitethernet", "TenGigabitEthernet"),
    ("twentyfivegige", "TwentyFiveGigE"),
    ("forty-gigabitethernet", "FortyGigabitEthernet"),
    ("fortygigabitethernet", "FortyGigabitEthernet"),
    ("hundredgige", "HundredGigE"),
    ("gigabitethernet", "GigabitEthernet"),
    # NEC IX。GigabitEthernet より先に見ると Cisco 側へ寄ってしまうので、
    # 正式名そのものだけをここに置く（略記 ge は Juniper と衝突するので下）
    ("gigaethernet", "GigaEthernet"),
    ("bridge-aggregation", "Bridge-Aggregation"),
    ("fastethernet", "FastEthernet"),
    ("port-channel", "Port-channel"),
    ("portchannel", "Port-channel"),
    ("management", "Management"),
    ("ethernet", "Ethernet"),
    ("vlan", "Vlan"),
    ("loopback", "Loopback"),
    ("tunnel", "Tunnel"),
    ("twe", "TwentyFiveGigE"),
    ("hun", "HundredGigE"),
    ("tengig", "TenGigabitEthernet"),
    ("fortygig", "FortyGigabitEthernet"),
    ("xge", "TenGigabitEthernet"),
    ("gig", "GigabitEthernet"),
    ("fas", "FastEthernet"),
    ("mgmt", "Management"),
    ("eth", "Ethernet"),
    ("fo", "FortyGigabitEthernet"),
    ("te", "TenGigabitEthernet"),
    ("gi", "GigabitEthernet"),
    ("ge", "GigabitEthernet"),  # Comware の GE1/0/1。Juniper の ge-0/0/1 は先に除外する
    ("fa", "FastEthernet"),
    ("po", "Port-channel"),
    ("lo", "Loopback"),
    ("tu", "Tunnel"),
    ("vl", "Vlan"),
    ("et", "Ethernet"),
)

# Juniper 系（ge-0/0/1 など）。ハイフン必須。無いと Comware の GE1/0/1 を Juniper と誤る
_JUNIPER = re.compile(r"^(ge|xe|et|fe|em|fxp|irb|ae|lo)-\d", re.IGNORECASE)
_SPLIT = re.compile(r"^([A-Za-z\-]+)\s*([\d/\.:]+.*)?$")


def expand(name: str) -> str:
    """`Gi 0/1` → `GigabitEthernet0/1`。判別できない名前はそのまま返す。"""
    text = (name or "").strip()
    if not text:
        return ""

    if _JUNIPER.match(text) or "/" in text and text[0].islower() and "-" in text.split("/")[0]:
        return text.lower()

    match = _SPLIT.match(text.replace(" ", ""))
    if not match:
        return text

    prefix, suffix = match.group(1).lower(), (match.group(2) or "")
    for abbreviation, full in ABBREVIATIONS:
        if prefix == abbreviation:
            return f"{full}{suffix}"
    return text


def normalize(name: str) -> str:
    """突き合わせ用のキー。大小文字と表記ゆれを吸収する。"""
    return expand(name).lower().replace(" ", "")


def normalize_hostname(hostname: str) -> str:
    """FQDN やシリアル付き表記を、機器名の突き合わせ用に整える。

    CDP は `sw2.example.com` や `sw2(FOC1234)` の形で返すことがある。
    """
    text = (hostname or "").strip()
    text = re.sub(r"\(.*?\)", "", text)  # 末尾の (シリアル)
    text = text.split(".")[0]  # ドメイン部
    return text.strip().lower()
