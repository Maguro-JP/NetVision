"""NEC の機器を読む。

NEC は系統が2つあり、書式がまったく違うので分けて扱う。

- IX シリーズ（IX2000 / IX3000）… 自社系のルーター。`device-name` でホスト名、
  インターフェースは `GigaEthernet0.0` のようにサブインターフェース番号まで含む
- QX シリーズ（QX-S）… H3C Comware ベースのスイッチ。機器名は `sysname`
  （`hostname` ではない）。否定は `no` ではなく `undo`。running-config
  だけでも sysname で判別する

どちらも Cisco 風に見える箇所があるため、判別は特徴的な語で確実に行う。
`interface` や `description` のような共通語で判定すると Cisco と混同する。
"""

from __future__ import annotations

import re

from app.services.configparse import policy, routing, tunnels, vlanlist
from app.services.configparse.base import (
    NeighborProtocol,
    ParsedConfig,
    ParsedCrypto,
    ParsedCryptoMapEntry,
    ParsedDhcp,
    ParsedDhcpPool,
    ParsedIkePolicy,
    ParsedInterface,
    ParsedNat,
    ParsedNatPool,
    ParsedNatRule,
    ParsedNeighbor,
    ParsedQos,
    ParsedQosAction,
    ParsedQosClass,
    ParsedQosPolicy,
    ParsedTransformSet,
    ParsedVrf,
    Vendor,
)

# --- IX -------------------------------------------------------------------

