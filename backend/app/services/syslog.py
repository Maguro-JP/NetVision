"""機器のログを読んで、何が起きたかを出す。

Config は「どう設定されているか」で、ログは「実際に何が起きたか」である。
片方だけでは分からないことが多い。設定は正しいのに落ちている、設定を直した
つもりが効いていない、片系で動き続けている。

NetVision は外へ通信しないので、ログを機器から集めには行かない。人が集めて
放り込む。それでも困らない。障害を追うときは、たいてい手元にログの塊がある。

### 読み方の決まり

- 形が違っても読む。機器ごと・収集の仕組みごとに頭の形が違うので、どれも
  受ける。読めない行は落とさず「読めなかった」として数える
- 数えることを第一にする。1行ずつ眺めるためのものではない。同じことが
  何度起きたか、どの機器で起きたか、いつからいつまでか
- 台帳と突き合わせる。ログに出てくる名前が台帳の機器と結びつくと、
  「どの拠点の何が落ちたか」まで言える
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device
from app.services.configparse import normalize_hostname

#: 重大度。数字が小さいほど重い（RFC 5424 と同じ並び）
SEVERITY_LABELS = {
    0: "緊急",
    1: "至急",
    2: "重大",
    3: "エラー",
    4: "警告",
    5: "注意",
    6: "情報",
    7: "デバッグ",
}

#: これ以下（重い）を「重い」として扱う。3 = エラー
HEAVY = 3

#: 出す件数の上限。全部並べても読めない
_TOP = 20


@dataclass
class LogEvent:
    """ログ1行から読み取れたもの。読めなかった部分は空のまま持つ。"""

    raw: str
    at: datetime | None = None
    host: str | None = None
    severity: int | None = None
    facility: str | None = None
    #: 機器が付ける事象の名前（`%LINK-3-UPDOWN` の LINK-3-UPDOWN 部分）
    code: str | None = None
    message: str = ""

    @property
    def heavy(self) -> bool:
        return self.severity is not None and self.severity <= HEAVY


@dataclass
class Pattern:
    """同じことが何度起きたか。1行ずつ眺めるためのものではない。"""

    code: str
    count: int
    severity: int | None
    hosts: list[str]
    first_at: datetime | None
    last_at: datetime | None
    sample: str
    meaning: str = ""


@dataclass
class LogReport:
    lines: int = 0
    parsed: int = 0
    unreadable: int = 0
    first_at: datetime | None = None
    last_at: datetime | None = None
    by_severity: dict[str, int] = field(default_factory=dict)
    by_host: list[dict] = field(default_factory=list)
    patterns: list[Pattern] = field(default_factory=list)
    #: 台帳と結びついた機器と、結びつかなかった名前
    known_hosts: list[str] = field(default_factory=list)
    unknown_hosts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1行を読む
# ---------------------------------------------------------------------------

#: `<134>` のような優先度。機能と重大度が1つの数字に畳まれている
_PRI = re.compile(r"^<(\d{1,3})>")
#: Cisco 系の事象名。`%LINK-3-UPDOWN:` `%SYS-5-CONFIG_I:`
_CODE = re.compile(r"%([A-Z0-9_]+(?:-[A-Z0-9_]+)*-\d-[A-Z0-9_]+)\s*:?\s*(.*)$")
#: Comware 系。`%%10SHELL/5/SHELL_LOGIN:`
_CODE_COMWARE = re.compile(r"%%\d*([A-Z0-9]+/\d+/[A-Z0-9_]+)\s*:?\s*(.*)$")
#: 日付。よくある3つの形だけ見る（増やすのは実物を見てから）
_STAMPS = (
    (re.compile(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})"), "%Y-%m-%d %H:%M:%S"),
    (re.compile(r"^([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"), "%b %d %H:%M:%S"),
    (re.compile(r"^\*?([A-Z][a-z]{2}\s+\d{1,2}\s+\d{4}\s+\d{2}:\d{2}:\d{2})"), "%b %d %Y %H:%M:%S"),
)
#: ホスト名らしい語。日付の後ろに来る
_HOST = re.compile(r"^([A-Za-z][\w.\-]{1,60})\s")


def parse_line(line: str) -> LogEvent | None:
    """1行を読む。読めなければ None。"""
    text = line.strip()
    if not text:
        return None

    event = LogEvent(raw=text)
    rest = text

    priority = _PRI.match(rest)
    if priority:
        value = int(priority.group(1))
        event.severity = value % 8
        event.facility = str(value // 8)
        rest = rest[priority.end() :].strip()

    for pattern, fmt in _STAMPS:
        found = pattern.match(rest)
        if not found:
            continue
        event.at = _to_datetime(found.group(1), fmt)
        rest = rest[found.end() :].strip()
        break

    host = _HOST.match(rest)
    if host and not host.group(1).startswith("%"):
        event.host = host.group(1)
        rest = rest[host.end() :].strip()

    for pattern in (_CODE_COMWARE, _CODE):
        found = pattern.search(rest)
        if not found:
            continue
        event.code = found.group(1)
        event.message = found.group(2).strip()
        # 事象名の中の数字は、機器自身がその出来事に付けた重さ。頭の `<134>` は
        # 途中の収集の仕組みで書き換わることがあるので、事象名のほうを採る
        from_code = _severity_from_code(event.code)
        if from_code is not None:
            event.severity = from_code
        break
    else:
        event.message = rest

    if event.code is None and event.severity is None and event.at is None:
        return None  # 何も読み取れていない。ログの行とみなさない
    return event


def _to_datetime(text: str, fmt: str) -> datetime | None:
    cleaned = text.replace("T", " ")
    try:
        parsed = datetime.strptime(cleaned, fmt)
    except ValueError:
        return None
    if parsed.year == 1900:
        # 年の無い形（`Aug 22 10:00:00`）。年をまたぐと狂うので、
        # 年は補わずに今年として扱う。並べ替えと期間の表示にしか使わない
        parsed = parsed.replace(year=datetime.now().year)
    return parsed


def _severity_from_code(code: str) -> int | None:
    """事象名の真ん中の数字が重大度になっている（`LINK-3-UPDOWN` の 3）。"""
    parts = code.replace("/", "-").split("-")
    for part in parts:
        if part.isdigit() and 0 <= int(part) <= 7:
            return int(part)
    return None


# ---------------------------------------------------------------------------
# まとめる
# ---------------------------------------------------------------------------

#: よく出る事象の意味。知らない語のまま出しても、読む人には何も伝わらない
MEANINGS: dict[str, str] = {
    "LINK-3-UPDOWN": "ポートの線が上がった / 落ちた",
    "LINEPROTO-5-UPDOWN": "ポートの通信が始まった / 止まった",
    "SYS-5-CONFIG_I": "設定が変えられた",
    "SYS-5-RESTART": "機器が再起動した",
    "SYS-1-RELOAD": "機器が再起動した",
    "OSPF-5-ADJCHG": "OSPF の相手との関係が変わった（つながった / 切れた）",
    "BGP-5-ADJCHANGE": "BGP の相手との関係が変わった",
    "STANDBY-6-STATECHANGE": "既定ゲートウェイの主が入れ替わった",
    "VRRP-6-STATECHANGE": "既定ゲートウェイの主が入れ替わった",
    "SPANTREE-2-ROOTGUARD_BLOCK": "STP が、想定外の機器からの通知でポートを塞いだ",
    "SPANTREE-2-LOOPGUARD_BLOCK": "STP が、輪になりかけたポートを塞いだ",
    "PORT_SECURITY-2-PSECURE_VIOLATION": "許していない端末がポートに挿された",
    "SEC_LOGIN-4-LOGIN_FAILED": "ログインに失敗した",
    "SEC_LOGIN-5-LOGIN_SUCCESS": "ログインに成功した",
    "DUAL-5-NBRCHANGE": "EIGRP の相手との関係が変わった",
    "PLATFORM_THERMAL-1-OVERTEMP": "機器が熱い",
    "SYS-3-CPUHOG": "処理が詰まった",
    "SHELL/5/SHELL_LOGIN": "ログインした",
    "IFNET/3/PHY_UPDOWN": "ポートの線が上がった / 落ちた",
    "IFNET/5/LINK_UPDOWN": "ポートの通信が始まった / 止まった",
}


def meaning_of(code: str | None) -> str:
    if not code:
        return ""
    if code in MEANINGS:
        return MEANINGS[code]
    # 末尾の語だけで引く（機種で頭が違うことがある）
    tail = code.split("-")[-1].split("/")[-1]
    for known, text in MEANINGS.items():
        if known.endswith(tail):
            return text
    return ""


def review(db: Session | None, text: str) -> LogReport:
    """貼られたログをまとめる。台帳があれば、機器と結びつける。"""
    report = LogReport()
    events: list[LogEvent] = []

    for line in text.splitlines():
        if not line.strip():
            continue
        report.lines += 1
        event = parse_line(line)
        if event is None:
            report.unreadable += 1
            continue
        report.parsed += 1
        events.append(event)

    if not events:
        report.notes.append(
            "ログとして読める行がありませんでした。機器が出したそのままの行を貼ってください。"
        )
        return report

    stamps = [e.at for e in events if e.at is not None]
    report.first_at = min(stamps) if stamps else None
    report.last_at = max(stamps) if stamps else None

    severities: Counter[str] = Counter()
    for event in events:
        label = (
            SEVERITY_LABELS.get(event.severity, "不明") if event.severity is not None else "不明"
        )
        severities[label] += 1
    report.by_severity = dict(severities)

    hosts: Counter[str] = Counter()
    heavy_by_host: Counter[str] = Counter()
    for event in events:
        if not event.host:
            continue
        hosts[event.host] += 1
        if event.heavy:
            heavy_by_host[event.host] += 1
    report.by_host = [
        {"host": host, "count": count, "heavy": heavy_by_host.get(host, 0)}
        for host, count in hosts.most_common(_TOP)
    ]

    report.patterns = _patterns(events)
    if db is not None:
        report.known_hosts, report.unknown_hosts = _match_devices(db, list(hosts))
        if report.unknown_hosts:
            report.notes.append(
                f"台帳に見当たらない名前が {len(report.unknown_hosts)} 件あります。"
                "別の呼び方で載っているか、台帳に無い機器のログです。"
            )
    if report.unreadable:
        report.notes.append(
            f"{report.unreadable} 行は読めませんでした。"
            "頭の形が違うか、ログ以外の行が混ざっています。"
        )
    return report


def _patterns(events: list[LogEvent]) -> list[Pattern]:
    """同じ事象ごとにまとめる。何度起きたかが分かると、当たりが付く。"""
    groups: dict[str, list[LogEvent]] = {}
    for event in events:
        key = event.code or "（事象名なし）"
        groups.setdefault(key, []).append(event)

    out: list[Pattern] = []
    for code, items in groups.items():
        stamps = [e.at for e in items if e.at is not None]
        severity = next((e.severity for e in items if e.severity is not None), None)
        hosts = sorted({e.host for e in items if e.host})
        out.append(
            Pattern(
                code=code,
                count=len(items),
                severity=severity,
                hosts=hosts[:_TOP],
                first_at=min(stamps) if stamps else None,
                last_at=max(stamps) if stamps else None,
                sample=items[0].raw[:300],
                meaning=meaning_of(code),
            )
        )

    # 重いものから、次に多いものから。眺める順に並べる
    out.sort(key=lambda p: (p.severity if p.severity is not None else 9, -p.count))
    return out[:_TOP]


def _match_devices(db: Session, hosts: list[str]) -> tuple[list[str], list[str]]:
    """ログに出てくる名前を、台帳の機器と結びつける。"""
    if not hosts:
        return [], []
    ledger = {
        normalize_hostname(name): name
        for (name,) in db.execute(select(Device.hostname)).all()
        if name
    }
    known, unknown = [], []
    for host in hosts:
        if normalize_hostname(host) in ledger:
            known.append(host)
        else:
            unknown.append(host)
    return sorted(known), sorted(unknown)
