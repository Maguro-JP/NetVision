"""図に描いた構成から、想定される Config を組み立てる。

新規構築やテスト環境を作るとき、先に図を描いてから Config を書き起こす。
その書き起こしは機械的な写経なので、図から起こせる分は起こしてしまう。

### 何を起こせて、何を起こせないか

図から確実に分かるのは「どの機器のどのポートが、どこに繋がっているか」だけである。
そこから起こせるのは次の範囲に限られる。

- ホスト名、管理IP
- VLAN の定義（台帳に登録されているもの）
- 結線しているポートの description と、上位/下位をつなぐトランク設定

起こせないもの（図に描かれていない情報）:

- 端末を挿すアクセスポートの割り当て
- 経路制御、ACL、認証、冗長化のパラメータ
- インターフェースの IP（管理IP以外）

起こせない部分は空欄にせず、要記入と分かる印を残す。黙って省くと、
「生成されたから完成している」と読まれてしまう。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, DeviceType, DeviceVlan, Vlan
from app.models.topology import Link
from app.services import configparse
from app.services.configparse import Vendor

# 図から起こせない箇所に付ける印。投入前に人が埋める前提であることを示す
TODO = "設計から起こせないため、投入前に記入が必要"


@dataclass
class DesignedLink:
    """図に描かれた結線1本を、その機器から見た形にしたもの。"""

    local_interface: str
    peer_hostname: str
    peer_interface: str
    peer_type: DeviceType

    @property
    def description(self) -> str:
        target = f"{self.peer_hostname}"
        if self.peer_interface:
            target += f" {self.peer_interface}"
        return f"to {target}"

    @property
    def is_infrastructure(self) -> bool:
        """機器同士をつなぐ線か（＝トランクにするのが自然か）。"""
        return self.peer_type in {
            DeviceType.SWITCH,
            DeviceType.ROUTER,
            DeviceType.FIREWALL,
            DeviceType.WIRELESS_CONTROLLER,
            DeviceType.WIRELESS_AP,
            DeviceType.LOAD_BALANCER,
        }


@dataclass
class DesignInput:
    device: Device
    links: list[DesignedLink] = field(default_factory=list)
    vlans: list[Vlan] = field(default_factory=list)
    # VLAN 番号 → 実機に投入できる名前。台帳が日本語のことが多いので別に持つ
    vlan_names: dict[int, str] = field(default_factory=dict)

    def vlan_name(self, vlan_id: int) -> str:
        return self.vlan_names.get(vlan_id, f"VLAN{vlan_id}")


def collect(db: Session, device: Device, *, vlan_ids: list[int] | None = None) -> DesignInput:
    """図と台帳から、その機器の設計情報を集める。"""
    devices = {d.id: d for d in db.scalars(select(Device))}

    links: list[DesignedLink] = []
    for link in db.scalars(select(Link).order_by(Link.id)):
        if link.a_device_id == device.id:
            peer, local, remote = devices.get(link.b_device_id), link.a_interface, link.b_interface
        elif link.b_device_id == device.id:
            peer, local, remote = devices.get(link.a_device_id), link.b_interface, link.a_interface
        else:
            continue
        if peer is None or not local:
            continue
        links.append(
            DesignedLink(
                local_interface=local,
                peer_hostname=peer.hostname,
                peer_interface=remote,
                peer_type=peer.device_type,
            )
        )

    catalog = list(db.scalars(select(Vlan).order_by(Vlan.vlan_id)))
    if vlan_ids is not None:
        catalog = [v for v in catalog if v.vlan_id in set(vlan_ids)]

    # 実機で使われている VLAN 名。台帳が日本語のとき、投入できる名前として使う
    observed: dict[int, str] = {}
    for row in db.scalars(select(DeviceVlan)):
        if row.name and row.vlan_id not in observed:
            observed[row.vlan_id] = row.name

    names = {vlan.vlan_id: _device_name(vlan, observed) for vlan in catalog}
    return DesignInput(device=device, links=links, vlans=catalog, vlan_names=names)


def _device_name(vlan: Vlan, observed: dict[int, str]) -> str:
    """実機に投入できる VLAN 名を決める。

    実機の VLAN 名に日本語は入らない。台帳は日本語で書かれていることが多いので、
    台帳 → 実機で使われている名前 → 番号から作るの順に落とす。
    黙って番号だけにすると、既存機器と名前が揃わなくなる。
    """
    for candidate in (vlan.name, observed.get(vlan.vlan_id)):
        cleaned = _ascii_only(candidate or "")
        if cleaned:
            return cleaned
    return f"VLAN{vlan.vlan_id}"


def _ascii_only(name: str) -> str:
    return "".join(
        char if char.isascii() and (char.isalnum() or char in "-_") else "" for char in name
    )


def build(db: Session, device: Device, *, vlan_ids: list[int] | None = None) -> str:
    """その機器の「想定される Config」を組み立てる。

    ベンダーごとに書式が違うので、書ける範囲で分ける。判別できないベンダーでは
    設計内容をコメントとして出す。それでも書き起こしの下敷きにはなるし、
    それらしい別ベンダーの構文を出すより害が無い。
    """
    plan = collect(db, device, vlan_ids=vlan_ids)
    vendor = _vendor_of(device)

    if vendor in (Vendor.CISCO_IOS, Vendor.CISCO_NXOS):
        return _cisco(plan)
    if vendor is Vendor.NEC_QX:
        return _comware(plan)
    if vendor is Vendor.NEC_IX:
        return _nec_ix(plan)
    if vendor is Vendor.JUNIPER:
        return _junos(plan)
    return _outline(plan, vendor)


def _vendor_of(device: Device) -> Vendor:
    """台帳のベンダー名から書式を決める。"""
    name = (device.vendor or "").strip().lower()
    model = (device.model or "").upper()

    if "juniper" in name:
        return Vendor.JUNIPER
    if "fortinet" in name or "forti" in name:
        return Vendor.FORTINET
    if "nec" in name:
        # NEC は系統が2つあり書式が違う。型番で分ける
        return Vendor.NEC_QX if model.startswith("QX") else Vendor.NEC_IX
    if "cisco" in name:
        return Vendor.CISCO_NXOS if model.startswith(("N9", "N7", "N5", "N3")) else Vendor.CISCO_IOS
    return Vendor.UNKNOWN


def _header(plan: DesignInput, vendor: Vendor, comment: str) -> list[str]:
    label = configparse.VENDOR_LABELS.get(vendor, "判別できません")
    return [
        f"{comment} ここから下は「図に描いた構成」から起こした想定 Config です。",
        f"{comment} {plan.device.hostname} / {plan.device.vendor} {plan.device.model} / {label}",
        f"{comment} 図に無い情報（アクセスポート・経路・ACL 等）は起こせません。",
        f"{comment} 投入前に必ず内容を確認してください。",
        comment,
    ]


def _trunk_vlans(plan: DesignInput) -> str:
    return ",".join(str(v.vlan_id) for v in plan.vlans)


# ---------------------------------------------------------------------------
# Cisco IOS / IOS-XE / NX-OS
# ---------------------------------------------------------------------------


def _cisco(plan: DesignInput) -> str:
    lines = _header(plan, Vendor.CISCO_IOS, "!")
    lines.append(f"hostname {plan.device.hostname}")
    lines.append("!")

    for vlan in plan.vlans:
        lines.append(f"vlan {vlan.vlan_id}")
        lines.append(f" name {plan.vlan_name(vlan.vlan_id)}")
        lines.append("!")

    allowed = _trunk_vlans(plan)
    for link in plan.links:
        lines.append(f"interface {link.local_interface}")
        lines.append(f" description {link.description}")
        if link.is_infrastructure and allowed:
            lines.append(" switchport mode trunk")
            lines.append(f" switchport trunk allowed vlan {allowed}")
            lines.append(f"! ネイティブ VLAN: {TODO}")
        elif link.is_infrastructure:
            lines.append(f"! VLAN が台帳に無いため許可 VLAN を出せません: {TODO}")
        lines.append("!")

    if plan.device.mgmt_ip:
        lines.append(f"! 管理IP: {plan.device.mgmt_ip}")
        lines.append(f"! 管理VLAN / マスク / 既定経路: {TODO}")
        lines.append("!")

    lines.append(f"! アクセスポートの割り当て: {TODO}")
    lines.append(f"! 経路制御・ACL・認証・冗長化: {TODO}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# NEC QX（Comware）
# ---------------------------------------------------------------------------


def _comware(plan: DesignInput) -> str:
    lines = _header(plan, Vendor.NEC_QX, "#")
    lines.append(f"sysname {plan.device.hostname}")
    lines.append("#")

    for vlan in plan.vlans:
        lines.append(f"vlan {vlan.vlan_id}")
        lines.append(f" name {plan.vlan_name(vlan.vlan_id)}")
        lines.append("#")

    permitted = " ".join(str(v.vlan_id) for v in plan.vlans)
    for link in plan.links:
        lines.append(f"interface {link.local_interface}")
        lines.append(f" description {link.description}")
        if link.is_infrastructure and permitted:
            lines.append(" port link-type trunk")
            lines.append(f" port trunk permit vlan {permitted}")
            lines.append(f"# PVID: {TODO}")
        lines.append("#")

    lines.append(f"# アクセスポートの割り当て: {TODO}")
    lines.append(f"# 経路制御・ACL・認証: {TODO}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# NEC IX
# ---------------------------------------------------------------------------


def _nec_ix(plan: DesignInput) -> str:
    lines = _header(plan, Vendor.NEC_IX, "!")
    lines.append(f"device-name {plan.device.hostname}")
    lines.append("!")

    for link in plan.links:
        lines.append(f"interface {link.local_interface}")
        lines.append(f" description {link.description}")
        lines.append(f"! IPアドレス: {TODO}")
        lines.append(" no shutdown")
        lines.append("!")

    if plan.vlans:
        lines.append("! VLAN を使う場合はサブインターフェースを切る")
        for vlan in plan.vlans:
            lines.append(f"! 例) interface GigaEthernet0.{vlan.vlan_id} / vlan-id {vlan.vlan_id}")
        lines.append("!")

    lines.append(f"! 経路制御・NAT・IPsec・フィルター: {TODO}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Juniper（set 形式）
# ---------------------------------------------------------------------------


def _junos(plan: DesignInput) -> str:
    lines = _header(plan, Vendor.JUNIPER, "#")
    lines.append(f"set system host-name {plan.device.hostname}")

    for vlan in plan.vlans:
        lines.append(f"set vlans {plan.vlan_name(vlan.vlan_id)} vlan-id {vlan.vlan_id}")

    members = " ".join(plan.vlan_name(v.vlan_id) for v in plan.vlans)
    for link in plan.links:
        port = link.local_interface
        lines.append(f'set interfaces {port} description "{link.description}"')
        if link.is_infrastructure and members:
            lines.append(
                f"set interfaces {port} unit 0 family ethernet-switching interface-mode trunk"
            )
            lines.append(
                f"set interfaces {port} unit 0 family ethernet-switching vlan members [ {members} ]"
            )

    lines.append(f"# アクセスポートの割り当て: {TODO}")
    lines.append(f"# 経路制御・セキュリティポリシー: {TODO}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 判別できないベンダー
# ---------------------------------------------------------------------------


def _outline(plan: DesignInput, vendor: Vendor) -> str:
    """書式が分からないベンダーでは、設計内容だけを書き出す。

    それらしい別ベンダーの構文を出すより、何を書くべきかの一覧の方が役に立つ。
    """
    lines = _header(plan, vendor, "#")
    lines.append("# このベンダーの書式には未対応です。以下は設計の内容です。")
    lines.append("#")
    lines.append(f"# ホスト名: {plan.device.hostname}")
    if plan.device.mgmt_ip:
        lines.append(f"# 管理IP: {plan.device.mgmt_ip}")

    if plan.vlans:
        lines.append("#")
        lines.append("# 定義する VLAN")
        for vlan in plan.vlans:
            label = f" {vlan.name}" if vlan.name else ""
            purpose = f" / {vlan.purpose}" if vlan.purpose else ""
            lines.append(f"#   {vlan.vlan_id}{label}{purpose}")

    if plan.links:
        lines.append("#")
        lines.append("# 結線")
        for link in plan.links:
            role = "機器間（トランク想定）" if link.is_infrastructure else "端末側"
            lines.append(
                f"#   {link.local_interface} → {link.peer_hostname} {link.peer_interface} / {role}"
            )

    lines.append("#")
    lines.append(f"# 経路制御・ACL・認証: {TODO}")
    return "\n".join(lines) + "\n"
