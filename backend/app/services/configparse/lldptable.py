"""`show lldp neighbors` の表を、見出しの並びから読む。

ベンダーごとに列の名前も順番も違うが、どれも見出しの行を持っている。
その行から「何列目が何か」を決めてから中身を読む。列の位置を決め打ちに
すると、桁がずれた出力で黙って別の値を拾うので、見出しから決める。

見出しが無い出力は、読めないものとして何も返さない。線が引けないことと、
線が無いことを混ぜない。
"""

from __future__ import annotations

import re

from app.services.configparse.base import NeighborProtocol, ParsedConfig, ParsedNeighbor

#: 見出しの語と、それが何を指すか。表記ゆれはここに足す。
#: 使わない列も「読み飛ばす」として並べる。桁を数えるのに要るため
_ROLES: dict[str, str] = {
    "local intf": "local",
    "local interface": "local",
    "local port": "local",
    "lcl port": "local",
    "neighbor dev": "remote_host",
    "neighbor device": "remote_host",
    "system name": "remote_host",
    "device id": "remote_host",
    "neighbor intf": "remote_port",
    "neighbor interface": "remote_port",
    "neighbor port": "remote_port",
    "port id": "remote_port",
    "chassis id": "skip",
    "port description": "skip",
    "system description": "skip",
    "capability": "skip",
    "exptime": "skip",
    "hold": "skip",
    "ttl": "skip",
    # 区切りに `-` を使う書き方（Aruba CX など）
    "local-port": "local",
    "local-intf": "local",
    "system-name": "remote_host",
    "device-id": "remote_host",
    "neighbor-port": "remote_port",
    "port-id": "remote_port",
    "chassis-id": "skip",
    "port-descr": "skip",
    "port-description": "skip",
    "hold-time": "skip",
    # Cisco Small Business / Catalyst 1200・1300 系
    # （出典: Catalyst 1200 Series CLI「show lldp neighbors」の出力例。
    # Device ID は名前のことも MAC のこともあり、名前は System Name に出る）
    "port": "local",
    "sysname": "remote_host",
    "capabilities": "skip",
    # FortiGate から見た FortiSwitch の表
    # （出典: Fortinet Community KB 227554「How to display LLDP, trunk and STP
    # information of a managed FortiSwitch from FortiGate CLI」。
    # 文書ライブラリではなくメーカーのナレッジベース）
    "portname": "local",
    "device-name": "remote_host",
    "med-type": "skip",
    "status": "skip",
    # Dell SmartFabric OS10（出典: OS10 User Guide「show lldp neighbors」）
    "loc portid": "local",
    "rem host name": "remote_host",
    "rem port id": "remote_port",
    "rem chassis id": "skip",
}
#: 名前の欄に出る「名乗っていない」の印。これを名前として取ると、台帳に
#: 居ない機器が増える（出典: OS10 User Guide「show lldp neighbors」の出力例）
_NOT_A_NAME = {"not advertised", "n/a", "-"}
_MAC = re.compile(r"^(?:[0-9a-f]{2}[:.-]?){5}[0-9a-f]{2}$", re.IGNORECASE)
#: 見出しの語は長いものから当てる。`port id` を `port description` より先に
#: 当てると、桁の数え方がずれる
_LABELS = sorted(_ROLES, key=len, reverse=True)


def _columns(header: str) -> list[tuple[int, str]]:
    """見出しの行から、（開始位置, 役割）の並びを作る。"""
    lowered = header.lower()
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for label in _LABELS:
        start = 0
        while True:
            at = lowered.find(label, start)
            if at < 0:
                break
            start = at + 1
            end = at + len(label)
            if any(a < end and at < b for a, b in taken):
                continue
            if at and lowered[at - 1].isalnum():
                continue
            if end < len(lowered) and lowered[end].isalnum():
                continue
            taken.append((at, end))
            found.append((at, _ROLES[label]))
    return sorted(found)


def parse(text: str, parsed: ParsedConfig) -> int:
    """表が見つかれば読んで、足した本数を返す。見つからなければ 0。"""
    rows = text.splitlines()
    added = 0
    for index, line in enumerate(rows):
        columns = _columns(line)
        roles = [role for _, role in columns]
        if "local" not in roles or "remote_host" not in roles:
            continue
        added += _read_rows(rows[index + 1 :], columns, parsed)
    return added


def _read_rows(rows: list[str], columns: list[tuple[int, str]], parsed: ParsedConfig) -> int:
    """見出しで決めた桁で切る。空白で割ると、空白を含む列でずれる。"""
    bounds = [
        (start, columns[index + 1][0] if index + 1 < len(columns) else None, role)
        for index, (start, role) in enumerate(columns)
    ]
    added = 0
    for raw in rows:
        if not raw.strip():
            break
        if set(raw.strip()) <= set("-=+ "):
            continue
        values: dict[str, str] = {}
        for start, end, role in bounds:
            if role == "skip":
                continue
            values[role] = raw[start:end].strip()
        local, host = values.get("local"), values.get("remote_host")
        if not local or not host or _MAC.match(host) or " " in local:
            continue
        if host.lower() in _NOT_A_NAME or host.startswith("["):
            continue
        port = values.get("remote_port") or None
        parsed.add_neighbor(
            ParsedNeighbor(
                local_interface=local,
                remote_hostname=host,
                remote_interface=None if port and _MAC.match(port) else port,
                protocol=NeighborProtocol.LLDP,
            )
        )
        added += 1
    return added
