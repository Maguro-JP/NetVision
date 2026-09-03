"""マルチキャストの配り方を見て、配信が始まらない・止まる形を指摘する。

映像や放送が「映らない」「しばらくすると止まる」の原因は、たいてい
次のどれかで、どれも実機を触らずに Config から言える。

判断の材料は1台分の Config だけ。設計そのもの（どこに RP を置くべきか）は
要件が要るので言わない。
"""

from __future__ import annotations

import ipaddress

from app.services.configparse import ParsedConfig
from app.services.configparse.multicast import ParsedMulticast
from app.services.reviewnote import HIGH, LOW, MEDIUM, Finding


def _note(level: str, title: str, why: str, evidence: list[str]) -> Finding:
    return Finding(level=level, title=title, why=why, evidence=evidence)


def _reachable(parsed: ParsedConfig, address: str) -> bool:
    """その相手が、この機器の口か経路表から届くと言えるか。"""
    try:
        target = ipaddress.ip_address(address)
    except ValueError:
        return True  # 名前で書かれている。ここでは判断しない
    for interface in parsed.interfaces:
        text = interface.ip_address or ""
        try:
            if target in ipaddress.ip_network(text.replace(" ", "/"), strict=False):
                return True
        except ValueError:
            continue
    for route in parsed.routes:
        try:
            if target in ipaddress.ip_network(route.destination, strict=False):
                return True
        except ValueError:
            continue
    return False


def _has_l3(parsed: ParsedConfig, multicast: ParsedMulticast) -> bool:
    """この機器が経路も見ているか。L2 だけの機器かを分けるために使う。"""
    return bool(multicast.pim_interfaces or parsed.routes or parsed.ospf or parsed.bgp)


def review(parsed: ParsedConfig) -> list[Finding]:
    multicast = getattr(parsed, "multicast", None)
    if multicast is None:
        return []

    findings: list[Finding] = []
    names = [item.name for item in multicast.pim_interfaces]

    # 1. 集合場所が決まっていない。sparse-mode はここを通らないと配信が始まらない
    if multicast.has_sparse and not multicast.rps and not multicast.dynamic_rp:
        findings.append(
            _note(
                HIGH,
                "sparse-mode で配る設定ですが、集合場所（RP）が決まっていません",
                "sparse-mode は、配る側と受ける側が RP を経由してはじめて結び付きます。"
                "RP を静的に書くか、候補方式（BSR / Auto-RP）で決める必要があります。"
                "このままでは、受け手が要求しても映像は流れてきません。",
                [f"PIM を入れている口: {', '.join(names[:8])}"],
            )
        )

    # 2. RP のアドレスへ、この機器から行けない
    for rp in multicast.rps:
        if _reachable(parsed, rp.address):
            continue
        findings.append(
            _note(
                MEDIUM,
                f"集合場所（RP）{rp.address} への行き方がこの Config にありません",
                "RP のアドレスが、この機器のどの口の帯にも、経路表にも見当たりません。"
                "RP を移したときの書き換え漏れか、経路の設定漏れが疑われます。"
                "RP へ行けないと、その範囲のマルチキャストは配信されません。",
                [f"ip pim rp-address {rp.address}" + (f" {rp.scope}" if rp.scope else "")],
            )
        )

    # 3. スヌーピングだけ入れて、定期的に聞いて回る役が居ない
    if not _has_l3(parsed, multicast):
        silent = [item for item in multicast.vlans if item.enabled and not item.querier]
        if silent and multicast.snooping is not False:
            findings.append(
                _note(
                    MEDIUM,
                    "IGMP スヌーピングを入れた帯に、定期的に聞いて回る役（querier）が居ません",
                    "スヌーピングは「受け取りたい」という申告を聞いて、要る口にだけ流す"
                    "仕組みです。申告は定期的な問い合わせに答える形で更新されるため、"
                    "問い合わせる役が居ないと、一定時間で申告が消えて配信が止まります。"
                    "この機器は経路を見ていないので、上位が問い合わせているか、"
                    "この機器で querier を立てるかを確かめてください。",
                    [f"VLAN {item.vlan}" for item in silent[:8]],
                )
            )

    # 4. PIM を入れた口が1つだけ。上流か下流のどちらかが抜けている
    if len(multicast.pim_interfaces) == 1 and multicast.routing:
        findings.append(
            _note(
                LOW,
                f"PIM を入れている口が {names[0]} の1つだけです",
                "マルチキャストは、配る側から受ける側まで、通る口すべてに入れて"
                "はじめて届きます。上流か下流のどちらかで入れ忘れていないか"
                "確かめてください。",
                [f"PIM: {names[0]}"],
            )
        )

    # 5. 転送は有効なのに、どの口にも入れていない
    if multicast.routing and not multicast.pim_interfaces:
        findings.append(
            _note(
                LOW,
                "マルチキャストの転送は有効ですが、どの口にも入れていません",
                "全体の設定だけ入っていて、口に当てていません。書きかけか、"
                "消し忘れのどちらかです。",
                ["multicast-routing"],
            )
        )

    return findings
