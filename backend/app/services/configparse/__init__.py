"""Config / show 出力のパース。

ベンダーを自動判別して、該当のパーサに渡す。判別できない場合も
「読めた分だけ」返す方針で、取り込みが丸ごと失敗しないようにしている。
"""

from __future__ import annotations

from app.services.configparse import (
    aireos,
    alaxala,
    allied,
    arista,
    aruba,
    arubaos,
    asa,
    banners,
    cisco,
    ciscosb,
    dellos10,
    exos,
    fastiron,
    features,
    fortinet,
    h3c,
    hapair,
    huawei,
    inputtext,
    iosxr,
    juniper,
    linkstate,
    lldptable,
    loadbalance,
    multicast,
    nec,
    panos,
    routeros,
    vyos,
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
    # AireOS の点線区切りは他に出てこない。IOS より先に見ないと、
    # 混ざっている show 出力のほうを拾ってしまう
    aireos.detect,
    fortinet.detect,
    # EXOS は configure で始まる1行ずつの書き方。他とぶつからない
    exos.detect,
    # RouterOS は / で始まる区画。他とぶつからない
    routeros.detect,
    # Vyatta 系も set で書く。Junos と紛れるので、口の名前で先に振り分ける
    vyos.detect,
    juniper.detect,
    yamaha.detect,
    panos.detect,
    asa.detect,
    # IOS-XR は IOS と書き方が違うところがある。IOS より先に見ないと、
    # 読めた形で通って住所も経路も抜ける
    iosxr.detect,
    arista.detect,
    # Cisco Business（SG / CBS）は `config-file-header` と `vlan database` を書く。
    # アライドテレシスより先に見ないと、`vlan database` で取り違える
    ciscosb.detect,
    # アライドテレシスの目印（port1.0.1 / vlan database）は Aruba CX より強い。
    # vrrp の書き方が似ているので、先に見ないと取り違える
    allied.detect,
    # アラクサラは名乗りか型番が出ているときだけ言う。IOS 風なので、
    # 目印の無い config を横取りしない
    alaxala.detect,
    # FastIron は `by port` / `port-name` が目印。IOS より先に見る
    fastiron.detect,
    # OS10 は IOS 風だが口の書き方が違う。FastIron と同じ書き出しの行が
    # あるので、そちらを見たあとに見る
    dellos10.detect,
    # AOS-S は VLAN の下に口を並べる。AOS-CX とは別物なので、先に見分ける
    arubaos.detect,
    aruba.detect,
    # ファーウェイ VRP は NEC QX と書き方が重なる箇所がある。名乗りが出ているうちに
    # 拾わないと NEC QX として読んでしまう
    huawei.detect,
    # H3C も書き方が重なる。製品の印が出ているうちに拾わないと NEC として読んでしまう
    h3c.detect,
    nec.detect,
    cisco.detect,
)

VENDOR_LABELS: dict[Vendor, str] = {
    Vendor.CISCO_IOS: "Cisco IOS / IOS-XE",
    Vendor.CISCO_NXOS: "Cisco NX-OS",
    Vendor.CISCO_ASA: "Cisco ASA / FTD",
    Vendor.CISCO_XR: "Cisco IOS-XR",
    Vendor.CISCO_SB: "Cisco Business（SG / SF / CBS）",
    Vendor.JUNIPER: "Juniper Junos",
    Vendor.FORTINET: "Fortinet FortiOS",
    Vendor.NEC_IX: "NEC IX シリーズ",
    Vendor.NEC_QX: "NEC QX シリーズ",
    Vendor.YAMAHA: "ヤマハ RTX / NVR",
    Vendor.PALO_ALTO: "Palo Alto PAN-OS",
    Vendor.ARISTA: "Arista EOS",
    Vendor.ARUBA_CX: "Aruba CX（AOS-CX）",
    Vendor.ARUBA_OS: "Aruba / HPE（AOS-S / ProCurve）",
    Vendor.ALLIED: "アライドテレシス AlliedWare Plus",
    Vendor.ALAXALA: "アラクサラ AX シリーズ",
    Vendor.HUAWEI: "ファーウェイ VRP",
    Vendor.H3C: "H3C（Comware）",
    Vendor.CISCO_WLC: "Cisco AireOS 無線コントローラ",
    Vendor.FASTIRON: "Ruckus / Brocade FastIron（ICX）",
    Vendor.EXOS: "Extreme EXOS",
    Vendor.DELL_OS10: "Dell OS10",
    Vendor.VYOS: "VyOS / EdgeOS",
    Vendor.ROUTEROS: "MikroTik RouterOS",
    Vendor.UNKNOWN: "判別できません",
}

