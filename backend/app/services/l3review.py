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
from app.services import aclcheck, capabilities, configparse, policyreview
from app.services.addition import read_upstream
from app.services.configparse import ParsedConfig
from app.services.configparse.routing import to_prefix
from app.services.reviewnote import HIGH, LOW, MEDIUM, Finding  # noqa: F401  再輸出


@dataclass
class L3Report:
    hostname: str
    has_config: bool = False
    routes: list[dict] = field(default_factory=list)
    ospf: dict | None = None
    bgp: dict | None = None
    #: OSPF / BGP 以外の経路プロトコル（EIGRP / RIP / IS-IS）
    protocols: list[dict] = field(default_factory=list)
    #: 経路の選び方・配り方を決める道具（route-map / prefix-list）
    policies: list[dict] = field(default_factory=list)
    #: ACL / NAT / DHCP / QoS / IPsec を、当てている場所まで並べたもの
    controls: list[dict] = field(default_factory=list)
    redundancy: list[dict] = field(default_factory=list)
    #: その機器で使っている機能（ACL / NAT / DHCP など）
    features: list[dict] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


def review(db: Session, device: Device) -> L3Report:
    parsed, raw = read_upstream(db, device)
    report = L3Report(hostname=device.hostname)
    if parsed is None:
        return report

    report.has_config = True
    report.routes = [_route_read(route) for route in parsed.routes]
    report.ospf = _ospf_read(parsed)
    report.bgp = _bgp_read(parsed)
    report.protocols = [
        {
            "name": protocol.name,
            "identifier": protocol.identifier,
            "router_id": protocol.router_id,
            "networks": protocol.networks,
            "passive_interfaces": protocol.passive_interfaces,
            "redistributes": protocol.redistributes,
        }
        for protocol in parsed.protocols
    ]
    report.policies = [
        {
            "kind": policy.kind,
            "name": policy.name,
            "detail": policy.detail,
            "used_by": policy.used_by,
            "entries": [
                {
                    "seq": entry.seq,
                    "action": entry.action,
                    "matches": entry.matches,
                    "sets": entry.sets,
                }
                for entry in policy.entries
            ],
        }
        for policy in parsed.policies
    ]
    report.controls = _controls(parsed)
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

    report.findings = _findings(db, device, parsed, raw)
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
        "redistributes": parsed.bgp.redistributes,
        "aggregates": parsed.bgp.aggregates,
        "neighbors": [
            {
                "address": n.address,
                "remote_as": n.remote_as,
                "description": n.description,
                "external": n.is_ebgp(parsed.bgp.local_as),
                # 出入りに掛けている道具。in と out は別物なので分けて出す
                "route_map_in": n.route_map_in,
                "route_map_out": n.route_map_out,
                "prefix_list_in": n.prefix_list_in,
                "prefix_list_out": n.prefix_list_out,
                "update_source": n.update_source,
                "next_hop_self": n.next_hop_self,
                "maximum_prefix": n.maximum_prefix,
                "password": n.password,
                "shutdown": n.shutdown,
            }
            for n in parsed.bgp.neighbors
            if not n.is_peer_group
        ],
    }


