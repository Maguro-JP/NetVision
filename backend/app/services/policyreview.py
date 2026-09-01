"""読み取った ACL / NAT / DHCP / QoS / IPsec / BGP / 無線の中身を見て、粗を挙げる。

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
import re

from app.services import aclcheck, asareview, wirelessreview
from app.services.configparse import ParsedConfig
from app.services.configparse.base import ParsedAcl, ParsedAclEntry, ParsedBgp, ParsedBgpNeighbor
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


def review(parsed: ParsedConfig, text: str | None = None) -> list[Finding]:
    """1台分の Config を見て、気になったところを並べる。

    `text` は元の Config。ポートや経路として読み取っていない書き方
    （line vty など）を見るのに使う。渡されなければ、その分は見ない。
    """
    findings: list[Finding] = []
    findings.extend(_bgp(parsed))
    findings.extend(_route_maps(parsed))
    findings.extend(_acls(parsed))
    findings.extend(_nat(parsed))
    findings.extend(_dhcp(parsed))
    findings.extend(_qos(parsed))
    findings.extend(_crypto(parsed))
    findings.extend(wirelessreview.review(parsed))
    findings.extend(asareview.review(parsed))
    if text is not None:
        findings.extend(_management_access(text, parsed))
    return findings


def _note(level: str, title: str, why: str, evidence: list[str]) -> Finding:
    return Finding(level=level, title=title, why=why, evidence=[e for e in evidence if e][:5])


def _serves(pool, address: str) -> bool:  # noqa: ANN001
    """そのアドレスを、実際に端末へ配るか。

    配る範囲をそのまま書く機種では、範囲の外にあるものは配られない。
    範囲が書かれていない（Cisco のように network から引く）ときは、
    配る側に倒す。言えないことを言わない代わりに、見落としも作らない。
    """
    if not pool.range_start or not pool.range_end:
        return True
    try:
        low = ipaddress.ip_address(pool.range_start)
        high = ipaddress.ip_address(pool.range_end)
        target = ipaddress.ip_address(address)
    except ValueError:
        return True
    return low <= target <= high


def _names(parsed: ParsedConfig, kind: str) -> set[str]:
    return {policy.name for policy in parsed.policies if policy.kind == kind}


# ---------------------------------------------------------------------------
# BGP
# ---------------------------------------------------------------------------


#: 相手の状態が変わったことを、書かなくても記録する機種。
#: 「書いていない＝残らない」と読むと、そこだけ誤って言うことになる
_LOGS_BGP_BY_DEFAULT = {"arista"}


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

    logs_anyway = parsed.vendor.value in _LOGS_BGP_BY_DEFAULT
    if not bgp.log_neighbor_changes and bgp.neighbors and not logs_anyway:
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
        findings.extend(_lets_everything_through(acl, rules))
        findings.extend(_drops_without_a_trace(acl, rules))
    return findings


#: 1つの ACL から出す影の指摘の上限。多いときは中身を作り直すほうが早く、
#: 20 件並べても読まれない
_SHADOW_LIMIT = 3


def _shadowed(acl: ParsedAcl, rules: list[ParsedAclEntry]) -> list[Finding]:
    """上の行に隠れて、読まれない行を挙げる。

    字面ではなく、条件の集合として比べる。`permit ip any any` と
    `deny tcp 10.0.0.0 0.0.0.255 any eq 23` は、字面はまったく違うが、
    上が下を丸ごと覆っている。書いた人はたいてい気づいていない。
    """
    findings: list[Finding] = []
    for index, entry in enumerate(rules):
        upper = next((item for item in rules[:index] if aclcheck.covers(item, entry)), None)
        if upper is None:
            continue
        if upper.action == entry.action:
            findings.append(
                _note(
                    LOW,
                    f"ACL {acl.name} に、上の行と同じ意味の行があります",
                    f"上の行で決まるので、この行は読まれません。上: {upper.raw}",
                    [upper.raw, entry.raw],
                )
            )
        else:
            doing = "通して" if upper.action == "permit" else "落として"
            findings.append(
                _note(
                    MEDIUM,
                    f"ACL {acl.name} の {entry.raw} は読まれません",
                    f"手前の {upper.raw} が、この行に当たる通信を先に{doing}います。"
                    "書いたつもりの制限が効いていません。行の順番を入れ替えてください。",
                    [upper.raw, entry.raw],
                )
            )
        if len(findings) >= _SHADOW_LIMIT:
            findings.append(
                _note(
                    MEDIUM,
                    f"ACL {acl.name} には、読まれない行がほかにもあります",
                    "順番の直しでは追いつきません。何を通したいのかを決め直して、"
                    "書き直すほうが早いところです。",
                    [entry.raw],
                )
            )
            break
    return findings


def _lets_everything_through(acl: ParsedAcl, rules: list[ParsedAclEntry]) -> list[Finding]:
    """実質、何も止めていない ACL。

    影の行としては出ない（後ろに行が無ければ隠れるものが無い）ので、別に見る。
    当てていない ACL には言わない。何も止めていなくても、効いていなければ害は無い。
    """
    if not acl.applied_to or acl.stateful:
        # 戻りの通信を動的に通す仕組みと組み合わせているものは、出る側を
        # 広く開けておくのが普通の書き方になる。そこを毎回言うと、
        # 直す必要の無いものが並んで、本当の指摘が埋もれる
        return []
    wide = next(
        (entry for entry in rules if entry.action == "permit" and aclcheck.is_wide_open(entry)),
        None,
    )
    if wide is None:
        return []
    return [
        _note(
            MEDIUM,
            f"ACL {acl.name} は、実質すべて通しています",
            f"{wide.raw} は送信元も宛先もポートも限定していません。"
            f"{', '.join(acl.applied_to)} に当ててはいますが、止まる通信はありません。",
            [wide.raw],
        )
    ]


def _drops_without_a_trace(acl: ParsedAcl, rules: list[ParsedAclEntry]) -> list[Finding]:
    """落としているのに、記録が残らない ACL。

    ACL の末尾には、書かなくても「残り全部を落とす」が入っている。これは
    ログに残らないので、「繋がらない」と言われたときに、この ACL で
    落ちたのかどうかを確かめられない。
    """
    if not acl.applied_to or acl.kind == "policy":
        # ポリシー表（FortiOS）の末尾の落としは、機器が別に記録する
        return []
    denies = [entry for entry in rules if entry.action == "deny"]
    if any(entry.log for entry in denies):
        return []
    last = rules[-1]
    if denies and aclcheck.is_wide_open(last) and last.action == "deny":
        return [
            _note(
                LOW,
                f"ACL {acl.name} は、落とした通信を記録していません",
                "末尾の落とす行に log がありません。「繋がらない」と言われたときに、"
                "ここで落ちたのかどうかを確かめられません。",
                [last.raw],
            )
        ]
    if not denies:
        return [
            _note(
                LOW,
                f"ACL {acl.name} には、明示的に落とす行がありません",
                "ACL の末尾には、書かなくても「残り全部を落とす」が入っています。"
                "書いていないと、それがログに残らず、落ちたことに気づけません。",
                [rules[-1].raw],
            )
        ]
    return []


# ---------------------------------------------------------------------------
# 管理の口
# ---------------------------------------------------------------------------

_VTY = re.compile(r"^line vty\s+(\S+(?:\s+\S+)?)\s*$", re.IGNORECASE)


def _management_access(text: str, parsed: ParsedConfig) -> list[Finding]:
    """機器に入る口に当てた ACL が、ちゃんと存在するか。

    ACL を書いていても、当て先の中身が無ければ絞れていない。
    access-class を書いていないこと自体は、ここでは言わない。入る口を
    どこまで絞るかは現場の決めごとで、間違いとは言い切れないため。
    """
    findings: list[Finding] = []
    acl_names = {acl.name for acl in parsed.acls}
    for head, body in _blocks(text, _VTY):
        for line in body:
            match = re.match(r"^(?:ip\s+)?access-class\s+(\S+)", line, re.IGNORECASE)
            if match and match.group(1) not in acl_names:
                findings.append(
                    _note(
                        HIGH,
                        f"管理の口に当てた {match.group(1)} が、この Config にありません",
                        "当て先の中身が無いので、絞れているのかどうかが分かりません。"
                        "名前の打ち間違いか、定義の入れ忘れです。",
                        [f"line vty {head.group(1)}", line],
                    )
                )
    return findings


def _blocks(text: str, head: re.Pattern[str]) -> list[tuple[re.Match[str], list[str]]]:
    """見出しと、そこにぶら下がる字下げ行。"""
    out: list[tuple[re.Match[str], list[str]]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        match = head.match(line)
        if match:
            current = []
            out.append((match, current))
            continue
        if current is None:
            continue
        if line.strip() == "" or line.startswith((" ", "\t")):
            if line.strip():
                current.append(line.strip())
            continue
        current = None
    return out


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
        known = acl_names | route_maps | set(parsed.address_objects)
        if rule.matched_is_name and rule.matched and rule.matched not in known:
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
            elif _serves(pool, router) and router not in excluded:
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
                    # 名前の付き方は機種で違う（policy-map / shaper /
                    # scheduler-map）。どれでも読めるよう、種類の語は入れない
                    f"優先制御の {policy.name} は、どのポートにも当たっていません",
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
        # AH だけを指定している書き方のときに言う。ESP が当たり前の機種
        # （FortiGate の proposal など）では、そもそも esp- と書かない
        ah_only = any(
            value.lower().startswith("ah-") for value in transform.transforms
        ) and not any(value.lower().startswith("esp-") for value in transform.transforms)
        if ah_only:
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
        # 番号を付けない機種（NEC IX の autokey-map など）がある。
        # 「None 番」と出すくらいなら、名前だけで呼ぶ
        label = f"{entry.name} の {entry.seq} 番" if entry.seq else f"{entry.name} "
        head = f"crypto map {entry.name} {entry.seq}" if entry.seq else f"crypto map {entry.name}"
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
                    f"{label}は、このままでは上がりません",
                    f"{'、'.join(missing)}が書かれていません。"
                    "3つそろってはじめてトンネルになります。",
                    [head],
                )
            )
        if entry.match_is_acl and entry.match_address and entry.match_address not in acl_names:
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
                    f"{label}は、鍵を作り直していません",
                    "PFS を入れておくと、鍵が1つ漏れても、それ以前の通信までは読まれません。",
                    [head],
                )
            )
    return findings


def _method_name(value: str) -> str:
    """方式の書き方を、比べられる形にそろえる。

    同じ方式でも、機種ごとに書き方が違う（des / des-cbc / esp-des、
    sha256 / sha-256 / hmac-sha-256-128）。頭の付け方と鍵長の書き方を外して、
    方式の名前だけを取り出す。強さを表す数字が付いているものは、そのまま残す
    （sha と sha-256 は別物で、混ぜると新しい方まで古いと言ってしまう）。
    """
    token = value.strip().lower().split()[0]
    # 頭に付く「どの部分を守るか」（esp / ah）と、鍵付きハッシュの印（hmac）を外す。
    # Cisco の esp-des のように、そのままの形で表に載っているものは呼ぶ側で先に見る
    token = re.sub(r"^(?:esp-|ah-|hmac-)+", "", token)
    parts = token.split("-")
    if not parts:
        return token
    head = parts[0]
    # 続きが強さの数字なら、方式の名前に含める（sha-256 → sha256）
    if len(parts) > 1 and parts[1].isdigit():
        return f"{head}{parts[1]}"
    # 続きが動作の種類（cbc / gcm など）なら、方式の名前だけにする
    return head


def _weak(value: str | None, head: str, what: str) -> list[Finding]:
    if not value:
        return []
    raw = value.strip().lower().split()[0]
    # 表に載っている書き方をそのまま見て、無ければ方式の名前だけで見る
    token = raw if raw in _BROKEN_CRYPTO or raw in _WEAK_CRYPTO else _method_name(value)
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
