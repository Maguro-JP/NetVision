"""取り込んだ Config から、テンプレートを起こす。

同じ役割の機器の Config は、たいてい8割が同じである。違うのはホスト名と
アドレスと VLAN 番号ぐらいで、残りは現場の決めごとがそのまま並んでいる。
その「同じところ」を取り出せば、それがその現場のテンプレートになる。

人が最初から書くより、いま動いているものから起こすほうが早いし、確かでも
ある。実機に入っている以上、少なくとも動く形にはなっている。

### 決め方

- 何台に入っているかで決める。全部に入っている行だけを骨組みにする
- 形は同じで値だけ違う行は、値を変数にする。全部同じ値なら、そのまま残す
  （変数にすると、入れる人が毎回同じ値を打つことになる）
- 何を根拠に起こしたかを残す。何台から、どの行が全台共通だったか
- パスワードや鍵を含む行は入れない。見せられないものは持ち回れない
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Device
from app.models.document import TemplateCategory
from app.models.topology import ConfigSnapshot
from app.services.conventions import _SECRET
from app.services.template_from_docs import _PATTERNS as _DOC_PATTERNS

#: 番号が名前の一部になっているポート（Vlan10 / Loopback0 / Port-channel1）。
#: これを値として扱わないと、機器ごとに番号が違うだけの行が「別の行」に
#: 見えてしまい、骨組みから落ちる。落ちると、その下にぶら下がる行だけが
#: 残って、貼れない Config になる
_INTERFACE_WORD = re.compile(
    r"\b(?:Vlan-interface\s?\d+"
    r"|(?:Vlan|Loopback|Port-channel|Bridge-Aggregation|Tunnel|Dialer)\d+)\b",
    re.IGNORECASE,
)

#: 当てる順番。アドレスを先に当てないと、その中の数字が別々に拾われる
_PATTERNS = (
    ("ip_address", _DOC_PATTERNS[0][1]),
    ("interface", _INTERFACE_WORD),
    *_DOC_PATTERNS[1:],
)

#: 全台に入っていなくても骨組みに入れる下限。1台だけの事情を持ち込まない
_KEEP = 1.0
#: 起こすのに使う機器の上限
_SAMPLE = 20
#: これ未満の台数では起こさない。2台の共通部分は「たまたま」でもありうる
_MIN_DEVICES = 2


@dataclass
class ConfigTemplateDraft:
    name: str
    vendor: str
    body: str
    variables: list[dict] = field(default_factory=list)
    category: TemplateCategory = TemplateCategory.OTHER
    devices_seen: int = 0
    hostnames: list[str] = field(default_factory=list)
    #: どこをどう決めたか
    notes: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _mask(line: str) -> tuple[str, list[tuple[str, str]]]:
    """値らしいところを伏せて、行の「形」を出す。

    形が同じ行どうしを重ねると、どこが値でどこが決めごとかが見えてくる。
    """
    values: list[tuple[str, str]] = []
    masked = line

    for name, pattern in _PATTERNS:

        def swap(match: re.Match[str], name: str = name) -> str:
            values.append((name, match.group(0)))
            return f"\x00{name}\x00"

        masked = pattern.sub(swap, masked)
    return masked, values


def draft(
    db: Session,
    *,
    vendor: str | None = None,
    hostname_prefix: str | None = None,
    limit: int = _SAMPLE,
) -> ConfigTemplateDraft | None:
    """似た機器の Config を重ねて、テンプレートの下書きを作る。"""
    rows = _configs(db, vendor=vendor, hostname_prefix=hostname_prefix, limit=limit)
    if len(rows) < _MIN_DEVICES:
        return None

    hostnames = [hostname for hostname, _text, _vendor in rows]
    result = ConfigTemplateDraft(
        name=f"{hostname_prefix or vendor or '共通'} の下書き",
        vendor=vendor or (rows[0][2] or ""),
        body="",
        devices_seen=len(rows),
        hostnames=hostnames,
    )

    shapes: list[dict[str, list[list[tuple[str, str]]]]] = []
    order: list[str] = []
    for _hostname, text, _vendor in rows:
        here: dict[str, list[list[tuple[str, str]]]] = {}
        occurrences: dict[str, int] = {}
        for raw in text.splitlines():
            line = raw.rstrip()
            if not line.strip():
                continue
            if _SECRET.search(line):
                result.skipped.append(line.strip())
                continue
            masked, values = _mask(line)
            # 同じ形の行が何度も出てくる（区切りの `!` など）。何番目かまで
            # 込みで数えないと、2回目以降が消えて、貼れない形になる
            occurrences[masked] = occurrences.get(masked, 0) + 1
            key = f"{masked}\x01{occurrences[masked]}"
            here.setdefault(key, []).append(values)
            if key not in order:
                order.append(key)
        shapes.append(here)

    lines: list[str] = []
    variables: list[dict] = []
    counts: dict[str, int] = {}
    common = 0

    for key in order:
        seen = sum(1 for here in shapes if key in here)
        if seen / len(shapes) < _KEEP:
            continue
        common += 1
        lines.append(_fill(key, shapes, variables, counts))

    if not lines:
        return None

    result.body = "\n".join(lines) + "\n"
    result.variables = variables
    result.notes = [
        f"{len(rows)} 台の Config を重ねました（{', '.join(hostnames[:5])}"
        + ("…" if len(hostnames) > 5 else "")
        + "）。",
        f"全部の機器に入っていた {common} 行を骨組みにしました。",
        f"値が機器ごとに違う {len(variables)} か所を、入れる欄にしました。"
        "全部同じ値だった箇所は、そのまま残しています。",
    ]
    if result.skipped:
        result.notes.append(f"パスワードや鍵を含む {len(result.skipped)} 行は入れていません。")
    return result


def _fill(
    key: str,
    shapes: list[dict[str, list[list[tuple[str, str]]]]],
    variables: list[dict],
    counts: dict[str, int],
) -> str:
    """伏せた場所を、変数か、そのままの値かに決める。

    全部の機器で同じ値なら、そのまま残す。変数にすると、入れる人が
    毎回同じ値を打つことになる。
    """
    masked = key.split("\x01")[0]
    per_slot: list[list[str]] = []
    for here in shapes:
        for values in here.get(key, []):
            for index, (_name, value) in enumerate(values):
                while len(per_slot) <= index:
                    per_slot.append([])
                per_slot[index].append(value)

    out = masked
    slot = 0
    while "\x00" in out:
        start = out.index("\x00")
        end = out.index("\x00", start + 1)
        name = out[start + 1 : end]
        found = per_slot[slot] if slot < len(per_slot) else []
        unique = sorted(set(found))

        if len(unique) == 1:
            replacement = unique[0]
        else:
            counts[name] = counts.get(name, 0) + 1
            key = name if counts[name] == 1 else f"{name}_{counts[name]}"
            variables.append(
                {
                    "name": key,
                    "label": key,
                    "required": True,
                    "default": unique[0] if unique else "",
                    "help": f"機器ごとに違いました（例: {', '.join(unique[:3])}）",
                }
            )
            replacement = "{{ " + key + " }}"

        out = out[:start] + replacement + out[end + 1 :]
        slot += 1
    return out


def _configs(
    db: Session,
    *,
    vendor: str | None,
    hostname_prefix: str | None,
    limit: int,
) -> list[tuple[str, str, str | None]]:
    """機器ごとの、いちばん新しい Config。

    古い世代まで混ぜると、直す前と直した後が同じ重みで重なり、
    共通部分が実際より狭くなる。
    """
    newest = (
        select(func.max(ConfigSnapshot.id))
        .where(ConfigSnapshot.device_id.isnot(None))
        .group_by(ConfigSnapshot.device_id)
    )
    stmt = (
        select(Device.hostname, ConfigSnapshot.content, Device.vendor)
        .join(Device, Device.id == ConfigSnapshot.device_id)
        .where(ConfigSnapshot.id.in_(newest))
        .order_by(Device.hostname)
        .limit(limit)
    )
    if vendor:
        stmt = stmt.where(Device.vendor.ilike(f"%{vendor}%"))
    if hostname_prefix:
        stmt = stmt.where(Device.hostname.ilike(f"{hostname_prefix}%"))
    return [(h, c, v) for h, c, v in db.execute(stmt).all()]


def save(db: Session, drafted: ConfigTemplateDraft, *, name: str | None = None):
    """下書きをテンプレートとして登録する。

    どの機器から起こしたかを説明に残す。「この書式どこ見て書いたの？」に
    答えられないテンプレートは保守できない。
    """
    from app.models.document import ConfigTemplate

    template = ConfigTemplate(
        name=name or drafted.name,
        description="いま動いている Config から起こしました。" + " ".join(drafted.notes),
        category=drafted.category,
        vendor=drafted.vendor or "",
        body=drafted.body,
        variables=drafted.variables,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template
