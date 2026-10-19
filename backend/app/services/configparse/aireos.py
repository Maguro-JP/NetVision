"""Cisco AireOS の無線コントローラ（5508 / 5520 / 3504 など）の show 出力を読む。

AireOS は設定ファイルを持ち出す形が現場に馴染まないので、`show sysinfo` などの
画面をそのまま貼ったものが来る。IOS-XE の C9800 とは別物で、`interface` も
`wlan` も IOS の書き方ではない。

読むのは、貼られていた表だけ。無かったものは「無い」ではなく「読んでいない」
として、そもそも値を作らない。

- `show sysinfo` … 機器名と版数、管理アドレス
- `show inventory` … 型番（PID）とシリアル
- `show interface summary` … 口の名前・VLAN・アドレス
- `show wlan summary` … 電波の名前（SSID）と、載せている口
- `show ap summary` … ぶら下がっている AP の名前・型番・アドレス

出典: Cisco「Wireless LAN Controller Port and Interface Command Reference, Release 7.4」の
`show interface summary` の作例（`management 1 untagged …`、`service-port N/A N/A …`）と、
「Cisco Mobility Express Command Reference, Release 8.10」の Ports and Interfaces Commands の
作例（`management LAG 56 …`、`redundancy-port - untagged …`）。名前に空白が入る
`vlan 81` は、同社の技術資料「Configure VLANs on Wireless LAN Controllers」の出力で確かめた。
`show wlan summary` の列（WLAN ID / Profile Name / SSID / Status / Interface Name）は
「Cisco Wireless Controller Command Reference, Release 8.5」と上の技術資料の作例で確かめた。
表番号は無い。
`show ap summary` の列は「Configure AireOS Wireless LAN Controller and Lightweight Access
Point」の作例で確かめた（アドレスの列が無い版）。`show sysinfo` の列は未確認。
"""

from __future__ import annotations

import re

from app.services.configparse.base import (
    ParsedConfig,
    ParsedInterface,
    ParsedWireless,
    ParsedWirelessAp,
    ParsedWlan,
    Vendor,
)
from app.services.configparse.ifname import normalize

#: `show sysinfo` の点線区切り。AireOS 以外でこの形は出てこない
_FIELD = re.compile(r"^([A-Za-z][\w'’ /()\.-]*?)\.{3,}\s*(.*)$", re.MULTILINE)
_BANNER = re.compile(r"\(Cisco Controller\)|Cisco Controller\b|AireOS", re.IGNORECASE)
_PID = re.compile(r"^PID:\s*([\w-]+)\s*,.*?SN:\s*(\S+?)\s*$", re.IGNORECASE | re.MULTILINE)

#: `show interface summary` の行。名前・ポート番号・VLAN・アドレス。
#: ポートは番号のほか `LAG`・`-`・`N/A`、VLAN は番号のほか `untagged`・`N/A` が入る。
#: 名前には空白が入ることがある（`vlan 81`）
_INTERFACE_ROW = re.compile(
    r"^(?P<name>[\w-]+(?: [\w-]+)?)\s+(?P<port>\d+|LAG|-{1,2}|N/A)\s+"
    r"(?P<vlan>\d+|untagged|N/A)\s+(?P<address>\d+\.\d+\.\d+\.\d+)\s+",
    re.MULTILINE | re.IGNORECASE,
)
#: `show wlan summary` の行。`1  Corp / Corp-WiFi  Enabled  management`
_WLAN_ROW = re.compile(
    r"^(?P<id>\d+)\s+(?P<profile>\S.*?)\s*/\s*(?P<ssid>\S.*?)\s{2,}"
    r"(?P<status>Enabled|Disabled)\b"
    # 載せている口。名前に空白が入ることがある（`vlan 81`）。
    # 後ろに PMIPv6 Mobility の列が付く版がある
    r"(?:[ \t]+(?P<interface>[\w-]+(?: \d+)?))?",
    re.MULTILINE | re.IGNORECASE,
)
#: `show ap summary` の行。名前・スロット数・型番・MAC のあとに国とアドレスが続く
_AP_ROW = re.compile(
    r"^(?P<name>[\w.-]+)\s+\d+\s+(?P<model>AIR-[\w-]+)\s+"
    r"(?P<mac>[0-9a-f]{2}(?::[0-9a-f]{2}){5})\s+(?P<rest>.*)$",
    re.MULTILINE | re.IGNORECASE,
)
_ADDRESS_IN = re.compile(r"\b(\d+\.\d+\.\d+\.\d+)\b")


