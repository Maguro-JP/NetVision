"""ファイアウォール（Cisco ASA）の設定を見て、粗を挙げる。

ASA は「どちら側が内側か」を security-level という数字で決める。名前を
outside にしても、数字が内側と同じなら内側として扱われる。ここが、
名前だけ見ていると気づけないところで、そのまま通ってしまう。

ここで見るのは、動かしてみなくても言えることに限る。通すべき通信が
通るかどうかは要件が要るので言わない。
"""

from __future__ import annotations

import re

from app.services import defaults
from app.services.configparse import ParsedConfig
from app.services.configparse.asa import nameif_of, security_level_of
from app.services.configparse.base import ParsedInterface
from app.services.reviewnote import HIGH, MEDIUM, Finding

#: 外向きと読める名前。現場でこの名前を使うときは、外に面している
_OUTSIDE_WORDS = ("outside", "wan", "internet", "untrust", "外部")


#: 同じ数字の口どうしを通す指定。書いてあれば「同じ数字どうしは通れない」は当たらない
_SAME_SECURITY_PERMIT = re.compile(
    r"^same-security-traffic\s+permit\s+inter-interface\b", re.IGNORECASE | re.MULTILINE
)


def review(parsed: ParsedConfig, text: str | None = None) -> list[Finding]:
    """`text` は元の Config。読み取っていない全体の指定（same-security-traffic）を見るのに使う。"""
    if parsed.vendor.value != "cisco_asa":
        return []

    findings: list[Finding] = []
    ports = [i for i in parsed.interfaces if security_level_of(i) is not None]
    same_level_permitted = bool(text and _SAME_SECURITY_PERMIT.search(text))
    findings.extend(_levels(ports, same_level_permitted))
    findings.extend(_unguarded(parsed, ports))
    # 「落としたことが記録に残らない」は、ベンダーに依らない指摘として
    # policyreview 側が既に見ている。ここで重ねて言わない
    return findings


def _label(interface: ParsedInterface) -> str:
    return f"{interface.name}（{nameif_of(interface) or '名前なし'}）"


def _levels(ports: list[ParsedInterface], same_level_permitted: bool = False) -> list[Finding]:
    """名前と数字が食い違っているところ。

    同じ数字の口が並ぶ指摘は、同じ数字どうしが既定で通れないことに拠る。
    `same-security-traffic permit inter-interface` を書いてあれば通れるので言わない。
    出典: CLI Book 1: Cisco ASA Series General Operations CLI Configuration Guide, 9.16
    「Routed and Transparent Mode Interfaces」の Allow Same Security Level Communication
    （By default, interfaces on the same security level cannot communicate with each other）
    """
    out: list[Finding] = []
    for interface in ports:
        level = security_level_of(interface) or 0
        name = (nameif_of(interface) or "").lower()
        outward = any(word in name for word in _OUTSIDE_WORDS)
        if outward and level >= 100:
            out.append(
                Finding(
                    level=HIGH,
                    title=f"{_label(interface)} は外向きの名前なのに、内側と同じ扱いです",
                    why="security-level が 100 なので、内側から来た通信と同じに扱われます。"
                    "名前ではなく数字で決まるところなので、名前を見ただけでは気づけません。",
                    evidence=[f"{interface.name}: nameif {name} / security-level {level}"],
                )
            )
        if not outward and level == 0 and name not in ("", "dmz"):
            out.append(
                Finding(
                    level=MEDIUM,
                    title=f"{_label(interface)} は内向きの名前なのに、いちばん外の扱いです",
                    why="security-level が 0 なので、外から来た通信と同じに扱われます。"
                    "この口から入る通信は、ACL で明示的に通していなければ全部落ちます。",
                    evidence=[f"{interface.name}: nameif {name} / security-level {level}"],
                )
            )

    if same_level_permitted:
        return out
    same = {}
    for interface in ports:
        same.setdefault(security_level_of(interface), []).append(interface)
    for level, group in sorted(same.items(), key=lambda item: item[0] or 0):
        if len(group) < 2 or level in (None, 0):
            continue
        out.append(
            Finding(
                level=MEDIUM,
                title=f"security-level が同じ口が {len(group)} つあります（{level}）",
                why="同じ数字どうしは、既定では互いに通れません。"
                "通す必要があるなら same-security-traffic の指定が要ります。"
                "通さないつもりなら、数字を分けたほうが意図が読めます。",
                evidence=[_label(interface) for interface in group[:5]],
                sources=[defaults.ASA_SAME_SECURITY_SOURCE],
            )
        )
    return out


def _unguarded(parsed: ParsedConfig, ports: list[ParsedInterface]) -> list[Finding]:
    """外に面した口に、入る側の ACL が当たっていない。"""
    guarded = {
        applied.split()[1]
        for acl in parsed.acls
        for applied in acl.applied_to
        if applied.startswith("interface ") and applied.endswith("入り")
    }
    out: list[Finding] = []
    for interface in ports:
        level = security_level_of(interface) or 0
        name = nameif_of(interface) or ""
        if level > 0 or not name or name in guarded:
            continue
        out.append(
            Finding(
                level=HIGH,
                title=f"外に面した {_label(interface)} に、入る側の制限がありません",
                why="ASA は外から内へ入る通信を既定で落としますが、変換（NAT）で"
                "内側へ通している宛先があると、そこは ACL でしか止められません。"
                "当てていないと、開けたつもりの無い口が開いたままになります。",
                evidence=[f"access-group ... in interface {name} の行なし"],
                sources=[defaults.ASA_LOWER_TO_HIGHER_SOURCE],
            )
        )
    return out
