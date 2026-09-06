"""Config に何の機能が入っているかを、ベンダーをまたいで拾う。

インターフェースや経路のように「値」を取り出すのとは別に、「この機器では
何をしているのか」を知りたい場面がある。ACL が入っているのか、NAT を
しているのか、STP はどのモードか、ログはどこへ送っているのか。

台帳としては、まずここが埋まっていることが大事になる。更改のときに
「この機器は何をしていたのか」が分からないと、同じものを用意できない。

### 方針

- 中身の解釈まではしない。「入っている」「何行ある」「主な値」まで。
  ACL の1行1行を評価するのは、この層の仕事ではない
- 書き方はベンダーごとに違うが、意味は同じところへ落ちる。表で持ち、
  機種ごとの差は行を足して吸収する
- 拾えたものだけ返す。「無い」のか「読めていない」のかは区別しない
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 機能の区分。画面の並び順もこの順にする
ACCESS_CONTROL = "access_control"
NAT = "nat"
DHCP = "dhcp"
SPANNING_TREE = "spanning_tree"
AGGREGATION = "aggregation"
QOS = "qos"
AUTHENTICATION = "authentication"
MANAGEMENT = "management"
VPN = "vpn"
MULTICAST = "multicast"
LOAD_BALANCE = "load_balance"
HIGH_AVAILABILITY = "high_availability"

FEATURE_LABELS = {
    ACCESS_CONTROL: "通信の制限（ACL / ポリシー）",
    NAT: "アドレス変換（NAT）",
    DHCP: "アドレス配布（DHCP / リレー）",
    SPANNING_TREE: "ループ防止（STP）",
    AGGREGATION: "ポートの束ね（LAG）",
    QOS: "優先制御（QoS）",
    AUTHENTICATION: "端末の認証（802.1X / ポートセキュリティ）",
    MANAGEMENT: "管理（SNMP / Syslog / NTP / AAA）",
    VPN: "拠点間の暗号化（IPsec / VPN）",
    MULTICAST: "マルチキャスト",
    LOAD_BALANCE: "負荷分散",
    HIGH_AVAILABILITY: "機器の冗長（HSRP / VRRP / スタック）",
}


@dataclass
class ParsedFeature:
    """その機器で使っている機能1つ。

    `detail` は「何が入っているか」の要約（ACL の本数、ログの送り先など）。
    `evidence` は根拠にした行。人が確かめられるように、原文をそのまま持つ。
    """

    category: str
    name: str
    detail: str = ""
    count: int = 0
    evidence: list[str] = field(default_factory=list)

    def merge(self, other: ParsedFeature) -> None:
        self.count += other.count
        for line in other.evidence:
            if line not in self.evidence and len(self.evidence) < 5:
                self.evidence.append(line)
        if not self.detail:
            self.detail = other.detail


@dataclass
class _Rule:
    category: str
    name: str
    pattern: re.Pattern[str]
    #: 拾った1行から詳しい値を作る。省くと行数だけ数える
    detail: str = ""


def _rule(category: str, name: str, pattern: str, detail: str = "") -> _Rule:
    return _Rule(category, name, re.compile(pattern, re.IGNORECASE), detail)


#: ベンダーをまたいで拾う表。機種ごとの差は、行を足して吸収する。
#: 「その機能が入っていると言い切れる書き方」だけを載せる。曖昧な語で
#: 拾うと、入っていない機能が台帳に載ってしまう
_RULES: tuple[_Rule, ...] = (
    # 通信の制限
    _rule(ACCESS_CONTROL, "ACL", r"^\s*(?:ip\s+|ipv6\s+|mac\s+)?access-list\s+"),
    _rule(ACCESS_CONTROL, "ACL", r"^\s*acl\s+(?:number|name|advanced|basic)\s"),
    _rule(ACCESS_CONTROL, "ACL の適用", r"^\s*ip\s+access-group\s"),
    _rule(ACCESS_CONTROL, "ACL の適用", r"^\s*packet-filter\s"),
    _rule(ACCESS_CONTROL, "ACL の適用", r"^\s*traffic-filter\s+(?:inbound|outbound)\s"),
    _rule(ACCESS_CONTROL, "フィルター", r"^\s*set\s+firewall\s+family\s"),
    _rule(ACCESS_CONTROL, "ポリシー", r"^\s*config\s+firewall\s+policy\s*$"),
    _rule(ACCESS_CONTROL, "ゾーン", r"^\s*config\s+system\s+zone\s*$"),
    # アドレス変換
    _rule(NAT, "NAT", r"^\s*ip\s+nat\s+(?:inside|outside|source|pool)\b"),
    _rule(NAT, "NAT", r"^\s*nat\s+(?:outbound|server|address-group)\b"),
    _rule(NAT, "NAT", r"^\s*set\s+security\s+nat\b"),
    _rule(NAT, "NAT", r"^\s*set\s+nat\s+(?:enable|disable)\b"),
    # アドレス配布
    _rule(DHCP, "DHCP サーバー", r"^\s*ip\s+dhcp\s+pool\s"),
    _rule(DHCP, "DHCP サーバー", r"^\s*dhcp\s+server\s+ip-pool\s"),
    _rule(DHCP, "DHCP リレー", r"^\s*ip\s+helper-address\s+(\S+)", "転送先"),
    _rule(DHCP, "DHCP リレー", r"^\s*dhcp\s+relay\s+server-address\s+(\S+)", "転送先"),
    # ループ防止
    _rule(SPANNING_TREE, "STP", r"^\s*spanning-tree\s+mode\s+(\S+)", "モード"),
    _rule(SPANNING_TREE, "STP", r"^\s*stp\s+(?:mode|global)\s+(\S+)", "モード"),
    _rule(
        SPANNING_TREE,
        "STP の優先度",
        r"^\s*spanning-tree\s+vlan\s+\S+\s+priority\s+(\S+)",
        "優先度",
    ),
    # Comware / VRP は `stp instance 0 priority 4096`、FastIron は VLAN の下に
    # `spanning-tree priority 4096` と書く
    _rule(
        SPANNING_TREE,
        "STP の優先度",
        r"^\s*stp\s+(?:instance\s+\d+\s+)?priority\s+(\S+)",
        "優先度",
    ),
    _rule(SPANNING_TREE, "STP の優先度", r"^\s*spanning-tree\s+priority\s+(\S+)", "優先度"),
    _rule(SPANNING_TREE, "エッジポート", r"^\s*spanning-tree\s+portfast\b"),
    _rule(SPANNING_TREE, "エッジポート", r"^\s*stp\s+edged-port\b"),
    # ポートの束ね
    _rule(AGGREGATION, "LAG", r"^\s*channel-group\s+(\d+)", "グループ"),
    _rule(AGGREGATION, "LAG", r"^\s*port\s+link-aggregation\s+group\s+(\d+)", "グループ"),
    _rule(AGGREGATION, "LAG", r"^\s*eth-trunk\s+(\d+)", "グループ"),
    _rule(AGGREGATION, "LAG", r"^\s*lag\s+\S+\s+\S+\s+id\s+(\d+)", "グループ"),
    _rule(AGGREGATION, "LAG", r"^\s*set\s+interfaces\s+ae\d+\b"),
    # Aruba CX は `lag 1`、アライドテレシスは `static-channel-group 1`
    _rule(AGGREGATION, "LAG", r"^\s+lag\s+(\d+)\s*$", "グループ"),
    _rule(AGGREGATION, "LAG", r"^\s*static-channel-group\s+(\d+)", "グループ"),
    # 優先制御
    _rule(QOS, "QoS", r"^\s*(?:policy-map|class-map)\s+"),
    _rule(QOS, "QoS", r"^\s*qos\s+(?:policy|apply|trust)\b"),
    _rule(QOS, "QoS", r"^\s*service-policy\s+"),
    # 端末の認証
    _rule(AUTHENTICATION, "802.1X", r"^\s*dot1x\s+(?:system-auth-control|pae|port-control)\b"),
    _rule(AUTHENTICATION, "802.1X", r"^\s*aaa\s+authentication\s+port-access\s+dot1x\b"),
    _rule(AUTHENTICATION, "802.1X", r"^\s*set\s+protocols\s+dot1x\b"),
    _rule(AUTHENTICATION, "802.1X", r"^\s*dot1x\s*$"),
    # VRP は `dot1x enable`、FastIron は `dot1x-enable`
    _rule(AUTHENTICATION, "802.1X", r"^\s*dot1x[- ]enable\b"),
    _rule(AUTHENTICATION, "ポートセキュリティ", r"^\s*switchport\s+port-security\b"),
    # FastIron は `port security`（ハイフン無し）
    _rule(AUTHENTICATION, "ポートセキュリティ", r"^\s*port\s+security\s*$"),
    _rule(AUTHENTICATION, "ポートセキュリティ", r"^\s*port-security\s+enable\b"),
    # 管理
    _rule(MANAGEMENT, "SNMP", r"^\s*snmp-server\s+(?:community|host|group)\b"),
    _rule(MANAGEMENT, "SNMP", r"^\s*snmp-agent\s"),
    _rule(MANAGEMENT, "SNMP", r"^\s*set\s+snmp\s+(?:community|trap-group)\b"),
    _rule(MANAGEMENT, "SNMP", r"^\s*config\s+system\s+snmp\s+(?:community|user)\s*$"),
    _rule(MANAGEMENT, "SNMP", r"^\s*set\s+deviceconfig\s+system\s+snmp-setting\b"),
    _rule(MANAGEMENT, "Syslog", r"^\s*logging\s+(?:host\s+)?(\d+\.\d+\.\d+\.\d+)", "送り先"),
    _rule(MANAGEMENT, "Syslog", r"^\s*info-center\s+loghost\s+(\S+)", "送り先"),
    # ASA は口の名前が間に入る（logging host inside 10.0.0.1）
    _rule(MANAGEMENT, "Syslog", r"^\s*logging\s+host\s+\S+\s+(\d+\.\d+\.\d+\.\d+)", "送り先"),
    # ヤマハ / Junos / アライドテレシス / FortiGate / PAN-OS
    _rule(MANAGEMENT, "Syslog", r"^\s*syslog\s+host\s+(\S+)", "送り先"),
    _rule(MANAGEMENT, "Syslog", r"^\s*set\s+system\s+syslog\s+host\s+(\S+)", "送り先"),
    _rule(MANAGEMENT, "Syslog", r"^\s*log\s+host\s+(\S+)", "送り先"),
    _rule(MANAGEMENT, "Syslog", r"^\s*config\s+log\s+syslogd\s+setting\s*$"),
    _rule(MANAGEMENT, "Syslog", r"^\s*set\s+shared\s+log-settings\s+syslog\s+(\S+)", "送り先"),
    _rule(MANAGEMENT, "NTP", r"^\s*ntp(?:-service)?\s+(?:server|unicast-server)\s+(\S+)", "参照先"),
    # ヤマハは ntpdate（schedule で定期実行する書き方も含む）
    _rule(MANAGEMENT, "NTP", r"^\s*(?:schedule\s+at\s+.*?\s)?ntpdate\s+(\S+)", "参照先"),
    _rule(MANAGEMENT, "NTP", r"^\s*set\s+system\s+ntp\s+server\s+(\S+)", "参照先"),
    _rule(MANAGEMENT, "NTP", r"^\s*set\s+ntpsync\s+enable\s*$"),
    _rule(
        MANAGEMENT,
        "NTP",
        r"^\s*set\s+deviceconfig\s+system\s+ntp-servers\s+\S+\s+ntp-server-address\s+(\S+)",
        "参照先",
    ),
    _rule(MANAGEMENT, "AAA", r"^\s*aaa\s+(?:new-model|authentication|authorization)\b"),
    # VRP は `aaa` の下に利用者を並べる
    _rule(MANAGEMENT, "AAA", r"^\s*local-user\s+\S+\s+privilege\b"),
    _rule(MANAGEMENT, "AAA", r"^\s*hwtacacs-server\s"),
    _rule(MANAGEMENT, "AAA", r"^\s*(?:radius|tacacs)-server\s"),
    _rule(MANAGEMENT, "SSH", r"^\s*transport\s+input\s+ssh\b"),
    _rule(MANAGEMENT, "SSH", r"^\s*protocol\s+inbound\s+ssh\b"),
    # 拠点間の暗号化
    _rule(VPN, "IPsec", r"^\s*crypto\s+(?:isakmp|ikev2|map|ipsec)\b"),
    _rule(VPN, "IPsec", r"^\s*ipsec\s+(?:policy|profile|transform-set)\b"),
    _rule(VPN, "IPsec", r"^\s*config\s+vpn\s+ipsec\b"),
    _rule(VPN, "IPsec", r"^\s*set\s+security\s+ipsec\b"),
    # ヤマハ（tunnel select の下に ipsec を書く）と NEC IX（autokey-map）
    _rule(VPN, "IPsec", r"^\s*ipsec\s+(?:ike|sa\s+policy|autokey-map|autokey-proposal)\b"),
    # マルチキャスト
    _rule(MULTICAST, "PIM", r"^\s*ip\s+pim\s+(?:sparse-mode|dense-mode|sparse-dense-mode)\b"),
    _rule(MULTICAST, "PIM", r"^\s*pim\s+(?:sm|dm)\b"),
    _rule(MULTICAST, "IGMP スヌーピング", r"^\s*ip\s+igmp\s+snooping\b"),
    _rule(MULTICAST, "IGMP スヌーピング", r"^\s*igmp-snooping\s+enable\b"),
    # 負荷分散
    _rule(LOAD_BALANCE, "仮想サーバー", r"^\s*ltm\s+virtual\s+"),
    _rule(LOAD_BALANCE, "仮想サーバー", r"^\s*config\s+firewall\s+vip\s*$"),
    _rule(LOAD_BALANCE, "サーバー負荷分散", r"^\s*(?:slb|server\s+farm)\b"),
    _rule(LOAD_BALANCE, "サーバー負荷分散", r"^\s*virtual-server\s+"),
    # 機器の冗長
    _rule(HIGH_AVAILABILITY, "HSRP", r"^\s*standby\s+\d+\s+ip\b"),
    _rule(HIGH_AVAILABILITY, "VRRP", r"^\s*vrrp\s+(?:vrid\s+)?\d+\s+(?:ip|virtual-ip)\b"),
    _rule(HIGH_AVAILABILITY, "スタック", r"^\s*(?:switch\s+\d+\s+provision|stack-port|irf-port)\b"),
    _rule(HIGH_AVAILABILITY, "機器間の同期", r"^\s*config\s+system\s+ha\s*$"),
    # Aruba CX は VSX、Arista は MLAG で2台を1台に見せる。
    # どちらも「片方を落としても通る」ことの前提になるので、入っていることを残す
    _rule(HIGH_AVAILABILITY, "VSX", r"^vsx\s*$"),
    _rule(HIGH_AVAILABILITY, "MLAG", r"^mlag\s+configuration\s*$"),
)


def parse(text: str) -> list[ParsedFeature]:
    """その Config で使っている機能を並べる。区分ごとにまとめて返す。"""
    found: dict[tuple[str, str], ParsedFeature] = {}
    details: dict[tuple[str, str], list[str]] = {}

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        for rule in _RULES:
            match = rule.pattern.match(line)
            if not match:
                continue
            key = (rule.category, rule.name)
            entry = found.get(key)
            if entry is None:
                entry = ParsedFeature(category=rule.category, name=rule.name)
                found[key] = entry
            entry.count += 1
            if len(entry.evidence) < 5:
                stripped = line.strip()
                if stripped not in entry.evidence:
                    entry.evidence.append(stripped)
            if rule.detail and match.groups():
                value = match.group(1)
                values = details.setdefault(key, [])
                if value not in values:
                    values.append(value)
            break  # 同じ行を2つの規則で数えない

    for key, values in details.items():
        rule_detail = next(
            (r.detail for r in _RULES if (r.category, r.name) == key and r.detail), ""
        )
        found[key].detail = f"{rule_detail}: {', '.join(values[:4])}"

    order = list(FEATURE_LABELS)
    return sorted(found.values(), key=lambda f: (order.index(f.category), f.name))
