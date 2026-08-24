"""指摘1件の形。見る場所が増えても、出す形は1つにそろえる。

L3 の指摘も、ACL や NAT の指摘も、受け取る側（画面・API）から見れば同じ
「気になったこと」である。ここに置いておけば、判断する層を足すたびに
似た dataclass が増えることも、片方だけ形が変わることも無い。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 気になる度合い。台帳の重要度と同じ言葉にはしない（別の軸なので混ぜない）
HIGH = "high"
MEDIUM = "medium"
LOW = "low"


@dataclass
class Finding:
    """気になったこと1件。

    `why` は「なぜ気になるのか」、`evidence` は「どこを見てそう言ったか」。
    どちらも無いものは指摘として出さない。
    """

    level: str
    title: str
    why: str
    evidence: list[str] = field(default_factory=list)
