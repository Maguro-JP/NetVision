"""パーサが返す構造。ベンダーごとの差はここに吸収して、上位は同じ形で扱う。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from app.services.configparse.ifname import expand, normalize, normalize_hostname


class Vendor(enum.StrEnum):
    CISCO_IOS = "cisco_ios"
    CISCO_NXOS = "cisco_nxos"
    JUNIPER = "juniper"
    FORTINET = "fortinet"
    NEC_IX = "nec_ix"
    NEC_QX = "nec_qx"
    UNKNOWN = "unknown"


class NeighborProtocol(enum.StrEnum):
    CDP = "cdp"
    LLDP = "lldp"


@dataclass
class ParsedInterface:
    """ポート1つ分。

    VLAN は3つの顔を分けて持つ。混ぜると現場の判断に使えないため。

    - `vlan` … アクセス VLAN（そのポートに繋いだ端末が乗るセグメント）
    - `native_vlan` … トランクのタグ無し VLAN。片側だけ変えると通信が漏れる
    - `trunk_vlans` … トランクで許可している VLAN。ここが両端で食い違うと不通になる
    """

    name: str
    description: str | None = None
    ip_address: str | None = None
    vlan: int | None = None
    mode: str | None = None  # access / trunk / hybrid / routed
    native_vlan: int | None = None
    trunk_vlans: list[int] = field(default_factory=list)
    # 許可 VLAN を書いていないトランクは「全部通す」。番号を並べて持つと
    # 4094個になるので、意味の方を持っておき、実際の集合は機器の定義 VLAN で解決する
    trunk_all: bool = False
    trunk_excluded: set[int] = field(default_factory=set)  # `except` 指定の除外分
    vrf: str | None = None
    #: 束ねているポートの番号（LAG / ポートチャネル）。同じ番号どうしが1本の線になる
    lag_group: int | None = None
    enabled: bool = True

    @property
    def key(self) -> str:
        return normalize(self.name)

    def carried_vlans(self, defined: set[int] | None = None) -> set[int]:
        """このポートが運ぶ VLAN すべて。図でセグメントを追うのに使う。

        `defined` はその機器に定義されている VLAN。「全部通す」トランクは
        ここと突き合わせて実際の集合にする。
        """
        out = set(self.trunk_vlans)
        if self.trunk_all and defined:
            out |= defined - self.trunk_excluded
        for value in (self.vlan, self.native_vlan):
            if value is not None:
                out.add(value)
        return out


@dataclass
class ParsedVrf:
    """機器の中に切った仮想ルーター（VRF / VPN インスタンス / VDOM）。

    経路表が分かれるので、同じ図に見えていても VRF が違えば通信できない。
    「繋がっているのに通らない」の原因になりやすいところなので、明示的に持つ。
    """

    name: str
    rd: str | None = None  # route-distinguisher
    description: str | None = None


@dataclass
class ParsedTunnel:
    """トンネル1本分（GRE / IPsec など）。

    論理構成図の材料。物理では「拠点→支社→センター」と辿っていても、
    論理ではセンターと直結して見えることがある。そのズレが障害切り分けの
    肝になるので、物理と別に持つ。

    宛先は IP でしか書かれていないことが多い。相手機器の特定は、
    台帳の管理IP・インターフェースIP と突き合わせて後から行う。
    """

    name: str
    kind: str | None = None  # gre / ipsec / vxlan など、設定に書かれていた種別
    source: str | None = None  # 送信元 IP、またはインターフェース名
    destination: str | None = None
    vrf: str | None = None
    description: str | None = None


@dataclass
class ParsedNeighbor:
    """隣接情報。ここから物理結線を推定する。"""

    local_interface: str
    remote_hostname: str
    remote_interface: str | None = None
    remote_platform: str | None = None
    protocol: NeighborProtocol = NeighborProtocol.CDP

    @property
    def remote_key(self) -> str:
        return normalize_hostname(self.remote_hostname)


@dataclass
class ParsedConfig:
    vendor: Vendor = Vendor.UNKNOWN
    hostname: str | None = None
    os_name: str | None = None
    os_version: str | None = None
    model: str | None = None
    serial_number: str | None = None
    interfaces: list[ParsedInterface] = field(default_factory=list)
    neighbors: list[ParsedNeighbor] = field(default_factory=list)
    vlans: dict[int, str] = field(default_factory=dict)
    vrfs: list[ParsedVrf] = field(default_factory=list)
    tunnels: list[ParsedTunnel] = field(default_factory=list)

    def add_tunnel(self, tunnel: ParsedTunnel) -> ParsedTunnel:
        """同じトンネルが複数箇所に出てきたらまとめる。"""
        for existing in self.tunnels:
            if normalize(existing.name) == normalize(tunnel.name):
                for name in ("kind", "source", "destination", "vrf", "description"):
                    if getattr(existing, name) is None:
                        setattr(existing, name, getattr(tunnel, name))
                return existing
        self.tunnels.append(tunnel)
        return tunnel

    def add_interface(self, interface: ParsedInterface) -> ParsedInterface:
        """同じポートが複数箇所に出てきたらまとめる（config と show 出力の両方に出るため）。"""
        for existing in self.interfaces:
            if existing.key == interface.key:
                for name in (
                    "description",
                    "ip_address",
                    "vlan",
                    "mode",
                    "native_vlan",
                    "vrf",
                    "lag_group",
                ):
                    if getattr(existing, name) is None:
                        setattr(existing, name, getattr(interface, name))
                if interface.trunk_vlans and not existing.trunk_vlans:
                    existing.trunk_vlans = interface.trunk_vlans
                existing.trunk_all = existing.trunk_all or interface.trunk_all
                return existing
        interface.name = expand(interface.name)
        self.interfaces.append(interface)
        return interface

    def add_neighbor(self, neighbor: ParsedNeighbor) -> None:
        neighbor.local_interface = expand(neighbor.local_interface)
        if neighbor.remote_interface:
            neighbor.remote_interface = expand(neighbor.remote_interface)

        key = (normalize(neighbor.local_interface), neighbor.remote_key)
        for existing in self.neighbors:
            if (normalize(existing.local_interface), existing.remote_key) == key:
                # CDP と LLDP の両方に出た場合、情報が多い方を残す
                if not existing.remote_interface and neighbor.remote_interface:
                    existing.remote_interface = neighbor.remote_interface
                if not existing.remote_platform and neighbor.remote_platform:
                    existing.remote_platform = neighbor.remote_platform
                return
        self.neighbors.append(neighbor)

    def add_vrf(self, vrf: ParsedVrf) -> ParsedVrf:
        """同じ VRF が定義箇所と参照箇所の両方に出てくるのでまとめる。"""
        for existing in self.vrfs:
            if existing.name == vrf.name:
                existing.rd = existing.rd or vrf.rd
                existing.description = existing.description or vrf.description
                return existing
        self.vrfs.append(vrf)
        return vrf
