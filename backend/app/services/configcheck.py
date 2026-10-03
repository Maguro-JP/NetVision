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
from app.services import configparse, defaults, l3review, policyreview
from app.services import conventions as conventions_module
from app.services.configparse import ParsedConfig, Vendor, banners, inputtext
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
            r"|telnet server enable\b"  # NEC QX
            r"|protocol inbound (?:all|telnet)\b"  # NEC QX / VRP の vty
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
            r"|(?:set\s+authentication\s+)?password\s+simple\s)",  # NEC QX（cipher なら言わない）
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
    # 行を直に見る検算も、読み手と同じ整え方をしてから見る。
    # 文言（banner）の中は設定として見ない（注意書きに設定例を書いてある機器は多い）
    text = banners.strip(inputtext.clean(text))
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
    found.extend(_stp(text, parsed.vendor))
    found.extend(_port_admission(text))
    found.extend(_hsrp_version(text))
    found.extend(_etherchannel(text))
    found.extend(_dhcp_snooping(text))
    found.extend(_arp_inspection(text))
    found.extend(_vtp_overwrite(text))
    found.extend(_access_on_vlan1(text))
    found.extend(_negotiable_ports(text, parsed.vendor))
    found.extend(_ssh_version(text))
    found.extend(_nxos_features(parsed, text))
    found.extend(_password_types(parsed, text))
    found.extend(_junos_plain_password(text))
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


#: Cisco で、番号として使えない帯。1002〜1005 は Token Ring / FDDI に取ってあり、
#: 消すことも使うこともできない。1〜4094 の外も番号にならない
#: （出典: VLAN Configuration Guide, Cisco IOS XE 26.x.x（Catalyst 9500）
#: 「Configuring VLANs」: VLANs are identified by a number from 1 to 4094.
#: VLAN IDs 1002 through 1005 are reserved for Token Ring and FDDI VLANs.
#: All of the VLANs except 1002 to 1005 are available for user configuration）
_CISCO_VENDORS = {Vendor.CISCO_IOS, Vendor.CISCO_NXOS, Vendor.CISCO_SB}


