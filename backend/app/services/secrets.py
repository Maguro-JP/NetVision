"""Config の中の秘密を、見せるときだけ伏せる。

取り込んだ Config は原文のまま保存している。パーサを直したときに読み直せる
ようにするためで、これは変えない。だが画面と書き出しでそのまま出すと、
画面共有やファイル受け渡しのたびに、パスワードやコミュニティ名が一緒に
出ていく。

そこで、保存は原文のまま・見せるときに伏せる、という分け方にする。

### 決めごと

- 伏せるのは値だけ。行そのものは残す。行ごと消すと、その設定が「無い」のか
  「伏せてある」のかが分からなくなる
- どう伏せたかを1か所にまとめる。あちこちで書くと、片方だけ直し忘れる
- 同じ値は同じ印にしない。ここは照合用ではないので、数えられる形に
  しないほうがよい（何種類あるかも情報になる）
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 伏せたことが分かる印。空白にすると、消したのか元から無いのか分からない
MASK = "<伏せています>"


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern[str]
    #: 何を伏せたか（画面に出す説明用）
    label: str


#: 値を持っている行だけを対象にする。`service password-encryption` のように
#: 語が出てくるだけの行は、伏せる中身が無い
_RULES: tuple[_Rule, ...] = (
    _Rule(
        re.compile(r"^(\s*(?:enable\s+)?(?:secret|password)(?:\s+\d+)?\s+)(\S.*)$", re.IGNORECASE),
        "パスワード",
    ),
    _Rule(
        re.compile(
            r"^(\s*username\s+\S+\s+(?:privilege\s+\d+\s+)?(?:secret|password)(?:\s+\d+)?\s+)(\S.*)$",
            re.IGNORECASE,
        ),
        "利用者のパスワード",
    ),
    _Rule(
        re.compile(r"^(\s*snmp-server\s+community\s+)(\S+)(.*)$", re.IGNORECASE),
        "SNMP のコミュニティ名",
    ),
    _Rule(
        re.compile(r"^(\s*snmp-agent\s+community\s+(?:read|write)\s+)(\S+)(.*)$", re.IGNORECASE),
        "SNMP のコミュニティ名",
    ),
    _Rule(
        re.compile(
            r"^(\s*(?:radius|tacacs)-server\s+(?:host\s+\S+\s+)?key(?:\s+\d+)?\s+)(\S.*)$",
            re.IGNORECASE,
        ),
        "認証サーバーの鍵",
    ),
    _Rule(
        re.compile(r"^(\s*key\s+\d*\s*string\s+)(\S.*)$", re.IGNORECASE),
        "鍵",
    ),
    _Rule(
        re.compile(
            r"^(\s*(?:pre-shared-key|pre-shared\s+key)(?:\s+address\s+\S+)?\s+)(\S.*)$",
            re.IGNORECASE,
        ),
        "事前共有鍵",
    ),
    _Rule(
        re.compile(r"^(\s*crypto\s+isakmp\s+key\s+)(\S+)(.*)$", re.IGNORECASE),
        "事前共有鍵",
    ),
    _Rule(
        re.compile(
            r"^(\s*(?:ppp|chap|pap)\s+(?:chap\s+)?password(?:\s+\d+)?\s+)(\S.*)$", re.IGNORECASE
        ),
        "回線のパスワード",
    ),
    _Rule(
        re.compile(r"^(\s*set\s+\S*(?:password|psksecret|passwd)\s+)(\S.*)$", re.IGNORECASE),
        "パスワード",
    ),
    _Rule(
        re.compile(r"^(\s*ip\s+ftp\s+password(?:\s+\d+)?\s+)(\S.*)$", re.IGNORECASE),
        "パスワード",
    ),
)


@dataclass
class Masked:
    text: str
    #: 何を何行伏せたか。画面で「伏せました」と言うために使う
    counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def mask(text: str) -> Masked:
    """見せる用に、秘密の値を伏せる。行は残す。"""
    counts: dict[str, int] = {}
    out: list[str] = []

    for line in (text or "").splitlines():
        # 差分（unified diff）では行の頭に +/- が付く。付いたままだと
        # 当たらず、変わった行として秘密がいちばん目立つ場所に残る
        prefix = ""
        body = line
        if line[:1] in {"+", "-"} and not line.startswith(("+++", "---")):
            prefix, body = line[0], line[1:]

        replaced = body
        for rule in _RULES:
            match = rule.pattern.match(replaced)
            if not match:
                continue
            head = match.group(1)
            tail = match.group(3) if match.lastindex and match.lastindex >= 3 else ""
            replaced = f"{head}{MASK}{tail}"
            counts[rule.label] = counts.get(rule.label, 0) + 1
            break
        out.append(prefix + replaced)

    return Masked(text="\n".join(out) + ("\n" if text.endswith("\n") else ""), counts=counts)


def describe(counts: dict[str, int]) -> str:
    """何を伏せたかの一言。伏せたことを黙っていると、無いものと読まれる。"""
    if not counts:
        return ""
    parts = [f"{label} {count} 行" for label, count in sorted(counts.items())]
    return "見せる用に伏せています: " + " / ".join(parts)
