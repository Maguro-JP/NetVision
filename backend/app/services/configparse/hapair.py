"""2台で1台に見せる仕組み（HA ペア）を、ベンダーをまたいで読む。

FortiGate の HA、ASA の failover、Aruba CX の VSX、Arista の MLAG。書き方は
違うが、決めていることは同じ。どの組か（グループ）、自分はどちらか（役割）、
相手はどこか（相手のアドレス）。

1台の Config だけでは「片方にしか入っていない」「両方とも primary」が
言えないので、比べられる形で持ち帰る。判断は `hareview.py` で行う。
確かめられる書き方だけ読む。読めないものは None のままにする。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 種類の表示名
KIND_LABELS = {
    "fortigate-ha": "FortiGate HA",
    "asa-failover": "ASA failover",
    "vsx": "Aruba VSX",
    "mlag": "Arista MLAG",
}


@dataclass
class ParsedHaPair:
    kind: str
    #: 組の名前や番号。両方で同じでないと組にならない
    group: str | None = None
    #: primary / secondary（書き方は機種ごとに違うので、ここでそろえる）
    role: str | None = None
    #: 相手のアドレス（書いてあるときだけ）
    peer_address: str | None = None
    #: 優先度（FortiGate の priority など）。両方同じだと決め方が運任せになる
    priority: int | None = None
    #: 相手とつなぐ線（VSX の inter-switch-link、MLAG の peer-link）
    link: str | None = None


_FORTI_HA = re.compile(r"^\s*config\s+system\s+ha\s*$", re.IGNORECASE)
_FORTI_SET = re.compile(r"^\s*set\s+(\S+)\s+(.+?)\s*$", re.IGNORECASE)
_ASA_FAILOVER = re.compile(r"^failover\s+lan\s+unit\s+(primary|secondary)", re.IGNORECASE)
_ASA_KEY = re.compile(r"^failover\s+key\b", re.IGNORECASE)
_ASA_LINK = re.compile(r"^failover\s+lan\s+interface\s+(\S+)\s+(\S+)", re.IGNORECASE)
_VSX = re.compile(r"^vsx\s*$", re.IGNORECASE)
_MLAG = re.compile(r"^mlag\s+configuration\s*$", re.IGNORECASE)


def parse(text: str) -> ParsedHaPair | None:
    """入っていれば1つ返す。2つ以上の仕組みを同時に入れる機器は見ない。"""
    for reader in (_fortigate, _asa, _vsx, _mlag):
        found = reader(text)
        if found is not None:
            return found
    return None


def _block(text: str, head: re.Pattern[str]) -> list[str]:
    """見出しの下に、字下げで続く行。"""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not head.match(line):
            continue
        body: list[str] = []
        for raw in lines[index + 1 :]:
            if not raw.strip():
                continue
            if not raw[0].isspace() or raw.strip().lower() == "end":
                break
            body.append(raw.strip())
        return body
    return []


def _fortigate(text: str) -> ParsedHaPair | None:
    body = _block(text, _FORTI_HA)
    if not body:
        return None
    pair = ParsedHaPair(kind="fortigate-ha")
    for line in body:
        found = _FORTI_SET.match(line)
        if not found:
            continue
        key, value = found.group(1).lower(), found.group(2).strip().strip('"')
        if key == "group-name":
            pair.group = value
        elif key == "group-id":
            pair.group = pair.group or value
        elif key == "priority" and value.isdigit():
            pair.priority = int(value)
        elif key == "hbdev":
            pair.link = value.split()[0].strip('"')
        elif key == "mode" and value.lower() == "standalone":
            return None
    return pair


def _asa(text: str) -> ParsedHaPair | None:
    for raw in text.splitlines():
        found = _ASA_FAILOVER.match(raw.strip())
        if found:
            pair = ParsedHaPair(kind="asa-failover", role=found.group(1).lower())
            link = re.search(_ASA_LINK.pattern, text, re.IGNORECASE | re.MULTILINE)
            if link:
                pair.link = link.group(2)
            return pair
    return None


def _vsx(text: str) -> ParsedHaPair | None:
    body = _block(text, _VSX)
    if not body:
        return None
    pair = ParsedHaPair(kind="vsx")
    for line in body:
        words = line.split()
        if not words:
            continue
        head = words[0].lower()
        if head == "system-mac" and len(words) > 1:
            pair.group = words[1].lower()
        elif head == "role" and len(words) > 1:
            pair.role = words[1].lower()
        elif head == "inter-switch-link" and len(words) > 1:
            pair.link = " ".join(words[1:])
        elif head == "keepalive" and "peer" in words:
            index = words.index("peer")
            if index + 1 < len(words):
                pair.peer_address = words[index + 1]
    return pair


def _mlag(text: str) -> ParsedHaPair | None:
    body = _block(text, _MLAG)
    if not body:
        return None
    pair = ParsedHaPair(kind="mlag")
    for line in body:
        words = line.split()
        if not words:
            continue
        head = words[0].lower()
        if head == "domain-id" and len(words) > 1:
            pair.group = words[1]
        elif head == "peer-address" and len(words) > 1:
            pair.peer_address = words[1]
        elif head == "peer-link" and len(words) > 1:
            pair.link = words[1]
    return pair