_IX_BANNER = re.compile(
    r"IX\s*Series\s+(IX\d+\w*)\s*\([^)]*\)\s*Software,\s*Version\s+([\w.]+)", re.IGNORECASE
)
_IX_DEVICE_NAME = re.compile(r"^\s*device-name\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_IX_HOSTNAME_LINE = re.compile(r"^\s*Hostname:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_IX_SERIAL = re.compile(
    r"^\s*(?:System\s+)?[Ss]erial\s*(?:number|No\.?)\s*[:=]?\s*(\S+)", re.MULTILINE
)
_IX_INTERFACE = re.compile(r"^interface\s+(\S+)\s*$", re.IGNORECASE)
_IX_IP = re.compile(r"^\s*ip\s+address\s+(\d+\.\d+\.\d+\.\d+(?:/\d+)?)", re.IGNORECASE)
# 回線から貰う設定。アドレスは決め打ちできないが、使っているポートだとは分かる
_IX_IP_LEARNED = re.compile(r"^\s*ip\s+address\s+(dhcp|ipcp)\b", re.IGNORECASE)
_IX_NO_SHUTDOWN = re.compile(r"^\s*no\s+shutdown\s*$", re.IGNORECASE)
# サブインターフェースへの VLAN 割り当て。書き方が数種あるのでまとめて見る
_IX_VLAN_TAG = re.compile(
    r"^\s*(?:encapsulation\s+dot1q|vlan-id|ip\s+dot1q\s+vlan)\s+(\d+)", re.IGNORECASE
)
_IX_VRF_DEF = re.compile(r"^(?:ip\s+)?vrf\s+(?:definition\s+)?(\S+)\s*$", re.IGNORECASE)
_IX_VRF_FORWARDING = re.compile(r"^\s*(?:ip\s+)?vrf\s+forwarding\s+(\S+)", re.IGNORECASE)

# --- QX（Comware）---------------------------------------------------------

_QX_MODEL = re.compile(r"\b(?:NEC\s+)?(QX-S\d+\w*)\b", re.IGNORECASE)
_QX_COMWARE_VERSION = re.compile(
    r"Comware\s+(?:Software|Platform Software).*?Version\s+([\w.]+)", re.IGNORECASE | re.DOTALL
)
#: 設定の先頭に出る ` version 7.1.070, Release 3506P05`。show を付けずに
#: 設定だけ貼られたときは、版が分かるのはこの行しかない
_QX_CONFIG_VERSION = re.compile(r"^\s*version\s+(\d[\w.]*)(?:,|\s|$)", re.IGNORECASE | re.MULTILINE)
_QX_SYSNAME = re.compile(r'^\s*sysname\s+"?([^"\n]+?)"?\s*$', re.IGNORECASE | re.MULTILINE)
_HOSTNAME_CMD = re.compile(r"^\s*hostname\s+\S+", re.IGNORECASE | re.MULTILINE)
_IX_IFACE = re.compile(r"^interface\s+GigaEthernet", re.IGNORECASE | re.MULTILINE)
_QX_HINT = re.compile(
    r"Comware|\bH3C\b|\bNEC\b|port\s+link-type|interface\s+Vlan-interface|^\s*undo\s+",
    re.IGNORECASE | re.MULTILINE,
)
_QX_INTERFACE = re.compile(r"^interface\s+(\S+)\s*$", re.IGNORECASE)
_QX_IP = re.compile(
    r"^\s*ip\s+address\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)", re.IGNORECASE
)
# `display current-configuration` は行頭に空白が1つ入る。行頭固定で見ると
# VLAN の定義を丸ごと取りこぼす（VLAN 名が台帳に載らない）
_QX_VLAN = re.compile(r"^\s*vlan\s+(\d+)\s*$", re.IGNORECASE)
# `vlan 20 to 22` / `vlan 10 20 30` のまとめ書き
_QX_VLAN_RANGE = re.compile(r"^\s*vlan\s+(\d[\d\s]*(?:to\s+\d+)?[\d\s]*)$", re.IGNORECASE)
# LAG（Comware では Bridge-Aggregation）への所属
# Comware は `port link-aggregation group 1`、VRP は `eth-trunk 1`。同じ意味
_QX_LAG_MEMBER = re.compile(
    r"^\s*(?:port\s+link-aggregation\s+group|eth-trunk)\s+(\d+)", re.IGNORECASE
)
_QX_ACCESS_VLAN = re.compile(r"^\s*port\s+access\s+vlan\s+(\d+)", re.IGNORECASE)
_QX_LINK_TYPE = re.compile(r"^\s*port\s+link-type\s+(\S+)", re.IGNORECASE)
# `port trunk permit vlan 10 20 30 to 40` / `port trunk permit vlan all`
_QX_TRUNK_PERMIT = re.compile(r"^\s*port\s+trunk\s+permit\s+vlan\s+(.+?)\s*$", re.IGNORECASE)
# ネイティブ VLAN にあたるのが PVID
_QX_PVID = re.compile(r"^\s*port\s+(?:trunk|hybrid)\s+pvid\s+vlan\s+(\d+)", re.IGNORECASE)
# hybrid ポートは tagged / untagged を並べて書く
_QX_HYBRID_VLAN = re.compile(
    r"^\s*port\s+hybrid\s+vlan\s+(.+?)\s+(tagged|untagged)\s*$", re.IGNORECASE
)
# VRF にあたるのが VPN インスタンス
_QX_VPN_INSTANCE = re.compile(r"^ip\s+vpn-instance\s+(\S+)", re.IGNORECASE)
_QX_BIND_VPN = re.compile(
    r"^\s*(?:ip\s+binding\s+vpn-instance|vpn-instance)\s+(\S+)", re.IGNORECASE
)
# `display device manuinfo` / `display esn`。電源・FAN のシリアルは捨て、シャーシ側を取る
_QX_MANUINFO_HEADER = re.compile(
    r"^(Slot\s+\d+|Chassis(?:\s+\d+)?|Power(?:Supply)?\s+\d+|PSU\s+\d+|Fan\s+\d+)\s*:",
    re.IGNORECASE,
)
_QX_DEVICE_SERIAL = re.compile(r"^DEVICE_SERIAL_NUMBER\s*:\s*(\S+)", re.IGNORECASE)
_QX_DEVICE_NAME_FIELD = re.compile(r"^DEVICE_NAME\s*:\s*(.+)$", re.IGNORECASE)
_QX_ESN = re.compile(r"^ESN of slot\s+\d+\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_QX_SERIAL_SENTENCE = re.compile(
    r"^The Serial Number of the device is:\s*(\S+)", re.IGNORECASE | re.MULTILINE
)
_INVALID_SERIALS = frozenset({"none", "n/a", "na", "null", "unknown", "unavailable", "--", "-"})

_DESCRIPTION = re.compile(r"^\s*description\s+(.+?)\s*$", re.IGNORECASE)

# 実機の画面をそのまま貼ったファイルには、設定が無くてもプロンプトに機器名が出る。
# ここを読まないと、show 出力だけのファイルが台帳のどの機器か分からない
_QX_PROMPT = re.compile(r"^[<\[]([A-Za-z0-9][\w.\-]*)[>\]]", re.MULTILINE)
_IX_PROMPT = re.compile(r"^([A-Za-z0-9][\w.\-]*)[#>]\s*\S", re.MULTILINE)


def _from_prompt(text: str, pattern: re.Pattern[str]) -> str | None:
    """プロンプトに出ている機器名。いちばん多く出てくるものを採る。"""
    names: dict[str, int] = {}
    for match in pattern.finditer(text):
        name = match.group(1)
        names[name] = names.get(name, 0) + 1
    if not names:
        return None
    return max(names.items(), key=lambda item: item[1])[0]


def _looks_like_vlan_block(raw: str) -> bool:
    """インターフェースの中の行ではなく、VLAN の定義行か。

    Comware はどちらも空白で始まる。VLAN の定義は `vlan 10` だけの行で、
    ポートの設定は `port access vlan 10` のように前に語が付く。
    """
    return bool(re.match(r"^\s*vlan\s+\d+\s*$", raw, re.IGNORECASE))


def _with_prefix(address: str, mask: str) -> str:
    """`10.0.0.1 255.255.255.0` を `10.0.0.1/24` にする。"""
    try:
        bits = sum(bin(int(part)).count("1") for part in mask.split("."))
    except ValueError:
        return address
    if not 0 <= bits <= 32:
        return address
    return f"{address}/{bits}"


def _unquote(value: str) -> str:
    return value.strip().strip('"')


def detect(text: str) -> Vendor | None:
    """NEC かどうかを見る。共通語ではなく、その機種にしか出ない語で判別する。"""
    if _IX_BANNER.search(text) or _IX_DEVICE_NAME.search(text) or _IX_IFACE.search(text):
        return Vendor.NEC_IX
    # QX は Comware ベース。現場では running-config だけ（sysname のみ、
    # Comware のバナーも型番も無い）が来ることが多い
    if _QX_MODEL.search(text):
        return Vendor.NEC_QX
    if _QX_SYSNAME.search(text) and (_QX_HINT.search(text) or not _HOSTNAME_CMD.search(text)):
        return Vendor.NEC_QX
    return None


def parse(text: str, vendor: Vendor) -> ParsedConfig:
    if vendor is Vendor.NEC_QX:
        return _parse_qx(text)
    return _parse_ix(text)


# ---------------------------------------------------------------------------
# IX
# ---------------------------------------------------------------------------


def _parse_ix(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.NEC_IX, os_name="IX")

    banner = _IX_BANNER.search(text)
    if banner:
        parsed.model = banner.group(1)
        parsed.os_version = banner.group(2)

    name = (
        _IX_DEVICE_NAME.search(text) or _IX_HOSTNAME_LINE.search(text) or _QX_SYSNAME.search(text)
    )
    if name:
        parsed.hostname = _unquote(name.group(1))
    else:
        parsed.hostname = _from_prompt(text, _IX_PROMPT)

    serial = _IX_SERIAL.search(text)
    if serial:
        parsed.serial_number = serial.group(1)

    # IX のインターフェースは、既定では止まっている（`no shutdown` を書いて初めて
    # 上がる）。書いていないポートを「使っている」と読むと、空きポートの数を
    # 間違える。config のブロックとして出てきたものだけ、この規則で見る
    started: set[str] = set()
    from_config: set[str] = set()

    current: ParsedInterface | None = None
    for raw in text.splitlines():
        match = _IX_INTERFACE.match(raw)
        if match:
            current = parsed.add_interface(ParsedInterface(name=match.group(1)))
            from_config.add(current.key)
            continue
        if current is None:
            continue
        if raw and not raw[0].isspace():
            # インデントが戻ったらブロックの外
            current = None
            continue

        description = _DESCRIPTION.match(raw)
        if description and current.description is None:
            current.description = description.group(1)
            continue
        ip = _IX_IP.match(raw)
        if ip and current.ip_address is None:
            current.ip_address = ip.group(1)
            continue
        # IX はサブインターフェースにタグを載せる（GigaEthernet0.1 に VLAN 10 など）
        tag = _IX_VLAN_TAG.match(raw)
        if tag:
            current.vlan = int(tag.group(1))
            current.mode = current.mode or "access"
            continue
        vrf = _IX_VRF_FORWARDING.match(raw)
        if vrf:
            current.vrf = vrf.group(1)
            continue
        if _IX_IP_LEARNED.match(raw) and current.ip_address is None:
            current.ip_address = None  # 貰うアドレスなので台帳には書かない
            current.mode = current.mode or "routed"
            continue
        if _IX_NO_SHUTDOWN.match(raw):
            started.add(current.key)
            continue
        if re.match(r"^\s*shutdown\s*$", raw, re.IGNORECASE):
            current.enabled = False

    for interface in parsed.interfaces:
        if interface.key in from_config and interface.key not in started:
            interface.enabled = False

    _parse_ix_vrfs(text, parsed)
    _parse_ix_lldp(text, parsed)
    tunnels.parse_block_style(text, parsed)
    routing.parse_ix(text, parsed)
    policy.parse_ix(text, parsed)
    _parse_ix_napt(text, parsed)
    _parse_ix_dhcp(text, parsed)
    _parse_ix_ipsec(text, parsed)
    return parsed


# --- IX の変換と配布 --------------------------------------------------------
#
# IX は Cisco とも Comware とも違う書き方をする。読めるのは、実機の設定で
# ふつうに出てくる形（ip napt enable / ip napt static / ip dhcp profile）に
# 限ってある。自信の無い書き方を当てずっぽうで読むと、間違った断定が資料に
# 載るので、読めないものは読めないままにしてある。

_IX_NAPT_ENABLE = re.compile(r"^\s*ip\s+napt\s+enable\s*$", re.IGNORECASE)
_IX_NAPT_STATIC = re.compile(r"^\s*ip\s+napt\s+static\s+(.+)$", re.IGNORECASE)
_IX_DHCP_PROFILE = re.compile(r"^ip\s+dhcp\s+profile\s+(\S+)", re.IGNORECASE)
_IX_DHCP_BINDING = re.compile(r"^\s*ip\s+dhcp\s+binding\s+(\S+)", re.IGNORECASE)
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _parse_ix_napt(text: str, parsed: ParsedConfig) -> None:
    """NAPT を読む。IX は変換をポートごとに有効にする。"""
    nat = ParsedNat()
    interface: str | None = None

    for raw in text.splitlines():
        head = _IX_INTERFACE.match(raw)
        if head:
            interface = head.group(1)
            continue
        if _IX_NAPT_ENABLE.match(raw):
            # 出口のアドレスに束ねる。Cisco の overload と同じ
            nat.rules.append(
                ParsedNatRule(
                    kind="dynamic",
                    direction="inside",
                    matched="内側から出る通信",
                    translated=interface or "出口のアドレス",
                    overload=True,
                    matched_is_name=False,
                    raw=f"interface {interface} ip napt enable",
                )
            )
            if interface and interface not in nat.outside_interfaces:
                nat.outside_interfaces.append(interface)
            continue
        static = _IX_NAPT_STATIC.match(raw)
        if static:
            # 引数の並びは版で違うので、順番には頼らない。読み取れるのは
            # 「外側と内側のアドレス」までにして、原文をそのまま残す
            addresses = _IPV4.findall(static.group(1))
            nat.rules.append(
                ParsedNatRule(
                    kind="static-port",
                    direction="outside",
                    matched=addresses[0] if addresses else None,
                    translated=addresses[1] if len(addresses) > 1 else None,
                    matched_is_name=False,
                    raw=raw.strip(),
                )
            )

    if nat.rules:
        # 内側は「napt を掛けていないポート」なので、ここでは決められない。
        # 片側しか無いことを理由にした指摘が出ないよう、両方を埋めておく
        nat.inside_interfaces = [
            i.name for i in parsed.interfaces if i.name not in nat.outside_interfaces
        ]
        parsed.nat = nat


def _parse_ix_dhcp(text: str, parsed: ParsedConfig) -> None:
    """配るアドレスを読む。IX は profile を作って、ポートに結ぶ。"""
    dhcp = ParsedDhcp()
    pools: dict[str, ParsedDhcpPool] = {}
    current: ParsedDhcpPool | None = None
    interface: str | None = None

    for raw in text.splitlines():
        head = _IX_DHCP_PROFILE.match(raw)
        if head:
            current = ParsedDhcpPool(name=f"dhcp profile {head.group(1)}")
            pools[head.group(1)] = current
            dhcp.pools.append(current)
            interface = None
            continue
        if not raw.startswith((" ", "\t")):
            found = _IX_INTERFACE.match(raw)
            interface = found.group(1) if found else None
            current = None
        body = raw.strip()
        if current is not None:
            _ix_dhcp_detail(current, body)
            continue
        bound = _IX_DHCP_BINDING.match(raw)
        if bound and bound.group(1) in pools and interface:
            pool = pools[bound.group(1)]
            pool.name = f"{pool.name}（{interface}）"

    if dhcp.pools:
        parsed.dhcp = dhcp


def _ix_dhcp_detail(pool: ParsedDhcpPool, body: str) -> None:
    found = re.match(r"^assignable-range\s+(\S+)\s+(\S+)", body, re.IGNORECASE)
    if found:
        pool.range_start, pool.range_end = found.group(1), found.group(2)
        return
    found = re.match(r"^network\s+(\S+)", body, re.IGNORECASE)
    if found:
        pool.network = found.group(1)
        return
    found = re.match(r"^default-gateway\s+(\S+)", body, re.IGNORECASE)
    if found:
        pool.default_routers.append(found.group(1))
        return
    found = re.match(r"^dns-server\s+(.+)$", body, re.IGNORECASE)
    if found:
        pool.dns_servers.extend(found.group(1).split())
        return
    found = re.match(r"^lease-time\s+(\S+)", body, re.IGNORECASE)
    if found:
        pool.lease = found.group(1)
        return
    found = re.match(r"^domain-name\s+(\S+)", body, re.IGNORECASE)
    if found:
        pool.domain = found.group(1)


def _parse_ix_vrfs(text: str, parsed: ParsedConfig) -> None:
    """IX の VRF（VRF-lite）を読む。ルーターの中に経路表を分けて持つ機能。"""
    current: ParsedVrf | None = None
    for raw in text.splitlines():
        match = _IX_VRF_DEF.match(raw)
        if match:
            current = parsed.add_vrf(ParsedVrf(name=match.group(1)))
            continue
        if current is None:
            continue
        if raw and not raw[0].isspace():
            current = None
            continue
        stripped = raw.strip()
        if stripped.startswith("rd "):
            current.rd = stripped.split(maxsplit=1)[1]
        elif stripped.startswith("description "):
            current.description = stripped.split(maxsplit=1)[1]


# `show lldp info remote-device detail` は Port ID が System Name より先に来る。
# 「Name のあとに Port」で拾うと、隣の機器のポートを掴んでしまう
_IX_LLDP_IFACE = re.compile(r"^Interface\s*:\s*(\S+)", re.IGNORECASE)
_IX_LLDP_FIELD = re.compile(
    r"^\s*(System Name|Port ID|Port Description)\s*:\s*(.+?)\s*$", re.IGNORECASE
)
# 実機は `Interface` のことも `Local Interface` のこともある。
# 見出しを厳しく見ると、表そのものを丸ごと読み飛ばして隣接が0本になる
_IX_LLDP_TABLE_HEADER = re.compile(
    r"^\s*(?:Local\s+)?Interface\s+Chassis\s*ID\s+Port\s*ID\s+System\s*Name\s*$",
    re.IGNORECASE,
)


def _parse_ix_lldp(text: str, parsed: ParsedConfig) -> None:
    _parse_ix_lldp_detail(text, parsed)
    _parse_ix_lldp_table(text, parsed)


def _parse_ix_lldp_detail(text: str, parsed: ParsedConfig) -> None:
    local: str | None = None
    remote_host: str | None = None
    remote_port: str | None = None

    def flush() -> None:
        if local and remote_host:
            parsed.add_neighbor(
                ParsedNeighbor(
                    local_interface=local,
                    remote_hostname=remote_host,
                    remote_interface=remote_port,
                    protocol=NeighborProtocol.LLDP,
                )
            )

    for raw in text.splitlines():
        iface = _IX_LLDP_IFACE.match(raw)
        if iface:
            flush()
            local, remote_host, remote_port = iface.group(1), None, None
            continue
        field = _IX_LLDP_FIELD.match(raw)
        if field and local:
            key = field.group(1).lower()
            value = _unquote(field.group(2))
            if key == "system name":
                remote_host = value
            elif key == "port id" and not _looks_like_mac(value):
                remote_port = value
            elif key == "port description" and remote_port is None:
                remote_port = value
    flush()


def _parse_ix_lldp_table(text: str, parsed: ParsedConfig) -> None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _IX_LLDP_TABLE_HEADER.match(line):
            continue
        for row in lines[index + 1 :]:
            if not row.strip() or row.strip().startswith(("#", "!", "Total")):
                break
            columns = row.split()
            if len(columns) < 4:
                continue
            parsed.add_neighbor(
                ParsedNeighbor(
                    local_interface=columns[0],
                    remote_hostname=columns[-1],
                    remote_interface=columns[-2],
                    protocol=NeighborProtocol.LLDP,
                )
            )


def _looks_like_mac(value: str) -> bool:
    compact = re.sub(r"[:.\-]", "", value)
    return len(compact) == 12 and all(c in "0123456789abcdefABCDEF" for c in compact)


def _serial_looks_valid(serial: str) -> bool:
    if not serial or serial.lower() in _INVALID_SERIALS:
        return False
    if _looks_like_mac(serial):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{3,}", serial))


def _qx_is_chassis_section(header: str | None) -> bool:
    if header is None:
        return True
    key = header.lower()
    return key.startswith("slot") or key.startswith("chassis")


def _qx_serial(text: str) -> str | None:
    """Comware の display device manuinfo / ESN からシャーシのシリアルを取る。"""
    section: str | None = None
    for raw in text.splitlines():
        header = _QX_MANUINFO_HEADER.match(raw.strip())
        if header:
            section = header.group(1)
            continue
        match = _QX_DEVICE_SERIAL.match(raw.strip())
        if match and _qx_is_chassis_section(section):
            serial = match.group(1).strip().rstrip(".,;")
            if _serial_looks_valid(serial):
                return serial

    for pattern in (_QX_ESN, _QX_SERIAL_SENTENCE, _IX_SERIAL):
        match = pattern.search(text)
        if match:
            serial = match.group(1).strip().rstrip(".,;")
            if _serial_looks_valid(serial):
                return serial
    return None


def _qx_device_name_from_manuinfo(text: str) -> str | None:
    section: str | None = None
    for raw in text.splitlines():
        header = _QX_MANUINFO_HEADER.match(raw.strip())
        if header:
            section = header.group(1)
            continue
        match = _QX_DEVICE_NAME_FIELD.match(raw.strip())
        if match and _qx_is_chassis_section(section):
            name = match.group(1).strip()
            if name and name.lower() not in _INVALID_SERIALS:
                return name
    # 読めなかったのであって、空の型番があるわけではない
    return None


# ---------------------------------------------------------------------------
# QX（Comware）
# ---------------------------------------------------------------------------


def _parse_qx(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.NEC_QX, os_name="Comware")

    model = _QX_MODEL.search(text)
    if model:
        parsed.model = model.group(1)

    version = _QX_COMWARE_VERSION.search(text) or _QX_CONFIG_VERSION.search(text)
    if version:
        parsed.os_version = version.group(1)

    sysname = _QX_SYSNAME.search(text)
    if sysname:
        parsed.hostname = _unquote(sysname.group(1))
    else:
        parsed.hostname = _from_prompt(text, _QX_PROMPT)

    parsed.serial_number = _qx_serial(text)
    if not parsed.model:
        parsed.model = _qx_device_name_from_manuinfo(text)

    current: ParsedInterface | None = None
    current_vlan: int | None = None
    permitted: set[str] = set()

    for raw in text.splitlines():
        match = _QX_INTERFACE.match(raw)
        if match:
            current = parsed.add_interface(ParsedInterface(name=match.group(1)))
            current_vlan = None
            continue

        vlan = _QX_VLAN.match(raw)
        if vlan and (current is None or not raw[:1].isspace() or _looks_like_vlan_block(raw)):
            current = None
            current_vlan = int(vlan.group(1))
            parsed.vlans.setdefault(current_vlan, "")
            continue
        many = _QX_VLAN_RANGE.match(raw) if current is None else None
        if many:
            for number in vlanlist.parse_ranges(many.group(1)):
                parsed.vlans.setdefault(number, "")
            current_vlan = None
            continue

        if raw and not raw[0].isspace():
            current = None
            current_vlan = None
            continue

        if current_vlan is not None:
            name = re.match(r"^\s*name\s+(.+?)\s*$", raw, re.IGNORECASE)
            if name:
                parsed.vlans[current_vlan] = name.group(1)
            continue

        if current is None:
            continue

        description = _DESCRIPTION.match(raw)
        if description and current.description is None:
            current.description = description.group(1)
            continue
        ip = _QX_IP.match(raw)
        if ip and current.ip_address is None:
            # 他ベンダーと同じ書き方に揃える。マスクを落とすと、どの範囲の
            # アドレスなのかが台帳から消える
            current.ip_address = _with_prefix(ip.group(1), ip.group(2))
            continue
        lag = _QX_LAG_MEMBER.match(raw)
        if lag:
            current.lag_group = int(lag.group(1))
            continue
        link_type = _QX_LINK_TYPE.match(raw)
        if link_type:
            # Comware の access / trunk / hybrid を、他ベンダーと同じ語に寄せる
            current.mode = link_type.group(1).lower()
            continue
        access = _QX_ACCESS_VLAN.match(raw)
        if access:
            current.vlan = int(access.group(1))
            continue
        permit = _QX_TRUNK_PERMIT.match(raw)
        if permit:
            permitted.add(current.key)
            if vlanlist.is_all(permit.group(1)):
                current.trunk_all = True
            else:
                current.trunk_vlans = sorted(
                    set(current.trunk_vlans) | set(vlanlist.parse_ranges(permit.group(1)))
                )
            continue
        hybrid = _QX_HYBRID_VLAN.match(raw)
        if hybrid:
            permitted.add(current.key)
            values = vlanlist.parse_ranges(hybrid.group(1))
            current.trunk_vlans = sorted(set(current.trunk_vlans) | set(values))
            continue
        pvid = _QX_PVID.match(raw)
        if pvid:
            current.native_vlan = int(pvid.group(1))
            continue
        binding = _QX_BIND_VPN.match(raw)
        if binding:
            current.vrf = binding.group(1)
            continue
        mtu = re.match(r"^\s*(?:ip\s+)?mtu\s+(\d+)\s*$", raw, re.IGNORECASE)
        if mtu:
            current.mtu = int(mtu.group(1))
            continue
        speed = re.match(r"^\s*speed\s+(\S+)\s*$", raw, re.IGNORECASE)
        if speed:
            current.speed = speed.group(1)
            continue
        duplex = re.match(r"^\s*duplex\s+(\S+)\s*$", raw, re.IGNORECASE)
        if duplex:
            current.duplex = duplex.group(1)
            continue
        lag_mode = re.match(r"^\s*link-aggregation\s+mode\s+(\S+)\s*$", raw, re.IGNORECASE)
        if lag_mode:
            # Comware は dynamic / static。他ベンダーの語に寄せる
            current.lag_mode = "active" if lag_mode.group(1).lower() == "dynamic" else "on"
            continue
        if re.match(r"^\s*shutdown\s*$", raw, re.IGNORECASE):
            current.enabled = False

    # Comware も、許可 VLAN を書いていないトランクは VLAN 1 だけが通る既定。
    # ただし「書いていない」と読み落としの区別がつかないので、全許可とはみなさない
    for interface in parsed.interfaces:
        if interface.mode == "trunk" and interface.key not in permitted:
            interface.native_vlan = interface.native_vlan or 1

    _parse_qx_vpn_instances(text, parsed)

    _parse_qx_lldp(text, parsed)
    tunnels.parse_block_style(text, parsed)
    routing.parse_comware(text, parsed)
    policy.parse_comware(text, parsed)
    _parse_qx_nat(text, parsed)
    _parse_qx_dhcp(text, parsed)
    _parse_qx_qos(text, parsed)
    _parse_qx_ipsec(text, parsed)
    return parsed


# --- QX（Comware）の変換・配布・優先制御 ------------------------------------

_QX_NAT_GROUP = re.compile(r"^nat address-group\s+(\S+)", re.IGNORECASE)
_QX_NAT_OUTBOUND = re.compile(
    r"^\s*nat outbound(?:\s+(?P<acl>\d+|\S+))?"
    r"(?:\s+address-group\s+(?P<group>\S+))?(?P<rest>.*)$",
    re.IGNORECASE,
)
_QX_NAT_SERVER = re.compile(
    r"^\s*nat server\s+(?:protocol\s+(?P<protocol>\S+)\s+)?global\s+(?P<global>\S+)"
    r"(?:\s+(?P<gport>\S+))?\s+inside\s+(?P<inside>\S+)(?:\s+(?P<iport>\S+))?",
    re.IGNORECASE,
)
# VRP は `ip pool NAME`、Comware は `dhcp server ip-pool NAME`。同じ器で読む
_QX_DHCP_POOL = re.compile(r"^(?:dhcp server ip-pool|ip pool)\s+(\S+)", re.IGNORECASE)
_QX_CLASSIFIER = re.compile(r"^traffic classifier\s+(\S+)", re.IGNORECASE)
_QX_BEHAVIOR = re.compile(r"^traffic behavior\s+(\S+)", re.IGNORECASE)
# Comware は `qos policy` と `qos apply policy`、VRP は `traffic policy` と
# `traffic-policy`。中身（classifier / behavior）は同じ書き方なので、
# 見出しと当て方だけ両方を見る
_QX_POLICY = re.compile(r"^(?:qos policy|traffic policy)\s+(\S+)", re.IGNORECASE)
_QX_APPLY = re.compile(
    r"^\s*(?:qos apply policy|traffic-policy)\s+(\S+)\s+(inbound|outbound)", re.IGNORECASE
)


def _parse_qx_nat(text: str, parsed: ParsedConfig) -> None:
    """アドレス変換を読む。Comware は変換をポートの下に書く。"""
    nat = ParsedNat()
    interface: str | None = None
    group: ParsedNatPool | None = None

    for raw in text.splitlines():
        head = _QX_INTERFACE.match(raw)
        if head:
            interface, group = head.group(1), None
            continue
        found = _QX_NAT_GROUP.match(raw)
        if found:
            group = ParsedNatPool(name=found.group(1))
            nat.pools.append(group)
            interface = None
            continue
        body = raw.strip()
        if group is not None and body.lower().startswith("address "):
            parts = body.split()
            group.start = parts[1] if len(parts) > 1 else None
            group.end = parts[2] if len(parts) > 2 else None
            continue

        outbound = _QX_NAT_OUTBOUND.match(raw)
        if outbound and raw.strip().lower().startswith("nat outbound"):
            pool = outbound.group("group")
            nat.rules.append(
                ParsedNatRule(
                    kind="dynamic",
                    direction="inside",
                    matched=outbound.group("acl") or "内側から出る通信",
                    translated=pool or (interface or "出口のアドレス"),
                    # address-group に no-pat が付いていなければポートで束ねる
                    overload="no-pat" not in outbound.group("rest").lower(),
                    matched_is_name=bool(outbound.group("acl")),
                    raw=body,
                )
            )
            if interface and interface not in nat.outside_interfaces:
                nat.outside_interfaces.append(interface)
            continue

        server = _QX_NAT_SERVER.match(raw)
        if server:
            nat.rules.append(
                ParsedNatRule(
                    kind="static-port" if server.group("gport") else "static",
                    direction="outside",
                    matched=server.group("global"),
                    translated=server.group("inside"),
                    protocol=server.group("protocol"),
                    matched_is_name=False,
                    raw=body,
                )
            )
            if interface and interface not in nat.outside_interfaces:
                nat.outside_interfaces.append(interface)

    if nat.rules or nat.pools:
        nat.inside_interfaces = [
            i.name for i in parsed.interfaces if i.name not in nat.outside_interfaces
        ]
        parsed.nat = nat
        # 変換で使っている ACL は「どこにも当たっていない」ではない
        by_name = {acl.name: acl for acl in parsed.acls}
        for rule in nat.rules:
            acl = by_name.get(rule.matched or "")
            if acl is not None and "NAT" not in acl.referenced_by:
                acl.referenced_by.append("NAT")


_QX_IKE_PROPOSAL = re.compile(r"^ike proposal\s+(\S+)", re.IGNORECASE)
_QX_TRANSFORM = re.compile(r"^ipsec transform-set\s+(\S+)", re.IGNORECASE)
_QX_IPSEC_POLICY = re.compile(r"^ipsec policy\s+(\S+)\s+(\S+)(?:\s+(\S+))?", re.IGNORECASE)
_QX_IPSEC_APPLY = re.compile(r"^\s*ipsec apply policy\s+(\S+)", re.IGNORECASE)
_QX_IKE_KEY = re.compile(r"^\s*pre-shared-key\s+(?:address|hostname)\s+(\S+)", re.IGNORECASE)


def _parse_qx_ipsec(text: str, parsed: ParsedConfig) -> None:
    """拠点間の暗号化を読む。

    Comware は ike proposal（鍵の交換）・ipsec transform-set（中身の暗号化）・
    ipsec policy（相手と範囲）に分かれる。Cisco の isakmp policy /
    transform-set / crypto map と役割が同じなので、そこへ読み替える。
    """
    crypto = ParsedCrypto()
    ike: ParsedIkePolicy | None = None
    transform: ParsedTransformSet | None = None
    entry: ParsedCryptoMapEntry | None = None
    interface: str | None = None

    for raw in text.splitlines():
        head = _QX_IKE_PROPOSAL.match(raw)
        if head:
            ike = ParsedIkePolicy(number=head.group(1), version=2)
            crypto.ike_policies.append(ike)
            transform = entry = interface = None
            continue
        head = _QX_TRANSFORM.match(raw)
        if head:
            transform = ParsedTransformSet(name=head.group(1))
            crypto.transform_sets.append(transform)
            ike = entry = interface = None
            continue
        head = _QX_IPSEC_POLICY.match(raw)
        if head:
            entry = ParsedCryptoMapEntry(
                name=head.group(1),
                seq=head.group(2),
                kind=(head.group(3) or "isakmp").lower(),
            )
            crypto.maps.append(entry)
            ike = transform = interface = None
            continue
        head = _QX_INTERFACE.match(raw)
        if head:
            interface = head.group(1)
            ike = transform = entry = None
            continue

        if raw and not raw.startswith((" ", "\t")):
            # 見出しの行だが、こちらで扱うものではない（ike keychain など）。
            # 前の塊のままにしておくと、その中身として読んでしまう
            ike = transform = entry = None
            if not _QX_INTERFACE.match(raw):
                interface = None

        body = raw.strip()
        if ike is not None:
            _qx_ike_detail(ike, body)
            continue
        if transform is not None:
            _qx_transform_detail(transform, body)
            continue
        if entry is not None:
            _qx_policy_detail(entry, body)
            continue
        applied = _QX_IPSEC_APPLY.match(raw)
        if applied and interface:
            crypto.applied.append((interface, applied.group(1)))
            continue
        key = _QX_IKE_KEY.match(raw)
        if key:
            crypto.keyed_peers.append(key.group(1))

    if crypto.maps or crypto.ike_policies:
        parsed.crypto = crypto
        # 暗号化する範囲に使っている ACL は「どこにも当たっていない」ではない
        by_name = {acl.name: acl for acl in parsed.acls}
        for item in crypto.maps:
            acl = by_name.get(item.match_address or "")
            if acl is not None and "IPsec" not in acl.referenced_by:
                acl.referenced_by.append("IPsec")


def _qx_ike_detail(ike: ParsedIkePolicy, body: str) -> None:
    found = re.match(r"^encryption-algorithm\s+(\S+)", body, re.IGNORECASE)
    if found:
        ike.encryption = found.group(1)
        return
    found = re.match(r"^authentication-algorithm\s+(\S+)", body, re.IGNORECASE)
    if found:
        ike.hash = found.group(1)
        return
    found = re.match(r"^dh\s+group(\S+)", body, re.IGNORECASE)
    if found:
        ike.group = found.group(1)
        return
    found = re.match(r"^authentication-method\s+(\S+)", body, re.IGNORECASE)
    if found:
        ike.authentication = found.group(1)
        return
    found = re.match(r"^sa duration\s+(\d+)", body, re.IGNORECASE)
    if found:
        ike.lifetime = int(found.group(1))


def _qx_transform_detail(transform: ParsedTransformSet, body: str) -> None:
    found = re.match(
        r"^(esp|ah)\s+(?:encryption|authentication)-algorithm\s+(.+)$", body, re.IGNORECASE
    )
    if found:
        # Cisco の transform-set と読み比べられるよう、esp- / ah- を頭に付ける
        for value in found.group(2).split():
            transform.transforms.append(f"{found.group(1).lower()}-{value}")
        return
    found = re.match(r"^encapsulation-mode\s+(\S+)", body, re.IGNORECASE)
    if found:
        transform.mode = found.group(1)


def _qx_policy_detail(entry: ParsedCryptoMapEntry, body: str) -> None:
    found = re.match(r"^transform-set\s+(.+)$", body, re.IGNORECASE)
    if found:
        entry.transform_sets.extend(found.group(1).split())
        return
    found = re.match(r"^security acl\s+(\S+)", body, re.IGNORECASE)
    if found:
        entry.match_address = found.group(1)
        return
    found = re.match(r"^remote-address\s+(\S+)", body, re.IGNORECASE)
    if found:
        entry.peers.append(found.group(1))
        return
    found = re.match(r"^ike-profile\s+(\S+)", body, re.IGNORECASE)
    if found:
        entry.profile = found.group(1)
        return
    found = re.match(r"^pfs\s+(\S+)", body, re.IGNORECASE)
    if found:
        entry.pfs = found.group(1)


def _parse_qx_dhcp(text: str, parsed: ParsedConfig) -> None:
    """配るアドレスを読む。Comware は pool の中に範囲と配るものを書く。"""
    dhcp = ParsedDhcp()
    current: ParsedDhcpPool | None = None

    for raw in text.splitlines():
        head = _QX_DHCP_POOL.match(raw)
        if head:
            current = ParsedDhcpPool(name=f"ip-pool {head.group(1)}")
            dhcp.pools.append(current)
            continue
        if not raw.startswith((" ", "\t")):
            current = None
        body = raw.strip()
        relay = re.match(r"^dhcp select relay|^\s*dhcp server select relay", raw, re.IGNORECASE)
        if relay:
            continue
        if current is None:
            continue
        found = re.match(r"^network\s+(\S+)(?:\s+mask\s+(\S+))?", body, re.IGNORECASE)
        if found:
            current.network = (
                f"{found.group(1)} {found.group(2)}" if found.group(2) else found.group(1)
            )
            continue
        found = re.match(r"^gateway-list\s+(.+)$", body, re.IGNORECASE)
        if found:
            current.default_routers.extend(found.group(1).split())
            continue
        found = re.match(r"^dns-list\s+(.+)$", body, re.IGNORECASE)
        if found:
            current.dns_servers.extend(found.group(1).split())
            continue
        found = re.match(r"^domain-name\s+(\S+)", body, re.IGNORECASE)
        if found:
            current.domain = found.group(1)
            continue
        found = re.match(
            r"^(?:forbidden-ip|excluded-ip-address)\s+(\S+)(?:\s+(\S+))?", body, re.IGNORECASE
        )
        if found:
            # 配らないアドレス。Cisco の excluded-address と同じ扱いにする
            dhcp.excluded.append((found.group(1), found.group(2) or found.group(1)))
            continue
        found = re.match(r"^expired\s+(.+)$", body, re.IGNORECASE)
        if found:
            current.lease = found.group(1)

    if dhcp.pools:
        parsed.dhcp = dhcp


def _parse_qx_qos(text: str, parsed: ParsedConfig) -> None:
    """優先制御を読む。Comware は classifier・behavior・policy に分かれる。"""
    qos = ParsedQos()
    behaviors: dict[str, list[str]] = {}
    classifier: ParsedQosClass | None = None
    behavior: str | None = None
    qos_policy: ParsedQosPolicy | None = None
    interface: str | None = None

    for raw in text.splitlines():
        head = _QX_CLASSIFIER.match(raw)
        if head:
            classifier = ParsedQosClass(name=head.group(1))
            qos.classes.append(classifier)
            behavior = qos_policy = interface = None
            continue
        head = _QX_BEHAVIOR.match(raw)
        if head:
            behavior = head.group(1)
            behaviors.setdefault(behavior, [])
            classifier = qos_policy = interface = None
            continue
        head = _QX_POLICY.match(raw)
        if head:
            qos_policy = ParsedQosPolicy(name=head.group(1))
            qos.policies.append(qos_policy)
            classifier = behavior = interface = None
            continue
        head = _QX_INTERFACE.match(raw)
        if head:
            interface = head.group(1)
            classifier = behavior = qos_policy = None
            continue

        body = raw.strip()
        if classifier is not None and body.lower().startswith("if-match"):
            classifier.matches.append(body)
            continue
        if behavior is not None and body and not body.startswith("#"):
            behaviors[behavior].append(body)
            continue
        if qos_policy is not None:
            found = re.match(r"^classifier\s+(\S+)\s+behavior\s+(\S+)", body, re.IGNORECASE)
            if found:
                qos_policy.classes.append(
                    ParsedQosAction(class_name=found.group(1), actions=[found.group(2)])
                )
            continue
        applied = _QX_APPLY.match(raw)
        if applied and interface:
            direction = "input" if applied.group(2).lower() == "inbound" else "output"
            qos.applied.append((interface, direction, applied.group(1)))

    # behavior に書いてある中身を、その仕分けの行に添える
    for policy_entry in qos.policies:
        for action in policy_entry.classes:
            action.actions.extend(behaviors.get(action.actions[0], []))

    if qos.policies or qos.classes:
        parsed.qos = qos


def _parse_qx_vpn_instances(text: str, parsed: ParsedConfig) -> None:
    """Comware の VPN インスタンス（他ベンダーで言う VRF）を読む。"""
    current: ParsedVrf | None = None
    for raw in text.splitlines():
        match = _QX_VPN_INSTANCE.match(raw)
        if match:
            current = parsed.add_vrf(ParsedVrf(name=match.group(1)))
            continue
        if current is None:
            continue
        if raw and not raw[0].isspace():
            current = None
            continue
        stripped = raw.strip()
        if stripped.startswith("route-distinguisher "):
            current.rd = stripped.split(maxsplit=1)[1]
        elif stripped.startswith("description "):
            current.description = stripped.split(maxsplit=1)[1]


# Comware の `display lldp neighbor-information` は
# 「LLDP neighbor-information of port N[GigabitEthernet1/0/1]」で始まる
_QX_LLDP_PORT = re.compile(
    r"LLDP\s+neighbor-information\s+of\s+port\s+\d+\s*\[([^\]]+)\]", re.IGNORECASE
)
_QX_LLDP_FIELD = re.compile(
    r"^\s*(System name|Port ID|Port description)\s*:\s*(.+?)\s*$", re.IGNORECASE
)
_QX_LLDP_LIST_HEADER = re.compile(
    r"^\s*System Name\s+Local Interface\s+.*Port ID\s*$", re.IGNORECASE
)


def _parse_qx_lldp(text: str, parsed: ParsedConfig) -> None:
    local: str | None = None
    remote_host: str | None = None
    remote_port: str | None = None
    port_descr: str | None = None

    def flush() -> None:
        port = remote_port
        if port is None or _looks_like_mac(port):
            port = port_descr or port
        if local and remote_host:
            parsed.add_neighbor(
                ParsedNeighbor(
                    local_interface=local,
                    remote_hostname=remote_host,
                    remote_interface=port,
                    protocol=NeighborProtocol.LLDP,
                )
            )

    for raw in text.splitlines():
        port = _QX_LLDP_PORT.search(raw)
        if port:
            flush()
            local, remote_host, remote_port, port_descr = port.group(1), None, None, None
            continue
        field = _QX_LLDP_FIELD.match(raw)
        if field and local:
            key = field.group(1).lower()
            value = _unquote(field.group(2))
            if key == "system name":
                remote_host = value
            elif key == "port id":
                remote_port = value
            else:
                port_descr = value
    flush()
    _parse_qx_lldp_list(text, parsed)


def _parse_qx_lldp_list(text: str, parsed: ParsedConfig) -> None:
    """`display lldp neighbor-information list` の表。詳細が無いときに使う。"""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _QX_LLDP_LIST_HEADER.match(line):
            continue
        for row in lines[index + 1 :]:
            if not row.strip() or row.strip().startswith(("#", "Total", "Displaying")):
                break
            columns = row.split()
            if len(columns) < 3:
                continue
            parsed.add_neighbor(
                ParsedNeighbor(
                    local_interface=columns[1],
                    remote_hostname=columns[0],
                    remote_interface=columns[-1],
                    protocol=NeighborProtocol.LLDP,
                )
            )


# --- IX の IPsec ------------------------------------------------------------
#
# 拠点間 VPN。トンネルは `interface Tunnel0.0` として既に読めているので、
# ここでは強さ（鍵の交換と中身の暗号化）と、相手をどこに置いてあるかを
# 読む。器は Cisco と同じものを使う。そうすると DES や MD5 が残っている
# ときの指摘が、判断層を足さずにそのまま出る。

_IKE_PROPOSAL = re.compile(r"^ike\s+proposal\s+(\S+)\s+(.+)$", re.IGNORECASE)
_IKE_POLICY = re.compile(r"^ike\s+policy\s+(\S+)\s+peer\s+(\S+)\s+key\s+\S+\s+(\S+)", re.IGNORECASE)
_IPSEC_PROPOSAL = re.compile(r"^ipsec\s+autokey-proposal\s+(\S+)\s+(.+)$", re.IGNORECASE)
_IPSEC_MAP = re.compile(r"^ipsec\s+autokey-map\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", re.IGNORECASE)
_IPSEC_APPLY = re.compile(r"^\s*ipsec\s+policy\s+(\S+)\s+(\S+)", re.IGNORECASE)
#: `encryption aes hash sha group 1024-bit lifetime 24h` のような並び
_KEYWORD = re.compile(r"(encryption|hash|group|lifetime)\s+(\S+)", re.IGNORECASE)


def _parse_ix_ipsec(text: str, parsed: ParsedConfig) -> None:
    crypto = parsed.crypto or ParsedCrypto()
    found_any = False
    interface: str | None = None

    for raw in text.splitlines():
        head = _IX_INTERFACE.match(raw)
        if head:
            interface = head.group(1)
            continue

        body = raw.strip()

        proposal = _IKE_PROPOSAL.match(body)
        if proposal:
            found_any = True
            values = {key.lower(): value for key, value in _KEYWORD.findall(proposal.group(2))}
            crypto.ike_policies.append(
                ParsedIkePolicy(
                    number=proposal.group(1),
                    encryption=values.get("encryption"),
                    hash=values.get("hash"),
                    group=_dh_group(values.get("group")),
                )
            )
            continue

        policy = _IKE_POLICY.match(body)
        if policy:
            found_any = True
            # 合言葉そのものは持たない。どの相手に置いてあるかだけ残す
            crypto.keyed_peers.append(policy.group(2))
            continue

        transform = _IPSEC_PROPOSAL.match(body)
        if transform:
            found_any = True
            crypto.transform_sets.append(
                ParsedTransformSet(
                    name=transform.group(1),
                    transforms=[
                        value
                        for value in transform.group(2).split()
                        if value.lower().startswith(("esp-", "ah-"))
                    ],
                )
            )
            continue

        mapped = _IPSEC_MAP.match(body)
        if mapped:
            found_any = True
            crypto.maps.append(
                ParsedCryptoMapEntry(
                    name=mapped.group(1),
                    seq=None,
                    peers=[mapped.group(3)],
                    transform_sets=[mapped.group(4)],
                    match_address=mapped.group(2),
                )
            )
            continue

        applied = _IPSEC_APPLY.match(body)
        if applied and interface is not None and raw[:1].isspace():
            found_any = True
            crypto.applied.append((interface, applied.group(1)))

    if not found_any:
        return
    parsed.crypto = crypto
    # 暗号化する範囲に使っている ACL は「どこにも当たっていない」ではない
    by_name = {acl.name: acl for acl in parsed.acls}
    for item in crypto.maps:
        acl = by_name.get(item.match_address or "")
        if acl is not None and "IPsec" not in acl.referenced_by:
            acl.referenced_by.append("IPsec")


def _dh_group(value: str | None) -> str | None:
    """鍵の作り方。IX は長さで書くので、判断の側と同じ群の番号に直す。"""
    if value is None:
        return None
    lengths = {"768-bit": "1", "1024-bit": "2", "1536-bit": "5", "2048-bit": "14"}
    return lengths.get(value.lower(), value)
