"""資料からテンプレートを起こす。

コマンドの書式は機種とバージョンで変わる。人が手で書き写すと、写し間違いと
版ずれが必ず入る。取り込んだ資料の中には、その機種の正しい書き方が既に
あるので、そこから起こす。

やっていることは3つだけで、賢いことはしない。

  1. 資料の章から、コマンドの行を拾う
  2. 値らしいところ（番号・アドレス・ポート名）を変数にする
  3. どの資料のどの章から起こしたかを残す

3 が要るのは、「この書式どこ見て書いたの？」に答えられないテンプレートは
保守できないため。版が変わったときに、元の章へ戻れる形にしておく。

値の見分けは規則ベースなので、外すこともある。起こしたものは人が直す前提で、
そのまま投入できるものとしては扱わない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.document import ConfigTemplate, Document, DocumentSection, TemplateCategory

#: 章の見出しから種別を推測する手がかり
_CATEGORY_HINTS: tuple[tuple[TemplateCategory, tuple[str, ...]], ...] = (
    (TemplateCategory.VLAN, ("vlan", "ブロードキャストドメイン")),
    (TemplateCategory.INTERFACE, ("interface", "インタフェース", "インターフェース", "ポート")),
    (TemplateCategory.ROUTING, ("route", "routing", "ospf", "bgp", "経路")),
    (TemplateCategory.ACL, ("acl", "access-list", "アクセスリスト", "フィルタ")),
    (TemplateCategory.QOS, ("qos", "shaping", "優先")),
    (TemplateCategory.MANAGEMENT, ("snmp", "ntp", "syslog", "aaa", "管理", "ログ")),
    (TemplateCategory.SECURITY, ("security", "認証", "暗号")),
)

#: 変数にする値。名前は人が読んで分かるものにする
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ip_address", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?\b")),
    ("interface", re.compile(r"\b(?:[A-Za-z]{2,}[A-Za-z-]*)\d+(?:/\d+)+(?:\.\d+)?\b")),
    ("vlan_id", re.compile(r"(?<=\bvlan )\d+", re.IGNORECASE)),
    ("number", re.compile(r"(?<![\w./])\d+(?![\w./])")),
)


@dataclass
class Draft:
    name: str
    category: TemplateCategory
    vendor: str
    model: str | None
    body: str
    variables: list[dict] = field(default_factory=list)
    source_document_id: int | None = None
    heading: str | None = None
    notes: list[str] = field(default_factory=list)


def _category_of(heading: str | None, body: str) -> TemplateCategory:
    text = f"{heading or ''} {body}".lower()
    for category, needles in _CATEGORY_HINTS:
        if any(needle in text for needle in needles):
            return category
    return TemplateCategory.OTHER


def _variablize(commands: list[str]) -> tuple[str, list[dict]]:
    """値らしいところを変数にする。同じ値は同じ変数にまとめる。"""
    seen: dict[str, str] = {}
    counts: dict[str, int] = {}
    lines: list[str] = []

    for line in commands:
        replaced = line
        for name, pattern in _PATTERNS:

            def swap(match: re.Match[str], name: str = name) -> str:
                value = match.group(0)
                if value in seen:
                    return "{{ " + seen[value] + " }}"
                counts[name] = counts.get(name, 0) + 1
                key = name if counts[name] == 1 else f"{name}_{counts[name]}"
                seen[value] = key
                return "{{ " + key + " }}"

            replaced = pattern.sub(swap, replaced)
        lines.append(replaced)

    variables = [
        {
            "name": key,
            "label": key,
            "required": True,
            # 資料に書いてあった値を初期値にする。何を入れる欄なのかが分かる
            "default": value,
            "help": f"資料では {value} でした",
        }
        for value, key in seen.items()
    ]
    variables.sort(key=lambda item: item["name"])
    return "\n".join(lines), variables


def draft_from_section(document: Document, section: DocumentSection) -> Draft | None:
    """章1つからテンプレートの下書きを作る。コマンドが無い章は None。"""
    commands = [line for line in (section.commands or "").splitlines() if line.strip()]
    if not commands:
        return None

    body, variables = _variablize(commands)
    heading = section.heading or document.title
    return Draft(
        name=f"{heading}（{document.title} より）"[:200],
        category=_category_of(section.heading, "\n".join(commands)),
        vendor=document.vendor,
        model=document.model,
        body=body,
        variables=variables,
        source_document_id=document.id,
        heading=section.heading,
        notes=[
            "資料のコマンドから起こした下書きです。値の見分けは規則ベースなので、"
            "変数の切り方は人が直してください",
        ],
    )


def drafts_for_document(db: Session, document: Document, *, limit: int = 20) -> list[Draft]:
    """1つの資料から、章ごとに下書きを作る。"""
    drafts: list[Draft] = []
    for section in sorted(document.sections, key=lambda item: item.position):
        draft = draft_from_section(document, section)
        if draft is not None:
            drafts.append(draft)
        if len(drafts) >= limit:
            break
    return drafts


def save(db: Session, draft: Draft) -> ConfigTemplate:
    """下書きをテンプレートとして登録する。"""
    template = ConfigTemplate(
        name=draft.name,
        description=(
            f"{draft.heading} より起こしました。値は資料に書かれていたものを初期値にしてあります"
            if draft.heading
            else "資料から起こしました"
        ),
        category=draft.category,
        vendor=draft.vendor,
        model=draft.model,
        body=draft.body,
        variables=draft.variables,
        source_document_id=draft.source_document_id,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template
