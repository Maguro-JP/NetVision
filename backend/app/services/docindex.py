"""渡された資料を、こちらで整理する。

ファイルを置いておくだけでは「この機種のこのコマンドはどう書くのか」を引けない。
資料を章に切り、章ごとに出てくるコマンドを抜き出しておくと、機器・型番から
辿れるようになる。

閉域が前提なので、外の検索や外部 API には出さない。抜き出しも探しも、この中で
完結させる。

規則で切るだけなので、章立ての無い資料や画像だけの PDF は「読めなかった」と
残す（黙って空にしない）。後からモデル（llm.py）を差し込めるよう、
テキストを取り出すところと、章に切るところを分けてある。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 章の見出しらしい行。番号付き（1.2.3 …）と、記号だけの区切りを見る
_HEADING = re.compile(
    r"^\s{0,6}(?:"
    r"(?:第?\d+(?:[.\-]\d+){0,3}[.、]?\s+\S.*)"  # 1.2 見出し / 第3章 …
    r"|(?:Chapter\s+\d+\b.*)"
    r"|(?:[A-Z][A-Za-z0-9 /\-]{3,60})"  # Configuring VLANs のような英字見出し
    r"|(?:[■◆●【][^\n]{2,60})"  # ■ 見出し
    r")\s*$"
)

#: コマンドらしい行。設定モードで打つ語で始まるものを拾う
_COMMAND_HEAD = re.compile(
    r"^\s{0,8}(?:no\s+|undo\s+)?"
    r"(interface|vlan|ip|ipv6|switchport|port|spanning-tree|stp|lldp|cdp|"
    r"router|ospf|bgp|static|route|access-list|acl|snmp-server|snmp-agent|"
    r"ntp|clock|logging|info-center|aaa|username|line|user-interface|"
    r"description|shutdown|speed|duplex|mtu|hostname|sysname|device-name|"
    r"link-aggregation|bridge-aggregation|tunnel|crypto|ike|ipsec|nat|napt|"
    r"dhcp|vrf|vpn-instance|qos|policy-map|class-map|traffic|save|write)\b",
    re.IGNORECASE,
)

#: 例文でなく本文の説明文にありがちな終わり方。コマンドとして拾わない
_PROSE_TAIL = re.compile(r"[。、．，:：]$|です$|ます$|ください$")

_MAX_SECTION_CHARS = 8000
_MAX_COMMANDS = 40


@dataclass
class Section:
    heading: str | None
    body: str
    page: int | None = None
    commands: list[str] = field(default_factory=list)


@dataclass
class IndexResult:
    sections: list[Section] = field(default_factory=list)
    note: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.sections)


def extract_text(content: bytes, filename: str | None, content_type: str | None) -> str | None:
    """ファイルから文字を取り出す。取り出せない形式は None。

    PDF は追加のライブラリがある環境だけで読む。閉域へ持ち込む一式を
    重くしたくないので、必須の依存にはしない。
    """
    name = (filename or "").lower()
    kind = (content_type or "").lower()

    if name.endswith((".txt", ".md", ".cfg", ".conf", ".log", ".csv")) or kind.startswith("text/"):
        return _decode(content)
    if name.endswith((".htm", ".html")) or "html" in kind:
        return _strip_tags(_decode(content))
    if name.endswith(".pdf") or "pdf" in kind:
        return _pdf_text(content)
    # 中身を見て判断する。拡張子が付いていないファイルが現場では普通にある
    head = content[:1024]
    if head.startswith(b"%PDF"):
        return _pdf_text(content)
    if b"\x00" not in head:
        return _decode(content)
    return None


def build(text: str) -> IndexResult:
    """文字の塊を章に切り、章ごとのコマンドを抜き出す。"""
    lines = text.splitlines()
    if not any(line.strip() for line in lines):
        return IndexResult(note="中身が空でした")

    sections: list[Section] = []
    heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if not body and heading is None:
            return
        sections.append(
            Section(
                heading=heading,
                body=body[:_MAX_SECTION_CHARS],
                commands=commands_in(body),
            )
        )

    for line in lines:
        if _HEADING.match(line) and len(line.strip()) <= 80 and not _COMMAND_HEAD.match(line):
            flush()
            heading = line.strip()
            buffer = []
            continue
        buffer.append(line)
    flush()

    if not sections:
        return IndexResult(note="章に切れませんでした（見出しが見つかりません）")
    return IndexResult(sections=sections)


def commands_in(body: str) -> list[str]:
    """その章に出てくるコマンド行。説明文は落とす。"""
    out: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or len(line) > 120:
            continue
        if not _COMMAND_HEAD.match(line):
            continue
        if _PROSE_TAIL.search(line):
            continue
        if line not in out:
            out.append(line)
        if len(out) >= _MAX_COMMANDS:
            break
    return out


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932", "utf-8", "euc_jp"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _strip_tags(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", "\n", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;?", "&", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def _pdf_text(content: bytes) -> str | None:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError:
        return None
    import io

    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:  # 壊れた PDF でも取り込み自体は止めない
        return None
