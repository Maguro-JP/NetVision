"""Config からトンネルを読む。

論理構成図の材料。物理では「拠点 → 支社 → センター」と何段も経由していても、
論理ではセンターと直結して見えることがある。障害の切り分けで効くのは
たいていこのズレなので、物理とは別に読んでおく。

宛先は IP でしか書かれていないことが多い。ここでは書いてある値をそのまま
持ち帰り、相手機器の特定（IP → 機器）は台帳と突き合わせる側でやる。
設定に無いものを推測して埋めない。
"""

from __future__ import annotations

import re

from app.services.configparse.base import ParsedConfig, ParsedTunnel

# 種別が名前から分かるもの。書いていなければ空のままにする
_KIND_FROM_MODE = re.compile(r"^\s*tunnel\s+mode\s+(.+?)\s*$", re.IGNORECASE)

# Cisco の設定例は `interface Tunnel 0` と番号の前に空白を置く（出典: IP Routing Configuration
# Guide, Cisco IOS XE 17.x の Implementing Tunnels）。名前と番号をつないで `Tunnel0` として持つ
_BLOCK_INTERFACE = re.compile(r"^interface\s+(\S+)(?:\s+(\d\S*))?\s*$", re.IGNORECASE)
_TUNNEL_NAME = re.compile(r"^(?:tunnel|st0|gr-|ipip|vti)", re.IGNORECASE)


def is_tunnel(name: str) -> bool:
    return bool(_TUNNEL_NAME.match(name.strip()))


def parse_block_style(text: str, parsed: ParsedConfig) -> None:
    """`interface TunnelN` … の形（Cisco IOS / NEC IX / NEC QX）を読む。

    Cisco の行（tunnel source / destination / mode / vrf）は、Cisco「IP Routing Configuration Guide,
    Cisco IOS XE 17.x」の Implementing Tunnels と VRF-Aware Tunnels で確かめた。
    `vrf forwarding` が内側、`tunnel vrf` が外側（トンネルの通り道）の VRF。

    書式の細かい差（`tunnel source` と `source`）は両方受ける。
    どちらか片方しか見ないと、ベンダーを跨いだ瞬間に黙って空になる。
    """
    current: ParsedTunnel | None = None
    for raw in text.splitlines():
        match = _BLOCK_INTERFACE.match(raw.strip())
        if match and not raw[:1].isspace():
            name = match.group(1) + (match.group(2) or "")
            current = parsed.add_tunnel(ParsedTunnel(name=name)) if is_tunnel(name) else None
            continue
        if current is None:
            continue
        if raw and not raw[0].isspace():
            current = None
            continue

        body = raw.strip()
        mode = _KIND_FROM_MODE.match(body)
        if mode:
            current.kind = mode.group(1)
        elif body.lower().startswith(("tunnel source ", "source ")):
            current.source = body.split(maxsplit=2)[-1]
        elif body.lower().startswith(("tunnel destination ", "destination ")):
            # NEC IX は名前で書くとき `tunnel destination fqdn 名前`（出典: IX-R/IX-V 12.1）
            current.destination = body.split(maxsplit=2)[-1].removeprefix("fqdn ").strip()
        elif body.lower().startswith("description "):
            current.description = body[len("description ") :].strip()
        elif body.lower().startswith(("vrf forwarding ", "ip vrf forwarding ")):
            current.vrf = body.rsplit(maxsplit=1)[-1]
        elif body.lower().startswith("tunnel vrf "):
            # 外側（トンネルの通り道）の VRF。内側の `vrf forwarding` とは別で、
            # 両方書いてあれば内側を優先して持つ（経路の帰属は内側で決まる）
            current.vrf = current.vrf or body.rsplit(maxsplit=1)[-1]


_SET_TUNNEL = re.compile(
    r"^set interfaces (\S+)(?: unit (\d+))? tunnel (source|destination)\s+(\S+)", re.MULTILINE
)


#: Junos の口の名前から分かる種別。出典: Juniper「Chassis Cluster Redundant Ethernet
#: Interfaces」の IP-over-IP Tunneling（GRE は gr-0/0/0、IP-IP は ip-0/0/0 を使う）。
#: IPsec の st0 は `tunnel source` を持たず、宛先は IKE の gateway に書くのでここには来ない
_JUNOS_TUNNEL_KIND = (("gr-", "gre"), ("ip-", "ipip"))


def parse_junos(text: str, parsed: ParsedConfig) -> None:
    """Junos の set 形式。`gr-0/0/0`（GRE）と `ip-0/0/0`（IP-IP）の tunnel source / destination。

    種別は口の名前で決める。どちらでもない口は種別を空のままにする（推測で埋めない）。
    """
    for interface, unit, field, value in _SET_TUNNEL.findall(text):
        name = f"{interface}.{unit}" if unit else interface
        tunnel = parsed.add_tunnel(ParsedTunnel(name=name))
        setattr(tunnel, field, value)
        if tunnel.kind is None:
            tunnel.kind = next(
                (kind for prefix, kind in _JUNOS_TUNNEL_KIND if interface.startswith(prefix)),
                None,
            )


_FORTI_BLOCK = re.compile(r"config vpn ipsec phase1-interface(.*?)^end", re.MULTILINE | re.DOTALL)
_FORTI_EDIT = re.compile(r'^\s*edit\s+"?([^"\n]+)"?', re.MULTILINE)


def parse_fortios(text: str, parsed: ParsedConfig) -> None:
    """FortiOS の IPsec（phase1-interface）。宛先は `set remote-gw`（IPv6 は `remote-gw6`）。

    出典: FortiOS 6.4.7 / 7.0.1 / 7.0.12 CLI Reference の config vpn ipsec phase1-interface
    （interface・local-gw・local-gw6・remote-gw・remote-gw6・comments）。
    """
    for block in _FORTI_BLOCK.findall(text):
        current: ParsedTunnel | None = None
        for raw in block.splitlines():
            edit = _FORTI_EDIT.match(raw)
            if edit:
                current = parsed.add_tunnel(ParsedTunnel(name=edit.group(1), kind="ipsec"))
                continue
            if current is None:
                continue
            body = raw.strip().strip('"')
            # IPv6 で張るときは remote-gw6 / local-gw6（CLI Reference の phase1-interface）
            if body.startswith(("set remote-gw ", "set remote-gw6 ")):
                current.destination = body.split()[-1].strip('"')
            elif body.startswith(("set local-gw ", "set local-gw6 ")):
                current.source = body.split()[-1].strip('"')
            elif body.startswith("set interface "):
                # 送信元が IP で書かれていない場合は、出ていくポート名を残す。
                # 空にするより「どこから出ているか」だけでも分かる方がよい
                current.source = current.source or body.split()[-1].strip('"')
            elif body.startswith("set comments "):
                current.description = body[len("set comments ") :].strip('"')
            elif body == "next":
                current = None