def _vlan_numbers(parsed: ParsedConfig) -> list[Finding]:
    """使えない番号を VLAN に当てている。

    他社では使える帯なので、Cisco の書き方として読めたときだけ言う。
    """
    if parsed.vendor not in _CISCO_VENDORS and parsed.vendor is not Vendor.CISCO_NXOS:
        return []

    def unusable(vlan: int) -> bool:
        if vlan < 1 or vlan > 4094:
            return True
        # NX-OS は 3968〜4047 を装置の中で使う（出典: Cisco Nexus 9000 Series
        # NX-OS Interfaces Configuration Guide「Configuring the Allowed VLANs
        # for Trunking Ports」: VLANs 3968 to 4047 are the default VLANs
        # reserved for internal use by default）
        if parsed.vendor is Vendor.CISCO_NXOS:
            return 3968 <= vlan <= 4047
        return 1002 <= vlan <= 1005

    seen: dict[int, list[str]] = {}
    for interface in parsed.interfaces:
        for vlan in sorted(interface.carried_vlans()):
            if unusable(vlan):
                seen.setdefault(vlan, []).append(interface.name)
    for vlan in sorted(parsed.vlans):
        if unusable(vlan):
            seen.setdefault(vlan, []).append("vlan の定義")
    if not seen:
        return []
    return [
        Finding(
            level=HIGH,
            title="使えない番号を VLAN に当てています",
            why="この機種で使えるのは 1〜4094 で、そのうち取ってある帯"
            "（IOS は 1002〜1005 の古い媒体用、NX-OS は 3968〜4047 の装置内部用）は"
            "当てても通りません。"
            "他社から移してきた設計で、そのまま番号が残っていることがあります。",
            evidence=[
                f"VLAN {vlan}: {', '.join(names[:3])}" for vlan, names in sorted(seen.items())
            ][:5],
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

    # タグ無しの VLAN を、許可の一覧から外しているトランク。タグ無しの通信だけが落ちる
    # （出典: VLAN Configuration Guide, Cisco IOS XE 26.x.x（Catalyst 9300）
    # 「Configuring VLAN Trunks」: A trunk port can become a member of a VLAN
    # if the VLAN is enabled, if VTP knows of the VLAN, and if the VLAN is in
    # the allowed list for the port）
    # AlliedWare Plus は、既定のタグ無し VLAN（1）を許可の一覧に入れなくても一員になる。
    # 既定から変えるときだけ、先に許可しておく必要がある（出典: Allied Telesis「VLAN Feature
    # Overview and Configuration Guide」C613-22001-00 REV P の Setting native VLAN in trunk mode:
    # By default, a Trunk mode switch port's native VLAN … is VLAN 1. … If required, you can change
    # the native VLAN from the default. The new native VLAN must already be allowed for this
    # switch port.）
    native_dropped = [
        i
        for i in trunks
        if i.native_vlan
        and i.trunk_vlans
        and not i.trunk_all
        and i.native_vlan not in i.trunk_vlans
        and not (parsed.vendor is Vendor.ALLIED and i.native_vlan == 1)
    ]
    if native_dropped:
        out.append(
            Finding(
                level=HIGH,
                title="タグ無しの VLAN を、許可の一覧から外しています",
                why="その口はタグ無しの VLAN の一員になれないので、タグ無しで来た通信だけが"
                "落ちます。タグ付きは通るので、一部だけつながらないという形で出ます。"
                "許可の一覧にその番号を足すか、タグ無しの番号を変えてください。",
                evidence=[
                    f"{i.name}: native vlan {i.native_vlan} / 許可 "
                    + ",".join(str(v) for v in sorted(i.trunk_vlans)[:6])
                    for i in native_dropped[:5]
                ],
            )
        )

    # 電話用と端末用に同じ番号を当てている口。公式が「同時に同じ番号にするな」と
    # 書いている（出典: Security Configuration Guide, Cisco IOS XE 17.16.x
    # （Catalyst 9200）「Configuring IEEE 802.1x Port-Based Authentication」:
    # Do not configure the same VLAN ID for both voice VLAN and access VLAN at
    # the same time, because it may cause authentication failures）
    voice_same = [
        i for i in parsed.interfaces if i.voice_vlan and i.vlan and i.voice_vlan == i.vlan
    ]
    if voice_same:
        out.append(
            Finding(
                level=MEDIUM,
                title="電話用と端末用に、同じ VLAN を当てています",
                why="同じ番号にすると、電話と端末を分ける意味が無くなるうえ、"
                "本人確認（802.1X）が通らなくなることがあります。"
                "電話用は別の番号にしてください。",
                evidence=[
                    f"{i.name}: access vlan {i.vlan} / voice vlan {i.voice_vlan}"
                    for i in voice_same[:5]
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

    out.extend(_vlan_numbers(parsed))

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
    #: IOS-XE は `portfast edge [trunk]` とも書く（出典: Command Reference, Cisco IOS XE
    #: 17.16.x（Catalyst 9300）「spanning-tree portfast edge [trunk]」）
    r"^(?:spanning-tree\s+portfast(?:\s+edge)?(?:\s+trunk)?\s*$"
    r"|stp\s+edged-port(?:\s+enable)?\s*$"  # NEC QX / VRP
    r"|spanning-tree\s+802-1w\s+admin-edge-port\s*$"  # FastIron
    r"|spanning-tree\s+port-type\s+admin-edge\s*$)",  # Aruba CX
    re.IGNORECASE,
)
#: 全体でトランク以外の口を端末口にする書き方
_PORTFAST_DEFAULT = re.compile(
    r"^spanning-tree\s+portfast(?:\s+edge)?\s+default\s*$", re.IGNORECASE | re.MULTILINE
)
#: 誰かがスイッチを挿したときに、その口だけ落とす仕組み
_BPDU_GUARD = re.compile(r"bpdu[-_ ]?guard|bpdu[-_ ]?protection", re.IGNORECASE)
#: 機種によっては1行で全ポートに掛ける。口ごとに書いていなくても効いている
#: 行頭（インデント無し）だけを見る。口の中に書く `spanning-tree bpdu-guard`
#: （Aruba CX）まで拾うと、1口守っただけで機器全体が守られていることになり、
#: 守っていない口を見逃す
_GLOBAL_BPDU_GUARD = re.compile(
    r"^(?:stp\s+bpdu-protection"
    #: 同じく `portfast edge bpduguard default`（出典: 同「spanning-tree portfast edge
    #: bpduguard default」）
    r"|spanning-tree\s+portfast(?:\s+edge)?\s+bpduguard\s+default"
    r"|spanning-tree\s+bpdu-guard\s*$)",
    re.IGNORECASE | re.MULTILINE,
)


#: Junos の端末口（`set protocols rstp interface ge-0/0/1 edge`）
#: 出典: Junos OS CLI Reference「edge (edit protocols stp)」
_JUNOS_EDGE = re.compile(
    r"^set\s+protocols\s+(?:rstp|mstp|vstp)\s+interface\s+(\S+)\s+edge\s*$",
    re.IGNORECASE | re.MULTILINE,
)
#: 全部の端末口をまとめて守る書き方
#: 出典: Junos OS CLI Reference「bpdu-block-on-edge」
_JUNOS_EDGE_GUARD = re.compile(
    r"^set\s+protocols\s+(?:rstp|mstp|vstp)\s+bpdu-block-on-edge\s*$",
    re.IGNORECASE | re.MULTILINE,
)
#: 口ごとに守る書き方
#: 出典: Junos OS「BPDU Protection for Spanning-Tree Protocols」
_JUNOS_BPDU_BLOCK = re.compile(
    r"^set\s+protocols\s+layer2-control\s+bpdu-block\s+interface\s+(\S+)",
    re.IGNORECASE | re.MULTILINE,
)


def _junos_edge_without_guard(text: str) -> list[Finding]:
    """Junos で、端末口にしてあるのに BPDU から守っていないものを言う。

    端末口はつないだ瞬間に通す口なので、そこへスイッチを挿されると輪ができる。
    `bpdu-block-on-edge`（まとめて）か `layer2-control bpdu-block`（口ごと）の
    どちらかが要る。

    出典: Junos OS「BPDU Protection for Spanning-Tree Protocols」
    （"you must protect edge ports by enabling bpdu-block-on-edge"）。
    読むのは `set` 形だけ。囲みの形はここでは見ていない
    """
    if _JUNOS_EDGE_GUARD.search(text):
        return []
    blocked = {name.split(".")[0] for name in _JUNOS_BPDU_BLOCK.findall(text)}
    naked = [name for name in _JUNOS_EDGE.findall(text) if name.split(".")[0] not in blocked]
    if not naked:
        return []
    return [
        Finding(
            level=MEDIUM,
            title="端末用の口に、BPDU から守る設定が入っていません",
            why="端末用（edge）の口はつないだ瞬間に通します。そこへ誰かがスイッチを"
            "挿すと、輪ができて網全体が止まります。"
            "bpdu-block-on-edge を入れておけば、その口だけが落ちて済みます。",
            evidence=[f"{name}: edge のみ" for name in naked[:5]],
        )
    ]


#: Junos の、値まで書いてある平文の合言葉。
#: 機器に入れた時点で暗号化されるので（出典: Junos OS CLI Reference「user (Access)」
#: "The plain text password will be automatically encrypted as soon as you
#: configure it."）、この形が残っているのは投入前の設定文のほう。
#: その文を誰かに渡すと、合言葉がそのまま渡る
_JUNOS_PLAIN_PASSWORD = re.compile(
    r"^set\s+system\s+(?:root-authentication"
    r"|login\s+user\s+\S+\s+authentication)\s+plain-text-password\s+\S",
    re.IGNORECASE,
)


def _junos_plain_password(text: str) -> list[Finding]:
    """Junos の設定文に、合言葉が字のまま書かれているのを言う。

    機器の running config には出ない形なので、これが見えているのは
    投入用の設定文。渡した相手にそのまま合言葉が渡る。

    証跡には値を出さない。どの行かが分かる形だけ残す。
    """
    heads = [
        " ".join(line.strip().split()[:4])
        for line in text.splitlines()
        if _JUNOS_PLAIN_PASSWORD.match(line.strip())
    ]
    if not heads:
        return []
    return [
        Finding(
            level=HIGH,
            title="設定文に、合言葉が字のまま書かれています",
            why="Junos は入れた時点で暗号化するので、この形が残っているのは"
            "投入前の設定文です。その文を渡すと合言葉もそのまま渡ります。"
            "渡す前に消すか、暗号化した形（encrypted-password）に替えてください。",
            evidence=heads[:3],
        )
    ]


#: `spanning-tree mode` の書き方が無い機種。STP の種類は別の書き方で選ぶ
#: （FastIron は `spanning-tree`・`spanning-tree single`・`spanning-tree 802-1w`。出典: Ruckus
#: 「FastIron 08.0.60 Layer 2 Switching Configuration Guide」の「Enabling or disabling the
#: Spanning Tree Protocol (STP)」）
_NO_STP_MODE_COMMAND = frozenset({Vendor.FASTIRON})


def _stp(text: str, vendor: Vendor | None = None) -> list[Finding]:
    """ループを止める仕組み（STP）まわりで、後から効いてくるもの。

    STP は「入っていれば動く」ものではない。端末をつなぐ口に、誰かが
    スイッチを挿したときにどうなるかまで決めて、はじめて意味がある。
    ここで見るのは、その決め忘れである。

    ルートをどこに置くかは、1台の Config だけでは言えない（相手側の
    優先度が分からない）ので、ここでは見ない。
    """
    out: list[Finding] = []
    out.extend(_junos_edge_without_guard(text))
    ports = _interface_blocks(text)

    # 端末をつなぐ口の言い方は機種で違う。1つの書き方だけを見ていると、
    # 他社の機器では黙って通る
    guarded_here = _GLOBAL_BPDU_GUARD.search(text) is not None
    # 全体で `spanning-tree portfast [edge] default` を入れると、トランクでない口は
    # 口ごとに書かなくても端末口になる。アクセスと書いた口はここで拾う
    # （出典: Layer 2 Configuration Guide, Cisco IOS XE 17.16.x（Catalyst 9300）
    # 「Enabling PortFast」: use the spanning-tree portfast default global
    # configuration command to globally enable the PortFast feature on all
    # nontrunking ports）
    fast_by_default = _PORTFAST_DEFAULT.search(text) is not None
    fast_without_guard = [
        name
        for name, body in ports
        if (
            any(_EDGE_PORT.match(line) for line in body)
            or (
                fast_by_default
                and any(re.match(r"^switchport mode access\b", line, re.I) for line in body)
                and not any(re.match(r"^no spanning-tree portfast\b", line, re.I) for line in body)
            )
        )
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
                    # 端末口の書き方は機種で違うので、証跡では書き方を決め打ちしない
                    f"{name}: 端末用の口の指定のみ"
                    for name in fast_without_guard[:5]
                ],
            )
        )

    fast_on_trunk = [
        name
        for name, body in ports
        if any(line.lower().startswith("switchport mode trunk") for line in body)
        and any(
            re.match(r"^spanning-tree portfast(?:\s+edge)?\s*$", line, re.IGNORECASE)
            for line in body
        )
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

    # 口ごとの BPDU フィルタは、その口の STP を止めるのと同じ
    # （出典: Layer 2 Configuration Guide, Cisco IOS XE 17.18.x（Catalyst 9400）
    # 「Bridge Protocol Data Unit Filtering」: Enabling BPDU filtering on an
    # interface is the same as disabling spanning tree on it and can result in
    # spanning-tree loops。Nexus 9000 の同節も同じ注意を書いている）
    filtered: list[tuple[str, bool]] = []
    for name, body in ports:
        if not any(
            re.match(r"^spanning-tree\s+bpdufilter\s+enable\b", line.strip(), re.IGNORECASE)
            for line in body
        ):
            continue
        uplink = any(line.lower().strip().startswith("switchport mode trunk") for line in body)
        filtered.append((name, uplink))
    if filtered:
        out.append(
            Finding(
                level=HIGH if any(uplink for _, uplink in filtered) else MEDIUM,
                title="BPDU フィルタを口ごとに入れています（その口の STP が止まります）",
                why="この口は BPDU を送りも受けもしません。相手が機器だと、輪ができても"
                "誰も気づかないまま通し続けます。端末だけをつなぐ口でなければ外して、"
                "BPDU guard（挿されたら落とす）に替えてください。",
                evidence=[
                    f"{name}: spanning-tree bpdufilter enable" + ("（trunk の口）" if up else "")
                    for name, up in filtered[:5]
                ],
            )
        )

    # loop guard は portfast の口には効かない（出典: Catalyst 6500
    # 「Configuring Optional STP Features」Loop Guard の使用上の注意:
    # You cannot enable loop guard on PortFast-enabled ports）
    loop_on_edge = [
        name
        for name, body in ports
        if any(_EDGE_PORT.match(line.strip()) for line in body)
        and any(
            re.match(r"^spanning-tree\s+guard\s+loop\b", line.strip(), re.IGNORECASE)
            for line in body
        )
    ]
    if loop_on_edge:
        out.append(
            Finding(
                level=LOW,
                title="端末用の口に loop guard を入れています（効きません）",
                why="loop guard は、根へ向かう口と塞いである口で効く仕組みです。"
                "つないだ瞬間に通す口では働きません。入れたつもりのまま残ります。",
                evidence=[f"{name}: portfast と guard loop の同居" for name in loop_on_edge[:5]],
            )
        )

    lines = [line.strip() for line in text.splitlines()]
    if (
        vendor not in _NO_STP_MODE_COMMAND
        and any(line.lower().startswith("spanning-tree ") for line in lines)
        and not any(line.lower().startswith("spanning-tree mode ") for line in lines)
    ):
        out.append(
            Finding(
                level=LOW,
                title="STP のモードを決めていません",
                why="既定のまま動きます。相手の機器が別のモードだと、"
                "収束の速さも VLAN ごとの木の作られ方も揃いません。"
                "現場で1つに決めて、そろえてください。",
                evidence=["spanning-tree mode の行なし"],
                # 既定のモードは機種と版で違いうる。資料で確かめた機種だけ出典を付ける
                sources=[source]
                if (source := defaults.stp_mode_source(vendor.value if vendor else None))
                else [],
            )
        )
    return out


#: 束ねた線の口で、そろっていないと束にならない書き方
#: （出典: Layer 2 Configuration Guide, Cisco IOS XE 26.x.x（Catalyst 9500）
#: 「EtherChannel Configuration Guidelines」「Layer 2 EtherChannel
#: Configuration Guidelines」: 同じ VLAN かトランク、ネイティブ VLAN が違うと
#: 束にならない、許可 VLAN の範囲が同じでないと束にならない、
#: on と active/desirable を混ぜない）
_CHANNEL_KEYS: tuple[tuple[str, str], ...] = (
    (r"^switchport\s+mode\s+(\S+)", "口の形"),
    (r"^switchport\s+access\s+vlan\s+(\S+)", "access VLAN"),
    (r"^switchport\s+trunk\s+native\s+vlan\s+(\S+)", "ネイティブ VLAN"),
    (r"^switchport\s+trunk\s+allowed\s+vlan\s+(?!add\b)(\S+)", "許可 VLAN"),
    (r"^switchport\s+trunk\s+encapsulation\s+(\S+)", "トランクの型"),
    (r"^channel-group\s+\d+\s+mode\s+(\S+)", "束ね方"),
)


def _etherchannel(text: str) -> list[Finding]:
    """同じ束（channel-group）の口で、そろえるべきものが食い違っている。

    公式の制限で、そろっていない口は束に入らない。1本だけ抜けた状態で動くので、
    帯域が出ない・片寄るという形で出て、落ちるわけではないから気づきにくい。

    出典: Layer 2 Configuration Guide, Cisco IOS XE 26.x.x（Catalyst 9500）
    「EtherChannel Configuration Guidelines」および「Layer 2 EtherChannel
    Configuration Guidelines」
    """
    groups: dict[str, dict[str, dict[str, list[str]]]] = {}
    for name, body in _interface_blocks(text):
        found = next(
            (
                match
                for line in body
                if (match := re.match(r"^channel-group\s+(\d+)\b", line.strip(), re.IGNORECASE))
            ),
            None,
        )
        if found is None:
            continue
        values = groups.setdefault(found.group(1), {})
        for pattern, label in _CHANNEL_KEYS:
            value = next(
                (
                    hit.group(1).lower()
                    for line in body
                    if (hit := re.match(pattern, line.strip(), re.IGNORECASE))
                ),
                "（書いていない）",
            )
            values.setdefault(label, {}).setdefault(value, []).append(name)

    out: list[Finding] = []
    for number, values in sorted(groups.items(), key=lambda item: int(item[0])):
        evidence = [
            f"{label}: "
            + " / ".join(f"{value} → {', '.join(ports)}" for value, ports in seen.items())
            for label, seen in values.items()
            if len(seen) > 1
        ]
        if not evidence:
            continue
        out.append(
            Finding(
                level=HIGH,
                title=f"束ね {number} の口で、そろえるべき設定が食い違っています",
                why="この機種は、口の形・VLAN・束ね方がそろっていない口を束に入れません。"
                "入らなかった口の分だけ帯域が出ず、落ちるわけではないので気づきにくい"
                "形で残ります。",
                evidence=evidence[:5],
            )
        )
    return out


def _dhcp_snooping(text: str) -> list[Finding]:
    """DHCP の見張り（snooping）が、入れただけで効いていない。

    全体で有効にしても、帯（VLAN）を指定するまで働かない。また、口は既定で
    「信用しない」なので、本物の DHCP サーバーへ向かう口を信用する設定に
    しないと、配られるはずのアドレスまで落とす。どちらも「入れてあるのに
    アドレスが取れない／守れていない」という形で出る。

    出典: Catalyst 3560 Switch Software Configuration Guide, Cisco IOS
    Release 15.0(2)SE「Configuring DHCP Features and IP Source Guard」
    （You must globally enable DHCP snooping on the switch. / DHCP snooping is
    not active until DHCP snooping is enabled on a VLAN. / For DHCP snooping to
    function properly, all DHCP servers must be connected to the switch through
    trusted interfaces）
    """
    out: list[Finding] = []
    lines = [line.strip() for line in text.splitlines()]
    global_on = any(re.match(r"^ip dhcp snooping\s*$", line, re.IGNORECASE) for line in lines)
    if not global_on:
        return out
    on_vlan = any(re.match(r"^ip dhcp snooping vlan\b", line, re.IGNORECASE) for line in lines)
    if not on_vlan:
        out.append(
            Finding(
                level=MEDIUM,
                title="DHCP の見張りを、どの VLAN にも掛けていません",
                why="全体で有効にしても、帯（VLAN）を指定するまで働きません。"
                "入れたつもりで、偽の DHCP サーバーを止められない状態です。",
                evidence=["ip dhcp snooping はあるが ip dhcp snooping vlan が無い"],
            )
        )
        return out
    trusted = [
        name
        for name, body in _interface_blocks(text)
        if any(re.match(r"^ip dhcp snooping trust\b", line.strip(), re.IGNORECASE) for line in body)
    ]
    if not trusted:
        out.append(
            Finding(
                level=HIGH,
                title="DHCP の見張りで、信用する口を1つも決めていません",
                why="口は既定で「信用しない」です。本物の DHCP サーバーへ向かう口"
                "（上位へのトランクを含む）を信用する設定にしないと、"
                "配られるはずのアドレスまで落ちて、端末がアドレスを取れません。",
                evidence=["ip dhcp snooping vlan はあるが ip dhcp snooping trust が無い"],
                sources=[defaults.DHCP_SNOOPING_UNTRUSTED_SOURCE],
            )
        )
        return out

    out.extend(_dhcp_snooping_option82(lines))
    return out


def _dhcp_snooping_option82(lines: list[str]) -> list[Finding]:
    """見張りを入れた L2 のスイッチが、上位で落とされる形。

    option 82 の挿入は既定で有効。自分でリレー（`ip helper-address`）を
    していない L2 のスイッチがこれを付けると、中の giaddr は 0.0.0.0 の
    ままになる。上位の機器から見ると「リレーでないのに option 82 が付いて
    いる」ので、その口を信用していなければ落とす。端末がアドレスを
    取れない形で出る。

    上位側が信用する口にしてあれば通るので、これだけで壊れると決めつけない。
    上位の設定はこの1台の資料からは読めないので、確認してほしいものとして出す。

    出典:
    - Catalyst 6500 Supervisor Engine 2T Software Configuration Guide
      「Default Configuration for DHCP Snooping」（DHCP snooping information
      option: Enabled / DHCP option-82 on untrusted port feature: Disabled）
    - Catalyst 4500 Software Configuration Guide「Configuring DHCP Snooping」
      （If the downstream switch ... adds DHCP information Option 82 to the
      DHCP packets, the DHCP packets would be dropped on arriving on a
      snooping untrusted port）
    """
    if any(re.match(r"^no ip dhcp snooping information option\b", line, re.I) for line in lines):
        return []
    if any(re.match(r"^\s*ip helper-address\b", line, re.IGNORECASE) for line in lines):
        return []
    return [
        Finding(
            level=MEDIUM,
            title="見張りを入れた口から、上位で落とされる形の DHCP が出ます",
            why="option 82 の挿入は既定で有効です。この機器は自分でリレー"
            "（ip helper-address）をしていないので、付けた option 82 の中の"
            "giaddr は 0.0.0.0 のままになります。上位がその口を信用していなければ"
            "落とすので、端末がアドレスを取れません。上位を信用する口にするか、"
            "この機器で no ip dhcp snooping information option を書くかの"
            "どちらかにしてください。",
            evidence=[
                "ip dhcp snooping vlan はあるが、no ip dhcp snooping information option も "
                "ip helper-address も無い"
            ],
            sources=[defaults.DHCP_OPTION82_SOURCE],
        )
    ]


_VTP_VERSION = re.compile(r"^\s*VTP version running\s*:\s*(\d)", re.IGNORECASE | re.MULTILINE)
_VTP_DOMAIN = re.compile(r"^\s*VTP Domain Name\s*:[ \t]*(\S*)", re.IGNORECASE | re.MULTILINE)
_VTP_MODE = re.compile(r"^\s*VTP Operating Mode\s*:\s*(\w+)", re.IGNORECASE | re.MULTILINE)
_VTP_REVISION = re.compile(r"^\s*Configuration Revision\s*:\s*(\d+)", re.IGNORECASE | re.MULTILINE)


def _vtp_overwrite(text: str) -> list[Finding]:
    """VTP の版 1・2 で、つなぐと VLAN を上書きしうる状態。

    同じドメインでは、構成リビジョンのいちばん大きい機器の VLAN が全体に
    効く。版 1・2 では、リビジョンの大きい機器をつなぐと、ドメイン全体の
    VLAN が消えることがある。サーバー／クライアントのままリビジョンが
    0 でない機器は、他所へつなぐ前にリビジョンを戻す必要がある。

    running-config には出ない（出るのは transparent のときだけ）ので、
    `show vtp status` が貼られているときだけ見る。

    出典:
    - VLAN Configuration Guide, Cisco IOS XE 17.15.x（Catalyst 9300）
      「Configuring VTP」の Caution（With VTP versions 1 and 2, adding a device
      that has a revision number higher than the revision number in the VTP
      domain can erase all VLAN information … With VTP version 3, the VLAN
      information is not erased）
    - Command Reference, Cisco IOS XE 16.5.x（Catalyst 9300）「show vtp」の出力例
    """
    version = _VTP_VERSION.search(text)
    mode = _VTP_MODE.search(text)
    revision = _VTP_REVISION.search(text)
    domain = _VTP_DOMAIN.search(text)
    if not (version and mode and revision and domain):
        return []
    if version.group(1) not in ("1", "2"):
        return []
    if mode.group(1).lower() not in ("server", "client"):
        return []
    if not domain.group(1) or int(revision.group(1)) == 0:
        return []
    return [
        Finding(
            level=MEDIUM,
            title="VTP の版 1・2 で、つなぐと VLAN を上書きしうる状態です",
            why="同じ VTP ドメインでは、構成リビジョンのいちばん大きい機器の VLAN が"
            "全体に効きます。版 1・2 では、この機器を他の場所へつなぐと、ドメイン全体の"
            "VLAN が消えることがあります。VTP を使わないなら transparent（または off）に、"
            "使うなら版 3 にするか、つなぐ前にリビジョンを 0 に戻してください。",
            evidence=[
                f"VTP version running: {version.group(1)} / Operating Mode: {mode.group(1)}"
                f" / Domain: {domain.group(1)} / Configuration Revision: {revision.group(1)}"
            ],
        )
    ]


def _access_on_vlan1(text: str) -> list[Finding]:
    """アクセスの口が、VLAN を書かずに既定の VLAN 1 に乗っている。

    VLAN 1 は管理用の通信（CDP・VTP など）も流れる帯で、端末を置くと
    分けたつもりの帯が混ざる。書き忘れで VLAN 1 に入っている口を言う。
    止めてある口（shutdown）は、使っていないので言わない。

    出典: Cisco IOS XE 17 Interface Characteristics Configuration Guide
    「Default Ethernet interface configuration」（Default VLAN (for access ports):
    VLAN 1）
    """
    loose = [
        name
        for name, body in _interface_blocks(text)
        if any(re.match(r"^switchport mode access\b", line, re.I) for line in body)
        and not any(re.match(r"^switchport access vlan\b", line, re.I) for line in body)
        and not any(re.match(r"^shutdown\s*$", line, re.I) for line in body)
    ]
    if not loose:
        return []
    return [
        Finding(
            level=LOW,
            title="VLAN を書いていないアクセスの口が、既定の VLAN 1 に乗っています",
            why="アクセスの口は、VLAN を書かなければ VLAN 1 に入ります。VLAN 1 は"
            "管理用の通信も流れる帯なので、端末をここに置くと、分けたつもりの帯が"
            "混ざります。意図した VLAN を switchport access vlan で書いてください。",
            evidence=[f"{name}: switchport access vlan が無い" for name in loose[:5]],
            sources=[defaults.ACCESS_VLAN_SOURCE],
        )
    ]


def _negotiable_ports(text: str, vendor: Vendor | None = Vendor.CISCO_IOS) -> list[Finding]:
    """VLAN は書いたのに mode を書いておらず、相手次第でトランクになる口。

    Catalyst（IOS / IOS-XE）の L2 の口は、mode を書かなければ dynamic auto で、
    相手がトランクを求めるとトランクになる。`switchport access vlan` だけ書いて
    アクセスのつもりでいると、挿された機器次第で全 VLAN が流れ込む。

    NX-OS は既定がアクセスで DTP を使わないので、NX-OS の資料では言わない。
    Arista EOS も既定は static access で、switchport mode に動的な形が無い（EOS User Manual
    「Virtual LANs (VLANs)」）。既定の dynamic auto を確かめたのは Catalyst（IOS）だけなので、
    IOS の資料でだけ言う。

    出典: Cisco IOS XE 17 Interface Characteristics Configuration Guide
    「Default Ethernet interface configuration」（VLAN trunking: Switchport mode
    dynamic auto (supports DTP)）
    """
    if vendor is not Vendor.CISCO_IOS:
        return []
    if re.search(r"\bNX-OS\b|^\s*feature\s+\S+", text, re.IGNORECASE | re.MULTILINE):
        return []
    loose = [
        name
        for name, body in _interface_blocks(text)
        if any(re.match(r"^switchport access vlan\b", line, re.I) for line in body)
        and not any(re.match(r"^switchport mode\b", line, re.I) for line in body)
        and not any(re.match(r"^no switchport\s*$", line, re.I) for line in body)
        # 止めてある口は相手と交渉しないので言わない
        and not any(re.match(r"^shutdown\s*$", line, re.I) for line in body)
    ]
    if not loose:
        return []
    return [
        Finding(
            level=MEDIUM,
            title="VLAN だけ書いて mode を書いていない口が、相手次第でトランクになります",
            why="mode を書かない L2 の口は dynamic auto で、相手がトランクを求めると"
            "トランクになります。アクセスのつもりでも、挿された機器次第で全 VLAN が"
            "流れ込みます。switchport mode access を書いてください。",
            evidence=[f"{name}: switchport access vlan はあるが mode が無い" for name in loose[:5]],
            sources=[defaults.DTP_DYNAMIC_AUTO_SOURCE],
        )
    ]


def _ssh_version(text: str) -> list[Finding]:
    """IOS / IOS-XE の SSH が、版 1 でもつながる形。

    `ip ssh version` を書かなければ互換モード（1.99）で、版 1 の接続も受ける。
    版 1 は規格として定まっていないので、版 2 に固定するよう公式が勧めている。
    SSH を使っている資料（`transport input` に ssh があるか `ip ssh` の行が
    ある）だけを見る。NX-OS は別の書き方なので言わない。

    出典: Security and VPN Configuration Guide, Cisco IOS XE 17.x
    「Secure Shell Version 2 Support」（If you do not configure this command, SSH
    by default runs in compatibility mode … If you do not want your device to
    fall back to the undefined protocol (Version 1), you should use the ip ssh
    version command and specify Version 2）
    """
    if re.search(r"\bNX-OS\b|^\s*feature\s+\S+", text, re.IGNORECASE | re.MULTILINE):
        return []
    uses_ssh = re.search(
        r"^\s*transport input\b.*\bssh\b|^ip ssh\b", text, re.IGNORECASE | re.MULTILINE
    )
    if not uses_ssh:
        return []
    if re.search(r"^ip ssh version 2\s*$", text, re.IGNORECASE | re.MULTILINE):
        return []
    forced_v1 = re.search(r"^ip ssh version 1\s*$", text, re.IGNORECASE | re.MULTILINE)
    return [
        Finding(
            level=MEDIUM if forced_v1 else LOW,
            title="SSH が版 1 でもつながる形です",
            why="ip ssh version を 2 に固定しないと、版 1 の接続も受けます"
            "（書かなければ 1.99 の互換モード）。版 1 は規格として定まっていないので、"
            "ip ssh version 2 を書いてください。",
            evidence=["ip ssh version 1 がある" if forced_v1 else "ip ssh version 2 が無い"],
        )
    ]


def _arp_inspection(text: str) -> list[Finding]:
    """ARP の見張り（dynamic ARP inspection）で、通信が落ちる形。

    口は既定で「信用しない」ので、上位へ向かう口を信用しないままだと、
    そこから来た ARP まで捨てて不通になる。また、判断のもとは DHCP の
    見張りが作る対応表なので、それが無いまま入れると、DHCP で配られた
    アドレスの端末が ARP を通せない。

    出典: Security Configuration Guide, Cisco IOS XE 17.17.x（Catalyst 9300）
    「Configuring Dynamic ARP Inspection」（By default, all interfaces are
    untrusted. / Use the trust state configuration carefully. Configuring
    interfaces as untrusted when they should be trusted can result in a loss of
    connectivity. / Dynamic ARP inspection depends on the entries in the DHCP
    snooping binding database … Make sure to enable DHCP snooping。
    DHCP を使わない場合は ARP ACL を当てる）
    """
    out: list[Finding] = []
    lines = [line.strip() for line in text.splitlines()]
    on_vlan = any(re.match(r"^ip arp inspection vlan\b", line, re.IGNORECASE) for line in lines)
    if not on_vlan:
        return out
    trusted = [
        name
        for name, body in _interface_blocks(text)
        if any(
            re.match(r"^ip arp inspection trust\b", line.strip(), re.IGNORECASE) for line in body
        )
    ]
    if not trusted:
        out.append(
            Finding(
                level=HIGH,
                title="ARP の見張りで、信用する口を1つも決めていません",
                why="口は既定で「信用しない」です。機器どうしをつなぐ口を信用しないままだと、"
                "そこから来た ARP まで捨てて不通になります。"
                "上位へ向かう口に ip arp inspection trust を入れてください。",
                evidence=["ip arp inspection vlan はあるが ip arp inspection trust が無い"],
                sources=[defaults.ARP_INSPECTION_UNTRUSTED_SOURCE],
            )
        )
    snooping = any(re.match(r"^ip dhcp snooping vlan\b", line, re.IGNORECASE) for line in lines)
    arp_acl = any(re.match(r"^ip arp inspection filter\b", line, re.IGNORECASE) for line in lines)
    if not snooping and not arp_acl:
        out.append(
            Finding(
                level=HIGH,
                title="ARP の見張りに、判断のもとがありません",
                why="この仕組みは、DHCP の見張りが作る対応表を見て良し悪しを決めます。"
                "その見張りも、代わりの ARP の許可一覧（ARP ACL）も無いと、"
                "DHCP でアドレスを取った端末の ARP が通らず、つながらなくなります。",
                evidence=[
                    "ip arp inspection vlan はあるが ip dhcp snooping vlan / "
                    "ip arp inspection filter が無い"
                ],
            )
        )
    return out


#: 合言葉の保存のしかた（Cisco の「タイプ」）。番号ごとに強さが違う
#: （出典: Cisco Trust Center「Protecting Secrets on Cisco Network Devices」の表:
#: 0 平文・4 弱い SHA256・5 MD5・7 Vigenère は安全でない、6 / 8 / 9 / 10 は安全。
#: NSA「Cisco Password Types: Best Practices」も 0・4・7 は使うな、5 は他が
#: 使えないときだけ、8 を推奨、としている）
_WEAK_PASSWORD_TYPES = {
    "0": (HIGH, "そのままの字（平文）で入っています"),
    "4": (HIGH, "弱い作り（type 4）で入っています。公式が撤回した方式です"),
    "5": (MEDIUM, "古い作り（type 5 / MD5）で入っています"),
    "7": (HIGH, "元に戻せる形（type 7）で入っています。誰でも戻せます"),
}
_CREDENTIAL_LINE = re.compile(
    r"^(?:username\s+\S+\s+(?:secret|password)|enable\s+(?:secret|password))\s+(\d+)\s+\S",
    re.IGNORECASE,
)


def _password_types(parsed: ParsedConfig, text: str) -> list[Finding]:
    """合言葉の保存のしかたが弱い。

    Config は人手で渡ることがあるので、弱い形で入っていると、その中身が
    そのまま鍵になる。特に type 7 は公開された鍵で戻せるだけで、守りにならない。

    出典: Cisco Trust Center「Protecting Secrets on Cisco Network Devices」
    （type 0 / 4 / 5 / 7 は Secure ではない、8 / 9 / 10 は Secure）、
    NSA「Cisco Password Types: Best Practices」（Type 8 を推奨、0・4・7 は使うな）
    """
    if parsed.vendor not in _CISCO_VENDORS and parsed.vendor is not Vendor.CISCO_NXOS:
        return []
    seen: dict[str, list[str]] = {}
    for line in text.splitlines():
        found = _CREDENTIAL_LINE.match(line.strip())
        if found and found.group(1) in _WEAK_PASSWORD_TYPES:
            # 合言葉そのものは持たない。どの行かが分かる形だけ残す
            head = " ".join(line.strip().split()[:3])
            seen.setdefault(found.group(1), []).append(head)
    out: list[Finding] = []
    for kind, heads in sorted(seen.items()):
        level, what = _WEAK_PASSWORD_TYPES[kind]
        out.append(
            Finding(
                level=level,
                title=f"合言葉が{what}",
                why="Config は人手で渡ることがあります。弱い形で入っていると、"
                "その中身がそのまま鍵になります。"
                "type 8（または 9）で入れ直してください"
                "（enable algorithm-type sha256 secret / "
                "username 名前 algorithm-type sha256 secret）。",
                evidence=[f"{head} …" for head in heads[:5]],
            )
        )
    return out


def _nxos_features(parsed: ParsedConfig, text: str) -> list[Finding]:
    """NX-OS で、先に有効にしていない機能を使っている。

    この OS は機能ごとに `feature …` で入れてから使う。入れていないと、
    その下の設定はそもそも作れない。他機種の設定をそのまま持ってきたときに
    起きる。

    出典: Cisco Nexus 9000 Series NX-OS Interfaces Configuration Guide,
    Release 10.6(x)「Configuring Layer 3 Interfaces」（Enable the VLAN network
    interface feature using the feature interface-vlan configuration。手順も
    feature interface-vlan → interface vlan number の順）
    """
    if parsed.vendor is not Vendor.CISCO_NXOS:
        return []
    lines = [line.strip() for line in text.splitlines()]
    if any(re.match(r"^feature interface-vlan\b", line, re.IGNORECASE) for line in lines):
        return []
    svis = [
        name
        for name, _ in _interface_blocks(text)
        if re.match(r"^vlan\d+$", name.replace(" ", ""), re.IGNORECASE)
    ]
    if not svis:
        return []
    return [
        Finding(
            level=HIGH,
            title="VLAN の口を作る機能を、先に有効にしていません",
            why="この OS は feature interface-vlan を入れてからでないと、"
            "VLAN の口（SVI）を作れません。入れ忘れると、貼った設定が"
            "そのまま入らず、その帯の出口ができません。",
            evidence=[f"interface {name}" for name in svis[:5]]
            + ["feature interface-vlan の行なし"],
        )
    ]


def _hsrp_version(text: str) -> list[Finding]:
    """HSRP の版と、グループ番号の食い違い。

    既定は版1で、そのときのグループ番号は 0〜255 までしか使えない。
    VLAN 番号に合わせて 300 番のような番号を書くと、版2を指定していない
    かぎり成り立たない。設定を貼った先で弾かれるか、その組だけ動かない。

    出典: Network Services Configuration Guide, Cisco IOS XE 17.x「HSRP
    Version 2」（In HSRP version 1, group numbers are restricted to the range
    from 0 to 255. HSRP version 2 expands the group number range from 0 to
    4095）、Cisco IOS First Hop Redundancy Protocols Command Reference の
    standby ip（The group number range is from 0 to 255 for HSRP version 1）
    """
    out: list[Finding] = []
    too_big: list[str] = []
    for name, body in _interface_blocks(text):
        version_two = any(
            re.match(r"^standby\s+version\s+2\b", line.strip(), re.IGNORECASE) for line in body
        )
        if version_two:
            continue
        for line in body:
            found = re.match(r"^standby\s+(\d+)\s+", line.strip(), re.IGNORECASE)
            if found and int(found.group(1)) > 255:
                too_big.append(f"{name}: standby {found.group(1)}（版の指定なし）")
                break
    if too_big:
        out.append(
            Finding(
                level=HIGH,
                title="HSRP のグループ番号が、版1で使える範囲を超えています",
                why="既定の版1で使えるグループ番号は 0〜255 までです。"
                "256 以上を使うなら、そのポートに standby version 2 が要ります。"
                "VLAN 番号に合わせて番号を振ったときに起きます。",
                evidence=too_big[:5],
                sources=[defaults.HSRP_VERSION_SOURCE],
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
        # NEC QX（口の下に dot1x だけ）
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
                "radius scheme",  # NEC QX
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

    # Cisco は口の設定だけでは動かない。全体で `dot1x system-auth-control` を
    # 入れて初めて効く（出典: 802.1X Authentication Services Configuration Guide,
    # Cisco IOS Release 15SY「Enabling 802.1X Authentication」手順 5、
    # および Catalyst 3750-X 設定ガイド「dot1x system-auth-control global
    # configuration command only globally enables or disables 802.1x」）。
    # 口の書き方が Cisco の形（authentication / dot1x / access-session
    # port-control auto）のときだけ見る
    cisco_controlled = [
        name
        for name, body in ports
        if any(
            re.match(
                r"^(?:authentication|dot1x|access-session)\s+port-control\s+auto\b",
                line.strip(),
                re.IGNORECASE,
            )
            for line in body
        )
    ]
    system_auth = any(line.startswith("dot1x system-auth-control") for line in lowered)
    if cisco_controlled and not system_auth:
        out.append(
            Finding(
                level=HIGH,
                title="802.1X を口に当てていますが、全体で有効にしていません",
                why="この機種は、口の設定だけでは本人確認をしません。"
                "全体に dot1x system-auth-control が要ります。"
                "設定は入っているのに、誰でもつなげる状態のまま残ります。",
                evidence=[f"{name}: port-control auto" for name in cisco_controlled[:5]]
                + ["dot1x system-auth-control の行なし"],
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
        or line == "dot1x"  # NEC QX（全体で有効にする行）
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

    # violation を書かなければ shutdown（口ごと落ちる）。出典: Security Configuration Guide,
    # Cisco IOS XE 17.16.x（Catalyst 9500）「Port Security」の Default Port Security
    # Configuration（Violation mode: Shutdown）
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
                sources=[defaults.PORT_SECURITY_VIOLATION_SOURCE],
            )
        )

    # 公式の制限。動的な口（DTP まかせ）には掛けられない
    # （出典: Security Configuration Guide, Cisco IOS XE 17.17.x
    # Catalyst 9300「Port Security Configuration Guidelines」：
    # Port security can only be configured on static access ports or trunk ports）
    dynamic_security = [
        name
        for name, body in ports
        if any(line.lower().startswith("switchport port-security") for line in body)
        and any(
            re.match(r"^switchport\s+mode\s+dynamic\b", line.strip(), re.IGNORECASE)
            for line in body
        )
    ]
    if dynamic_security:
        out.append(
            Finding(
                level=MEDIUM,
                title="ポートセキュリティを、動的な口に掛けています",
                why="この機種は、口の形が決まっていない（DTP まかせの）ところには"
                "ポートセキュリティを掛けられません。switchport mode access か "
                "trunk に決めてください。書いてあっても効きません。",
                evidence=[f"{name}: switchport mode dynamic" for name in dynamic_security[:5]],
            )
        )

    # 電話と PC を数珠つなぎにする口は、MAC が2つ出る。既定の1では口が落ちる
    # （出典: 同ガイドライン：When you enable port security on an interface that
    # is also configured with a voice VLAN, set the maximum allowed secure
    # addresses on the port to two）
    voice_one = []
    for name, body in ports:
        lowered_body = [line.lower().strip() for line in body]
        if not any(line.startswith("switchport port-security") for line in lowered_body):
            continue
        if not any(line.startswith("switchport voice vlan") for line in lowered_body):
            continue
        maximum = next(
            (
                line.split()[-1]
                for line in lowered_body
                if line.startswith("switchport port-security maximum")
            ),
            "1",
        )
        written = any(line.startswith("switchport port-security maximum") for line in lowered_body)
        if maximum.isdigit() and int(maximum) < 2:
            voice_one.append((name, maximum, written))
    if voice_one:
        out.append(
            Finding(
                level=MEDIUM,
                title="電話をつなぐ口で、覚えられる数が足りません",
                why="電話と、その先の PC で MAC が2つ出ます。覚えられる数が1のままだと、"
                "つないだ瞬間に口が落ちます。電話を置く口は2つ以上にしてください。",
                evidence=[
                    f"{name}: maximum {value}（voice vlan あり）"
                    for name, value, _written in voice_one[:5]
                ],
                # 上限を書いていない口は、既定の 1 で数えている
                sources=[defaults.PORT_SECURITY_MAXIMUM_SOURCE]
                if any(not written for _name, _value, written in voice_one)
                else [],
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