def _controls(parsed: ParsedConfig) -> list[dict]:
    """通信の制限・変換・配布・優先制御・暗号化・電波を、同じ形で並べる。

    見る側が知りたいのはどれも同じ3つ、「何が」「どういう中身で」「どこに
    当たっているか」である。種類ごとに違う表を出すより、1つに揃えたほうが
    抜けに気づきやすい。当て先が空の行が、そのまま「効いていないもの」になる。
    """
    out: list[dict] = []

    def add(
        kind: str, name: str, detail: str, applied: list[str], rows: list[dict] | None = None
    ) -> None:
        out.append(
            {
                "kind": kind,
                "name": name,
                "detail": detail,
                "applied_to": applied,
                "rows": rows or [],
            }
        )

    for acl in parsed.acls:
        rules = [entry for entry in acl.entries if entry.action in {"permit", "deny"}]
        permits = sum(1 for entry in rules if entry.action == "permit")
        add(
            "通信の制限",
            acl.name,
            f"{len(rules)} 行（通す {permits} / 落とす {len(rules) - permits}）",
            [*acl.applied_to, *acl.referenced_by],
            _acl_rows(rules),
        )

    if parsed.nat is not None:
        for rule in parsed.nat.rules:
            kind = {"dynamic": "動的", "static": "固定", "static-port": "固定（ポート指定）"}
            detail = (
                f"{kind.get(rule.kind, rule.kind)} {rule.matched or ''} → {rule.translated or ''}"
            )
            if rule.overload:
                detail += "（ポートで束ねる）"
            where = parsed.nat.inside_interfaces + parsed.nat.outside_interfaces
            add("アドレス変換", rule.matched or rule.kind, detail.strip(), where)

    if parsed.dhcp is not None:
        for pool in parsed.dhcp.pools:
            gateway = ", ".join(pool.default_routers) or "既定ゲートウェイなし"
            add("アドレス配布", pool.name, f"{pool.network or '範囲不明'} / {gateway}", [])
        for interface, server in parsed.dhcp.relays:
            add(
                "アドレス配布",
                f"リレー {server}",
                "他所のサーバーへ回す",
                [f"interface {interface}"],
            )

    if parsed.qos is not None:
        for policy in parsed.qos.policies:
            where = [
                f"interface {interface} {'出' if direction == 'output' else '入り'}"
                for interface, direction, name in parsed.qos.applied
                if name == policy.name
            ]
            classes = ", ".join(entry.class_name for entry in policy.classes)
            add("優先制御", policy.name, classes or "区分なし", where)

    if parsed.crypto is not None:
        for entry in parsed.crypto.maps:
            where = [
                f"interface {interface}"
                for interface, name in parsed.crypto.applied
                if name == entry.name
            ]
            peers = ", ".join(entry.peers) or "相手なし"
            method = ", ".join(entry.transform_sets) or entry.profile or "方式なし"
            add("拠点間の暗号化", f"{entry.name} {entry.seq}", f"{peers} / {method}", where)

    if parsed.wireless is not None:
        for wlan in parsed.wireless.wlans:
            add(
                "電波",
                wlan.ssid or wlan.name,
                _wlan_detail(wlan),
                [f"VLAN {wlan.vlan}"] if wlan.vlan is not None else [],
            )
        # コントローラの一覧にある AP。台帳に載せる材料が1つでもあるものだけ
        for ap in parsed.wireless.aps:
            if not (ap.model or ap.ip_address or ap.serial or ap.mac):
                continue
            words = [w for w in (ap.model, ap.ip_address, ap.location, ap.serial) if w]
            if ap.insecure_access:
                words.append("入口: " + " / ".join(ap.insecure_access))
            add("AP", ap.name, " / ".join(words), [ap.profile] if ap.profile else [])

    multicast = getattr(parsed, "multicast", None)
    if multicast is not None:
        for rp in multicast.rps:
            add(
                "マルチキャスト",
                f"集合場所（RP）{rp.address}",
                rp.scope or "範囲の指定なし（全部）",
                [item.name for item in multicast.pim_interfaces],
            )
        if multicast.dynamic_rp and not multicast.rps:
            add("マルチキャスト", "集合場所（RP）", "候補方式（BSR / Auto-RP）で決めています", [])
        for item in multicast.pim_interfaces:
            add(
                "マルチキャスト", f"PIM {item.mode}", "この口で配ります", [f"interface {item.name}"]
            )
        for vlan in multicast.vlans:
            if not vlan.enabled:
                continue
            detail = "聞いて回る役あり" if vlan.querier else "聞いて回る役なし"
            add("マルチキャスト", "IGMP スヌーピング", detail, [f"VLAN {vlan.vlan}"])

    balance = getattr(parsed, "load_balance", None)
    if balance is not None:
        for virtual in balance.virtual_servers:
            where = virtual.address or "宛先の指定なし"
            if virtual.port:
                where += f":{virtual.port}"
            pool = balance.pool(virtual.pool)
            count = f"{len(pool.alive)} / {len(pool.members)} 台" if pool else "振り分け先なし"
            add(
                "負荷分散",
                virtual.name,
                f"{where} → {virtual.pool or '（未指定）'}（生きている先 {count}）",
                [f"pool {virtual.pool}"] if virtual.pool else [],
            )
        for pool in balance.pools:
            monitors = ", ".join(pool.monitors) or "健全性監視なし"
            members = [f"{m.label}:{m.port}" if m.port else m.label for m in pool.members]
            add(
                "負荷分散",
                f"pool {pool.name}",
                f"{pool.method or '振り分け方の指定なし'} / {monitors}",
                members,
            )

    return out


