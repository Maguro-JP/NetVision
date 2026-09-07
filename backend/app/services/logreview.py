"""ログを読んで、何が起きているのかを言う。

数えて並べるところまでは `syslog.py` がやる。ここでやるのはその先で、
「これは障害か」「設定が間違っているのではないか」「攻撃を受けていないか」を
判断して出す。

Config を書く道具である以上、そこは避けて通れない。設定を出しておいて、その
設定が原因で起きている事象を「ログの件数」としてしか出せないのでは、責任を
半分しか果たしていない。

### 判断の作り方

- 数えられることだけで判断する。モデルには通さない。同じログなら毎回同じ答えに
  なり、なぜそう出たのかを人が追える必要がある
- 断定しない。「〜の可能性があります」で止め、根拠（何回・いつからいつまで・
  どの機器）を必ず添える。ログは起きたことの一部しか写していない
- 次にやることを1つ書く。原因の候補だけ出されても、現場では動けない
- しきい値には理由を書く。1回でも重いもの（再起動・ループ）と、繰り返して
  はじめて意味を持つもの（ログイン失敗・deny）を分ける

### 3つの見方

- 障害: いま止まっている / 止まりかけている。人がすぐ動く必要がある
- 設定: ログの形から、設定が食い違っていると分かるもの。直せば消える
- 攻撃: 誰かが外から叩いている可能性。止めるかどうかは人が決める
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.services.syslog import LogEvent

#: 重さ。Config 点検・L3 の指摘と同じ言葉にそろえる
HIGH = "high"
MEDIUM = "medium"
LOW = "low"

#: 見方の分け方
TROUBLE = "障害"
CONFIG = "設定"
ATTACK = "攻撃"

#: 同じポートが上下する回数。線・SFP・速度の食い違いを疑うのはこの回数から。
#: 2回なら人が抜き差ししただけのことがある
_FLAP_MIN = 6

#: 経路の相手が落ちた回数。1回は再起動でも起きる。繰り返すと経路が揺れている
_NEIGHBOR_DOWN_MIN = 3

#: ログイン失敗の回数。打ち間違いは数回で終わる。桁が変わったら人ではない
_LOGIN_FAIL_MIN = 10

#: 同じ送信元からの deny の回数。これを超えると、探られていると見る
_DENY_MIN = 20

#: 走査とみなす宛先ポートの種類数。1つなら特定の通信、多ければ探り
_SCAN_PORTS = 5

#: 設定変更のあと、これだけの間に起きた重い事象は、変更と結び付けて出す。
#: 長く取ると無関係なものまで拾い、短いと反映待ちの分を落とす
_AFTER_CHANGE = timedelta(minutes=10)

#: 1件あたりに出す生ログの行数。多いと読む気が失せる
_EVIDENCE = 3


@dataclass
class Finding:
    """気づいたこと1件。

    `why` はなぜそう言えるか、`what_now` は次にやること。
    どちらも無いものは出さない。件数と時刻の幅は、判断の根拠として必ず持つ。
    """

    level: str
    kind: str
    title: str
    why: str
    what_now: str
    count: int = 0
    hosts: list[str] = field(default_factory=list)
    first_at: datetime | None = None
    last_at: datetime | None = None
    evidence: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ログ1行から、判断に使う値を取り出す
# ---------------------------------------------------------------------------

#: ポート名。機種で書き方が違うので、よくある形をまとめて拾う
_INTERFACE = re.compile(
    r"\b((?:Gigabit|TenGigabit|FastEthernet|GigabitEthernet|Ethernet|Te|Gi|Fa|Eth|Port|port|"
    r"XGE|GE|Vlanif|Vlan-interface|Eth-Trunk|Ve)[\w./:-]*\d[\w./:-]*"
    # ファーウェイは速度を頭に付ける（10GE1/0/1）ので、数字から始まる
    r"|\d{2,3}GE[\d/.:]+)"
)
#: IPv4。送信元・相手を見分けるのに使う
_IPV4 = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")
#: 宛先ポート番号（ACL の deny ログに出る `(10.1.1.1(53))` の形）
_PORT_IN_PAREN = re.compile(r"\((\d{1,5})\)")


def interface_of(event: LogEvent) -> str | None:
    found = _INTERFACE.search(event.message or event.raw)
    return found.group(1) if found else None


def addresses_of(event: LogEvent) -> list[str]:
    return _IPV4.findall(event.message or event.raw)


def _has(event: LogEvent, *words: str) -> bool:
    text = f"{event.code or ''} {event.message}".lower()
    return any(word.lower() in text for word in words)


def _span(events: list[LogEvent]) -> tuple[datetime | None, datetime | None]:
    stamps = [e.at for e in events if e.at is not None]
    return (min(stamps), max(stamps)) if stamps else (None, None)


def _hosts(events: list[LogEvent]) -> list[str]:
    return sorted({e.host for e in events if e.host})


def _evidence(events: list[LogEvent]) -> list[str]:
    return [e.raw[:300] for e in events[:_EVIDENCE]]


def _finding(
    level: str,
    kind: str,
    title: str,
    why: str,
    what_now: str,
    events: list[LogEvent],
) -> Finding:
    first, last = _span(events)
    return Finding(
        level=level,
        kind=kind,
        title=title,
        why=why,
        what_now=what_now,
        count=len(events),
        hosts=_hosts(events)[:10],
        first_at=first,
        last_at=last,
        evidence=_evidence(events),
    )


def _pick(events: list[LogEvent], *words: str) -> list[LogEvent]:
    return [e for e in events if _has(e, *words)]


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def review(events: list[LogEvent]) -> list[Finding]:
    """読み取ったログから、気づいたことを重い順に返す。"""
    if not events:
        return []

    ordered = sorted(events, key=lambda e: e.at or datetime.min)
    found: list[Finding] = []
    for detect in (
        _restarts,
        _hardware,
        _flapping_links,
        _neighbours_going_down,
        _failover,
        _spanning_tree,
        _resource_pressure,
        _duplex_mismatch,
        _native_vlan_mismatch,
        _routing_mismatch,
        _address_pool_empty,
        _tunnel_not_coming_up,
        _clock_not_set,
        _trouble_after_change,
        _login_attempts,
        _denied_a_lot,
        _mac_moving,
        _rogue_dhcp,
        _port_security,
        _attack_reported_by_device,
    ):
        item = detect(ordered)
        if item is not None:
            found.append(item)

    weight = {HIGH: 0, MEDIUM: 1, LOW: 2}
    found.sort(key=lambda f: (weight.get(f.level, 3), -f.count))
    return found


# ---------------------------------------------------------------------------
# 障害
# ---------------------------------------------------------------------------


def _restarts(events: list[LogEvent]) -> Finding | None:
    hits = _pick(events, "RESTART", "RELOAD", "SYS-5-RESTART", "System restarted", "reboot")
    if not hits:
        return None
    return _finding(
        HIGH,
        TROUBLE,
        "機器が再起動しています",
        f"再起動を示す行が {len(hits)} 件あります"
        f"（{'、'.join(_hosts(hits)[:5]) or '機器名なし'}）。"
        "計画した作業でなければ、電源・熱・不具合のどれかです。",
        "その機器の稼働時間（show version）と直前のログを見て、"
        "落ちたのか、人が落としたのかを先に分けてください。",
        hits,
    )


def _hardware(events: list[LogEvent]) -> Finding | None:
    hits = _pick(
        events,
        "OVERTEMP",
        "THERMAL",
        "FANFAIL",
        "FAN_FAIL",
        "PWR-3",
        "POWER_SUPPLY",
        "PSU",
        # ファーウェイ / Comware。同じ出来事でも語が違う
        "hwPowerRemove",
        "hwPowerFault",
        "hwEntityTemperature",
        "POWER_FAILED",
        "FAN_ABSENT",
        "TEMPERATURE_ALARM",
    )
    if not hits:
        return None
    return _finding(
        HIGH,
        TROUBLE,
        "電源か冷却の異常が出ています",
        f"熱・ファン・電源に関する行が {len(hits)} 件あります。"
        "この手の異常は、放っておくと機器ごと止まります。",
        "現地で機器の吸排気とファン、電源の予備系を確認してください。"
        "熱なら、まずラックの前後がふさがっていないかを見ます。",
        hits,
    )


def _flapping_links(events: list[LogEvent]) -> Finding | None:
    """同じポートが何度も上下する。線・光の部品・速度の食い違いを疑うところ。"""
    # 同じ出来事でも、機種によって事象名が違う。1つの語だけを見ていると、
    # そのベンダーの機器だけ揺れていないことになる
    updown = [
        e
        for e in events
        if _has(e, "UPDOWN", "LINK_UPDOWN", "PHY_UPDOWN", "IF_STATE", "LINK_STATE")
    ]
    if not updown:
        return None

    by_port: dict[tuple[str, str], list[LogEvent]] = defaultdict(list)
    for event in updown:
        port = interface_of(event)
        if port is None:
            continue
        by_port[(event.host or "", port)].append(event)

    flapping = {key: items for key, items in by_port.items() if len(items) >= _FLAP_MIN}
    if not flapping:
        return None

    hits = [event for items in flapping.values() for event in items]
    worst = sorted(flapping.items(), key=lambda kv: -len(kv[1]))[:5]
    where = "、".join(
        f"{host or '機器名なし'} {port}（{len(items)}回）" for (host, port), items in worst
    )
    return _finding(
        HIGH,
        TROUBLE,
        "同じポートが何度も上下しています",
        f"{len(flapping)} 本のポートで、上下が {_FLAP_MIN} 回以上出ています: {where}。"
        "線・光の部品・速度の食い違いのどれかで、通信は「たまに切れる」形になります。",
        "そのポートの線と光の部品を差し替えて切り分けてください。"
        "両端の速度と二重の設定が片側だけ固定になっていないかも見ます。",
        hits,
    )


def _neighbours_going_down(events: list[LogEvent]) -> Finding | None:
    """経路の相手が落ちる。1回は再起動でも起きるが、繰り返すと経路が揺れる。"""
    hits = [
        e
        for e in events
        if _has(e, "ADJCHG", "ADJCHANGE", "NBRCHANGE", "NEIGHBOR")
        and _has(e, "down", "Down", "DOWN", "Loading", "Init")
    ]
    if len(hits) < _NEIGHBOR_DOWN_MIN:
        return None
    return _finding(
        HIGH,
        TROUBLE,
        "経路の相手が繰り返し落ちています",
        f"隣り合う機器との関係が切れた行が {len(hits)} 件あります。"
        "経路が揺れているあいだ、通信は行ったり来たりします。"
        "止まらないぶん、気づくのが遅れます。",
        "落ちている相手との線（片側だけ落ちていないか）と、"
        "その区間の設定（Hello の間隔・MTU・認証）を突き合わせてください。",
        hits,
    )


def _failover(events: list[LogEvent]) -> Finding | None:
    hits = _pick(events, "STANDBY-6-STATECHANGE", "VRRP", "HSRP", "STATECHANGE")
    if not hits:
        return None
    return _finding(
        MEDIUM,
        TROUBLE,
        "既定ゲートウェイの主が入れ替わっています",
        f"主・控えの入れ替わりが {len(hits)} 件あります。"
        "通信は続くので気づきにくいのですが、いまは控え側で動いている可能性があります。",
        "いまどちらが主かを確認し、元の主が落ちたのか、"
        "優先度や監視の設定で降りたのかを見てください。",
        hits,
    )


def _spanning_tree(events: list[LogEvent]) -> Finding | None:
    hits = _pick(
        events,
        "SPANTREE",
        "LOOPGUARD",
        "ROOTGUARD",
        "BPDUGUARD",
        "TOPOLOGY",
        "MSTP",
        "STP",
    )
    if not hits:
        return None
    blocked = _pick(hits, "BLOCK", "LOOP", "INCONSISTENT")
    level = HIGH if blocked else MEDIUM
    return _finding(
        level,
        TROUBLE,
        "L2 が輪になりかけています",
        f"STP の行が {len(hits)} 件（うち塞いだ・食い違いが {len(blocked)} 件）あります。"
        "輪になると、同じ荷物が回り続けて、つながっている機器がまとめて重くなります。",
        "塞がれたポートの先に何が挿さっているかを確認してください。"
        "現場でハブや余った線が挿されていることが多いところです。",
        hits,
    )


def _resource_pressure(events: list[LogEvent]) -> Finding | None:
    hits = _pick(events, "CPUHOG", "MALLOCFAIL", "MEMORY", "HIGH_CPU", "CPU_USAGE")
    if not hits:
        return None
    return _finding(
        MEDIUM,
        TROUBLE,
        "機器の処理が詰まっています",
        f"CPU・メモリに関する行が {len(hits)} 件あります。"
        "詰まっている間は、通信そのものより先に、機器の管理や経路の計算が遅れます。",
        "詰まった時刻の前後で、何が流れていたか（輪・大量の通信・監視の取りすぎ）を見てください。",
        hits,
    )


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------


def _duplex_mismatch(events: list[LogEvent]) -> Finding | None:
    hits = _pick(events, "DUPLEX_MISMATCH", "duplex mismatch")
    if not hits:
        return None
    return _finding(
        HIGH,
        CONFIG,
        "両端で二重（duplex）の設定が食い違っています",
        f"食い違いの行が {len(hits)} 件あります。片側だけ手で固定すると、"
        "相談が成立せず、通ってはいるのに極端に遅くなります。",
        "両端をそろえてください（どちらも自動、またはどちらも同じ固定値）。",
        hits,
    )


def _native_vlan_mismatch(events: list[LogEvent]) -> Finding | None:
    hits = _pick(events, "NATIVE_VLAN_MISMATCH", "native vlan mismatch")
    if not hits:
        return None
    return _finding(
        HIGH,
        CONFIG,
        "トランクのタグ無し VLAN が両端で違います",
        f"食い違いの行が {len(hits)} 件あります。分けたはずの VLAN が、"
        "この線の上で1つにつながっている状態です。",
        "両端の native vlan をそろえてください。どちらが正かは VLAN 台帳の用途で決めます。",
        hits,
    )


def _routing_mismatch(events: list[LogEvent]) -> Finding | None:
    """経路の相手と決めごとが合っていない。落ちるのではなく、そもそも組めない。"""
    # 経路の話に限る。ここを「MISMATCH を含む行」で拾うと、二重や
    # タグ無し VLAN の食い違いまで経路の指摘として出てしまう（実際に出た）
    hits = [
        e
        for e in events
        if _has(e, "OSPF", "BGP", "EIGRP", "ISIS")
        and _has(
            e,
            "mismatch",
            "errrcv",
            "notification",
            "bad",
            "invalid",
            "denied",
            "reject",
            "hold time",
            "holdtime",
            "unknown",
        )
    ]
    if not hits:
        return None
    return _finding(
        MEDIUM,
        CONFIG,
        "経路の相手と、決めごとが合っていません",
        f"OSPF / BGP が相手を拒んだ行が {len(hits)} 件あります。"
        "エリア番号・Hello の間隔・MTU・認証・AS 番号のどれかが片側だけ違うと、この形になります。",
        "両端の設定を並べて、違っている値を1つずつ突き合わせてください。"
        "設定は合っているのに続くときは、途中で大きい荷物が落ちていないか（MTU）を見ます。",
        hits,
    )


def _address_pool_empty(events: list[LogEvent]) -> Finding | None:
    """配る住所が尽きた。設計の見積もりが足りていない、という設定側の話。"""
    hits = [
        e
        for e in events
        if _has(e, "DHCP", "NAT", "PAT")
        and _has(e, "exhaust", "no free", "POOL_EMPTY", "ADDR_ALLOC", "unavailable", "枯渇")
    ]
    if not hits:
        return None
    dhcp = bool(_pick(hits, "DHCP"))
    what = "配る住所（DHCP）" if dhcp else "外向きの住所（NAT / PAT）"
    return _finding(
        HIGH,
        CONFIG,
        f"{what}が足りていません",
        f"足りないことを示す行が {len(hits)} 件あります。"
        "端末は住所をもらえず、その島だけつながらない形になります。",
        "区画の広さと、いま使われている数を数えてください。"
        "端末数に対して区画が狭いなら、設計のほうを直します（貸出時間を縮めるのは一時しのぎです）。",
        hits,
    )


def _tunnel_not_coming_up(events: list[LogEvent]) -> Finding | None:
    hits = [
        e
        for e in events
        if _has(e, "IKE", "IPSEC", "ISAKMP", "CRYPTO", "VPN")
        and _has(
            e,
            "fail",
            "invalid",
            "reject",
            "no proposal",
            "mismatch",
            "spi",
            "delete",
            "no sa",
            "no_sa",
            "retransmit",
        )
    ]
    if not hits:
        return None
    return _finding(
        MEDIUM,
        CONFIG,
        "拠点間の暗号化（IPsec）が張れていません",
        f"鍵の交換に失敗した行が {len(hits)} 件あります。"
        "両端で決めごと（暗号方式・鍵・相手の住所・対象の通信）が1つでも違うと張れません。",
        "両端の設定を1行ずつ突き合わせてください。"
        "特に、事前共有鍵と、暗号化する通信の範囲（対象のネットワーク）がずれていないかを見ます。",
        hits,
    )


def _clock_not_set(events: list[LogEvent]) -> Finding | None:
    """時計が合っていないと、この解析そのものの前提が崩れる。"""
    hits = [
        e
        for e in events
        if _has(e, "NTP")
        and _has(e, "unsynchronized", "not synchronized", "sync lost", "reset", "unreachable")
    ]
    if not hits:
        return None
    return _finding(
        MEDIUM,
        CONFIG,
        "機器の時計が合っていません",
        f"時刻合わせに失敗した行が {len(hits)} 件あります。"
        "時計がずれていると、複数の機器のログを並べても順番が分からず、"
        "ここで出している「直後に起きた」という判断も当てになりません。",
        "時刻の参照先に届いているか（経路と ACL）を確認してください。",
        hits,
    )


def _trouble_after_change(events: list[LogEvent]) -> Finding | None:
    """設定変更の直後に重い事象が出ている。原因の当たりとしては最初に見る所。"""
    changes = _pick(events, "CONFIG_I", "CONFIG_CHANGE", "SHELL_CMD", "configured from")
    changes = [e for e in changes if e.at is not None]
    if not changes:
        return None

    heavy = [e for e in events if e.at is not None and e.severity is not None and e.severity <= 3]
    hits: list[LogEvent] = []
    for change in changes:
        for event in heavy:
            if change.at < event.at <= change.at + _AFTER_CHANGE:
                hits.append(event)
    if not hits:
        return None

    return _finding(
        HIGH,
        CONFIG,
        "設定を変えた直後に、重い事象が出ています",
        f"設定変更のあと {int(_AFTER_CHANGE.total_seconds() // 60)} 分以内に "
        f"重い行が {len(hits)} 件出ています。変更が原因である可能性が高いところです。",
        "その変更の中身（差分）を見て、戻すかどうかを先に決めてください。"
        "戻して収まるなら、原因はその変更です。",
        hits,
    )


# ---------------------------------------------------------------------------
# 攻撃
# ---------------------------------------------------------------------------


def _login_attempts(events: list[LogEvent]) -> Finding | None:
    """ログイン失敗の連発。打ち間違いは数回で終わる。"""
    failed = [
        e
        for e in events
        if _has(
            e, "LOGIN_FAILED", "AUTHFAIL", "authentication failed", "Failed password", "LOGIN-4"
        )
    ]
    if len(failed) < _LOGIN_FAIL_MIN:
        return None

    sources: Counter[str] = Counter()
    for event in failed:
        for address in addresses_of(event):
            sources[address] += 1
    top = sources.most_common(3)
    where = "、".join(f"{address}（{count}回）" for address, count in top) or "送信元は読み取れず"

    succeeded = [
        e
        for e in events
        if _has(e, "LOGIN_SUCCESS", "LOGIN-5", "authentication success", "Accepted password")
    ]
    broken = False
    if succeeded and failed:
        last_fail = max((e.at for e in failed if e.at), default=None)
        first_ok_after = [
            e
            for e in succeeded
            if e.at and last_fail and abs((e.at - last_fail).total_seconds()) < 600
        ]
        broken = bool(first_ok_after)

    if broken:
        return _finding(
            HIGH,
            ATTACK,
            "総当たりのあとにログインが成功しています",
            f"失敗が {len(failed)} 件（{where}）続いたあと、10分以内に成功しています。"
            "破られた可能性があります。",
            "そのアカウントを止め、機器に入られた前提で設定の差分を確認してください。"
            "入口（管理の口）を、決まった場所からだけに絞るのも同時に行います。",
            failed[-_EVIDENCE:] + first_ok_after[:1],
        )

    return _finding(
        HIGH,
        ATTACK,
        "ログインの失敗が続いています",
        f"失敗が {len(failed)} 件あります（{where}）。"
        "人の打ち間違いはこの回数になりません。総当たりの可能性があります。",
        "その送信元からの管理の口を閉じてください（ACL で絞る）。"
        "合言葉を使い回している機器があれば、あわせて変えます。",
        failed,
    )


def _denied_a_lot(events: list[LogEvent]) -> Finding | None:
    """止めた通信が同じ相手から大量に来ている。探られている形。"""
    denied = [
        e
        for e in events
        if _has(e, "IPACCESSLOG", "denied", "deny", "DENY", "blocked", "%ASA-4-106023")
    ]
    if len(denied) < _DENY_MIN:
        return None

    sources: Counter[str] = Counter()
    ports: dict[str, set[str]] = defaultdict(set)
    for event in denied:
        addresses = addresses_of(event)
        if not addresses:
            continue
        source = addresses[0]
        sources[source] += 1
        for port in _PORT_IN_PAREN.findall(event.message or event.raw):
            ports[source].add(port)

    if not sources:
        return None
    worst, count = sources.most_common(1)[0]
    if count < _DENY_MIN:
        return None

    from_worst = [e for e in denied if worst in (e.message or e.raw)]
    scanning = len(ports.get(worst, set())) >= _SCAN_PORTS
    title = "同じ相手から、止めた通信が大量に来ています"
    why = f"{worst} からの遮断が {count} 件あります。" + (
        f"宛先の口が {len(ports[worst])} 種類に散っており、探り（スキャン）の形です。"
        if scanning
        else "宛先の口は絞られています。設定漏れで止めているだけの可能性もあります。"
    )
    return _finding(
        HIGH if scanning else MEDIUM,
        ATTACK,
        title,
        why,
        "その送信元が社内なら、まず端末を特定してください（乗っ取られている可能性）。"
        "社外なら、手前の機器で落としているかを確認します。"
        "業務で必要な通信を止めていないかも、あわせて見ます。",
        from_worst or denied,
    )


def _mac_moving(events: list[LogEvent]) -> Finding | None:
    hits = _pick(events, "MACFLAP", "MAC_MOVE", "MACMOVE", "mac flapping")
    if not hits:
        return None
    return _finding(
        HIGH,
        ATTACK,
        "同じ機器が、複数のポートに現れています",
        f"移動を示す行が {len(hits)} 件あります。"
        "線が輪になっているか、誰かが同じ番号（MAC）を名乗っている可能性があります。",
        "そのポートの先を確認してください。輪であれば L2 の問題、"
        "そうでなければ、なりすましを疑います。",
        hits,
    )


def _rogue_dhcp(events: list[LogEvent]) -> Finding | None:
    hits = [
        e
        for e in events
        if _has(e, "DHCP_SNOOPING", "DHCPSNOOP") and _has(e, "DENY", "drop", "untrusted", "rogue")
    ]
    if not hits:
        return None
    return _finding(
        HIGH,
        ATTACK,
        "許していない機器が、住所を配ろうとしています",
        f"止めた行が {len(hits)} 件あります。"
        "持ち込まれた機器が DHCP を配ると、その島の端末がまとめて通信できなくなります。",
        "そのポートの先にある機器を外してください。"
        "持ち込みが続く場所なら、ポートセキュリティもあわせて入れます。",
        hits,
    )


#: 「許していない端末が挿された」の言い方。Comware / VRP は PORTSEC_VIOLATION
_PORT_SECURITY_WORDS = (
    "PSECURE_VIOLATION",
    "PORT_SECURITY",
    "SECURITY_VIOLATION",
    "PORTSEC_VIOLATION",
    "PORTSEC/",
    "MACSEC_VIOLATION",
)


def _port_security(events: list[LogEvent]) -> Finding | None:
    hits = _pick(events, *_PORT_SECURITY_WORDS)
    if not hits:
        return None
    return _finding(
        MEDIUM,
        ATTACK,
        "許していない端末が挿されています",
        f"違反の行が {len(hits)} 件あります。"
        "機器の入れ替えで解除を忘れているだけのこともあります。",
        "そのポートに何が挿さっているかを確認してください。"
        "入れ替えたのであれば、許す番号（MAC）を更新します。",
        hits,
    )


def _attack_reported_by_device(events: list[LogEvent]) -> Finding | None:
    """機器自身が「攻撃」と言っている行。読み替えずにそのまま上げる。"""
    hits = [
        e
        for e in events
        if _has(e, "attack", "anomaly", "ips", "intrusion", "flood", "spoof", "733100")
        # ポートに挿された、はもっと具体的な言い方ができる。同じ行を2つの
        # 見方で出すと、どちらを見ればよいのか分からなくなる
        and not _has(e, *_PORT_SECURITY_WORDS)
    ]
    if not hits:
        return None
    return _finding(
        HIGH,
        ATTACK,
        "機器が攻撃として記録しています",
        f"攻撃・異常として記録された行が {len(hits)} 件あります。"
        "機器自身の判断なので、内容はその機器の定義に依ります。",
        "その行の宛先が、外へ出している機器かどうかを先に見てください。"
        "出していないのに届いているなら、手前の機器で止まっていない道があります。",
        hits,
    )
