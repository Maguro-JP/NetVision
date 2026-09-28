"""負荷分散の中身を読む。

「サービスに繋がらない」の原因が、機器ではなく振り分けの設定にあることは
多い。仮想サーバーだけ作ってプールを空にした、健全性監視を付けずに
落ちた機に流し続けている、監視を厳しくしすぎて全部落ちた扱いになっている。
どれも Config を見れば分かる。

読めるのは次の書き方。

- F5 BIG-IP `ltm virtual` / `ltm pool` / `ltm monitor`（tmsh の保存形式）
- FortiGate `config firewall vip` の `type server-load-balance`
- A10 Thunder（ACOS）の `slb server` / `slb service-group` / `slb virtual-server`

判断はしない。判断は loadbalancereview.py の仕事。

出典（2026-09-29 JST に確認）
- F5: BIG-IP TMSH Manual の「ltm virtual」（destination の IPv4 `a.b.c.d:port` と
  IPv6 `a:b:c:d:e:f:g:h.port`、`disabled`）と「ltm pool」（members の `node_name:port`、
  `address`、`session user-disabled`、`state user-down`、`load-balancing-mode`、`monitor`）
- A10: vThunder Installation Guide（GCP）の SLB の設定例と、ARM テンプレートの
  「Verify Deployment」の show running-config（`member s1 80`、字下げした
  `port 80 http` と `service-group sg80`、`use-if-ip`）
- FortiGate の realservers の書き方は未確認（既存の試験の形のまま）
- A10 の受け口は、口（port）ごとに別のサービスグループを結べる。ここでは最後の
  口だけを持つ（ParsedVirtualServer が口を1つしか持たないため）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HINT = re.compile(r"ltm\s+(?:virtual|pool|monitor)|server-load-balance|\bslb\b", re.IGNORECASE)


@dataclass
class ParsedMember:
    """振り分け先の1台。

    F5 は名前（ノード名）で書けるので、名前と実際のアドレスは別に持つ。
    名前だけ見ていると「どこへ流れているのか」が言えない。
    """

    address: str
    port: str | None = None
    enabled: bool = True
    #: 名前で書かれている場合の、その名前
    name: str | None = None

    @property
    def label(self) -> str:
        if self.name and self.name != self.address:
            return f"{self.name}（{self.address}）"
        return self.address


@dataclass
class ParsedPool:
    """振り分け先のまとまり。"""

    name: str
    members: list[ParsedMember] = field(default_factory=list)
    #: 健全性監視の名前。空なら「落ちていても流し続ける」状態
    monitors: list[str] = field(default_factory=list)
    method: str | None = None

    @property
    def alive(self) -> list[ParsedMember]:
        return [item for item in self.members if item.enabled]


@dataclass
class ParsedVirtualServer:
    """外から見える受け口。"""

    name: str
    address: str | None = None
    port: str | None = None
    pool: str | None = None
    enabled: bool = True


@dataclass
class ParsedLoadBalance:
    virtual_servers: list[ParsedVirtualServer] = field(default_factory=list)
    pools: list[ParsedPool] = field(default_factory=list)
    #: 定義されている監視の名前（仮想サーバー側から参照の有無を見るのに使う）
    monitors: list[str] = field(default_factory=list)

    @property
    def in_use(self) -> bool:
        return bool(self.virtual_servers or self.pools)

    def pool(self, name: str | None) -> ParsedPool | None:
        for item in self.pools:
            if item.name == name:
                return item
        return None


def has_any(text: str) -> bool:
    return bool(_HINT.search(text))


# ---------------------------------------------------------------------------
# F5 BIG-IP（tmsh の保存形式）
# ---------------------------------------------------------------------------

_F5_HEAD = re.compile(r"^ltm\s+(virtual|pool|monitor)\s+(?:\S+\s+)??(\S+)\s*\{\s*$", re.IGNORECASE)
_F5_DEST = re.compile(r"^\s*destination\s+(\S+)\s*$", re.IGNORECASE)
_F5_POOL = re.compile(r"^\s*pool\s+(\S+)\s*$", re.IGNORECASE)
#: `/Common/web1:80 {`、IPv6 は `/Common/2001:db8::1.80 {`。分け方は _f5_endpoint
_F5_MEMBER = re.compile(r"^\s*(\S+[:.][\w-]+)\s*\{\s*$")
#: ポートの位置に来るもの。番号か、サービス名（http、any など）
_F5_PORT = re.compile(r"^[\w-]+$")
_F5_MONITOR = re.compile(r"^\s*monitor\s+(.+?)\s*$", re.IGNORECASE)
_F5_METHOD = re.compile(r"^\s*load-balancing-mode\s+(\S+)\s*$", re.IGNORECASE)
_F5_ADDRESS = re.compile(r"^\s*address\s+(\S+)\s*$", re.IGNORECASE)
#: 手で止めた状態。監視で落ちている状態は、保存した設定には出ない
_F5_STOPPED = re.compile(r"^\s*(?:session\s+user-disabled|state\s+user-down)\s*$", re.IGNORECASE)


def _f5_name(raw: str) -> str:
    """/Common/web_pool を web_pool として扱う。区画は判断に効かない。"""
    return raw.rsplit("/", 1)[-1]


def _f5_endpoint(raw: str) -> tuple[str, str | None]:
    """`アドレス:ポート` を分ける。IPv6 はコロンを含むので `アドレス.ポート` と書かれる。

    出典: F5 BIG-IP TMSH Manual「ltm virtual」の destination
    （The format for "ipv4" is a.b.c.d[:port]. The format for an "ipv6" address is
    a:b:c:d:e:f:g:h[.port]）。
    """
    text = _f5_name(raw)
    if text.count(":") >= 2:
        address, dot, port = text.rpartition(".")
        if dot and ":" in address and _F5_PORT.match(port):
            return address, port
        return text, None
    address, colon, port = text.rpartition(":")
    if colon and address:
        return address, port
    return text, None


def parse_f5(text: str, found: ParsedLoadBalance) -> None:
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        head = _F5_HEAD.match(lines[index])
        if head is None:
            index += 1
            continue
        kind, name = head.group(1).lower(), _f5_name(head.group(2))
        depth = 1
        index += 1
        virtual = ParsedVirtualServer(name=name) if kind == "virtual" else None
        pool = ParsedPool(name=name) if kind == "pool" else None
        member: ParsedMember | None = None
        member_depth = 0

        while index < len(lines) and depth > 0:
            line = lines[index]
            stripped = line.strip()
            opened = stripped.endswith("{")
            if kind == "pool" and depth >= 2:
                entry = _F5_MEMBER.match(line)
                if entry:
                    label, port = _f5_endpoint(entry.group(1))
                    member = ParsedMember(address=label, name=label, port=port)
                    assert pool is not None
                    pool.members.append(member)
                    member_depth = depth
                elif member is not None:
                    address = _F5_ADDRESS.match(line)
                    if address:
                        # 名前で書かれていても、実際の宛先はこの行にある
                        member.address = address.group(1)
                    elif _F5_STOPPED.match(line):
                        # 手で止めたものだけを「止めてある」とする。監視で落ちて
                        # いる状態は、保存した設定には残らない
                        member.enabled = False

            if virtual is not None and depth == 1:
                destination = _F5_DEST.match(line)
                if destination:
                    virtual.address, virtual.port = _f5_endpoint(destination.group(1))
                pool_ref = _F5_POOL.match(line)
                if pool_ref:
                    virtual.pool = _f5_name(pool_ref.group(1))
                if stripped in {"disabled", "state disabled"}:
                    virtual.enabled = False
            if pool is not None and depth == 1:
                monitor = _F5_MONITOR.match(line)
                if monitor:
                    pool.monitors = [
                        _f5_name(word)
                        for word in monitor.group(1).split()
                        if word not in {"and", "min", "of", "{", "}"} and not word.isdigit()
                    ]
                method = _F5_METHOD.match(line)
                if method:
                    pool.method = method.group(1)

            depth += 1 if opened else 0
            if stripped.startswith("}"):
                depth -= 1
                if member is not None and depth < member_depth:
                    member = None
            index += 1

        if virtual is not None:
            found.virtual_servers.append(virtual)
        if pool is not None:
            found.pools.append(pool)
        if kind == "monitor":
            found.monitors.append(name)


# ---------------------------------------------------------------------------
# FortiGate（config firewall vip の負荷分散）
# ---------------------------------------------------------------------------


def parse_fortios(text: str, found: ParsedLoadBalance) -> None:
    from app.services.configparse import fortitext

    for name, values in fortitext.blocks(text, "firewall vip"):
        if values.get("type", "").lower() != "server-load-balance":
            continue
        virtual = ParsedVirtualServer(
            name=name,
            address=values.get("extip"),
            port=values.get("extport"),
            pool=name,
            enabled=values.get("status", "enable").lower() != "disable",
        )
        found.virtual_servers.append(virtual)
        pool = ParsedPool(name=name, method=values.get("ldb-method"))
        monitor = values.get("monitor")
        if monitor:
            pool.monitors = [word.strip('"') for word in monitor.split()]
        found.pools.append(pool)

    # 振り分け先は realservers として、vip の中の入れ子で書かれる
    _fortios_members(text, found)


_FG_VIP = re.compile(r'^\s*edit\s+"?([^"]+)"?\s*$', re.IGNORECASE)


def parse_fortios_members(text: str, found: ParsedLoadBalance) -> None:
    _fortios_members(text, found)


def _fortios_members(text: str, found: ParsedLoadBalance) -> None:
    """`config realservers` の中の edit を、その vip のプールに足す。"""
    vip: str | None = None
    inside = False
    current: ParsedMember | None = None
    for raw in text.splitlines():
        line = raw.strip()
        lowered = line.lower()
        if lowered == "config firewall vip":
            vip = None
            continue
        if lowered == "config realservers":
            inside = True
            continue
        if inside and lowered == "end":
            inside = False
            current = None
            continue
        if not inside:
            head = _FG_VIP.match(line)
            if head:
                vip = head.group(1)
            continue
        if lowered.startswith("edit "):
            current = ParsedMember(address="")
            pool = found.pool(vip)
            if pool is not None:
                pool.members.append(current)
            continue
        if current is None:
            continue
        if lowered.startswith("set ip "):
            current.address = line.split()[2].strip('"')
        elif lowered.startswith("set port "):
            current.port = line.split()[2].strip('"')
        elif lowered.startswith("set status ") and "disable" in lowered:
            current.enabled = False


# ---------------------------------------------------------------------------
# A10 / ACE 系（slb）
# ---------------------------------------------------------------------------

_SLB_SERVER = re.compile(r"^slb\s+server\s+(\S+)\s+(\S+)\s*$", re.IGNORECASE)
_SLB_GROUP = re.compile(r"^slb\s+service-group\s+(\S+)(?:\s+(\S+))?\s*$", re.IGNORECASE)
#: `slb virtual-server vip 10.0.2.9`。受け口のアドレスを口から借りる
#: `slb virtual-server vip use-if-ip ethernet 1` もある（アドレスは Config に出ない）
_SLB_VIRTUAL = re.compile(r"^slb\s+virtual-server\s+(\S+)(?:\s+(.+?))?\s*$", re.IGNORECASE)


def parse_slb(text: str, found: ParsedLoadBalance) -> None:
    servers: dict[str, str] = {}
    group: ParsedPool | None = None
    virtual: ParsedVirtualServer | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        server = _SLB_SERVER.match(stripped)
        if server:
            servers[server.group(1)] = server.group(2)
            group, virtual = None, None
            continue
        head = _SLB_GROUP.match(stripped)
        if head:
            group = ParsedPool(name=head.group(1))
            found.pools.append(group)
            virtual = None
            continue
        vs = _SLB_VIRTUAL.match(stripped)
        if vs:
            rest = (vs.group(2) or "").split()
            address = rest[0] if rest and rest[0].lower() != "use-if-ip" else None
            virtual = ParsedVirtualServer(name=vs.group(1), address=address)
            found.virtual_servers.append(virtual)
            group = None
            continue
        if stripped and not line.startswith((" ", "\t")):
            group, virtual = None, None
            continue

        if group is not None:
            member = re.match(r"^member\s+(\S+)(?::|\s+)(\S+)\s*$", stripped, re.IGNORECASE)
            if member:
                name = member.group(1)
                group.members.append(
                    ParsedMember(address=servers.get(name, name), port=member.group(2))
                )
                continue
            health = re.match(r"^health-check\s+(\S+)", stripped, re.IGNORECASE)
            if health:
                group.monitors.append(health.group(1))
                continue
            method = re.match(r"^method\s+(\S+)", stripped, re.IGNORECASE)
            if method:
                group.method = method.group(1)
        if virtual is not None:
            used = re.match(r"^\s*service-group\s+(\S+)", stripped, re.IGNORECASE)
            if used:
                virtual.pool = used.group(1)
                continue
            port = re.match(r"^port\s+(\S+)", stripped, re.IGNORECASE)
            if port:
                virtual.port = port.group(1)


def parse(text: str, vendor: str = "f5") -> ParsedLoadBalance | None:
    """書いてあれば読む。1行も無ければ None。"""
    if not has_any(text):
        return None
    found = ParsedLoadBalance()
    if "server-load-balance" in text.lower():
        parse_fortios(text, found)
    if re.search(r"^ltm\s+", text, re.MULTILINE | re.IGNORECASE):
        parse_f5(text, found)
    if re.search(r"^slb\s+", text, re.MULTILINE | re.IGNORECASE):
        parse_slb(text, found)
    return found if found.in_use else None
