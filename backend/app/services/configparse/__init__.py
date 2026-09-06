"""Config / show 出力のパース。

ベンダーを自動判別して、該当のパーサに渡す。判別できない場合も
「読めた分だけ」返す方針で、取り込みが丸ごと失敗しないようにしている。
"""

from __future__ import annotations

from app.services.configparse import (
    alaxala,
    allied,
    arista,
    aruba,
    asa,
    cisco,
    features,
    fortinet,
    hapair,
    huawei,
    juniper,
    linkstate,
    loadbalance,
    multicast,
    nec,
    panos,
    wireless,
    yamaha,
)
from app.services.configparse.base import (
    FEATURE_LABELS,
    NeighborProtocol,
    ParsedBgp,
    ParsedBgpNeighbor,
    ParsedConfig,
    ParsedFeature,
    ParsedInterface,
    ParsedNeighbor,
    ParsedOspf,
    ParsedOspfArea,
    ParsedRedundancy,
    ParsedRoute,
    ParsedVrf,
    ParsedWireless,
    ParsedWirelessAp,
    ParsedWlan,
    Vendor,
)
from app.services.configparse.ifname import expand, normalize, normalize_hostname

# 判別は「特徴が強い順」に試す
# NEC は Cisco 風に見える箇所があるので、Cisco より先に試す
_DETECTORS = (
    fortinet.detect,
    juniper.detect,
    yamaha.detect,
    panos.detect,
    asa.detect,
    arista.detect,
    # アライドテレシスの目印（port1.0.1 / vlan database）は Aruba CX より強い。
    # vrrp の書き方が似ているので、先に見ないと取り違える
    allied.detect,
    # アラクサラは名乗りか型番が出ているときだけ言う。IOS 風なので、
    # 目印の無い config を横取りしない
    alaxala.detect,
    aruba.detect,
    # ファーウェイ VRP は Comware と書き方が重なる。名乗りが出ているうちに拾わないと
    # NEC QX として読んでしまう
    huawei.detect,
    nec.detect,
    cisco.detect,
)

VENDOR_LABELS: dict[Vendor, str] = {
    Vendor.CISCO_IOS: "Cisco IOS / IOS-XE",
    Vendor.CISCO_NXOS: "Cisco NX-OS",
    Vendor.CISCO_ASA: "Cisco ASA / FTD",
    Vendor.JUNIPER: "Juniper Junos",
    Vendor.FORTINET: "Fortinet FortiOS",
    Vendor.NEC_IX: "NEC IX シリーズ",
    Vendor.NEC_QX: "NEC QX シリーズ",
    Vendor.YAMAHA: "ヤマハ RTX / NVR",
    Vendor.PALO_ALTO: "Palo Alto PAN-OS",
    Vendor.ARISTA: "Arista EOS",
    Vendor.ARUBA_CX: "Aruba CX（AOS-CX）",
    Vendor.ALLIED: "アライドテレシス AlliedWare Plus",
    Vendor.ALAXALA: "アラクサラ AX シリーズ",
    Vendor.HUAWEI: "ファーウェイ VRP",
    Vendor.UNKNOWN: "判別できません",
}

VENDOR_NAMES: dict[Vendor, str] = {
    Vendor.CISCO_IOS: "Cisco",
    Vendor.CISCO_NXOS: "Cisco",
    Vendor.CISCO_ASA: "Cisco",
    Vendor.JUNIPER: "Juniper",
    Vendor.FORTINET: "Fortinet",
    Vendor.NEC_IX: "NEC",
    Vendor.NEC_QX: "NEC",
    Vendor.YAMAHA: "ヤマハ",
    Vendor.PALO_ALTO: "Palo Alto",
    Vendor.ARISTA: "Arista",
    Vendor.ARUBA_CX: "Aruba",
    Vendor.ALLIED: "アライドテレシス",
    Vendor.ALAXALA: "アラクサラ",
    Vendor.HUAWEI: "ファーウェイ",
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
    elif vendor is Vendor.YAMAHA:
        parsed = yamaha.parse(text)
    elif vendor is Vendor.CISCO_ASA:
        parsed = asa.parse(text)
    elif vendor is Vendor.PALO_ALTO:
        parsed = panos.parse(text)
    elif vendor is Vendor.ARISTA:
        parsed = arista.parse(text)
    elif vendor is Vendor.ARUBA_CX:
        parsed = aruba.parse(text)
    elif vendor is Vendor.ALLIED:
        parsed = allied.parse(text)
    elif vendor is Vendor.ALAXALA:
        parsed = alaxala.parse(text)
    elif vendor is Vendor.HUAWEI:
        parsed = huawei.parse(text)
    else:
        # 判別できなくても、Cisco 形式の show 出力が混ざっていることが多いので試す
        parsed = cisco.parse(text, Vendor.UNKNOWN)
        parsed.vendor = Vendor.UNKNOWN
        parsed.os_name = None

    _collect_referenced_vrfs(parsed)
    # 機能の拾い出しはベンダーに依らない。どの経路で来ても最後に通す
    parsed.features = features.parse(text)
    # 無線は書き方がベンダーごとに離れているので、判別とは別に最後へ通す。
    # 無線機かどうかは機種名では決まらない（コントローラ内蔵の機種がある）
    parsed.wireless = wireless.parse(text)
    # マルチキャストも書き方がベンダーごとに離れている。判別の結果だけ渡して最後へ
    parsed.load_balance = loadbalance.parse(text)
    parsed.multicast = multicast.parse(text, _MULTICAST_STYLE.get(parsed.vendor, "cisco"))
    parsed.ha_pair = hapair.parse(text)
    # 線が実際に上がっているか。show の表からだけ分かる
    linkstate.parse(text, parsed)
    return parsed


#: マルチキャストの書き方の系統。読み手はこの3つしか持たない
_MULTICAST_STYLE = {
    Vendor.JUNIPER: "juniper",
    Vendor.NEC_QX: "comware",
    Vendor.HUAWEI: "comware",
    Vendor.ARUBA_CX: "aruba",
}


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
    "FEATURE_LABELS",
    "NeighborProtocol",
    "ParsedBgp",
    "ParsedBgpNeighbor",
    "ParsedConfig",
    "ParsedFeature",
    "ParsedInterface",
    "ParsedNeighbor",
    "ParsedOspf",
    "ParsedOspfArea",
    "ParsedRedundancy",
    "ParsedRoute",
    "ParsedVrf",
    "ParsedWireless",
    "ParsedWirelessAp",
    "ParsedWlan",
    "VENDOR_LABELS",
    "VENDOR_NAMES",
    "Vendor",
    "detect_vendor",
    "expand",
    "normalize",
    "normalize_hostname",
    "parse",
]