def detect(text: str) -> Vendor | None:
    """AireOS かどうか。名乗りか、点線区切りの見出しで見る。"""
    if _BANNER.search(text):
        return Vendor.CISCO_WLC
    fields = _fields(text)
    if "Product Version" in fields and "System Name" in fields:
        return Vendor.CISCO_WLC
    return None


def _fields(text: str) -> dict[str, str]:
    """点線区切りの `見出し....値` を辞書にする。"""
    return {name.strip(): value.strip() for name, value in _FIELD.findall(text)}


def parse(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.CISCO_WLC, os_name="AireOS")
    fields = _fields(text)

    parsed.hostname = fields.get("System Name") or None
    parsed.os_version = fields.get("Product Version") or fields.get("RTOS Version") or None

    found = _PID.search(text)
    if found:
        parsed.model = found.group(1)
        parsed.serial_number = found.group(2)

    _read_interfaces(text, fields, parsed)
    parsed.wireless = _read_wireless(text, fields, parsed)
    return parsed


def _read_interfaces(text: str, fields: dict[str, str], parsed: ParsedConfig) -> None:
    """口は表からしか分からない。表が無ければ管理アドレスだけを持つ。"""
    rows = list(_INTERFACE_ROW.finditer(text))
    for row in rows:
        name = row.group("name")
        if name.lower() in ("interface", "name"):
            continue
        interface = parsed.add_interface(ParsedInterface(name=name, mode="routed"))
        vlan = row.group("vlan")
        # `untagged` はタグを付けない（VLAN 番号を持たない）、`N/A` は VLAN に載らない口
        interface.vlan = int(vlan) or None if vlan.isdigit() else None
        interface.ip_address = row.group("address")

    address = fields.get("IP Address")
    if not rows and address and _ADDRESS_IN.fullmatch(address):
        # 表が貼られていないときの拠りどころ。マスクは分からないので付けない
        parsed.add_interface(ParsedInterface(name="management", mode="routed", ip_address=address))


def _read_wireless(text: str, fields: dict[str, str], parsed: ParsedConfig) -> ParsedWireless:
    wireless = ParsedWireless()
    # WLAN の VLAN は、載せている口の VLAN（`show interface summary` が貼られていれば引ける）
    vlans = {normalize(i.name): i.vlan for i in parsed.interfaces}
    country = fields.get("Configured Country") or fields.get("Country")
    if country:
        wireless.country = country.split()[0]

    for row in _WLAN_ROW.finditer(text):
        wireless.wlans.append(
            ParsedWlan(
                name=row.group("profile").strip(),
                ssid=row.group("ssid").strip(),
                enabled=row.group("status").lower() == "enabled",
                vlan=vlans.get(normalize(row.group("interface") or "")),
                # 一覧の表には守り方が出てこない。`show wlan <id>` が要る
                security_read=False,
                raw=row.group(0).strip(),
            )
        )

    for row in _AP_ROW.finditer(text):
        addresses = _ADDRESS_IN.findall(row.group("rest"))
        wireless.aps.append(
            ParsedWirelessAp(
                name=row.group("name"),
                model=row.group("model"),
                mac=row.group("mac").lower(),
                ip_address=addresses[0] if addresses else None,
            )
        )
    return wireless
