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

from sqlalchemy.orm import Session

from app.services import configparse, l3review
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
        re.compile(r"^\s*ip http server\s*$", re.IGNORECASE),
        HIGH,
        "管理用の web が開いています",
        "暗号化されない口が開いたままです。使っていないなら閉じてください"
        "（no ip http server）。踏み台にされたときに、いちばん気づきにくい入口です。",
    ),
    (
        re.compile(r"^\s*transport input (?:all|telnet)\b", re.IGNORECASE),
        HIGH,
        "telnet で入れる状態です",
        "telnet は中身が丸見えで、パスワードもそのまま流れます。"
        "ssh だけに絞ってください（transport input ssh）。",
    ),
    (
        re.compile(r"^\s*snmp-server community\s+(public|private)\b", re.IGNORECASE),
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

    result.findings = _findings(parsed, text)
    result.differences = _differences(db, parsed, text)
    if not result.findings and not result.differences:
        result.notes.append(
            "見た範囲では、気になるところはありませんでした。"
            "ここで見ているのは Config から読み取れることだけで、"
            "実機の状態や業務の要件は見ていません。"
        )
    return result


def _findings(parsed: ParsedConfig, text: str) -> list[Finding]:
    """機器に紐づかなくてもできる検算。

    台帳を引く必要があるもの（対向機器と突き合わせる、向き先が台帳にいるか）
    はここでは動かない。1つの Config だけで言えることに絞る。
    """
    local = l3review._local_networks(parsed)
    found: list[Finding] = []
    found.extend(l3review._no_default_route(parsed))
    found.extend(l3review._unreachable_next_hops(parsed, local))
    found.extend(l3review._same_destination_twice(parsed))
    found.extend(l3review._segments_nobody_advertises(parsed, local))
    found.extend(l3review._unused_policies(parsed))
    found.extend(_hygiene(parsed, text))
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
    if parsed.interfaces and not described:
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

    missing = [line for line in house.baseline_lines if line not in lines]
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
