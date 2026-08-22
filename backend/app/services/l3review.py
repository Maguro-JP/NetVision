"""読み取った L3 を並べ、気になるところを指摘する。

経路の設定は、間違っていても半分は動く。だから間違いに気づくのが遅れる。
「行きは通るが戻れない」「切り替わらないまま片系で動いている」といった形で
残り、障害の当日に初めて分かる。

ここでやるのは、Config から読み取れる範囲で、その手の粗を先に挙げること。
挙げるときは必ず、なぜ気になるのかと、根拠にした行を添える。理由の無い
指摘は直しようが無いうえ、直す必要が無いものまで直させてしまう。

読み取れないものは指摘しない。「無い」のか「読めていない」のかを、
このモジュールでは区別できないため。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Device
from app.models.topology import Link
from app.services import capabilities, configparse
from app.services.addition import read_upstream
from app.services.configparse import ParsedConfig
from app.services.configparse.routing import to_prefix

#: 気になる度合い。台帳の重要度と同じ言葉にはしない（別の軸なので混ぜない）
HIGH = "high"
MEDIUM = "medium"
LOW = "low"


@dataclass
class Finding:
    """気になったこと1件。

    `why` は「なぜ気になるのか」、`evidence` は「どこを見てそう言ったか」。
    どちらも無いものは指摘として出さない。
    """

    level: str
    title: str
    why: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class L3Report:
    hostname: str
    has_config: bool = False
    routes: list[dict] = field(default_factory=list)
    ospf: dict | None = None
    bgp: dict | None = None
    redundancy: list[dict] = field(default_factory=list)
    #: その機器で使っている機能（ACL / NAT / DHCP など）
    features: list[dict] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


def review(db: Session, device: Device) -> L3Report:
    parsed, _raw = read_upstream(db, device)
    report = L3Report(hostname=device.hostname)
    if parsed is None:
        return report

    report.has_config = True
    report.routes = [_route_read(route) for route in parsed.routes]
    report.ospf = _ospf_read(parsed)
    report.bgp = _bgp_read(parsed)
    report.redundancy = [_redundancy_read(entry) for entry in parsed.redundancy]
    report.features = [
        {
            "category": feature.category,
            "category_label": capabilities.feature_label(feature.category),
            "name": feature.name,
            "detail": feature.detail,
            "count": feature.count,
            "evidence": feature.evidence,
        }
        for feature in parsed.features
    ]

    report.findings = _findings(db, device, parsed)
    return report


def _route_read(route) -> dict:  # noqa: ANN001
    return {
        "destination": route.destination,
        "next_hop": route.next_hop,
        "interface": route.interface,
        "distance": route.distance,
        "vrf": route.vrf,
        "is_default": route.is_default,
    }


def _ospf_read(parsed: ParsedConfig) -> dict | None:
    if parsed.ospf is None:
        return None
    return {
        "process_id": parsed.ospf.process_id,
        "router_id": parsed.ospf.router_id,
        "areas": [{"area": area.area, "networks": area.networks} for area in parsed.ospf.areas],
        "passive_default": parsed.ospf.passive_default,
        "passive_interfaces": parsed.ospf.passive_interfaces,
        "redistributes": parsed.ospf.redistributes,
    }


def _bgp_read(parsed: ParsedConfig) -> dict | None:
    if parsed.bgp is None:
        return None
    return {
        "local_as": parsed.bgp.local_as,
        "router_id": parsed.bgp.router_id,
        "networks": parsed.bgp.networks,
        "neighbors": [
            {"address": n.address, "remote_as": n.remote_as, "description": n.description}
            for n in parsed.bgp.neighbors
        ],
    }


def _redundancy_read(entry) -> dict:  # noqa: ANN001
    return {
        "interface": entry.interface,
        "group": entry.group,
        "virtual_ip": entry.virtual_ip,
        "priority": entry.priority,
        "protocol": entry.protocol,
    }


# ---------------------------------------------------------------------------
# 指摘
# ---------------------------------------------------------------------------


def _findings(db: Session, device: Device, parsed: ParsedConfig) -> list[Finding]:
    findings: list[Finding] = []
    local = _local_networks(parsed)

    findings.extend(_no_default_route(parsed))
    findings.extend(_unreachable_next_hops(parsed, local))
    findings.extend(_next_hop_not_in_ledger(db, parsed))
    findings.extend(_same_destination_twice(parsed))
    findings.extend(_segments_nobody_advertises(parsed, local))
    findings.extend(_redundancy_only_on_one_side(db, device, parsed))
    return findings


def _local_networks(parsed: ParsedConfig) -> list[ipaddress.IPv4Network]:
    """この機器が直接持っているセグメント。"""
    out = []
    for interface in parsed.interfaces:
        if not interface.ip_address:
            continue
        prefix = to_prefix(interface.ip_address)
        if prefix is None:
            continue
        try:
            network = ipaddress.ip_network(prefix, strict=False)
        except ValueError:
            continue
        if network.version == 4 and network.prefixlen < 32:
            out.append(network)
    return out


def _no_default_route(parsed: ParsedConfig) -> list[Finding]:
    if not parsed.routes:
        return []
    if any(route.is_default for route in parsed.routes):
        return []
    if parsed.ospf is not None or parsed.bgp is not None:
        return []  # 動的に受け取っている可能性があるので言わない
    return [
        Finding(
            level=MEDIUM,
            title="既定の経路が見当たりません",
            why="静的経路だけで組まれていて、書いてある宛先以外へは届きません。"
            "意図してそうしている（閉じた網）なら問題ありませんが、"
            "上位へ抜ける経路のつもりなら足りていません。",
            evidence=[f"読み取れた静的経路: {len(parsed.routes)} 本、うち既定の経路は 0 本"],
        )
    ]


def _unreachable_next_hops(
    parsed: ParsedConfig, local: list[ipaddress.IPv4Network]
) -> list[Finding]:
    """ネクストホップが、この機器のどのセグメントにも居ない経路。

    そのまま入れても経路として使われない（隣に居ない相手は指せない）。
    セグメントを付け替えたときの直し忘れで、いちばんよく残る。
    """
    if not local:
        return []
    out = []
    for route in parsed.routes:
        if not route.next_hop:
            continue
        try:
            address = ipaddress.ip_address(route.next_hop)
        except ValueError:
            continue
        if any(address in network for network in local):
            continue
        out.append(
            Finding(
                level=HIGH,
                title=f"{route.destination} のネクストホップに届きません",
                why=f"{route.next_hop} は、この機器が持っているどのセグメントにも居ません。"
                "隣に居ない相手はネクストホップに指せないので、この経路は使われません。"
                "セグメントを付け替えたときの直し忘れでよく残ります。",
                evidence=[
                    f"経路: {route.destination} → {route.next_hop}",
                    "この機器のセグメント: " + ", ".join(str(n) for n in local[:4]),
                ],
            )
        )
    return out


def _next_hop_not_in_ledger(db: Session, parsed: ParsedConfig) -> list[Finding]:
    """ネクストホップの機器が台帳に見当たらない。"""
    hops = {route.next_hop for route in parsed.routes if route.next_hop}
    if not hops:
        return []
    known = {
        ip for (ip,) in db.execute(select(Device.mgmt_ip).where(Device.mgmt_ip.isnot(None))).all()
    }
    if not known:
        return []
    missing = sorted(hop for hop in hops if hop not in known)
    if not missing:
        return []
    return [
        Finding(
            level=LOW,
            title="経路の向き先が台帳にありません",
            why="ネクストホップに指している相手が、台帳の管理IPと一致しません。"
            "台帳に載っていない機器が経路上に居るか、管理IPが違うかのどちらかです。"
            "更改のときに影響範囲から漏れるので、どちらなのか確かめてください。",
            evidence=[f"台帳に見当たらない向き先: {', '.join(missing[:5])}"],
        )
    ]


def _same_destination_twice(parsed: ParsedConfig) -> list[Finding]:
    seen: dict[tuple[str, str | None], list] = {}
    for route in parsed.routes:
        seen.setdefault((route.destination, route.vrf), []).append(route)

    out = []
    for (destination, _vrf), routes in seen.items():
        if len(routes) < 2:
            continue
        distances = {route.distance or 1 for route in routes}
        if len(distances) > 1:
            continue  # 距離が違うなら、待機側を用意した形。意図が読める
        out.append(
            Finding(
                level=MEDIUM,
                title=f"{destination} への経路が同じ重みで複数あります",
                why="重みが同じ経路が並ぶと、通信が両方に分かれます。"
                "帯域が違う回線だと、遅いほうにも半分流れます。"
                "待機の回線のつもりなら、片方の distance を上げてください。",
                evidence=[
                    f"{destination} → {route.next_hop or route.interface}"
                    f"（distance {route.distance or '既定'}）"
                    for route in routes
                ],
            )
        )
    return out


def _segments_nobody_advertises(
    parsed: ParsedConfig, local: list[ipaddress.IPv4Network]
) -> list[Finding]:
    """IP は振ってあるのに、OSPF にも BGP にも載っていないセグメント。

    その機器の中では通じるが、外からは届かない。増やしたときの入れ忘れ。
    """
    if parsed.ospf is None and parsed.bgp is None:
        return []  # 静的で回している構成では、載っていないのが普通

    advertised: set[str] = set()
    if parsed.ospf is not None:
        for area in parsed.ospf.areas:
            advertised |= {n for n in area.networks if not n.startswith("interface ")}
        interfaces = {
            n.removeprefix("interface ")
            for area in parsed.ospf.areas
            for n in area.networks
            if n.startswith("interface ")
        }
    else:
        interfaces = set()
    if parsed.bgp is not None:
        advertised |= set(parsed.bgp.networks)

    missing = []
    for interface in parsed.interfaces:
        if not interface.ip_address:
            continue
        prefix = to_prefix(interface.ip_address)
        if prefix is None or prefix in advertised:
            continue
        if interface.name in interfaces:
            continue
        if any(
            ipaddress.ip_network(prefix, strict=False).subnet_of(
                ipaddress.ip_network(text, strict=False)
            )
            for text in advertised
            if _same_family(prefix, text)
        ):
            continue
        missing.append((interface.name, prefix))

    if not missing:
        return []
    return [
        Finding(
            level=MEDIUM,
            title="外へ広告されていないセグメントがあります",
            why="IP は振ってあるのに、OSPF にも BGP にも載っていません。"
            "この機器の中では通じますが、外からは届きません。"
            "セグメントを増やしたときの入れ忘れでよく起きます。"
            "管理用など、わざと広告していないなら問題ありません。",
            evidence=[f"{name}: {prefix}" for name, prefix in missing[:5]],
        )
    ]


def _same_family(left: str, right: str) -> bool:
    try:
        return (
            ipaddress.ip_network(left, strict=False).version
            == ipaddress.ip_network(right, strict=False).version
        )
    except ValueError:
        return False


def _redundancy_only_on_one_side(
    db: Session, device: Device, parsed: ParsedConfig
) -> list[Finding]:
    """冗長の設定が、対向の機器に入っていない。

    片側だけだと、その機器が落ちたときに切り替わらない。動いているうちは
    気づかず、落ちた当日に初めて分かる。
    """
    if not parsed.redundancy:
        return []

    peers = _peer_devices(db, device)
    if not peers:
        return []

    out = []
    for entry in parsed.redundancy:
        partner = None
        for peer in peers:
            peer_parsed, _ = read_upstream(db, peer)
            if peer_parsed is None:
                continue
            for other in peer_parsed.redundancy:
                if other.group == entry.group and other.protocol == entry.protocol:
                    partner = (peer, other)
                    break
            if partner:
                break
        if partner is not None:
            peer, other = partner
            if other.priority == entry.priority:
                out.append(
                    Finding(
                        level=MEDIUM,
                        title=f"{entry.protocol.upper()} グループ {entry.group} の優先度が同じです",
                        why="両方が同じ優先度だと、どちらが主になるかが起動の順番で決まります。"
                        "入れ替えのたびに主が変わり、通信の経路が変わります。"
                        "主にしたい側の優先度を上げてください。",
                        evidence=[
                            f"{device.hostname} {entry.interface}: 優先度 {entry.priority}",
                            f"{peer.hostname} {other.interface}: 優先度 {other.priority}",
                        ],
                    )
                )
            continue

        out.append(
            Finding(
                level=HIGH,
                title=f"{entry.protocol.upper()} グループ {entry.group} が片側だけです",
                why="この機器にだけ設定が入っていて、つながっている機器の Config には"
                "同じグループが見当たりません。片側だけだと、この機器が落ちたときに"
                "切り替わりません。動いているうちは気づかず、落ちた当日に分かります。"
                "対向の Config がまだ取り込まれていないだけの場合もあります。",
                evidence=[
                    f"{device.hostname} {entry.interface}: 仮想IP {entry.virtual_ip}",
                    "つながっている機器: " + ", ".join(p.hostname for p in peers[:4]),
                ],
            )
        )
    return out


def _peer_devices(db: Session, device: Device) -> list[Device]:
    links = db.scalars(
        select(Link).where(or_(Link.a_device_id == device.id, Link.b_device_id == device.id))
    ).all()
    ids = {
        (link.b_device_id if link.a_device_id == device.id else link.a_device_id) for link in links
    }
    if not ids:
        return []
    return list(db.scalars(select(Device).where(Device.id.in_(ids)).order_by(Device.hostname)))


def vendor_label(parsed: ParsedConfig) -> str:
    return configparse.VENDOR_LABELS.get(parsed.vendor, "判別できません")
