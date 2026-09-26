"""2台で1台に見せる仕組み（HA ペア）が、組として成り立っているかを見る。

片方の機器にしか入っていない、両方とも primary、組の名前が違う。どれも
1台の Config の中では正しいので、1台ずつ見ている限り見つからない。
しかも普段は片方だけで動いてしまうので、切り替わる日まで気づかない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device
from app.models.topology import ConfigSnapshot
from app.services.addition import latest_config_ids
from app.services.configparse import hapair
from app.services.configparse.hapair import KIND_LABELS, ParsedHaPair
from app.services.reviewnote import HIGH, LOW, MEDIUM, Finding


@dataclass
class Member:
    hostname: str
    pair: ParsedHaPair


@dataclass
class HaReport:
    devices_seen: int = 0
    #: 組ごとの機器名。鍵は「種類 / 組の名前」
    groups: dict[str, list[str]] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


#: 1台の中で完結する仕組み。読んで一覧には出すが、機器をまたいで比べない。
#: 比べると、別々のスタックが「同じ組」に見えて、優先度がぶつかっていると言う
_WITHIN_ONE_DEVICE = frozenset({"stack", "stackwise", "irf"})


def review(db: Session) -> HaReport:
    report = HaReport()
    members: list[Member] = []
    latest = latest_config_ids(db)
    for device in db.scalars(select(Device)):
        snapshot_id = latest.get(device.id)
        snapshot = db.get(ConfigSnapshot, snapshot_id) if snapshot_id is not None else None
        if snapshot is None:
            continue
        # 全部を読み解く必要は無い。組の設定だけを見る読み手を直に使う
        # （台帳が大きいと、1回の表示で全機器の Config を読み直すことになる）
        pair = hapair.parse(snapshot.content)
        if pair is not None:
            members.append(Member(hostname=device.hostname, pair=pair))

    if not members:
        report.notes.append(
            "2台で1台に見せる設定（HA / failover / VSX / MLAG）を読み取れる Config が"
            "ありません。取り込むと、組として成り立っているかを機器をまたいで見られます。"
        )
        return report

    report.devices_seen = len(members)
    by_group: dict[str, list[Member]] = {}
    for member in members:
        label = KIND_LABELS.get(member.pair.kind, member.pair.kind)
        key = f"{label} / {member.pair.group or '（組の名前なし）'}"
        by_group.setdefault(key, []).append(member)
    report.groups = {key: [m.hostname for m in group] for key, group in by_group.items()}

    for key, group in by_group.items():
        if group[0].pair.kind in _WITHIN_ONE_DEVICE:
            # スタックは1台の中の話。別々の機器が同じ台数で組んでいるだけで
            # 「同じ組」ではないので、機器をまたいだ突き合わせはしない
            continue
        report.findings.extend(_alone(key, group))
        report.findings.extend(_roles(key, group))
        report.findings.extend(_priorities(key, group))
        report.findings.extend(_too_many(key, group))
    return report


def _alone(key: str, group: list[Member]) -> list[Finding]:
    if len(group) != 1:
        return []
    member = group[0]
    return [
        Finding(
            level=HIGH,
            title=f"{member.hostname} の相手が台帳にありません",
            why="2台で1台に見せる設定が入っているのに、同じ組の相手の Config が"
            "ありません。相手が台帳に無いか、相手側に設定が入っていないかの"
            "どちらかです。どちらでも、切り替わる日に初めて分かります。",
            evidence=[f"{key}: {member.hostname} のみ"],
        )
    ]


def _roles(key: str, group: list[Member]) -> list[Finding]:
    roles = [m.pair.role for m in group if m.pair.role]
    if len(group) < 2 or len(roles) < 2:
        return []
    if len(set(roles)) == 1:
        return [
            Finding(
                level=HIGH,
                title=f"{key} は、両方とも {roles[0]} です",
                why="役割が同じだと、どちらが主になるかを機器どうしで決められません。"
                "片方を secondary にしてください。",
                evidence=[f"{m.hostname}: {m.pair.role}" for m in group],
            )
        ]
    return []


def _priorities(key: str, group: list[Member]) -> list[Finding]:
    values = [m.pair.priority for m in group if m.pair.priority is not None]
    if len(group) < 2 or len(values) < 2:
        return []
    if len(set(values)) == 1:
        return [
            Finding(
                level=MEDIUM,
                title=f"{key} は、優先度が両方同じです",
                why="どちらが主になるかが、起動の順やシリアル番号で決まります。"
                "戻ってきたときにどちらに乗るかが読めないので、差を付けてください。",
                evidence=[f"{m.hostname}: priority {m.pair.priority}" for m in group],
            )
        ]
    return []


def _too_many(key: str, group: list[Member]) -> list[Finding]:
    if len(group) <= 2:
        return []
    return [
        Finding(
            level=LOW,
            title=f"{key} に {len(group)} 台が入っています",
            why="2台で組む仕組みに3台以上が同じ組の名前で入っています。"
            "別の組のつもりなら、組の名前を分けてください。",
            evidence=[m.hostname for m in group][:5],
        )
    ]
