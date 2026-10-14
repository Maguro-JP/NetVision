"""インターフェース名の正規化。

同じポートが `Gi0/1` `Gig 0/1` `GigabitEthernet0/1` と書かれるため、
突き合わせる前に正式名へ寄せる。表示は元の表記を残し、比較にだけ正規化名を使う。

出典: Catalyst 9000 の mGig の名前（TwoGigabitEthernet・FiveGigabitEthernet・`Tw`）は
Cisco の Command Reference と Migration Guide で、`fi`・`hu` の略記は Interface and Hardware
Components Configuration Guide で、H3C Comware の `BAGG`・`RAGG` は S9820 の、`WGE`・`FGE`・
`HGE` は S6850・S7500E-XS・S12500R の Command Reference で確かめた（tests/test_ifname.py）。
ほかの略記は既存の試験の形のまま（未確認）。
"""

from __future__ import annotations

import functools
import re

# 略記 → 正式名。長い接頭辞から順に判定する。
ABBREVIATIONS: tuple[tuple[str, str], ...] = (
    ("ten-gigabitethernet", "TenGigabitEthernet"),
    ("tengigabitethernet", "TenGigabitEthernet"),
    ("twentyfivegige", "TwentyFiveGigE"),
    # H3C Comware の正式名はハイフン入り。出典: H3C「S6850 & S9850 Switch Series Command
    # References, Release 655x-6W100」03-Ethernet interface commands
    ("twenty-fivegige", "TwentyFiveGigE"),
    # Catalyst 9000 の mGig（2.5G・5G）。出典: Command Reference, Cisco IOS XE 17.10.x
    # （Catalyst 9300）の interface コマンド
    ("twogigabitethernet", "TwoGigabitEthernet"),
    ("fivegigabitethernet", "FiveGigabitEthernet"),
    ("forty-gigabitethernet", "FortyGigabitEthernet"),
    ("fortygigabitethernet", "FortyGigabitEthernet"),
    # H3C Comware の 40G の正式名（出典は下の fge）
    ("fortygige", "FortyGigabitEthernet"),
    ("hundredgige", "HundredGigE"),
    ("gigabitethernet", "GigabitEthernet"),
    # NEC IX。GigabitEthernet より先に見ると Cisco 側へ寄ってしまうので、
    # 正式名そのものだけをここに置く（略記 ge は Juniper と衝突するので下）
    ("gigaethernet", "GigaEthernet"),
    ("bridge-aggregation", "Bridge-Aggregation"),
    ("route-aggregation", "Route-Aggregation"),
    # H3C Comware の集約口の略記。出典: H3C「S9820-8M[S6880] Command References,
    # R1116Pxx-6W100」display link-aggregation summary の凡例
    # （BAGG -- Bridge-Aggregation, RAGG -- Route-Aggregation）
    ("bagg", "Bridge-Aggregation"),
    # 25G 口の略記（同じ資料の display interface twenty-fivegige 1/0/3 brief の出力が WGE1/0/3）
    ("wge", "TwentyFiveGigE"),
    # 40G・100G の略記。出典: H3C「S7500E-XS Command References, R2418P05-6W100」（display
    # interface fortygige 1/1/3 brief の出力が FGE1/1/3）、「S12500R Command References
    # (R36xx)-6W101」（display interface hundredgige 1/0/3 brief の出力が HGE1/0/3）
    ("fge", "FortyGigabitEthernet"),
    ("hge", "HundredGigE"),
    ("ragg", "Route-Aggregation"),
    ("fastethernet", "FastEthernet"),
    ("port-channel", "Port-channel"),
    ("portchannel", "Port-channel"),
    ("management", "Management"),
    ("ethernet", "Ethernet"),
    ("vlan", "Vlan"),
    ("loopback", "Loopback"),
    ("tunnel", "Tunnel"),
    ("twe", "TwentyFiveGigE"),
    # 2.5G は `Tw1/0/1`（Cisco「Migration Guide from Cisco Catalyst」Table 5）。
    # 25G の `Twe` とは別の口
    ("tw", "TwoGigabitEthernet"),
    ("hun", "HundredGigE"),
    # 5G は fi、100G は hu（出典: Cisco「Interface and Hardware Components Configuration
    # Guide, Cisco IOS XE Cupertino 17.9.x (Catalyst 9300 Switches)」Configuring Interface
    # Characteristics）
    ("fi", "FiveGigabitEthernet"),
    ("hu", "HundredGigE"),
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
    ("ge", "GigabitEthernet"),  # NEC QX の GE1/0/1。Juniper の ge-0/0/1 は先に除外する
    ("fa", "FastEthernet"),
    ("po", "Port-channel"),
    ("lo", "Loopback"),
    ("tu", "Tunnel"),
    ("vl", "Vlan"),
    ("et", "Ethernet"),
)

# Juniper 系（ge-0/0/1 など）。ハイフン必須。無いと NEC QX の GE1/0/1 を Juniper と誤る
_JUNIPER = re.compile(r"^(ge|xe|et|fe|em|fxp|irb|ae|lo)-\d", re.IGNORECASE)
_SPLIT = re.compile(r"^([A-Za-z\-]+)\s*([\d/\.:]+.*)?$")


@functools.lru_cache(maxsize=65536)
def expand(name: str) -> str:
    """`Gi 0/1` → `GigabitEthernet0/1`。判別できない名前はそのまま返す。

    文字列だけで決まるので結果を覚える。口を足すたびに既にある口と名前を
    突き合わせるため、数千口の Config では同じ名前を何百万回も直すことになる。
    """
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


@functools.lru_cache(maxsize=65536)
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
