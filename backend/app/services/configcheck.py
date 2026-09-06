"""渡された Config を、台帳に入れずにその場で見る。

いまの検算は、取り込んで機器に紐づけてからでないと動かない。だが実際には
「これ、どこか変じゃないか」を先に知りたい場面のほうが多い。試験用に書いた
もの、業者から届いたもの、自分で書いてみたもの。台帳に入れる前に見たい。

ここでやるのは、機器に紐づかなくてもできる検算である。あわせて、覚えた
現場の流儀と違うところも出す。「間違い」ではないが、その現場で揃っている
ものと違えば、たいてい理由があるか、抜けているかのどちらかである。

台帳には何も書かない。見るだけの口が書き込むと、確かめるつもりが増やす
ことになる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device
from app.services import configparse, l3review, policyreview
from app.services import conventions as conventions_module
from app.services.configparse import ParsedConfig, Vendor
from app.services.l3review import HIGH, LOW, MEDIUM, Finding


@dataclass
class CheckResult:
    filename: str
    vendor: str
    vendor_label: str
    hostname: str | None
    model: str | None
    os_version: str | None
    interfaces: int = 0
    vlans: int = 0
    routes: int = 0
    features: list[dict] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    #: 覚えた流儀と違うところ。間違いとは言い切れないので分けて出す
    differences: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


#: 機器に紐づかなくても言えること。どれも「入っていないと後で困る」もの
_HYGIENE = (
    (
        re.compile(r"^\s*switchport mode dynamic\s+(?:auto|desirable)\s*$", re.IGNORECASE),
        HIGH,
        "相手任せでトランクになる口があります",
        "つないだ相手と話し合ってトランクになる設定です。相手が仕掛けた機器だと、"
        "その口から全部の VLAN が見えます。使う形を決め打ちしてください"
        "（switchport mode access もしくは trunk）。",
    ),
    (
        re.compile(r"^\s*no login\s*$", re.IGNORECASE),
        HIGH,
        "機器に入る口で、認証を求めていません",
        "line の下に no login があります。その口からは合言葉なしで入れます。"
        "書き換えの途中で残ったものが、そのまま運用に入ることがあります。",
    ),
    (
        re.compile(
            r"^\s*(?:ip http server\s*$"  # Cisco IOS
            r"|set system services web-management http\b"  # Junos（https は別）
            r"|set allowaccess\b.*\bhttp\b(?!s))",  # FortiGate
            re.IGNORECASE,
        ),
        HIGH,
        "管理用の web が開いています",
        "暗号化されない口が開いたままです。使っていないなら閉じてください"
        "（no ip http server）。踏み台にされたときに、いちばん気づきにくい入口です。",
    ),
    (
        re.compile(
            r"^\s*(?:transport input (?:all|telnet)\b"  # Cisco IOS
            r"|set system services telnet\b"  # Junos
            r"|set allowaccess\b.*\btelnet\b"  # FortiGate
            r"|telnetd service on\b"  # ヤマハ
            r"|service telnet\s*$"  # アライドテレシス（no service telnet は止めた形）
            r"|telnet server enable\b"  # Comware
            r"|protocol inbound (?:all|telnet)\b"  # Comware / VRP の vty
            r"|telnet server\s*$"  # FastIron（no telnet server は止めた形）
            r"|telnet\s+\d+\.\d+\.\d+\.\d+\s+\d+\.\d+\.\d+\.\d+\s+\S+)",  # ASA
            re.IGNORECASE,
        ),
        HIGH,
        "telnet で入れる状態です",
        "telnet は中身が丸見えで、パスワードもそのまま流れます。"
        "ssh だけに絞ってください（transport input ssh）。",
    ),
    (
        re.compile(
            # アラクサラは値を引用符で囲う（`snmp-server community "public" ro`）
            r"^\s*(?:snmp-server community|set snmp community"
            r"|snmp-agent community\s+(?:read|write))\s+\"?(public|private)\b",
            re.IGNORECASE,
        ),
        HIGH,
        "SNMP の合言葉が既定のままです",
        "public / private は誰でも知っている値です。機器の中身が読めてしまいます。",
    ),
    (
        re.compile(r"^\s*enable password\b", re.IGNORECASE),
        MEDIUM,
        "パスワードが読める形で入っています",
        "enable password は元に戻せる形で保存されます。enable secret を使ってください。",
    ),
    (
        re.compile(
            r"^\s*(?:snmp-server community\s+\"?\S+?\"?\s+rw\b"
            r"|set snmp community\s+\S+\s+authorization\s+read-write\b"
            r"|snmp-agent community\s+write\b)",
            re.IGNORECASE,
        ),
        HIGH,
        "SNMP から設定を書き換えられます",
        "RW は読むだけでなく書き込みも許す指定です。合言葉を知られた時点で、"
        "設定を変えられます。監視で要るのは読むほうだけなので、RO で足ります。",
    ),
    (
        re.compile(r"^\s*ip ssh version 1\s*$", re.IGNORECASE),
        HIGH,
        "SSH が古い方式のままです",
        "version 1 は解読の方法が知られています。version 2 にしてください。",
    ),
    (
        re.compile(r"^\s*no service password-encryption\s*$", re.IGNORECASE),
        MEDIUM,
        "設定の中のパスワードが、そのまま見える状態です",
        "Config を渡す・保管する場面で、そのまま合言葉が読めます。"
        "service password-encryption を入れてください（強くはありませんが、素見えは防げます）。",
    ),
    (
        re.compile(r"^\s*exec-timeout 0 0\s*$", re.IGNORECASE),
        MEDIUM,
        "入ったままの画面が、いつまでも閉じません",
        "席を離れても操作できる状態が残ります。"
        "作業の途中で放置された端末が、そのまま入口になります。",
    ),
    (
        re.compile(
            r"^\s*(?:username\s+\S+\s+password\s+"  # Cisco IOS（secret なら言わない）
            r"|(?:set\s+authentication\s+)?password\s+simple\s)",  # Comware（cipher なら言わない）
            re.IGNORECASE,
        ),
        MEDIUM,
        "利用者のパスワードが、元に戻せる形で入っています",
        "username ... secret を使うと、元に戻せない形で保存されます。",
    ),
    (
        re.compile(r"^\s*ip source-route\s*$", re.IGNORECASE),
        LOW,
        "送る側が道順を指定できる状態です",
        "本来この機器が決める道順を、荷物の側から指定できます。"
        "使う用途はほとんど無いので、閉じておくのが既定です（no ip source-route）。",
    ),
)


def check(db: Session, text: str, *, filename: str = "config.txt") -> CheckResult:
    """その場で見る。台帳には何も書かない。"""
    parsed = configparse.parse(text)
    result = CheckResult(
        filename=filename,
        vendor=str(parsed.vendor),
        vendor_label=configparse.VENDOR_LABELS.get(parsed.vendor, "判別できません"),
        hostname=parsed.hostname,
        model=parsed.model,
        os_version=parsed.os_version,
        interfaces=len(parsed.interfaces),
        vlans=len(parsed.vlans),
        routes=len(parsed.routes),
        features=[
            {
                "category_label": configparse.FEATURE_LABELS.get(
                    feature.category, feature.category
                ),
                "name": feature.name,
                "detail": feature.detail,
                "count": feature.count,
            }
            for feature in parsed.features
        ],
    )

    if parsed.vendor is Vendor.UNKNOWN:
        result.notes.append(
            "メーカーを判別できませんでした。読めた範囲だけで見ています。"
            "show version の出力も一緒に渡すと、もう少し読めます。"
        )

    # 流儀との違いを先に出す。同じことを2度言わないよう、こちらで出した分は
    # 単体の検算から外す（現場の送り先まで書けるこちらのほうが直しやすい）
    result.differences = _differences(db, parsed, text)
    covered = {f.title.split("が")[0] for f in result.differences}
    result.findings = _findings(parsed, text, covered=covered)
    if not result.findings and not result.differences:
        result.notes.append(
            "見た範囲では、気になるところはありませんでした。"
            "ここで見ているのは Config から読み取れることだけで、"
            "実機の状態や業務の要件は見ていません。"
        )
    return result


def _findings(parsed: ParsedConfig, text: str, *, covered: set[str] | None = None) -> list[Finding]:
    """機器に紐づかなくてもできる検算。

    台帳を引く必要があるもの（対向機器と突き合わせる、向き先が台帳にいるか）
    はここでは動かない。1つの Config だけで言えることに絞る。
    """
    local = l3review._local_networks(parsed)
    found: list[Finding] = []
    found.extend(l3review._no_default_route(parsed))
    found.extend(l3review._same_destination_twice(parsed))
    found.extend(l3review._segments_nobody_advertises(parsed, local))
    found.extend(l3review._unused_policies(parsed))
    found.extend(policyreview.review(parsed, text))
    found.extend(_hygiene(parsed, text))
    found.extend(_operability(parsed, covered or set(), text))
    found.extend(_layer2(parsed))
    found.extend(_bundles(parsed))
    found.extend(_blocked_ports(parsed))
    found.extend(_broken_lines(parsed))
    found.extend(_stp(text))
    found.extend(_port_admission(text))
    return _sorted(found)


def _hygiene(parsed: ParsedConfig, text: str) -> list[Finding]:
    """1つの Config だけで言えること。どれも「後で困る」もの。"""
    out: list[Finding] = []
    lines = [line.rstrip() for line in text.splitlines()]
    for pattern, level, title, why in _HYGIENE:
        hits = [line for line in lines if pattern.match(line)]
        if hits:
            out.append(Finding(level=level, title=title, why=why, evidence=hits[:3]))

    described = [i for i in parsed.interfaces if i.description]
    # 口だけを貼った抜粋で「1つも書かれていません」と言うと、機器の話に
    # 聞こえる。設定ぜんぶを渡されたと言えるときに限る
    if parsed.interfaces and not described and _has_configuration(text):
        out.append(
            Finding(
                level=LOW,
                title="ポートに説明が1つも書かれていません",
                why="どのポートが何につながっているかが、Config だけでは分かりません。"
                "障害のときに、抜いてよい線かどうかを判断できません。",
                evidence=[f"読み取れたポート {len(parsed.interfaces)} 個すべてに description なし"],
            )
        )

    unnumbered = [i for i in parsed.interfaces if i.ip_address and not i.enabled]
    if unnumbered:
        out.append(
            Finding(
                level=MEDIUM,
                title="アドレスが振ってあるのに止めてあるポートがあります",
                why="設定だけ残して閉じてある状態です。戻し忘れなら、"
                "そのセグメントは動いていません。意図して閉じてあるなら問題ありません。",
                evidence=[f"{i.name}: {i.ip_address}" for i in unnumbered[:5]],
            )
        )
    return out


#: 入っていないと、後から追えなくなるもの。単体の Config だけで言える
_MUST_SEND = (
    (
        "Syslog",
        "ログの送り先",
        "この機器の記録は再起動で消えます。障害の後から、何が起きていたのかを追えません。",
    ),
    (
        "NTP",
        "時刻の参照先",
        "時計がずれたまま記録が残ります。複数の機器の記録を"
        "並べても順番が分からず、原因と結果が逆に見えます。",
    ),
)


#: 設定ぜんぶを渡された、と言える手掛かり。機器の名前を決める行は、
#: running-config なら必ず頭のほうに出る。口の設定だけを貼ったものには
#: 出てこないので、抜粋を「ぜんぶ」と取り違えない
_CONFIGURATION = re.compile(
    r"^\s*(?:hostname\s+\S|sysname\s+\S|device-name\s+\S"
    r"|set\s+system\s+host-name\s+\S|console\s+prompt\s+\S"
    r"|set\s+deviceconfig\s+system\s+hostname\s+\S"
    r"|config\s+system\s+\S)",
    re.IGNORECASE | re.MULTILINE,
)


def _has_configuration(text: str) -> bool:
    """設定そのものを渡されたか。show の出力だけかもしれない。

    渡されていないのに「入っていません」と言うと、書いてあるのに指摘される。
    無いのと、そもそも見ていないのは別のことなので、混ぜない。
    """
    return _CONFIGURATION.search(text) is not None


def _operability(parsed: ParsedConfig, covered: set[str], text: str = "") -> list[Finding]:
    """止まってからでは間に合わないもの。

    どれも動作には影響しないので、入っていなくても誰も困らない。
    困るのは障害が起きた日で、そのときに入れても手遅れになる。
    """
    out: list[Finding] = []
    names = {feature.name for feature in parsed.features}
    seen_config = _has_configuration(text) if text else True
    for name, label, why in _MUST_SEND:
        if name in names or label in covered or not seen_config:
            continue
        out.append(
            Finding(
                level=MEDIUM,
                title=f"{label}が入っていません",
                why=why,
                evidence=[f"{name} の設定が読み取れませんでした"],
            )
        )

    half_fixed = [
        i for i in parsed.interfaces if (i.speed and not i.duplex) or (i.duplex and not i.speed)
    ]
    if half_fixed:
        out.append(
            Finding(
                level=LOW,
                title="速度と二重のうち、片方だけ固定してあるポートがあります",
                why="片方だけ固定すると、相手との相談が成立せず遅いほうに落ちます。"
                "通ってはいるので気づかれず、遅いという申告だけが残ります。",
                evidence=[
                    f"{i.name}: speed={i.speed or '自動'} duplex={i.duplex or '自動'}"
                    for i in half_fixed[:5]
                ],
            )
        )
    return out


def _bundles(parsed: ParsedConfig) -> list[Finding]:
    """束ねた口が、実際に束に入っているか。

    設定だけ見ると束ねてあるように見えても、速度や相手側の設定が合わないと
    入らない。入っていない口は帯域に数えられていないので、「倍あるはず」が
    出ていないまま動き続ける。落ちるわけではないので、気づかれにくい。
    """
    outside = [
        port
        for port in parsed.interfaces
        if port.lag_group is not None and port.lag_bundled is False
    ]
    if not outside:
        return []
    return [
        Finding(
            level=MEDIUM,
            title="束ねた口のうち、実際には束に入っていないものがあります",
            why="設定は束ねてありますが、機器の側では束に入っていません。"
            "速度や相手側の設定が合っていないと、こうなります。落ちるわけでは"
            "ないので気づかれませんが、帯域はその分だけ出ていません。",
            evidence=[f"{port.name}（束 {port.lag_group}）" for port in outside[:5]],
        )
    ]


def _blocked_ports(parsed: ParsedConfig) -> list[Finding]:
    """STP が塞いでいる口。

    冗長のために塞いでいるなら正常。ただし「線はつながっているのに通らない」の
    原因はここであることが多く、設定を見ても分からない。分かる形で出しておく。
    """
    blocked = [port for port in parsed.interfaces if port.stp_blocked_vlans]
    if not blocked:
        return []
    return [
        Finding(
            level=LOW,
            title="STP が塞いでいる口があります",
            why="輪になっている経路を止めるために塞いでいるので、冗長の構成なら"
            "正常です。片方の線しか使っていないように見えるのはこのためです。"
            "意図していない口が塞がっている場合は、優先度の設計を見直してください。",
            evidence=[
                f"{port.name}: VLAN {', '.join(str(v) for v in port.stp_blocked_vlans[:6])}"
                for port in blocked[:5]
            ],
        )
    ]


def _broken_lines(parsed: ParsedConfig) -> list[Finding]:
    """受けたときに壊れていた数（CRC）。

    線・光・二重の食い違いでしか増えない。Config からは絶対に分からないので、
    show の出力があるときだけ言える。数え始めからの累計なので、いつからかは
    分からない。数字をそのまま出して、確かめ方を添える。
    """
    broken = [
        port
        for port in parsed.interfaces
        if port.input_crc_errors is not None and port.input_crc_errors > 0
    ]
    if not broken:
        return []
    broken.sort(key=lambda port: port.input_crc_errors or 0, reverse=True)
    return [
        Finding(
            level=MEDIUM,
            title="受けたときに壊れていた通信がある口があります",
            why="線・光の劣化か、両端の二重（duplex）の食い違いでしか増えません。"
            "通信はできてしまうので、遅い・たまに切れるという形で出ます。"
            "数え始めからの累計なので、一度 0 に戻してから増え方を見てください。",
            evidence=[f"{port.name}: {port.input_crc_errors} 件" for port in broken[:5]],
        )
    ]


def _layer2(parsed: ParsedConfig) -> list[Finding]:
    """VLAN の切り方で、あとから効いてくるもの。

    どれも「間違い」ではないため機器は何も言わない。分けたはずのものが
    つながっている、という形で後から出る。
    """
    out: list[Finding] = []
    trunks = [i for i in parsed.interfaces if i.mode == "trunk"]

    default_native = [i for i in trunks if (i.native_vlan or 1) == 1]
    if default_native:
        out.append(
            Finding(
                level=MEDIUM,
                title="トランクのタグ無し VLAN が 1 のままです",
                why="1 は初期値なので、どの機器でも同じ番号です。"
                "つないだ相手が違う意図で 1 を使っていると、分けたはずのものが1つにつながります。"
                "使っていない番号を決めて、両端で同じにしてください。",
                evidence=[
                    f"{i.name}: native vlan {i.native_vlan or 1}" for i in default_native[:5]
                ],
            )
        )

    wide_open = [i for i in trunks if i.trunk_all and not i.trunk_excluded]
    if wide_open:
        out.append(
            Finding(
                level=LOW,
                title="許可 VLAN を絞っていないトランクがあります",
                why="定義したものが全部流れます。要らない VLAN まで先の機器へ広がり、"
                "1か所で起きたことが遠くまで届きます。",
                evidence=[f"{i.name}: 許可 VLAN の指定なし" for i in wide_open[:5]],
            )
        )

    used: set[int] = set()
    for i in parsed.interfaces:
        used |= i.carried_vlans()
        if i.name.lower().startswith("vlan"):
            digits = "".join(c for c in i.name if c.isdigit())
            if digits:
                used.add(int(digits))
    # 逆向き。ポートで使っているのに、定義が無い VLAN。Cisco はこの状態の
    # アクセスポートを inactive にして、線はつながっているのに通らなくなる。
    # 定義を1つも読めていない Config（ルーターの抜粋など）では言わない
    if parsed.vlans:
        undefined: dict[int, list[str]] = {}
        for i in parsed.interfaces:
            for vlan in sorted(i.carried_vlans()):
                if vlan not in parsed.vlans and vlan != 1:
                    undefined.setdefault(vlan, []).append(i.name)
        if undefined:
            out.append(
                Finding(
                    level=HIGH,
                    title="ポートで使っているのに、定義が無い VLAN があります",
                    why="定義していない番号をアクセスポートに当てると、ポートは"
                    "つながっているのに通りません。トランクの許可に入れていても、"
                    "その番号の通信は落ちます。`vlan 番号` を定義してください。",
                    evidence=[
                        f"VLAN {vlan}: {', '.join(names[:3])}"
                        for vlan, names in sorted(undefined.items())
                    ][:5],
                )
            )

    # 口が1つも見えていないなら、使っているかどうかは分からない
    idle = sorted(v for v in parsed.vlans if v not in used and v != 1)
    if idle and parsed.interfaces:
        out.append(
            Finding(
                level=LOW,
                title="定義してあるだけで、どこにも使っていない VLAN があります",
                why="消し忘れなら消せます。これから使う予定のものなら問題ありません。"
                "残したままだと、番号が空いているのか使われているのかが分からなくなります。",
                evidence=[f"VLAN {', '.join(str(v) for v in idle[:10])}"],
            )
        )
    return out


#: 端末をつなぐ口（つないだ瞬間に通す設定）。機種ごとの言い方
_EDGE_PORT = re.compile(
    r"^(?:spanning-tree\s+portfast(?:\s+trunk)?\s*$"
    r"|stp\s+edged-port(?:\s+enable)?\s*$"  # Comware / VRP
    r"|spanning-tree\s+802-1w\s+admin-edge-port\s*$"  # FastIron
    r"|spanning-tree\s+port-type\s+admin-edge\s*$)",  # Aruba CX
    re.IGNORECASE,
)
#: 誰かがスイッチを挿したときに、その口だけ落とす仕組み
_BPDU_GUARD = re.compile(r"bpdu[-_ ]?guard|bpdu[-_ ]?protection", re.IGNORECASE)
#: 機種によっては1行で全ポートに掛ける。口ごとに書いていなくても効いている
_GLOBAL_BPDU_GUARD = re.compile(
    r"^\s*(?:stp\s+bpdu-protection"
    r"|spanning-tree\s+portfast\s+bpduguard\s+default"
    r"|spanning-tree\s+bpdu-guard\s*$)",
    re.IGNORECASE | re.MULTILINE,
)


def _stp(text: str) -> list[Finding]:
    """ループを止める仕組み（STP）まわりで、後から効いてくるもの。

    STP は「入っていれば動く」ものではない。端末をつなぐ口に、誰かが
    スイッチを挿したときにどうなるかまで決めて、はじめて意味がある。
    ここで見るのは、その決め忘れである。

    ルートをどこに置くかは、1台の Config だけでは言えない（相手側の
    優先度が分からない）ので、ここでは見ない。
    """
    out: list[Finding] = []
    ports = _interface_blocks(text)

    # 端末をつなぐ口の言い方は機種で違う。1つの書き方だけを見ていると、
    # 他社の機器では黙って通る
    guarded_here = _GLOBAL_BPDU_GUARD.search(text) is not None
    fast_without_guard = [
        name
        for name, body in ports
        if any(_EDGE_PORT.match(line) for line in body)
        and not guarded_here
        and not any(_BPDU_GUARD.search(line) for line in body)
    ]
    if fast_without_guard:
        out.append(
            Finding(
                level=MEDIUM,
                title="端末用の口に、BPDU guard が入っていません",
                why="portfast はつないだ瞬間に通す設定です。そこへ誰かがスイッチを"
                "挿すと、輪ができて網全体が止まります。BPDU guard を入れておけば、"
                "その口だけが落ちて済みます。",
                evidence=[
                    f"{name}: spanning-tree portfast のみ" for name in fast_without_guard[:5]
                ],
            )
        )

    fast_on_trunk = [
        name
        for name, body in ports
        if any(line.lower().startswith("switchport mode trunk") for line in body)
        and any(re.match(r"^spanning-tree portfast\s*$", line, re.IGNORECASE) for line in body)
    ]
    if fast_on_trunk:
        out.append(
            Finding(
                level=MEDIUM,
                title="スイッチをつなぐ口に portfast が入っています",
                why="機器どうしをつなぐ口で待ち時間を飛ばすと、輪ができていても"
                "そのまま通してしまいます。トランクに入れるなら "
                "portfast trunk（意図した指定）にしてください。",
                evidence=[
                    f"{name}: trunk なのに spanning-tree portfast" for name in fast_on_trunk[:5]
                ],
            )
        )

    lines = [line.strip() for line in text.splitlines()]
    if any(line.lower().startswith("spanning-tree ") for line in lines) and not any(
        line.lower().startswith("spanning-tree mode ") for line in lines
    ):
        out.append(
            Finding(
                level=LOW,
                title="STP のモードを決めていません",
                why="既定のまま動きます。相手の機器が別のモードだと、"
                "収束の速さも VLAN ごとの木の作られ方も揃いません。"
                "現場で1つに決めて、そろえてください。",
                evidence=["spanning-tree mode の行なし"],
            )
        )
    return out


def _port_admission(text: str) -> list[Finding]:
    """口に誰を入れるか（802.1X / ポートセキュリティ）の決め忘れ。

    入れてあるだけでは効かない。問い合わせ先が無い、破ったときに何もしない、
    仕組みは入れたのにどの口にも当てていない。どれも「入っている」という
    見た目になるので、入っているつもりで残る。
    """
    out: list[Finding] = []
    lines = [line.strip() for line in text.splitlines()]
    lowered = [line.lower() for line in lines]
    ports = _interface_blocks(text)

    controlled = [
        name
        for name, body in ports
        if any("port-control auto" in line.lower() for line in body)
        or any(line.lower().startswith("dot1x port-control auto") for line in body)
        # Aruba CX（口の下に aaa authentication port-access dot1x authenticator）と
        # Comware（口の下に dot1x だけ）
        or any(line.lower().startswith("aaa authentication port-access dot1x") for line in body)
        or any(line.lower() == "dot1x" for line in body)
        # VRP は口の下に `dot1x enable` と書く
        or any(line.lower() == "dot1x enable" for line in body)
    ]
    # Junos は口ごとの塊が無いので、行そのものを数える
    controlled += [
        line.split()[4]
        for line in lowered
        if line.startswith("set protocols dot1x authenticator interface ") and len(line.split()) > 4
    ]
    has_server = any(
        line.startswith(
            (
                "radius-server ",
                "radius server",
                "aaa group server radius",
                "set access radius-server",  # Junos
                "radius scheme",  # Comware
                "radius-server template",  # VRP
                "hwtacacs-server",  # VRP
            )
        )
        for line in lowered
    )
    if controlled and not has_server:
        out.append(
            Finding(
                level=HIGH,
                title="802.1X を当てていますが、問い合わせ先がありません",
                why="本人確認を RADIUS に頼む設定なのに、その相手が書かれていません。"
                "そのままだと、その口につないだ端末は誰も通れません。",
                evidence=[f"{name}: port-control auto" for name in controlled[:5]],
            )
        )

    dot1x_on = any(
        line.startswith(
            (
                "dot1x system-auth-control",
                "aaa authentication dot1x",
                "aaa authentication port-access dot1x",  # Aruba CX（全体）
                "set protocols dot1x",  # Junos
            )
        )
        or line == "dot1x"  # Comware（全体で有効にする行）
        for line in lowered
    )
    if dot1x_on and not controlled:
        out.append(
            Finding(
                level=MEDIUM,
                title="802.1X を入れてありますが、どの口にも当てていません",
                why="仕組みだけ有効になっていて、実際には誰の確認もしていません。"
                "入っているように見えるので、確認しているつもりのまま残ります。",
                evidence=["dot1x の全体設定あり / port-control auto のポートなし"],
            )
        )

    quiet_security = [
        name
        for name, body in ports
        if any(line.lower().startswith("switchport port-security") for line in body)
        and not any("violation" in line.lower() for line in body)
    ]
    if quiet_security:
        out.append(
            Finding(
                level=LOW,
                title="ポートセキュリティの、破ったときの動きを決めていません",
                why="既定では口ごと落ちます（shutdown）。意図しているなら問題ありませんが、"
                "決めていないと、端末を入れ替えただけで口が落ちて、"
                "現場では原因の分からない不通になります。",
                evidence=[f"{name}: violation の指定なし" for name in quiet_security[:5]],
            )
        )

    protect_only = [
        name for name, body in ports if any("violation protect" in line.lower() for line in body)
    ]
    if protect_only:
        out.append(
            Finding(
                level=LOW,
                title="ポートセキュリティが、黙って落とす設定になっています",
                why="protect は、超えた分を捨てるだけで記録も残りません。"
                "つながらないという申告だけが来て、機器側には何の跡もありません。"
                "restrict なら記録が残ります。",
                evidence=[f"{name}: violation protect" for name in protect_only[:5]],
            )
        )
    return out


def _interface_blocks(text: str) -> list[tuple[str, list[str]]]:
    """`interface X` ごとに、その中の行を集める。"""
    out: list[tuple[str, list[str]]] = []
    name: str | None = None
    body: list[str] = []
    for raw in text.splitlines():
        head = re.match(r"^interface\s+(\S+)", raw, re.IGNORECASE)
        if head:
            if name is not None:
                out.append((name, body))
            name, body = head.group(1), []
            continue
        if name is None:
            continue
        if raw and not raw.startswith((" ", "\t")):
            out.append((name, body))
            name, body = None, []
            continue
        body.append(raw.strip())
    if name is not None:
        out.append((name, body))
    return out


def _baseline_for(
    db: Session, parsed: ParsedConfig, house: conventions_module.Conventions
) -> list[str]:
    """この機器と比べてよい決まり文句。

    台帳に同じ種類の機器が足りないときは、何が決まり文句なのかを決められない。
    そこで別の種類の決まり文句を当てると、誤って「抜けている」と言う。
    分からないときは、何も言わないほうがよい。
    """
    if not parsed.hostname:
        return house.baseline_lines
    device = db.scalar(select(Device).where(Device.hostname == parsed.hostname))
    if device is None:
        return house.baseline_lines
    same_kind = conventions_module.learn(db, kinds={device.device_type})
    if same_kind.devices_seen < 2:
        return []
    return same_kind.baseline_lines


def _differences(db: Session, parsed: ParsedConfig, text: str) -> list[Finding]:
    """覚えた現場の流儀と違うところ。

    間違いとは言い切れない。だが、その現場で揃っているものと違えば、
    たいてい理由があるか、抜けているかのどちらかである。
    """
    house = conventions_module.learn(db)
    if house.devices_seen < 2:
        return []

    out: list[Finding] = []
    lines = {line.strip() for line in text.splitlines()}

    # 決まり文句は、同じ種類の機器どうしでしか比べられない。スイッチの
    # `spanning-tree mode` を無線コントローラに当てると、入っていないのが
    # 当たり前の行を「抜けている」と言うことになる
    baseline = _baseline_for(db, parsed, house)
    missing = [line for line in baseline if line not in lines]
    if missing:
        out.append(
            Finding(
                level=LOW,
                title="この現場では入っているのに、入っていない行があります",
                why=f"取り込んである {house.devices_seen} 台には入っている行です。"
                "抜けているなら足りず、意図して外しているなら、その理由が"
                "この Config からは読めません。",
                evidence=missing[:5],
            )
        )

    if house.vlan_step and parsed.vlans:
        numbers = sorted(v for v in parsed.vlans if 1 < v < 1000)
        gaps = {b - a for a, b in zip(numbers, numbers[1:], strict=False) if b > a}
        if gaps and house.vlan_step not in gaps:
            out.append(
                Finding(
                    level=LOW,
                    title="VLAN の採番が、この現場の刻みと違います",
                    why=f"この現場は {house.vlan_step} 刻みで採番しています。"
                    "刻みが違うと、番号から用途が読めなくなります。",
                    evidence=[f"この Config の VLAN: {', '.join(str(n) for n in numbers[:8])}"],
                )
            )

    for hosts, label, name in (
        (house.syslog_hosts, "ログの送り先", "Syslog"),
        (house.ntp_hosts, "時刻の参照先", "NTP"),
    ):
        if not hosts:
            continue
        found = next((f for f in parsed.features if f.name == name), None)
        here = conventions_module._values(found.detail) if found and found.detail else []
        if not here:
            out.append(
                Finding(
                    level=MEDIUM,
                    title=f"{label}が入っていません",
                    why=f"この現場では {' / '.join(hosts)} に送っています。"
                    "入っていないと、この機器だけ記録が残りません。",
                    evidence=[f"この現場の {label}: {' / '.join(hosts)}"],
                )
            )
        elif set(here) != set(hosts):
            out.append(
                Finding(
                    level=LOW,
                    title=f"{label}が、この現場のものと違います",
                    why="別の送り先を向いています。移行の途中ならそのままで構いませんが、"
                    "書き換え忘れなら、この機器だけ別のところへ送り続けます。",
                    evidence=[
                        f"この Config: {' / '.join(here)}",
                        f"この現場: {' / '.join(hosts)}",
                    ],
                )
            )
    return _sorted(out)


def _sorted(found: list[Finding]) -> list[Finding]:
    weight = {HIGH: 0, MEDIUM: 1, LOW: 2}
    return sorted(found, key=lambda f: weight.get(f.level, 3))