def _wlan_detail(wlan) -> str:  # noqa: ANN001
    """SSID 1つの中身を、1行で読める形にする。

    見る側が最初に知りたいのは「どう守っているか」なので、そこを先に出す。
    止めてある SSID は、その旨を頭に付ける（あとの語が同じでも意味が違う）。
    """
    guard = "/".join(wlan.security) or "守り無し"
    if wlan.akm:
        guard += f"（{'/'.join(wlan.akm)}）"
    if wlan.ciphers:
        guard += f" {'/'.join(wlan.ciphers)}"
    parts = [guard]
    if not wlan.broadcast:
        parts.append("名前を伏せる")
    if wlan.client_isolation:
        parts.append("端末どうしを通さない")
    detail = " / ".join(parts)
    return detail if wlan.enabled else f"止めてある: {detail}"


def _acl_rows(rules: list) -> list[dict]:  # noqa: ANN001
    """ACL の中身を、書いてある順に並べる。

    読まれない行には印を付ける。ACL は上から順に見て最初に当たった行で
    決まるので、順番のまま見えないと、なぜ通らないのかを追えない。
    """
    rows = []
    for index, entry in enumerate(rules):
        upper = next((item for item in rules[:index] if aclcheck.covers(item, entry)), None)
        rows.append(
            {
                "text": entry.raw or f"{entry.action} {entry.source or ''}".strip(),
                "action": entry.action,
                "reads": upper is None,
                "hidden_by": upper.raw if upper is not None else None,
            }
        )
    return rows


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


def _findings(
    db: Session, device: Device, parsed: ParsedConfig, text: str | None = None
) -> list[Finding]:
    findings: list[Finding] = []
    local = _local_networks(parsed)

    findings.extend(_no_default_route(parsed))
    findings.extend(_next_hop_not_in_ledger(db, parsed))
    findings.extend(_same_destination_twice(parsed))
    findings.extend(_segments_nobody_advertises(parsed, local))
    findings.extend(_redundancy_only_on_one_side(db, device, parsed))
    findings.extend(_unused_policies(parsed))
    findings.extend(policyreview.review(parsed, text))
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
    """IP は振ってあるのに、どの経路プロトコルにも載っていないセグメント。

    その機器の中では通じるが、外からは届かない。増やしたときの入れ忘れ。
    OSPF・BGP のほか、EIGRP・IS-IS・RIP（protocols）に載っていれば広告されている。
    """
    others = [p for p in parsed.protocols if p.name in {"eigrp", "isis", "rip", "ospfv3"}]
    if parsed.ospf is None and parsed.bgp is None and not others:
        return []  # 静的で回している構成では、載っていないのが普通

    advertised: set[str] = set()
    interfaces: set[str] = set()
    listed: list[str] = []
    if parsed.ospf is not None:
        listed += [n for area in parsed.ospf.areas for n in area.networks]
    for protocol in others:
        listed += protocol.networks
    for entry in listed:
        if entry.startswith("interface "):
            interfaces.add(entry.removeprefix("interface "))
        else:
            advertised.add(entry)
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
            why="IP は振ってあるのに、OSPF にも BGP にも（EIGRP / IS-IS にも）載っていません。"
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


def _unused_policies(parsed: ParsedConfig) -> list[Finding]:
    """定義してあるのに、どこからも使われていない道具。

    消し忘れなら消せばよいだけだが、「使っているつもりで当て先を書き忘れて
    いる」場合は、絞れていない・広告できていない状態がそのまま残る。
    見分けるには人が要るので、挙げるだけにする。
    """
    # 当てる先（口・BGP・再配布）が見えていないなら、使われていないとは
    # 言えない。route-map だけを貼った抜粋で言うと、当ててあるのに出る
    if not parsed.interfaces and parsed.bgp is None and not parsed.protocols:
        return []
    unused = [p for p in parsed.policies if not p.used_by]
    if not unused:
        return []
    return [
        Finding(
            level=LOW,
            title="どこからも使われていない route-map / prefix-list があります",
            why="定義はあるのに、当てている場所が見当たりません。"
            "消し忘れなら害はありませんが、使っているつもりで当て先を書き忘れて"
            "いる場合は、絞れていない・広告できていない状態がそのまま残ります。",
            evidence=[f"{p.kind} {p.name}（{p.detail}）" for p in unused[:5]],
        )
    ]


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
