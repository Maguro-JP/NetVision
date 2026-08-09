"""VLAN 番号の並びを扱う。

トランクの許可 VLAN はベンダーごとに書式が違う。

- Cisco   … `switchport trunk allowed vlan 10,20,30-40`（追記は `add`）
- Comware … `port trunk permit vlan 10 20 30 to 40`
- Junos   … `vlan members [ v10 v20 ]`（名前で書けるので番号は別途解決）

上位では「番号の集合」として扱いたいので、ここで一度ならして扱う。
DB には `10,20,30-40` の形で1カラムに入れる。数が多い現場では
1ポートに数十 VLAN 載るため、行に展開すると量が跳ねる割に使い道が無い。
"""

from __future__ import annotations

_ALL = frozenset({"all", "1-4094", "1-4093"})


def parse_ranges(text: str) -> list[int]:
    """`10,20,30-40` や `10 20 30 to 40` を番号のリストにする。

    読めない語は黙って捨てる。実機の出力には `none` や見出しが混ざるため。
    """
    if not text:
        return []

    normalized = text.replace(" to ", "-").replace(",", " ").replace("-", " - ")
    tokens = normalized.split()

    out: set[int] = set()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-" or not token.isdigit():
            index += 1
            continue
        start = int(token)
        if index + 2 < len(tokens) and tokens[index + 1] == "-" and tokens[index + 2].isdigit():
            end = int(tokens[index + 2])
            if start <= end <= 4094:
                out.update(range(start, end + 1))
            index += 3
            continue
        if 1 <= start <= 4094:
            out.add(start)
        index += 1
    return sorted(out)


def is_all(text: str) -> bool:
    """`switchport trunk allowed vlan` を書いていない＝全 VLAN 許可、の表現かどうか。"""
    return text.strip().lower() in _ALL


def expand_column(text: str | None, defined: set[int]) -> set[int]:
    """DB に入れた許可 VLAN の文字列を、実際に通る番号の集合に戻す。

    `all` / `all except 999` は、その機器に定義されている VLAN と
    突き合わせて解決する。番号を展開して持たない代わりに、ここで解く。
    """
    if not text:
        return set()

    lowered = text.strip().lower()
    if lowered == "all":
        return set(defined)
    if lowered.startswith("all except"):
        return set(defined) - set(parse_ranges(lowered[len("all except") :]))
    return set(parse_ranges(text))


def format_ranges(vlans: list[int] | set[int]) -> str:
    """番号の集合を `10,20,30-40` に畳む。DB とヘッダ表示用。"""
    ordered = sorted(set(vlans))
    if not ordered:
        return ""

    parts: list[str] = []
    start = previous = ordered[0]
    for value in ordered[1:]:
        if value == previous + 1:
            previous = value
            continue
        parts.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    parts.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(parts)
