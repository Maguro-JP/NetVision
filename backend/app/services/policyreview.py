"""読み取った ACL / NAT / DHCP / QoS / IPsec / BGP の中身を見て、粗を挙げる。

Config を書く道具である以上、書いたものが効いているかどうかまで言えないと
使えない。ここで見るのは、動かしてみなくても言えることに限る。

- 書いてあるのに、どこにも当てていない（ACL / policy-map / crypto map）
- 当ててはいるが、その中身では意図した通信が通らない（permit が無い、影の行）
- 掛けるべき安全策が抜けている（eBGP の上限・出す側のフィルタ、NAPT の overload）
- 古い方式のまま残っている（DES / MD5 / DH group 1）
- 参照している名前が、その Config の中に無い

読み取れないものは指摘しない。「無い」のか「読めていない」のかを、この層では
区別できないため。指摘には必ず、なぜ気になるのかと、根拠にした行を添える。
"""

from __future__ import annotations

import ipaddress

from app.services.configparse import ParsedConfig
from app.services.configparse.base import ParsedAcl, ParsedBgp, ParsedBgpNeighbor
from app.services.reviewnote import HIGH, LOW, MEDIUM, Finding

#: 今の目で見て、そのまま残しておけない方式。数字は鍵の短さ・衝突の作りやすさ
_BROKEN_CRYPTO = {
    "des": "DES は鍵が短く、いまの計算機なら現実的な時間で解けます",
    "md5": "MD5 は同じ値になる別のデータを作れます",
    "esp-des": "DES は鍵が短く、いまの計算機なら現実的な時間で解けます",
    "esp-md5-hmac": "MD5 は同じ値になる別のデータを作れます",
    "ah-md5-hmac": "MD5 は同じ値になる別のデータを作れます",
}
_WEAK_CRYPTO = {
    "3des": "3DES は現役ではありますが、速度も強さも AES に劣ります",
    "esp-3des": "3DES は現役ではありますが、速度も強さも AES に劣ります",
    "sha": "SHA-1 は衝突が見つかっています。SHA-256 以上にしてください",
    "esp-sha-hmac": "SHA-1 は衝突が見つかっています。SHA-256 以上にしてください",
}
#: Diffie-Hellman の群。1・2・5 は鍵の元が短く、いま作るものには使わない
_WEAK_GROUPS = {"1", "2", "5"}


def review(parsed: ParsedConfig) -> list[Finding]:
    """1台分の Config を見て、気になったところを並べる。"""
    findings: list[Finding] = []
    findings.extend(_bgp(parsed))
    findings.extend(_route_maps(parsed))
    findings.extend(_acls(parsed))
    findings.extend(_nat(parsed))
    findings.extend(_dhcp(parsed))
    findings.extend(_qos(parsed))
    findings.extend(_crypto(parsed))
    return findings


def _note(level: str, title: str, why: str, evidence: list[str]) -> Finding:
    return Finding(level=level, title=title, why=why, evidence=[e for e in evidence if e][:5])


def _names(parsed: ParsedConfig, kind: str) -> set[str]:
    return {policy.name for policy in parsed.policies if policy.kind == kind}


# ---------------------------------------------------------------------------
# BGP
# ---------------------------------------------------------------------------