VENDOR_NAMES: dict[Vendor, str] = {
    Vendor.CISCO_IOS: "Cisco",
    Vendor.CISCO_NXOS: "Cisco",
    Vendor.CISCO_ASA: "Cisco",
    Vendor.CISCO_XR: "Cisco",
    Vendor.CISCO_SB: "Cisco",
    Vendor.JUNIPER: "Juniper",
    Vendor.FORTINET: "Fortinet",
    Vendor.NEC_IX: "NEC",
    Vendor.NEC_QX: "NEC",
    Vendor.YAMAHA: "ヤマハ",
    Vendor.PALO_ALTO: "Palo Alto",
    Vendor.ARISTA: "Arista",
    Vendor.ARUBA_CX: "Aruba",
    Vendor.ARUBA_OS: "Aruba",
    Vendor.ALLIED: "アライドテレシス",
    Vendor.ALAXALA: "アラクサラ",
    Vendor.HUAWEI: "ファーウェイ",
    Vendor.H3C: "H3C",
    Vendor.CISCO_WLC: "Cisco",
    Vendor.FASTIRON: "Ruckus",
    Vendor.EXOS: "Extreme Networks",
    Vendor.DELL_OS10: "Dell",
    Vendor.VYOS: "VyOS",
    Vendor.ROUTEROS: "MikroTik",
}


def detect_vendor(text: str) -> Vendor:
    for detector in _DETECTORS:
        vendor = detector(text)
        if vendor is not None:
            return vendor
    return Vendor.UNKNOWN


def parse(text: str, vendor: Vendor | None = None) -> ParsedConfig:
    # 行末・字下げ・先頭の見えない印は、意味を持たない差。ここで揃える。
    # 揃えないと、中身は同じなのに「ホスト名が読めません」と出る
    text = inputtext.clean(text)
    # ログイン時に見せる文言の中は、機器にとってはただの文字列。設定として
    # 読むと、無い口や無い合言葉を有ると言うことになる
    text = banners.strip(text)
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
    elif vendor is Vendor.CISCO_XR:
        parsed = iosxr.parse(text)
    elif vendor is Vendor.CISCO_SB:
        parsed = ciscosb.parse(text)
    elif vendor is Vendor.PALO_ALTO:
        parsed = panos.parse(text)
    elif vendor is Vendor.ARISTA:
        parsed = arista.parse(text)
    elif vendor is Vendor.ARUBA_CX:
        parsed = aruba.parse(text)
    elif vendor is Vendor.ARUBA_OS:
        parsed = arubaos.parse(text)
    elif vendor is Vendor.ALLIED:
        parsed = allied.parse(text)
    elif vendor is Vendor.ALAXALA:
        parsed = alaxala.parse(text)
    elif vendor is Vendor.HUAWEI:
        parsed = huawei.parse(text)
    elif vendor is Vendor.H3C:
        parsed = h3c.parse(text)
    elif vendor is Vendor.CISCO_WLC:
        parsed = aireos.parse(text)
    elif vendor is Vendor.FASTIRON:
        parsed = fastiron.parse(text)
    elif vendor is Vendor.EXOS:
        parsed = exos.parse(text)
    elif vendor is Vendor.DELL_OS10:
        parsed = dellos10.parse(text)
    elif vendor is Vendor.VYOS:
        parsed = vyos.parse(text)
    elif vendor is Vendor.ROUTEROS:
        parsed = routeros.parse(text)
    else:
        # 判別できなくても、Cisco 形式の show 出力が混ざっていることが多いので試す
        parsed = cisco.parse(text, Vendor.UNKNOWN)
        parsed.vendor = Vendor.UNKNOWN
        parsed.os_name = None

    _collect_referenced_vrfs(parsed)
    # 機能の拾い出しはベンダーに依らない。どの経路で来ても最後に通す
    parsed.features = features.parse(_FEATURE_TEXT.get(parsed.vendor, lambda body: body)(text))
    # 無線は書き方がベンダーごとに離れているので、判別とは別に最後へ通す。
    # 無線機かどうかは機種名では決まらない（コントローラ内蔵の機種がある）
    # 読み手が先に無線を読んでいたら、そちらを残す（AireOS は表からしか読めない）
    if parsed.wireless is None or not (parsed.wireless.wlans or parsed.wireless.aps):
        parsed.wireless = wireless.parse(text)
    # マルチキャストも書き方がベンダーごとに離れている。判別の結果だけ渡して最後へ
    parsed.load_balance = loadbalance.parse(text)
    parsed.multicast = multicast.parse(text, _MULTICAST_STYLE.get(parsed.vendor, "cisco"))
    parsed.ha_pair = hapair.parse(text)
    # どのベンダーの読み手も隣を見つけられなかったときだけ、見出しから列を
    # 決める読み手を通す。書式の違いで線が引けないより、拾えるものは拾う。
    # 見出しが無ければ何も足さないので、無い線を作ることにはならない
    if not parsed.neighbors:
        lldptable.parse(text, parsed)

    # 線が実際に上がっているか。show の表からだけ分かる
    linkstate.parse(text, parsed)
    return parsed


#: 機能の拾い出しの前に、囲みの中でしか意味が決まらない行をほどく機種。
#: 行だけを見ると別の設定と見分けが付かない書き方があるもの
_FEATURE_TEXT = {
    Vendor.FASTIRON: fastiron.feature_text,
    Vendor.FORTINET: fortinet.feature_text,
}

#: マルチキャストの書き方の系統。読み手はこの3つしか持たない。
#: undoform は、否定を `undo` で書き `#` で区切る書式（NEC QX / VRP / H3C）
_MULTICAST_STYLE = {
    Vendor.JUNIPER: "juniper",
    Vendor.NEC_QX: "undoform",
    Vendor.HUAWEI: "undoform",
    Vendor.H3C: "undoform",
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
