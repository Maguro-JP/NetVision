"""Config / show 出力のパース。

ベンダーを自動判別して、該当のパーサに渡す。判別できない場合も
「読めた分だけ」返す方針で、取り込みが丸ごと失敗しないようにしている。
"""

from __future__ import annotations

from app.services.configparse import cisco, fortinet, juniper, nec
from app.services.configparse.base import (
    NeighborProtocol,
    ParsedBgp,
    ParsedBgpNeighbor,
    ParsedConfig,
    ParsedInterface,
    ParsedNeighbor,
    ParsedOspf,
    ParsedOspfArea,
    ParsedRedundancy,
    ParsedRoute,
    ParsedVrf,
    Vendor,
)
from app.services.configparse.ifname import expand, normalize, normalize_hostname

# 判別は「特徴が強い順」に試す
# NEC は Cisco 風に見える箇所があるので、Cisco より先に試す
_DETECTORS = (fortinet.detect, juniper.detect, nec.detect, cisco.detect)

VENDOR_LABELS: dict[Vendor, str] = {
    Vendor.CISCO_IOS: "Cisco IOS / IOS-XE",
    Vendor.CISCO_NXOS: "Cisco NX-OS",
    Vendor.JUNIPER: "Juniper Junos",
    Vendor.FORTINET: "Fortinet FortiOS",
    Vendor.NEC_IX: "NEC IX シリーズ",
    Vendor.NEC_QX: "NEC QX シリーズ",
    Vendor.UNKNOWN: "判別できません",
}

VENDOR_NAMES: dict[Vendor, str] = {
    Vendor.CISCO_IOS: "Cisco",
    Vendor.CISCO_NXOS: "Cisco",
    Vendor.JUNIPER: "Juniper",
    Vendor.FORTINET: "Fortinet",
    Vendor.NEC_IX: "NEC",
    Vendor.NEC_QX: "NEC",
}


def detect_vendor(text: str) -> Vendor:
    for detector in _DETECTORS:
        vendor = detector(text)
        if vendor is not None:
            return vendor
    return Vendor.UNKNOWN


def parse(text: str, vendor: Vendor | None = None) -> ParsedConfig:
    vendor = vendor or detect_vendor(text)

    if vendor in (Vendor.CISCO_IOS, Vendor.CISCO_NXOS):
        parsed = cisco.parse(text, vendor)
    elif vendor is Vendor.JUNIPER:
        parsed = juniper.parse(text)
    elif vendor is Vendor.FORTINET:
        parsed = fortinet.parse(text)
    elif vendor in (Vendor.NEC_IX, Vendor.NEC_QX):
        parsed = nec.parse(text, vendor)
    else:
        # 判別できなくても、Cisco 形式の show 出力が混ざっていることが多いので試す
        parsed = cisco.parse(text, Vendor.UNKNOWN)
        parsed.vendor = Vendor.UNKNOWN
        parsed.os_name = None

    _collect_referenced_vrfs(parsed)
    return parsed


def _collect_referenced_vrfs(parsed: ParsedConfig) -> None:
    """ポートから参照されているのに定義が見当たらない VRF も一覧に載せる。

    定義部分が別ファイルだったり、取り込んだのが抜粋だったりする。
    「ポートは VRF に入っているのに VRF が無いことになる」方が困るので、
    参照側からも起こしておく。
    """
    for interface in parsed.interfaces:
        if interface.vrf:
            parsed.add_vrf(ParsedVrf(name=interface.vrf))


__all__ = [
    "NeighborProtocol",
    "ParsedBgp",
    "ParsedBgpNeighbor",
    "ParsedConfig",
    "ParsedInterface",
    "ParsedNeighbor",
    "ParsedOspf",
    "ParsedOspfArea",
    "ParsedRedundancy",
    "ParsedRoute",
    "ParsedVrf",
    "VENDOR_LABELS",
    "VENDOR_NAMES",
    "Vendor",
    "detect_vendor",
    "expand",
    "normalize",
    "normalize_hostname",
    "parse",
]