def _bgp(parsed: ParsedConfig) -> list[Finding]:
    bgp = parsed.bgp
    if bgp is None:
        return []
    findings: list[Finding] = []
    tools = _names(parsed, "route-map") | _names(parsed, "prefix-list")

    for neighbor in bgp.neighbors:
        findings.extend(_bgp_neighbor(bgp, neighbor, tools))

    missing_ibgp = [
        neighbor.address
        for neighbor in bgp.neighbors
        if not neighbor.is_ebgp(bgp.local_as) and neighbor.remote_as and not neighbor.next_hop_self
    ]
    has_ebgp = any(n.is_ebgp(bgp.local_as) for n in bgp.neighbors)
    if has_ebgp and missing_ibgp:
        findings.append(
            _note(
                MEDIUM,
                "外から取った経路が、中の相手から使えない可能性があります",
                "外の AS から受けた経路は、次に渡す先がそのまま外のアドレスになります。"
                "中の相手はそこへの経路を持たないので、経路は見えているのに落ちます。"
                f"next-hop-self が入っていない中の相手: {', '.join(missing_ibgp)}",
                [f"neighbor {address} remote-as {bgp.local_as}" for address in missing_ibgp],
            )
        )

    if not bgp.log_neighbor_changes and bgp.neighbors:
        findings.append(
            _note(
                LOW,
                "BGP の相手が落ちてもログに残りません",
                "bgp log-neighbor-changes を入れておくと、切れた時刻がログに残ります。"
                "後から「いつから落ちていたか」を言えるかどうかが、ここで決まります。",
                [f"router bgp {bgp.local_as}"],
            )
        )
    return findings


