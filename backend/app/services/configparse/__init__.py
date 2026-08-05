"""Config / show 出力のパース。

ベンダーを自動判別して、該当のパーサに渡す。判別できない場合も
「読めた分だけ」返す方針で、取り込みが丸ごと失敗しないようにしている。
"""

from __future__ import annotations

from app.services.configparse import cisco, fortinet, juniper
from app.services.configparse.base import (
    NeighborProtocol,
    ParsedConfig,
    ParsedInterface,
    ParsedNeighbor,
    Vendor,
)
from app.services.configparse.ifname import expand, normalize, normalize_hostname

# 判別は「特徴が強い順」に試す
_DETECTORS = (fortinet.detect, juniper.detect, cisco.detect)

VENDOR_LABELS: dict[Vendor, str] = {
    Vendor.CISCO_IOS: "Cisco IOS / IOS-XE",
    Vendor.CISCO_NXOS: "Cisco NX-OS",
    Vendor.JUNIPER: "Juniper Junos",
    Vendor.FORTINET: "Fortinet FortiOS",
    Vendor.UNKNOWN: "判別できません",
}

VENDOR_NAMES: dict[Vendor, str] = {
    Vendor.CISCO_IOS: "Cisco",
    Vendor.CISCO_NXOS: "Cisco",
    Vendor.JUNIPER: "Juniper",
    Vendor.FORTINET: "Fortinet",
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
        return cisco.parse(text, vendor)
    if vendor is Vendor.JUNIPER:
        return juniper.parse(text)
    if vendor is Vendor.FORTINET:
        return fortinet.parse(text)

    # 判別できなくても、Cisco 形式の show 出力が混ざっていることが多いので試す
    fallback = cisco.parse(text, Vendor.UNKNOWN)
    fallback.vendor = Vendor.UNKNOWN
    fallback.os_name = None
    return fallback


__all__ = [
    "NeighborProtocol",
    "ParsedConfig",
    "ParsedInterface",
    "ParsedNeighbor",
    "VENDOR_LABELS",
    "VENDOR_NAMES",
    "Vendor",
    "detect_vendor",
    "expand",
    "normalize",
    "normalize_hostname",
    "parse",
]
