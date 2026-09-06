"""取り込んだ Config から、その現場の流儀を覚える。

生成する Config は、いまのところ一般的な書き方をしている。だが現場には
現場の決めごとがある。VLAN の刻み、ホスト名の付け方、ログの送り先、
管理アクセスの決まり文句。そこだけ違うものが混ざると、後で読む人が
「これは誰が入れたのか」と迷う。

覚え方は数えるだけにする。いちばん多いものを「この現場のやり方」とみなす。
学ぶというより、揃っているものを写しているだけである。だからこそ、外れた
1台に引きずられない（多数決なので）。

### 決めごと

- 覚えたことは必ず見せる。台数と割合まで出す。人が見て違えば直せる
- 揃っていないものは覚えない。半々に割れているものを「流儀」と呼ぶと、
  どちらでもない現場に片方を押し付けることになる
- 覚えた値をそのまま Config に入れるのは、人が確かめられるものだけにする。
  パスワードや鍵は覚えない
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Device, DeviceType
from app.models.topology import ConfigSnapshot
from app.services import configparse

#: これ未満しか揃っていないものは「流儀」と呼ばない
_AGREEMENT = 0.6
#: 最低でもこの台数を見てから決める。1台を流儀とは呼べない
_MIN_DEVICES = 2
#: 覚えるために読む Config の上限。全件読むと台数ぶんの時間がかかる
_SAMPLE = 200


@dataclass
class Learned:
    """覚えたこと1つ。

    `share` は、見た機器のうち何割がそうしていたか。低いものは覚えない。
    """

    topic: str
    value: str
    seen: int
    total: int
    detail: str = ""

    @property
    def share(self) -> float:
        return self.seen / self.total if self.total else 0.0


@dataclass
class Conventions:
    """その現場の流儀。生成のときに使う。"""

    devices_seen: int = 0
    vlan_step: int | None = None
    hostname_pattern: str | None = None
    syslog_hosts: list[str] = field(default_factory=list)
    ntp_hosts: list[str] = field(default_factory=list)
    snmp_communities: int = 0
    stp_mode: str | None = None
    #: 管理まわりで、どの機器にも入っている決まり文句
    baseline_lines: list[str] = field(default_factory=list)
    learned: list[Learned] = field(default_factory=list)

    def note(self, topic: str, value: str, seen: int, total: int, detail: str = "") -> None:
        self.learned.append(
            Learned(topic=topic, value=value, seen=seen, total=total, detail=detail)
        )


#: 覚えてよい管理まわりの行。パスワードや鍵は覚えない（見せられないものを
#: 生成に混ぜると、人が確かめられなくなる）
_BASELINE = (
    re.compile(r"^no ip http server$", re.IGNORECASE),
    re.compile(r"^no ip http secure-server$", re.IGNORECASE),
    re.compile(r"^service password-encryption$", re.IGNORECASE),
    re.compile(r"^ip ssh version \d$", re.IGNORECASE),
    re.compile(r"^logging (?:host )?\d+\.\d+\.\d+\.\d+$", re.IGNORECASE),
    re.compile(r"^ntp server \S+$", re.IGNORECASE),
    re.compile(r"^service timestamps \S+.*$", re.IGNORECASE),
    re.compile(r"^clock timezone \S+.*$", re.IGNORECASE),
    re.compile(r"^ip domain[- ]name \S+$", re.IGNORECASE),
    re.compile(r"^spanning-tree mode \S+$", re.IGNORECASE),
    re.compile(r"^vtp mode \S+$", re.IGNORECASE),
)

#: 値を持っている行だけを弾く。`service password-encryption` のように、
#: 語が出てくるだけで中身を持たない行まで弾くと、写せるものが写せなくなる
_SECRET = re.compile(
    r"\b(password|secret|key|community|psk|pre-shared(?:-key)?)\s+\S", re.IGNORECASE
)


def learn(
    db: Session, *, sample: int = _SAMPLE, kinds: set[DeviceType] | None = None
) -> Conventions:
    """取り込んである Config を数えて、その現場の流儀を出す。

    `kinds` を渡すと、その種類の機器だけで数える。スイッチの決まり文句を
    無線コントローラに当てると、入っていないのが当たり前の行を「抜けている」
    と言うことになる。種類をまたいで比べてよいのは、ログの送り先のように
    現場ぜんぶで同じにするものだけ。
    """
    snapshots = _latest_per_device(db, sample, kinds)
    result = Conventions(devices_seen=len(snapshots))
    if len(snapshots) < _MIN_DEVICES:
        return result

    parsed_all = [configparse.parse(text) for _hostname, text in snapshots]
    hostnames = [hostname for hostname, _text in snapshots if hostname]

    _learn_vlan_step(result, parsed_all)
    _learn_hostname(result, hostnames)
    _learn_management(result, parsed_all)
    _learn_baseline(result, [text for _hostname, text in snapshots])
    return result


def _latest_per_device(
    db: Session, sample: int, kinds: set[DeviceType] | None = None
) -> list[tuple[str | None, str]]:
    """機器ごとの、いちばん新しい Config。

    同じ機器の古い世代まで数えると、直した後の姿と直す前の姿が両方
    票を持ってしまう。
    """
    newest = (
        select(func.max(ConfigSnapshot.id))
        .where(ConfigSnapshot.device_id.isnot(None))
        .group_by(ConfigSnapshot.device_id)
        .limit(sample)
    )
    query = (
        select(Device.hostname, ConfigSnapshot.content)
        .join(Device, Device.id == ConfigSnapshot.device_id)
        .where(ConfigSnapshot.id.in_(newest))
    )
    if kinds is not None:
        query = query.where(Device.device_type.in_(kinds))
    rows = db.execute(query).all()
    return [(hostname, content) for hostname, content in rows]


def _learn_vlan_step(result: Conventions, parsed_all: list) -> None:
    """VLAN 番号の刻み。10 刻みなのか、1 刻みで詰めているのか。"""
    steps: Counter[int] = Counter()
    for parsed in parsed_all:
        numbers = sorted(v for v in parsed.vlans if 1 < v < 1000)
        for left, right in zip(numbers, numbers[1:], strict=False):
            gap = right - left
            if 0 < gap <= 100:
                steps[gap] += 1
    if not steps:
        return
    step, seen = steps.most_common(1)[0]
    total = sum(steps.values())
    if seen / total < _AGREEMENT:
        return  # 揃っていないものを流儀と呼ばない
    result.vlan_step = step
    result.note("VLAN 番号の刻み", f"{step} ずつ", seen, total)


def _learn_hostname(result: Conventions, hostnames: list[str]) -> None:
    """ホスト名の付け方。「拠点-役割-番号」のような形を見る。"""
    shapes: Counter[str] = Counter()
    for hostname in hostnames:
        shape = re.sub(r"\d+", "N", hostname.lower())
        shape = re.sub(r"[a-z]+", "A", shape)
        shapes[shape] += 1
    if not shapes:
        return
    shape, seen = shapes.most_common(1)[0]
    if seen / len(hostnames) < _AGREEMENT:
        return
    result.hostname_pattern = shape
    example = next(
        (h for h in hostnames if re.sub(r"\d+", "N", re.sub(r"[a-z]+", "A", h.lower())) == shape),
        "",
    )
    result.note(
        "ホスト名の形",
        shape.replace("A", "英字").replace("N", "数字"),
        seen,
        len(hostnames),
        detail=f"例: {example}" if example else "",
    )


def _learn_management(result: Conventions, parsed_all: list) -> None:
    """ログや時刻の送り先。ここが機器ごとにばらつくと、後から追えなくなる。"""
    syslog: Counter[str] = Counter()
    ntp: Counter[str] = Counter()
    stp: Counter[str] = Counter()
    snmp = 0

    for parsed in parsed_all:
        for feature in parsed.features:
            if feature.name == "Syslog" and feature.detail:
                for host in _values(feature.detail):
                    syslog[host] += 1
            elif feature.name == "NTP" and feature.detail:
                for host in _values(feature.detail):
                    ntp[host] += 1
            elif feature.name == "SNMP":
                snmp += 1
            elif feature.name == "STP" and feature.detail:
                for mode in _values(feature.detail):
                    stp[mode] += 1

    total = len(parsed_all)
    result.snmp_communities = snmp
    for counter, attribute, label in (
        (syslog, "syslog_hosts", "ログの送り先"),
        (ntp, "ntp_hosts", "時刻の参照先"),
    ):
        agreed = [host for host, seen in counter.items() if seen / total >= _AGREEMENT]
        if agreed:
            setattr(result, attribute, agreed)
            result.note(label, " / ".join(agreed), max(counter.values()), total)

    if stp:
        mode, seen = stp.most_common(1)[0]
        if seen / total >= _AGREEMENT:
            result.stp_mode = mode
            result.note("STP のモード", mode, seen, total)


def _values(detail: str) -> list[str]:
    """「送り先: 10.0.0.9, 10.0.0.10」から値だけ取り出す。"""
    _, _, rest = detail.partition(":")
    return [item.strip() for item in rest.split(",") if item.strip()]


def _learn_baseline(result: Conventions, texts: list[str]) -> None:
    """どの機器にも入っている、管理まわりの決まり文句。

    生成する Config にそのまま足せる形にしておく。ただし覚えるのは
    見せられる行だけにする。パスワードや鍵は覚えない。
    """
    counts: Counter[str] = Counter()
    for text in texts:
        seen_here = set()
        for line in text.splitlines():
            stripped = line.strip()
            if _SECRET.search(stripped):
                continue
            if any(pattern.match(stripped) for pattern in _BASELINE):
                seen_here.add(stripped)
        for line in seen_here:
            counts[line] += 1

    total = len(texts)
    result.baseline_lines = [
        line for line, seen in sorted(counts.items()) if seen / total >= _AGREEMENT
    ]
    if result.baseline_lines:
        result.note(
            "どの機器にも入っている行",
            f"{len(result.baseline_lines)} 行",
            max(counts.values()),
            total,
            detail=" / ".join(result.baseline_lines[:3]),
        )
