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
    name: str
    description: str | None = None
    ip_address: str | None = None
    vlan: int | None = None
    mode: str | None = None  # access / trunk / routed
    enabled: bool = True

    @property
    def key(self) -> str:
        return normalize(self.name)


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

    def add_interface(self, interface: ParsedInterface) -> ParsedInterface:
        """同じポートが複数箇所に出てきたらまとめる（config と show 出力の両方に出るため）。"""
        for existing in self.interfaces:
            if existing.key == interface.key:
                for name in ("description", "ip_address", "vlan", "mode"):
                    if getattr(existing, name) is None:
                        setattr(existing, name, getattr(interface, name))
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