def _bgp_neighbor(bgp: ParsedBgp, neighbor: ParsedBgpNeighbor, tools: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    where = f"neighbor {neighbor.address}"
    external = neighbor.is_ebgp(bgp.local_as)

    if not neighbor.remote_as and not neighbor.peer_group:
        findings.append(
            _note(
                HIGH,
                f"{neighbor.address} の相手 AS が書かれていません",
                "remote-as が無い相手とは、そもそも接続を張りません。"
                "peer-group にも入っていないので、この行は効いていません。",
                [where],
            )
        )

    for name, label in (
        (neighbor.route_map_in, "route-map in"),
        (neighbor.route_map_out, "route-map out"),
        (neighbor.prefix_list_in, "prefix-list in"),
        (neighbor.prefix_list_out, "prefix-list out"),
    ):
        if name and name not in tools:
            findings.append(
                _note(
                    HIGH,
                    f"{neighbor.address} に掛けた {name} が、この Config にありません",
                    "中身の無い道具を掛けると、機種によっては全部落ちます。"
                    "名前の打ち間違いか、定義の入れ忘れです。",
                    [f"{where} {label} {name}"],
                )
            )

    if external:
        if not (neighbor.route_map_out or neighbor.prefix_list_out or neighbor.filter_list_out):
            findings.append(
                _note(
                    HIGH,
                    f"{neighbor.address} へ出す経路を絞っていません",
                    "外の AS へは、自分の経路だけを出すのが決まりです。絞っていないと、"
                    "受けた経路をそのまま別の相手へ渡してしまい、自分の回線が"
                    "他所どうしの通信の通り道になります。",
                    [f"{where} remote-as {neighbor.remote_as}"],
                )
            )
        if not (neighbor.route_map_in or neighbor.prefix_list_in or neighbor.filter_list_in):
            findings.append(
                _note(
                    MEDIUM,
                    f"{neighbor.address} から受ける経路を絞っていません",
                    "相手の設定を間違えられると、こちらの経路表がそのまま影響を受けます。"
                    "既定経路や自分のアドレスまで受けてしまうと、内側の通信が外へ出ます。",
                    [f"{where} remote-as {neighbor.remote_as}"],
                )
            )
        if neighbor.maximum_prefix is None:
            findings.append(
                _note(
                    MEDIUM,
                    f"{neighbor.address} から受ける経路の数に上限がありません",
                    "相手が事故で全経路を流してくると、こちらのメモリを使い切って機器が止まります。"
                    "上限を入れておけば、その相手との接続が切れるだけで済みます。",
                    [f"{where} remote-as {neighbor.remote_as}"],
                )
            )
        if not neighbor.password:
            findings.append(
                _note(
                    LOW,
                    f"{neighbor.address} との接続に合言葉がありません",
                    "外の AS との接続は、合言葉（neighbor password）を入れるのが通例です。"
                    "経路を横から差し込まれるのを防ぎます。",
                    [f"{where} remote-as {neighbor.remote_as}"],
                )
            )
        if neighbor.update_source and neighbor.ebgp_multihop is None:
            findings.append(
                _note(
                    HIGH,
                    f"{neighbor.address} とは、このままでは接続が張れません",
                    f"update-source {neighbor.update_source} を指定した外の AS 相手には、"
                    "ebgp-multihop が要ります。既定では隣り合った機器としか張らないためです。",
                    [f"{where} update-source {neighbor.update_source}"],
                )
            )

    if neighbor.shutdown:
        findings.append(
            _note(
                LOW,
                f"{neighbor.address} は止めたままです",
                "shutdown が入っています。試験のときに止めて、戻し忘れていることがあります。",
                [f"{where} shutdown"],
            )
        )
    return findings


# ---------------------------------------------------------------------------
# route-map / prefix-list
# ---------------------------------------------------------------------------


def _route_maps(parsed: ParsedConfig) -> list[Finding]:
    findings: list[Finding] = []
    for policy in parsed.policies:
        if not policy.entries or not policy.used_by:
            continue
        if not policy.has_permit:
            findings.append(
                _note(
                    HIGH,
                    f"{policy.name} には通す行がありません",
                    "deny だけの道具を掛けると、当たったものは全部落ちます。"
                    f"使われているところ: {', '.join(policy.used_by)}",
                    [entry.raw for entry in policy.entries],
                )
            )
            continue
        if policy.kind != "route-map":
            continue
        # 条件の無い permit があると、そこで止まる。後ろの行は読まれない
        for index, entry in enumerate(policy.entries[:-1]):
            if entry.action == "permit" and not entry.matches:
                rest = policy.entries[index + 1 :]
                findings.append(
                    _note(
                        MEDIUM,
                        f"{policy.name} は、途中で全部通しています",
                        f"{entry.raw} には条件がありません。route-map は最初に当たった"
                        f"ところで止まるので、この後ろの {len(rest)} 行は読まれません。",
                        [entry.raw, *[item.raw for item in rest]],
                    )
                )
                break
    return findings


# ---------------------------------------------------------------------------
# 通信の制限（ACL）
# ---------------------------------------------------------------------------


def _acls(parsed: ParsedConfig) -> list[Finding]:
    findings: list[Finding] = []
    for acl in parsed.acls:
        rules = [entry for entry in acl.entries if entry.action in {"permit", "deny"}]
        if not rules:
            continue
        used = acl.applied_to or acl.referenced_by
        if not used:
            findings.append(
                _note(
                    MEDIUM,
                    f"ACL {acl.name} は、どこにも当たっていません",
                    "書いてあるだけでは何も起きません。当て忘れか、"
                    "使わなくなったものが残っているかのどちらかです。",
                    [entry.raw for entry in rules],
                )
            )
            continue
        if acl.applied_to and not any(entry.action == "permit" for entry in rules):
            findings.append(
                _note(
                    HIGH,
                    f"ACL {acl.name} は、当たった通信を全部落とします",
                    f"通す行が1行もありません。{', '.join(acl.applied_to)} に当たっているので、"
                    "このポートの通信は止まります。",
                    [entry.raw for entry in rules],
                )
            )
        findings.extend(_shadowed(acl, rules))
    return findings


def _shadowed(acl: ParsedAcl, rules: list) -> list[Finding]:  # noqa: ANN001
    """上の行に隠れて、読まれない行を挙げる。

    ACL は上から順に見て、最初に当たった行で決まる。無条件の行の下や、
    同じ条件の行の下は読まれない。書いた人はたいてい気づいていない。
    """
    findings: list[Finding] = []
    seen: dict[tuple, str] = {}
    for index, entry in enumerate(rules):
        key = (
            entry.protocol,
            entry.source,
            entry.source_port,
            entry.destination,
            entry.destination_port,
        )
        if key in seen:
            findings.append(
                _note(
                    LOW,
                    f"ACL {acl.name} に、同じ条件の行が2つあります",
                    f"上の行で決まるので、下の行は読まれません。上: {seen[key]}",
                    [seen[key], entry.raw],
                )
            )
            continue
        seen[key] = entry.raw
        if entry.is_any_any and entry.protocol in {None, "ip", "any"} and index + 1 < len(rules):
            rest = rules[index + 1 :]
            findings.append(
                _note(
                    MEDIUM,
                    f"ACL {acl.name} は、途中で全部決めています",
                    f"{entry.raw} は送信元も宛先も限定していません。"
                    f"この後ろの {len(rest)} 行は読まれません。",
                    [entry.raw, *[item.raw for item in rest]],
                )
            )
            break
    return findings


# ---------------------------------------------------------------------------
# アドレス変換（NAT / NAPT）
# ---------------------------------------------------------------------------


def _nat(parsed: ParsedConfig) -> list[Finding]:
    nat = parsed.nat
    if nat is None:
        return []
    findings: list[Finding] = []
    acl_names = {acl.name for acl in parsed.acls}
    route_maps = _names(parsed, "route-map")

    if nat.rules and not (nat.inside_interfaces and nat.outside_interfaces):
        missing = "内側" if not nat.inside_interfaces else "外側"
        findings.append(
            _note(
                HIGH,
                f"NAT の{missing}のポートが決まっていません",
                "変換は内側と外側の両方を指定してはじめて動きます。片方だけだと、"
                "設定は入っているのに1つも変換されません。",
                [rule.raw for rule in nat.rules],
            )
        )

    for rule in nat.rules:
        if rule.kind != "dynamic":
            continue
        if rule.matched and rule.matched not in acl_names and rule.matched not in route_maps:
            findings.append(
                _note(
                    HIGH,
                    f"NAT が指している {rule.matched} が、この Config にありません",
                    "変換する通信を決める側が無いので、この行は何も変換しません。",
                    [rule.raw],
                )
            )
        if not rule.overload:
            target = rule.translated or ""
            pool = next((p for p in nat.pools if p.name == target), None)
            if pool is None or pool.start == pool.end:
                findings.append(
                    _note(
                        HIGH,
                        "変換後のアドレスが足りません（overload が入っていません）",
                        "overload を書かないと、1台につき1アドレスを使います。"
                        "端末の数だけ外のアドレスが要り、足りなくなった端末から先に出られなくなります。"
                        "ポート番号で束ねる（NAPT）なら overload を足してください。",
                        [rule.raw],
                    )
                )

    used_pools = {rule.translated for rule in nat.rules}
    for pool in nat.pools:
        if pool.name not in used_pools:
            findings.append(
                _note(
                    LOW,
                    f"NAT の {pool.name} は使われていません",
                    "作ってあるだけで、どの行からも指されていません。",
                    [f"ip nat pool {pool.name} {pool.start} {pool.end}"],
                )
            )

    statics: dict[str, str] = {}
    for rule in nat.rules:
        if rule.kind not in {"static", "static-port"} or not rule.translated:
            continue
        if rule.translated in statics:
            findings.append(
                _note(
                    MEDIUM,
                    f"外側の {rule.translated} を、2か所で使っています",
                    "同じ外のアドレスに2つの機器を割り当てています。"
                    f"先に書いたほう（{statics[rule.translated]}）が優先されます。",
                    [statics[rule.translated], rule.raw],
                )
            )
        else:
            statics[rule.translated] = rule.raw
    return findings


# ---------------------------------------------------------------------------
# アドレス配布（DHCP）
# ---------------------------------------------------------------------------


def _dhcp(parsed: ParsedConfig) -> list[Finding]:
    dhcp = parsed.dhcp
    if dhcp is None:
        return []
    findings: list[Finding] = []
    excluded = _excluded_addresses(dhcp.excluded)

    for pool in dhcp.pools:
        head = f"ip dhcp pool {pool.name}"
        if not pool.default_routers:
            findings.append(
                _note(
                    HIGH,
                    f"{pool.name} は既定ゲートウェイを配っていません",
                    "アドレスは配られるので端末は繋がったように見えますが、"
                    "そのセグメントの外へ出られません。いちばん報告の遅れる形です。",
                    [head, f"network {pool.network}" if pool.network else ""],
                )
            )
        network = _network_of(pool.network)
        for router in pool.default_routers:
            if network is None:
                continue
            try:
                address = ipaddress.ip_address(router)
            except ValueError:
                continue
            if address not in network:
                findings.append(
                    _note(
                        HIGH,
                        f"{pool.name} の既定ゲートウェイが、配る範囲の外です",
                        f"{router} は {network} に入っていません。端末は届かない相手を"
                        "ゲートウェイとして覚えるので、外へ出られません。",
                        [head, f"default-router {router}"],
                    )
                )
            elif router not in excluded:
                findings.append(
                    _note(
                        MEDIUM,
                        f"{pool.name} は、ゲートウェイのアドレスも配ってしまいます",
                        f"{router} を excluded-address から外しています。"
                        "端末に配られると、アドレスがぶつかってその区画が止まります。",
                        [head, f"default-router {router}"],
                    )
                )
        if not pool.dns_servers:
            findings.append(
                _note(
                    LOW,
                    f"{pool.name} は名前解決の先を配っていません",
                    "アドレスは通っても、名前で繋ぐものは動きません。"
                    "手で入れて回ることになります。",
                    [head],
                )
            )
    return findings


def _excluded_addresses(ranges: list[tuple[str, str]]) -> set[str]:
    """配らないアドレスを1つずつに展開する。範囲が広すぎるものは端だけ見る。"""
    out: set[str] = set()
    for start, end in ranges:
        try:
            first = ipaddress.ip_address(start)
            last = ipaddress.ip_address(end)
        except ValueError:
            continue
        if int(last) - int(first) > 4096:  # 台帳の Config で現実的な幅を超える指定
            out.update({str(first), str(last)})
            continue
        current = first
        while current <= last:
            out.add(str(current))
            current += 1
    return out


def _network_of(text: str | None) -> ipaddress.IPv4Network | None:
    if not text:
        return None
    parts = text.split()
    try:
        if len(parts) == 2:
            return ipaddress.ip_network(f"{parts[0]}/{parts[1]}", strict=False)
        return ipaddress.ip_network(parts[0], strict=False)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 優先制御（QoS）
# ---------------------------------------------------------------------------


def _qos(parsed: ParsedConfig) -> list[Finding]:
    qos = parsed.qos
    if qos is None:
        return []
    findings: list[Finding] = []
    class_names = {entry.name for entry in qos.classes}
    applied = {name for _, _, name in qos.applied}

    for policy in qos.policies:
        if policy.name not in applied:
            findings.append(
                _note(
                    MEDIUM,
                    f"policy-map {policy.name} は、どのポートにも当たっていません",
                    "優先制御は当てたポートでしか効きません。混む場所に当てていなければ、"
                    "書いてあっても順番は変わりません。",
                    [f"policy-map {policy.name}"],
                )
            )
        for entry in policy.classes:
            if entry.class_name in class_names or entry.class_name == "class-default":
                continue
            findings.append(
                _note(
                    HIGH,
                    f"{policy.name} が指す class-map {entry.class_name} がありません",
                    "当てる相手が決まらないので、この区分に入る通信はありません。"
                    "名前の打ち間違いか、定義の入れ忘れです。",
                    [f"policy-map {policy.name}", f"class {entry.class_name}"],
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 拠点間の暗号化（IPsec / VPN）
# ---------------------------------------------------------------------------


def _crypto(parsed: ParsedConfig) -> list[Finding]:
    crypto = parsed.crypto
    if crypto is None:
        return []
    findings: list[Finding] = []
    acl_names = {acl.name for acl in parsed.acls}
    applied_maps = {name for _, name in crypto.applied}
    transform_names = {entry.name for entry in crypto.transform_sets}

    for policy in crypto.ike_policies:
        head = f"crypto isakmp policy {policy.number}"
        for value in (policy.encryption, policy.hash):
            findings.extend(_weak(value, head, "鍵の交換"))
        if policy.group and policy.group.split()[0] in _WEAK_GROUPS:
            findings.append(
                _note(
                    MEDIUM,
                    f"鍵の作り方が古いままです（group {policy.group}）",
                    "DH group 1・2・5 は鍵の元が短く、記録しておいて後から解く手が現実的です。"
                    "group 14 以上にしてください。",
                    [head, f"group {policy.group}"],
                )
            )

    for transform in crypto.transform_sets:
        head = f"crypto ipsec transform-set {transform.name}"
        for value in transform.transforms:
            findings.extend(_weak(value, head, "中身の暗号化"))
        if not any(value.lower().startswith("esp-") for value in transform.transforms):
            findings.append(
                _note(
                    HIGH,
                    f"{transform.name} は中身を暗号化していません",
                    "AH だけでは、改ざんは見つけられても中身はそのまま流れます。"
                    "拠点間の通信を守るなら ESP が要ります。",
                    [f"{head} {' '.join(transform.transforms)}"],
                )
            )

    for entry in crypto.maps:
        head = f"crypto map {entry.name} {entry.seq}"
        missing = []
        if not entry.peers:
            missing.append("相手（set peer）")
        if not entry.transform_sets and not entry.profile:
            missing.append("方式（set transform-set）")
        if not entry.match_address and not entry.profile:
            missing.append("暗号化する範囲（match address）")
        if missing:
            findings.append(
                _note(
                    HIGH,
                    f"{entry.name} の {entry.seq} 番は、このままでは上がりません",
                    f"{'、'.join(missing)}が書かれていません。"
                    "3つそろってはじめてトンネルになります。",
                    [head],
                )
            )
        if entry.match_address and entry.match_address not in acl_names:
            findings.append(
                _note(
                    HIGH,
                    f"{entry.name} が指す {entry.match_address} が、この Config にありません",
                    "何を暗号化するのかが決まらないので、通信はトンネルに乗りません。",
                    [head, f"match address {entry.match_address}"],
                )
            )
        for name in entry.transform_sets:
            if name not in transform_names:
                findings.append(
                    _note(
                        HIGH,
                        f"{entry.name} が指す方式 {name} がありません",
                        "方式が決まらないので、相手と話が合いません。",
                        [head, f"set transform-set {name}"],
                    )
                )
        if entry.name not in applied_maps:
            findings.append(
                _note(
                    HIGH,
                    f"crypto map {entry.name} は、どのポートにも当たっていません",
                    "書いてあるだけでは通信は暗号化されません。外向きのポートに"
                    "当てるまで、中身はそのまま流れます。",
                    [head],
                )
            )
        if entry.pfs is None and not entry.profile:
            findings.append(
                _note(
                    LOW,
                    f"{entry.name} の {entry.seq} 番は、鍵を作り直していません",
                    "PFS を入れておくと、鍵が1つ漏れても、それ以前の通信までは読まれません。",
                    [head],
                )
            )
    return findings


def _weak(value: str | None, head: str, what: str) -> list[Finding]:
    if not value:
        return []
    token = value.strip().lower().split()[0]
    if token in _BROKEN_CRYPTO:
        return [
            _note(
                HIGH,
                f"{what}に、破られている方式が残っています（{token}）",
                _BROKEN_CRYPTO[token] + "。いま作るものには使えません。",
                [head, value],
            )
        ]
    if token in _WEAK_CRYPTO:
        return [
            _note(
                MEDIUM,
                f"{what}の方式が古いままです（{token}）",
                _WEAK_CRYPTO[token] + "。更改のときに合わせて直してください。",
                [head, value],
            )
        ]
    return []
