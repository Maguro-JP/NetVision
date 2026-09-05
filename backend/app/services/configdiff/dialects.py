"""ベンダーごとの差の吸収。

差分そのものの計算はベンダーに依らない（木の比較）。ベンダーで変わるのは

- 消し方（`no X` / `delete X` / `unset X`）
- 順序が意味を持つブロックはどれか（ACL など、行単位で足し引きすると壊れる）
- 先に入れないと後の行が通らない組み合わせ（`switchport` を入れる前に
  `switchport mode access` は通らない、など）
- 投入すると自分の接続が切れる、通信が止まる行はどれか

の4点。ここに閉じ込めておけば、ベンダーを足すときに差分の計算側は触らずに済む。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.configparse.base import Vendor


@dataclass(frozen=True)
class Prerequisite:
    """ある行を入れる前に必要になる行。"""

    when: re.Pattern[str]  # 追加しようとしている行
    require: str  # 先に入れておく行
    within: re.Pattern[str]  # そのブロック配下でのみ効く


@dataclass(frozen=True)
class RiskRule:
    pattern: re.Pattern[str]
    message: str


_NEGATION = re.compile(r"^no\s+(.+)$", re.IGNORECASE)


@dataclass
class Dialect:
    """ベンダー1つ分の規則。"""

    name: str
    # 順序が意味を持つブロック（配下を行単位で足し引きしてはいけない）
    ordered_blocks: list[re.Pattern[str]] = field(default_factory=list)
    # 値を1つしか持てないコマンド。マッチした部分をキーにして「置き換え」に畳む
    single_valued: list[re.Pattern[str]] = field(default_factory=list)
    prerequisites: list[Prerequisite] = field(default_factory=list)
    risks: list[RiskRule] = field(default_factory=list)

    # --- 行の読み書き ----------------------------------------------------

    def parse_change(self, line: str) -> tuple[str, bool]:
        """1行を (中身, それは削除か) に分解する。"""
        match = _NEGATION.match(line)
        if match:
            return match.group(1).strip(), True
        return line.strip(), False

    def negate(self, line: str) -> str:
        return f"no {line}"

    def key_of(self, line: str) -> str:
        """木の中でこの行を指すキー。

        単一値コマンドはコマンド名までを返す。`description A` と `description B`
        が同じキーになり、値の変更が1件の置き換えとして扱われる。
        """
        for pattern in self.single_valued:
            match = pattern.match(line)
            if match:
                return match.group(0).strip().lower()
        return line

    # --- 判定 ------------------------------------------------------------

    def is_ordered(self, path: tuple[str, ...]) -> bool:
        head = path[0] if path else ""
        return any(p.match(head) for p in self.ordered_blocks)

    def prerequisites_for(self, parent_line: str, line: str) -> list[str]:
        out: list[str] = []
        for rule in self.prerequisites:
            if rule.when.match(line) and rule.within.match(parent_line):
                out.append(rule.require)
        return out

    def risks_for(self, command: str) -> list[str]:
        return [rule.message for rule in self.risks if rule.pattern.search(command)]

    def render_script(self, lines: list[str]) -> str:
        """投入できる形の文字列にする。

        たいていの機種は字下げがそのままブロックの入れ子になるので、並べるだけ。
        閉じる語が要る機種は、ここを差し替える。
        """
        return "\n".join(lines) + "\n" if lines else ""


# ---------------------------------------------------------------------------
# Cisco IOS / IOS-XE / NX-OS
# ---------------------------------------------------------------------------

_CISCO = Dialect(
    name="cisco",
    ordered_blocks=[
        # ACL は行の順序が評価順そのもの。1行足すだけでも意味が変わる
        re.compile(r"^ip(?:v6)?\s+access-list\b", re.I),
        re.compile(r"^mac\s+access-list\b", re.I),
        # prefix-list / route-map はシーケンス番号を持つが、番号無しの書き方もある
        re.compile(r"^ip(?:v6)?\s+prefix-list\b", re.I),
        re.compile(r"^route-map\b", re.I),
    ],
    single_valued=[
        re.compile(r"^description\b", re.I),
        re.compile(r"^hostname\b", re.I),
        re.compile(r"^name\b", re.I),
        re.compile(r"^ip\s+address(?:\s+secondary)?(?=\s|$)", re.I),
        re.compile(r"^ipv6\s+address(?=\s|$)", re.I),
        re.compile(r"^switchport\s+mode\b", re.I),
        re.compile(r"^switchport\s+access\s+vlan\b", re.I),
        re.compile(r"^switchport\s+trunk\s+native\s+vlan\b", re.I),
        re.compile(r"^switchport\s+trunk\s+allowed\s+vlan\b", re.I),
        re.compile(r"^speed\b", re.I),
        re.compile(r"^duplex\b", re.I),
        re.compile(r"^mtu\b", re.I),
        re.compile(r"^channel-group\b", re.I),
        re.compile(r"^vrf\s+forwarding\b", re.I),
        re.compile(r"^standby\s+\d+\s+(?:ip|priority|preempt)\b", re.I),
        re.compile(r"^ip\s+(?:helper-address|mtu|ospf\s+cost)\b", re.I),
        re.compile(r"^spanning-tree\s+(?:portfast|bpduguard|guard)\b", re.I),
        re.compile(r"^service-policy\s+(?:input|output)\b", re.I),
    ],
    prerequisites=[
        # スイッチポート系を入れる前に、そのポートが L2 である必要がある。
        # SVI / ループバック / トンネルには switchport 自体が存在しないので外す。
        Prerequisite(
            when=re.compile(r"^switchport\s+\S", re.I),
            require="switchport",
            within=re.compile(r"^interface\s+(?!(?:Vlan|Loopback|Tunnel|Null)\b)", re.I),
        ),
    ],
    risks=[
        RiskRule(
            re.compile(r"^\s*no\s+interface\b", re.I),
            "インターフェースをまるごと削除します。管理経路だと接続が切れます",
        ),
        RiskRule(
            re.compile(r"^\s*shutdown\b", re.I),
            "ポートを閉塞します。通信が止まります",
        ),
        RiskRule(
            re.compile(r"^\s*no\s+ip\s+address\b", re.I),
            "IPアドレスを外します。その経路で作業していると接続が切れます",
        ),
        RiskRule(
            re.compile(r"^\s*no\s+(?:router|ip\s+route|ipv6\s+route)\b", re.I),
            "経路が消えます。到達性に影響します",
        ),
        RiskRule(
            re.compile(r"^\s*no\s+ip(?:v6)?\s+access-list\b", re.I),
            "ACL を作り直します。消してから入れ直すまでの間、フィルターが効きません",
        ),
        RiskRule(
            # 番号で書く ACL は、1行だけ消すつもりでも全体が消える機種がある
            re.compile(r"^\s*no\s+access-list\s+\d+\s+\S", re.I),
            "番号で書いた ACL は、1行を指定しても中身がまるごと消える機種があります。"
            "残したい行も一緒に消えるので、入れ直す手順まで用意してください",
        ),
        RiskRule(
            re.compile(r"^\s*no\s+(?:username|aaa|line\s+vty|snmp-server)\b", re.I),
            "管理アクセスに関わる設定を消します。締め出しに注意してください",
        ),
        RiskRule(
            re.compile(r"^\s*no\s+vlan\b", re.I),
            "VLAN を削除します。所属ポートの通信が止まります",
        ),
    ],
)


class _JuniperDialect(Dialect):
    """Junos は set 形式（`show configuration | display set`）で扱う。

    括弧形式より set 形式の方が1行1設定で階層がパスとして表に出るため、
    差分と投入コマンドがそのまま対応する。消すのは `delete`。
    """

    def parse_change(self, line: str) -> tuple[str, bool]:
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith("delete "):
            return "set " + stripped[7:].strip(), True
        if lowered.startswith("deactivate "):
            return "set " + stripped[11:].strip(), True
        return stripped, False

    def negate(self, line: str) -> str:
        if line.lower().startswith("set "):
            return "delete " + line[4:]
        return f"delete {line}"


_JUNIPER = _JuniperDialect(
    name="juniper",
    risks=[
        RiskRule(
            re.compile(r"^\s*delete\s+interfaces\b", re.I),
            "インターフェース設定を削除します。管理経路だと接続が切れます",
        ),
        RiskRule(
            re.compile(r"^\s*delete\s+(?:routing-options|protocols)\b", re.I),
            "経路制御の設定が消えます。到達性に影響します",
        ),
        RiskRule(
            re.compile(r"\bdisable\b", re.I),
            "設定を無効化します。通信が止まります",
        ),
        RiskRule(
            re.compile(r"^\s*delete\s+system\s+(?:login|services)\b", re.I),
            "管理アクセスに関わる設定を消します。締め出しに注意してください",
        ),
    ],
)


class _FortiDialect(Dialect):
    """FortiOS は `set X 値` を `unset X` で戻し、`edit X` を `delete X` で消す。

    `unset` は値ではなくキーだけを指すので、木のキーも `set <名前>` までにする。
    そうしないと `unset` がどの行を消したのか対応が取れず、検証できない。
    """

    def parse_change(self, line: str) -> tuple[str, bool]:
        stripped = line.strip()
        parts = stripped.split()
        lowered = parts[0].lower() if parts else ""
        if lowered in {"next", "end"}:
            # ブロックを閉じる語。設定そのものではないので読み飛ばす
            return "", False
        if lowered == "unset" and len(parts) > 1:
            return f"set {parts[1]}", True
        if lowered == "delete" and len(parts) > 1:
            return f"edit {' '.join(parts[1:])}", True
        return stripped, False

    def render_script(self, lines: list[str]) -> str:
        """`next` と `end` を補って、そのまま貼れる形にする。

        FortiOS は字下げでは閉じない。閉じる語が無いコマンドを貼ると、
        次のブロックが前のブロックの中に入るか、そこで弾かれる。
        """
        out: list[str] = []
        stack: list[tuple[int, str]] = []  # (字下げ, 閉じる語)

        def close(until: int) -> None:
            while stack and stack[-1][0] >= until:
                indent, word = stack.pop()
                out.append(" " * indent + word)

        for line in lines:
            indent = len(line) - len(line.lstrip(" "))
            close(indent)
            out.append(line)
            head = line.strip().split()
            if not head:
                continue
            if head[0].lower() == "config":
                stack.append((indent, "end"))
            elif head[0].lower() == "edit":
                stack.append((indent, "next"))
        close(0)
        return "\n".join(out) + "\n" if out else ""

    def negate(self, line: str) -> str:
        parts = line.strip().split()
        if not parts:
            return line
        if parts[0].lower() == "set" and len(parts) > 1:
            return f"unset {parts[1]}"
        if parts[0].lower() == "edit" and len(parts) > 1:
            return f"delete {' '.join(parts[1:])}"
        if parts[0].lower() == "config":
            # ブロックごと消す書き方は無いので、中身を空にするしかない
            return f"# {line}（このブロックは手で見直してください）"
        return f"unset {line}"


_FORTINET = _FortiDialect(
    name="fortinet",
    single_valued=[re.compile(r"^set\s+\S+", re.I)],
    risks=[
        RiskRule(
            re.compile(r"^\s*delete\b", re.I),
            "オブジェクトを削除します。参照しているポリシーに影響します",
        ),
        RiskRule(
            re.compile(r"\bset\s+status\s+down\b", re.I),
            "インターフェースを落とします。通信が止まります",
        ),
        RiskRule(
            re.compile(r"^\s*unset\s+(?:ip|allowaccess)\b", re.I),
            "管理アクセスに関わる設定を戻します。締め出しに注意してください",
        ),
    ],
)


class _ComwareDialect(Dialect):
    """NEC QX（Comware）は否定が `no` ではなく `undo`。"""

    def parse_change(self, line: str) -> tuple[str, bool]:
        stripped = line.strip()
        if stripped.lower().startswith("undo "):
            return stripped[5:].strip(), True
        return stripped, False

    def negate(self, line: str) -> str:
        return f"undo {line}"


_NEC_QX = _ComwareDialect(
    name="nec_qx",
    single_valued=[
        re.compile(r"^description", re.I),
        re.compile(r"^sysname", re.I),
        re.compile(r"^name", re.I),
        re.compile(r"^ip\s+address", re.I),
        re.compile(r"^port\s+link-type", re.I),
        re.compile(r"^port\s+access\s+vlan", re.I),
        re.compile(r"^speed", re.I),
        re.compile(r"^duplex", re.I),
    ],
    risks=[
        RiskRule(
            re.compile(r"^\s*undo\s+interface\b", re.I),
            "インターフェースを削除します。管理経路だと接続が切れます",
        ),
        RiskRule(
            re.compile(r"^\s*shutdown\b", re.I),
            "ポートを閉塞します。通信が止まります",
        ),
        RiskRule(
            re.compile(r"^\s*undo\s+ip\s+address\b", re.I),
            "IPアドレスを外します。その経路で作業していると接続が切れます",
        ),
        RiskRule(
            re.compile(r"^\s*undo\s+vlan\b", re.I),
            "VLAN を削除します。所属ポートの通信が止まります",
        ),
        RiskRule(
            re.compile(r"^\s*undo\s+port\s+link-aggregation\s+group\b", re.I),
            "束ねているポートを外します。片系だけになり帯域が落ちます",
        ),
        RiskRule(
            re.compile(r"^\s*undo\s+port\s+trunk\s+permit\s+vlan\b", re.I),
            "トランクの許可 VLAN を外します。その VLAN が通らなくなります",
        ),
    ],
)

# IX は Cisco と同じく `no` で否定する
_NEC_IX = Dialect(
    name="nec_ix",
    single_valued=[
        re.compile(r"^description", re.I),
        re.compile(r"^device-name", re.I),
        re.compile(r"^ip\s+address", re.I),
        re.compile(r"^ip\s+mtu", re.I),
    ],
    risks=[
        RiskRule(
            re.compile(r"^\s*no\s+interface\b", re.I),
            "インターフェースを削除します。管理経路だと接続が切れます",
        ),
        RiskRule(
            re.compile(r"^\s*shutdown\b", re.I),
            "ポートを閉塞します。通信が止まります",
        ),
        RiskRule(
            re.compile(r"^\s*no\s+ip\s+address\b", re.I),
            "IPアドレスを外します。その経路で作業していると接続が切れます",
        ),
        RiskRule(
            re.compile(r"^\s*no\s+ip\s+route\b", re.I),
            "経路を削除します。その先へ通信できなくなります",
        ),
        RiskRule(
            # IX のポートは既定で止まっている。`no shutdown` を消すと落ちる
            re.compile(r"^\s*no\s+no\s+shutdown\b", re.I),
            "ポートを閉塞します。IX は既定が閉塞なので通信が止まります",
        ),
    ],
)


_BY_VENDOR: dict[Vendor, Dialect] = {
    Vendor.NEC_IX: _NEC_IX,
    Vendor.NEC_QX: _NEC_QX,
    Vendor.CISCO_IOS: _CISCO,
    Vendor.CISCO_NXOS: _CISCO,
    Vendor.JUNIPER: _JUNIPER,
    Vendor.FORTINET: _FORTINET,
}


def for_vendor(vendor: Vendor | str | None) -> Dialect:
    """ベンダーに対応する規則を返す。分からなければ Cisco 系として扱う。"""
    if isinstance(vendor, str):
        try:
            vendor = Vendor(vendor)
        except ValueError:
            vendor = None
    return _BY_VENDOR.get(vendor or Vendor.UNKNOWN, _CISCO)
