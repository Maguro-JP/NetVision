"""ACL の1行と1行を、意味で比べる。

ACL は上から順に見て、最初に当たった行で決まる。だから「上の行が下の行を
覆っている」と、下の行は書いてあっても読まれない。これは文字の一致では
見つからない。`permit ip any any` と `deny tcp 10.0.0.0 0.0.0.255 any eq 23`
は、字面はまったく違うが、上が下を丸ごと覆っている。

ここでやるのは、行の条件（プロトコル・送信元・宛先・ポート）を集合として
見て、覆っているかどうかを言うことだけ。良し悪しの判断はしない。

読み取れない書き方（object-group、neq、名前で書かれたポート範囲）は
「覆っているとは言えない」側に倒す。言えないものを言うと、直す必要の無い
ものまで直させてしまうため。
"""

from __future__ import annotations

import ipaddress
import re

from app.services.configparse.base import ParsedAclEntry

#: 名前で書かれるポート。実機の出力にも設定にも、両方の書き方が出てくる
_PORT_NAMES = {
    "ftp-data": 20,
    "ftp": 21,
    "ssh": 22,
    "telnet": 23,
    "smtp": 25,
    "domain": 53,
    "bootps": 67,
    "bootpc": 68,
    "tftp": 69,
    "www": 80,
    "http": 80,
    "pop3": 110,
    "ntp": 123,
    "netbios-ns": 137,
    "snmp": 161,
    "snmptrap": 162,
    "bgp": 179,
    "ldap": 389,
    "https": 443,
    "syslog": 514,
    "isakmp": 500,
    "rdp": 3389,
}
#: 上位のプロトコルを全部含む書き方
_ANY_PROTOCOL = {None, "ip", "ipv4", "any", "ipv6"}

_ALL_PORTS = (0, 65535)


def covers(upper: ParsedAclEntry, lower: ParsedAclEntry) -> bool:
    """上の行が、下の行に当たる通信を残らず先に拾ってしまうか。

    True なら、下の行は読まれない。分からないときは False を返す。
    """
    if not _protocol_covers(upper.protocol, lower.protocol):
        return False
    if not _address_covers(upper.source, lower.source):
        return False
    if not _address_covers(upper.destination, lower.destination):
        return False
    if not _port_covers(upper.source_port, lower.source_port):
        return False
    return _port_covers(upper.destination_port, lower.destination_port)


def _protocol_covers(upper: str | None, lower: str | None) -> bool:
    if upper in _ANY_PROTOCOL:
        return True
    if lower in _ANY_PROTOCOL:
        return False
    return upper == lower


def network_of(text: str | None) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    """ACL の書き方を、アドレスの集合にそろえる。

    `any` / `host 10.0.0.1` / `10.0.0.0 0.0.0.255` / `10.0.0.0/24` を扱う。
    object-group のように、その Config だけでは中身の分からないものは None。
    """
    if text is None:
        return ipaddress.ip_network("0.0.0.0/0")
    value = text.strip()
    if value.lower() == "any":
        return ipaddress.ip_network("0.0.0.0/0")
    if value.lower().startswith("host "):
        try:
            return ipaddress.ip_network(f"{value.split()[1]}/32", strict=False)
        except ValueError:
            return None
    if value.lower().startswith(("object-group", "addrgroup")):
        return None
    parts = value.split()
    try:
        if len(parts) == 2:
            # ワイルドカードマスク（0.0.0.255）。連続していないものは集合にできない
            mask = _wildcard_to_prefix(parts[1])
            if mask is None:
                return None
            return ipaddress.ip_network(f"{parts[0]}/{mask}", strict=False)
        return ipaddress.ip_network(parts[0], strict=False)
    except ValueError:
        return None


def _wildcard_to_prefix(mask: str) -> int | None:
    """0.0.0.255 のような書き方を /24 に直す。まとまらない指定は None。

    ワイルドカードは1ビットずつ「どこでもよい」を指せるので、
    0.0.0.254 のような飛び飛びの範囲も書ける。それは1つの集合にならない。
    """
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", mask):
        return None
    try:
        wildcard = int(ipaddress.ip_address(mask))
    except ValueError:
        return None
    netmask = wildcard ^ 0xFFFFFFFF
    if (wildcard + 1) & wildcard:  # 下位から連続して立っていない
        return None
    return bin(netmask).count("1")


def _address_covers(upper: str | None, lower: str | None) -> bool:
    top = network_of(upper)
    bottom = network_of(lower)
    if top is None or bottom is None:
        return False
    if top.version != bottom.version:
        return False
    return bottom.subnet_of(top)  # type: ignore[arg-type]


def port_range(text: str | None) -> tuple[int, int] | None:
    """ポートの指定を、番号の範囲にそろえる。読めないものは None。"""
    if text is None:
        return _ALL_PORTS
    tokens = text.split()
    if not tokens:
        return _ALL_PORTS
    operator = tokens[0].lower()
    values = [_port_number(token) for token in tokens[1:]]
    if any(value is None for value in values):
        return None
    numbers = [value for value in values if value is not None]
    if operator == "eq" and len(numbers) == 1:
        return (numbers[0], numbers[0])
    if operator == "range" and len(numbers) == 2:
        return (numbers[0], numbers[1])
    if operator == "gt" and len(numbers) == 1:
        return (numbers[0] + 1, 65535)
    if operator == "lt" and len(numbers) == 1:
        return (0, numbers[0] - 1)
    # neq は1つの範囲にならない。読めないものとして扱う
    return None


def _port_number(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return _PORT_NAMES.get(token.lower())


def _port_covers(upper: str | None, lower: str | None) -> bool:
    top = port_range(upper)
    bottom = port_range(lower)
    if top is None or bottom is None:
        return False
    return top[0] <= bottom[0] and bottom[1] <= top[1]


def is_wide_open(entry: ParsedAclEntry) -> bool:
    """送信元も宛先もポートも限定していない行かどうか。"""
    return covers(entry, ParsedAclEntry(action=entry.action, protocol=None))
