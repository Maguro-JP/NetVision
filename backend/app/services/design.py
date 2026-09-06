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

### 台帳から起こせる分は起こす

図に描かれていなくても、台帳に書いてあることは確かめられる。管理IP が
VLAN 台帳のどの区画に入るかは計算で決まるので、管理用の入口（SVI）は
起こせる。ここを毎回手で書かせると、マスクの写し間違いが入る。

### 安全側の既定を最初から入れる

新規構築の Config が、そのまま点検（configcheck）で赤くなるのはおかしい。
入り口を SSH に絞る、使わない web の口を閉じる、といった「どの現場でも
そうする」ものは最初から入れておく。設計の判断が要らないものに限る。
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, DeviceType, DeviceVlan, Vlan
from app.models.topology import Link
from app.services import configparse, conventions
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

    #: 管理IP が入る VLAN と、その区画。台帳の subnet から計算で決まる
    mgmt_vlan_id: int | None = None
    mgmt_netmask: str | None = None
    mgmt_prefixlen: int | None = None

    #: この現場ですでに使っている送り先。取り込んである Config から学んだもの。
    #: 設計の判断ではなく、そろえるだけのものなので起こしてよい
    syslog_hosts: list[str] = field(default_factory=list)
    ntp_hosts: list[str] = field(default_factory=list)

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
    plan = DesignInput(device=device, links=links, vlans=catalog, vlan_names=names)
    _locate_management(plan, catalog)

    # 記録の送り先は、この現場ですでに決まっている。起こさないでおくと、
    # 出した Config を自分の点検が「入っていません」と咎めることになる
    house = conventions.learn(db)
    plan.syslog_hosts = list(house.syslog_hosts[:2])
    plan.ntp_hosts = list(house.ntp_hosts[:2])
    return plan


def _locate_management(plan: DesignInput, catalog: list[Vlan]) -> None:
    """管理IP が、台帳のどの VLAN の区画に入るかを決める。

    ここは計算で決まるので、人に書かせるとマスクの写し間違いが入る。
    どの区画にも入らないときは何も決めない。当てずっぽうで近い VLAN を
    選ぶと、通らない Config が「起こせた」形で出てしまう。
    """
    if not plan.device.mgmt_ip:
        return
    try:
        address = ipaddress.ip_address(plan.device.mgmt_ip.strip())
    except ValueError:
        return

    for vlan in catalog:
        if not vlan.subnet:
            continue
        try:
            network = ipaddress.ip_network(vlan.subnet.strip(), strict=False)
        except ValueError:
            continue
        if address in network:
            plan.mgmt_vlan_id = vlan.vlan_id
            plan.mgmt_prefixlen = network.prefixlen
            plan.mgmt_netmask = str(network.netmask)
            return


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
    vendor = vendor_of(device)

    if vendor in (Vendor.CISCO_IOS, Vendor.CISCO_NXOS):
        return _cisco(plan)
    if vendor is Vendor.NEC_QX:
        return _comware(plan)
    if vendor is Vendor.NEC_IX:
        return _nec_ix(plan)
    if vendor is Vendor.JUNIPER:
        return _junos(plan)
    if vendor is Vendor.FORTINET:
        return _fortios(plan)
    if vendor is Vendor.ARISTA:
        return _arista(plan)
    if vendor is Vendor.ARUBA_CX:
        return _aruba_cx(plan)
    if vendor is Vendor.ALLIED:
        return _allied(plan)
    if vendor is Vendor.ALAXALA:
        return _alaxala(plan)
    if vendor is Vendor.HUAWEI:
        return _vrp(plan)
    if vendor is Vendor.FASTIRON:
        return _fastiron(plan)
    return _outline(plan, vendor)


def vendor_of(device: Device) -> Vendor:
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
    if "arista" in name:
        return Vendor.ARISTA
    if "aruba" in name or "hpe" in name:
        # Aruba CX（6xxx / 8xxx）。旧 ProVision 系は書式が別物なので、型番で分ける
        return Vendor.ARUBA_CX if model.startswith(("6", "8", "CX", "R")) else Vendor.UNKNOWN
    if "allied" in name or "アライド" in name:
        return Vendor.ALLIED
    if "alaxala" in name or "アラクサラ" in name or model.startswith("AX"):
        return Vendor.ALAXALA
    if "huawei" in name or "ファーウェイ" in name or "ﾌｧｰｳｪｲ" in name:
        return Vendor.HUAWEI
    if "ruckus" in name or "brocade" in name or model.startswith("ICX"):
        return Vendor.FASTIRON
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


def _ios_house_rules(plan: DesignInput) -> list[str]:
    """この現場ですでに使っている送り先を、IOS 系の書き方で出す。

    どこへ送るかは現場で決まっているので、設計の判断は要らない。入って
    いないと、出した Config を自分の点検が咎めることになる。
    """
    lines: list[str] = []
    for host in plan.syslog_hosts:
        lines.append(f"logging host {host}")
    if not plan.syslog_hosts:
        # この現場の送り先が、取り込んである Config から読み取れなかった。
        # 黙って省くと、記録が残らないまま「起こせた」形の Config になる
        lines.append(f"! ログの送り先: {TODO}")
        lines.append("!  例: logging host 10.10.20.10")
    for host in plan.ntp_hosts:
        lines.append(f"ntp server {host}")
    if not plan.ntp_hosts:
        lines.append(f"! 時刻の参照先: {TODO}")
        lines.append("!  例: ntp server 10.10.20.10")
    return lines


def _cisco(plan: DesignInput) -> str:
    lines = _header(plan, Vendor.CISCO_IOS, "!")
    lines.append(f"hostname {plan.device.hostname}")
    lines.append("!")
    lines.extend(
        [
            "! どの現場でもこうする、という既定。設計の判断は要らない",
            "service password-encryption",
            "no ip http server",
            "no ip http secure-server",
            "no ip source-route",
        ]
    )
    lines.extend(_ios_house_rules(plan))
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
            # IOS は方式の指定が要る機種がある。NX-OS では不要だが、
            # 入っていて困る行ではないので、下敷きとしては出しておく
            lines.append(" switchport trunk encapsulation dot1q")
            lines.append(" switchport mode trunk")
            lines.append(f" switchport trunk allowed vlan {allowed}")
            lines.append(" switchport nonegotiate")
            lines.append(f"! タグ無し VLAN（native）: {TODO}")
            lines.append("!  1 のままにしない。使っていない番号を決めて、両端で同じにする")
        elif link.is_infrastructure:
            lines.append(f"! VLAN が台帳に無いため許可 VLAN を出せません: {TODO}")
        else:
            # 端末側。番号は図に描かれていないので出せないが、
            # 端末を挿す口であることは図から分かる
            lines.append(" switchport mode access")
            lines.append(f"! アクセス VLAN: {TODO}")
            lines.append(" spanning-tree portfast")
            lines.append(" spanning-tree bpduguard enable")
        lines.append("!")

    lines.extend(_cisco_management(plan))

    lines.append("line vty 0 15")
    lines.append(" transport input ssh")
    lines.append(" exec-timeout 10 0")
    lines.append("!")
    lines.append(f"! ログの送り先 / 時刻の参照先 / 認証: {TODO}")
    lines.append(f"! アクセスポートの割り当て: {TODO}")
    lines.append(f"! 経路制御・ACL・認証・冗長化: {TODO}")
    return "\n".join(lines) + "\n"


def _cisco_management(plan: DesignInput) -> list[str]:
    """管理用の入口。台帳に区画が書いてあれば、そこまでは起こせる。"""
    if not plan.device.mgmt_ip:
        return []

    lines: list[str] = []
    if plan.mgmt_vlan_id is not None and plan.mgmt_netmask:
        lines.append(f"interface Vlan{plan.mgmt_vlan_id}")
        lines.append(" description management")
        lines.append(f" ip address {plan.device.mgmt_ip} {plan.mgmt_netmask}")
        lines.append(" no shutdown")
        lines.append("!")
        lines.append(f"! 既定の経路（この区画の出口）: {TODO}")
    else:
        lines.append(f"! 管理IP: {plan.device.mgmt_ip}")
        lines.append(
            f"! 管理VLAN / マスク / 既定経路: {TODO}"
            "（VLAN 台帳に、この住所が入る区画が登録されていません）"
        )
    lines.append("!")
    return lines


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
            lines.append(f"# タグ無し VLAN（PVID）: {TODO}")
            lines.append("#  1 のままにしない。使っていない番号を決めて、両端で同じにする")
        elif not link.is_infrastructure:
            lines.append(" port link-type access")
            lines.append(f"# アクセス VLAN: {TODO}")
            lines.append(" stp edged-port")
        lines.append("#")

    if plan.device.mgmt_ip and plan.mgmt_vlan_id is not None and plan.mgmt_prefixlen:
        lines.append(f"interface Vlan-interface{plan.mgmt_vlan_id}")
        lines.append(" description management")
        lines.append(f" ip address {plan.device.mgmt_ip} {plan.mgmt_prefixlen}")
        lines.append("#")
    elif plan.device.mgmt_ip:
        lines.append(f"# 管理IP: {plan.device.mgmt_ip}")
        lines.append(f"# 管理VLAN / マスク: {TODO}")
        lines.append("#")

    lines.append("# 入り口は ssh だけにする")
    lines.append("line vty 0 63")
    lines.append(" protocol inbound ssh")
    lines.append("#")
    lines.append(f"# アクセスポートの割り当て: {TODO}")
    lines.append(f"# ログの送り先 / 時刻の参照先: {TODO}")
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

    if plan.device.mgmt_ip and plan.mgmt_vlan_id is not None and plan.mgmt_prefixlen:
        lines.append(
            f"set interfaces irb unit {plan.mgmt_vlan_id} family inet address "
            f"{plan.device.mgmt_ip}/{plan.mgmt_prefixlen}"
        )
    elif plan.device.mgmt_ip:
        lines.append(f"# 管理IP: {plan.device.mgmt_ip}")
        lines.append(f"# 管理VLAN / マスク: {TODO}")

    lines.append("set system services ssh protocol-version v2")
    lines.append("delete system services telnet")
    lines.append(f"# タグ無し VLAN（native-vlan-id）: {TODO}")
    lines.append(f"# アクセスポートの割り当て: {TODO}")
    lines.append(f"# 経路制御・セキュリティポリシー: {TODO}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# FortiGate（FortiOS）
# ---------------------------------------------------------------------------


def _fortios(plan: DesignInput) -> str:
    """FortiGate は、通す通信を決めないと何も通らない。

    それが設計の中身そのものなので、ここでは起こさない。起こすのは
    ホスト名と、図から分かるポートの名前付けまでにする。
    """
    lines = _header(plan, Vendor.FORTINET, "#")
    lines.append("config system global")
    lines.append(f'    set hostname "{plan.device.hostname}"')
    lines.append("    set admin-telnet disable")
    lines.append("end")
    lines.append("#")

    if plan.links:
        lines.append("config system interface")
        for link in plan.links:
            lines.append(f'    edit "{link.local_interface}"')
            lines.append(f'        set description "{link.description}"')
            lines.append("        set allowaccess ping")
            lines.append("    next")
        lines.append("end")
        lines.append("#")

    if plan.device.mgmt_ip and plan.mgmt_netmask:
        lines.append(f"# 管理IP: {plan.device.mgmt_ip} {plan.mgmt_netmask}")
        lines.append(f"# どのポートに付けるかは図から決まりません: {TODO}")
    elif plan.device.mgmt_ip:
        lines.append(f"# 管理IP: {plan.device.mgmt_ip}")
        lines.append(f"# マスク / 付けるポート: {TODO}")
    lines.append("#")

    if plan.vlans:
        lines.append("# VLAN を受けるときは、ポートごとに VLAN インターフェースを作る")
        for vlan in plan.vlans:
            lines.append(
                f'#   例) edit "{plan.vlan_name(vlan.vlan_id)}" / set vlanid {vlan.vlan_id}'
            )
        lines.append("#")

    lines.append(f"# 通す通信（ポリシー）・NAT・経路: {TODO}")
    lines.append("#  ここを空のままにすると、線がつながっていても何も通りません")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 判別できないベンダー
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Arista EOS
# ---------------------------------------------------------------------------


def _arista(plan: DesignInput) -> str:
    """見た目は IOS 風。`encapsulation dot1q` は無く、アドレスは長さ付き。"""
    lines = _header(plan, Vendor.ARISTA, "!")
    lines.append(f"hostname {plan.device.hostname}")
    lines.append("!")
    for vlan in plan.vlans:
        lines.append(f"vlan {vlan.vlan_id}")
        lines.append(f"   name {plan.vlan_name(vlan.vlan_id)}")
        lines.append("!")

    allowed = _trunk_vlans(plan)
    for link in plan.links:
        lines.append(f"interface {link.local_interface}")
        lines.append(f"   description {link.description}")
        if link.is_infrastructure and allowed:
            lines.append("   switchport mode trunk")
            lines.append(f"   switchport trunk allowed vlan {allowed}")
            lines.append(f"! タグ無し VLAN（native）: {TODO}")
        elif link.is_infrastructure:
            lines.append(f"! VLAN が台帳に無いため許可 VLAN を出せません: {TODO}")
        else:
            lines.append("   switchport mode access")
            lines.append(f"! アクセス VLAN: {TODO}")
            lines.append("   spanning-tree portfast")
            lines.append("   spanning-tree bpduguard enable")
        lines.append("!")

    if plan.device.mgmt_ip and plan.mgmt_vlan_id is not None and plan.mgmt_prefixlen:
        lines.append(f"interface Vlan{plan.mgmt_vlan_id}")
        lines.append("   description management")
        lines.append(f"   ip address {plan.device.mgmt_ip}/{plan.mgmt_prefixlen}")
        lines.append("!")
    elif plan.device.mgmt_ip:
        lines.append(f"! 管理IP: {plan.device.mgmt_ip}")
        lines.append(f"! 管理VLAN / マスク: {TODO}")
        lines.append("!")

    lines.append("management ssh")
    lines.append("!")
    lines.append(f"! ログの送り先 / 時刻の参照先 / 認証: {TODO}")
    lines.append(f"! アクセスポートの割り当て: {TODO}")
    lines.append(f"! 経路制御・ACL・認証・冗長化（MLAG）: {TODO}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Aruba CX（AOS-CX）
# ---------------------------------------------------------------------------


def _aruba_cx(plan: DesignInput) -> str:
    """ポートは既定で落ちている。`no shutdown` を書いて初めて上がる。"""
    lines = _header(plan, Vendor.ARUBA_CX, "!")
    lines.append(f"hostname {plan.device.hostname}")
    lines.append("!")
    for vlan in plan.vlans:
        lines.append(f"vlan {vlan.vlan_id}")
        lines.append(f"    name {plan.vlan_name(vlan.vlan_id)}")
    if plan.vlans:
        lines.append("!")

    allowed = _trunk_vlans(plan)
    for link in plan.links:
        lines.append(f"interface {link.local_interface}")
        lines.append("    no shutdown")
        lines.append(f"    description {link.description}")
        lines.append("    no routing")
        if link.is_infrastructure and allowed:
            lines.append(f"    vlan trunk allowed {allowed}")
            lines.append(f"! タグ無し VLAN（native）: {TODO}")
        elif link.is_infrastructure:
            lines.append(f"! VLAN が台帳に無いため許可 VLAN を出せません: {TODO}")
        else:
            lines.append(f"! アクセス VLAN: {TODO}（vlan access 番号）")
            lines.append("    spanning-tree port-type admin-edge")
            lines.append("    spanning-tree bpdu-guard")
        lines.append("!")

    if plan.device.mgmt_ip and plan.mgmt_vlan_id is not None and plan.mgmt_prefixlen:
        lines.append(f"interface vlan {plan.mgmt_vlan_id}")
        lines.append("    description management")
        lines.append(f"    ip address {plan.device.mgmt_ip}/{plan.mgmt_prefixlen}")
        lines.append("!")
    elif plan.device.mgmt_ip:
        lines.append(f"! 管理IP: {plan.device.mgmt_ip}")
        lines.append(f"! 管理VLAN / マスク: {TODO}")
        lines.append("!")

    lines.append("ssh server vrf default")
    lines.append("!")
    lines.append(f"! ログの送り先 / 時刻の参照先 / 認証: {TODO}")
    lines.append(f"! アクセスポートの割り当て: {TODO}")
    lines.append(f"! 経路制御・ACL・認証・冗長化（VSX）: {TODO}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# アライドテレシス AlliedWare Plus
# ---------------------------------------------------------------------------


def _allied(plan: DesignInput) -> str:
    """IOS 風だが、VLAN の名前は `vlan database` の下に1行で並ぶ。"""
    lines = _header(plan, Vendor.ALLIED, "!")
    lines.append(f"hostname {plan.device.hostname}")
    lines.append("!")
    lines.append("service password-encryption")
    lines.append("no service telnet")
    lines.append("service ssh")
    lines.append("!")
    if plan.vlans:
        lines.append("vlan database")
        for vlan in plan.vlans:
            lines.append(f" vlan {vlan.vlan_id} name {plan.vlan_name(vlan.vlan_id)}")
        lines.append("!")

    allowed = _trunk_vlans(plan)
    for link in plan.links:
        lines.append(f"interface {link.local_interface}")
        lines.append(f" description {link.description}")
        lines.append(" switchport")
        if link.is_infrastructure and allowed:
            lines.append(" switchport mode trunk")
            lines.append(f" switchport trunk allowed vlan add {allowed}")
            lines.append(f"! タグ無し VLAN（native）: {TODO}")
        elif link.is_infrastructure:
            lines.append(f"! VLAN が台帳に無いため許可 VLAN を出せません: {TODO}")
        else:
            lines.append(" switchport mode access")
            lines.append(f"! アクセス VLAN: {TODO}")
            lines.append(" spanning-tree portfast")
            lines.append(" spanning-tree portfast bpdu-guard enable")
        lines.append("!")

    if plan.device.mgmt_ip and plan.mgmt_vlan_id is not None and plan.mgmt_prefixlen:
        lines.append(f"interface vlan{plan.mgmt_vlan_id}")
        lines.append(f" ip address {plan.device.mgmt_ip}/{plan.mgmt_prefixlen}")
        lines.append("!")
    elif plan.device.mgmt_ip:
        lines.append(f"! 管理IP: {plan.device.mgmt_ip}")
        lines.append(f"! 管理VLAN / マスク: {TODO}")
        lines.append("!")

    lines.append("line vty 0 4")
    lines.append(" exec-timeout 10 0")
    lines.append("!")
    lines.append(f"! ログの送り先 / 時刻の参照先 / 認証: {TODO}")
    lines.append(f"! アクセスポートの割り当て: {TODO}")
    lines.append(f"! 経路制御・ACL・認証・冗長化: {TODO}")
    return "\n".join(lines) + "\n"


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


# ---------------------------------------------------------------------------
# アラクサラ AX シリーズ
# ---------------------------------------------------------------------------


def _alaxala(plan: DesignInput) -> str:
    """IOS 風だが、口の種別と番号を分けて書き、名前は引用符で囲う。"""
    lines = _header(plan, Vendor.ALAXALA, "!")
    lines.append(f'hostname "{plan.device.hostname}"')
    lines.append("!")
    for vlan in plan.vlans:
        lines.append(f"vlan {vlan.vlan_id}")
        lines.append(f'  name "{plan.vlan_name(vlan.vlan_id)}"')
        lines.append("!")

    allowed = _trunk_vlans(plan)
    for link in plan.links:
        lines.append(f"interface {_ax_port(link.local_interface)}")
        lines.append(f'  description "{link.description}"')
        if link.is_infrastructure and allowed:
            lines.append("  switchport mode trunk")
            lines.append(f"  switchport trunk allowed vlan {allowed}")
            lines.append(f"! タグ無し VLAN（native）: {TODO}")
        elif link.is_infrastructure:
            lines.append(f"! VLAN が台帳に無いため許可 VLAN を出せません: {TODO}")
        else:
            lines.append("  switchport mode access")
            lines.append(f"! アクセス VLAN: {TODO}")
            lines.append("  spanning-tree portfast")
        lines.append("!")

    if plan.device.mgmt_ip and plan.mgmt_vlan_id is not None and plan.mgmt_netmask:
        lines.append(f"interface vlan {plan.mgmt_vlan_id}")
        lines.append(f"  ip address {plan.device.mgmt_ip} {plan.mgmt_netmask}")
        lines.append("!")
    elif plan.device.mgmt_ip:
        lines.append(f"! 管理IP: {plan.device.mgmt_ip}")
        lines.append(f"! 管理VLAN / マスク: {TODO}")
        lines.append("!")

    lines.append(f"! ログの送り先 / 時刻の参照先 / 認証: {TODO}")
    lines.append(f"! アクセスポートの割り当て: {TODO}")
    lines.append(f"! 経路制御・ACL・認証・冗長化: {TODO}")
    return "\n".join(lines) + "\n"


#: AX は種別と番号を空けて書く。`GigabitEthernet0/1` を `gigabitethernet 0/1` へ
_AX_PORT = re.compile(r"^([A-Za-z-]+?)\s*([\d/.:]+)$")


def _ax_port(name: str) -> str:
    found = _AX_PORT.match(name.strip())
    if not found:
        return name
    return f"{found.group(1).lower()} {found.group(2)}"


# ---------------------------------------------------------------------------
# ファーウェイ VRP
# ---------------------------------------------------------------------------


def _vrp(plan: DesignInput) -> str:
    """Comware とほぼ同じだが、VLAN の口の名前と許可の書き方が違う。"""
    lines = _header(plan, Vendor.HUAWEI, "#")
    lines.append(f"sysname {plan.device.hostname}")
    lines.append("#")

    for vlan in plan.vlans:
        lines.append(f"vlan {vlan.vlan_id}")
        lines.append(f" description {plan.vlan_name(vlan.vlan_id)}")
        lines.append("#")

    permitted = " ".join(str(v.vlan_id) for v in plan.vlans)
    for link in plan.links:
        lines.append(f"interface {link.local_interface}")
        lines.append(f" description {link.description}")
        if link.is_infrastructure and permitted:
            lines.append(" port link-type trunk")
            lines.append(f" port trunk allow-pass vlan {permitted}")
            lines.append(f"# タグ無し VLAN（PVID）: {TODO}")
            lines.append("#  1 のままにしない。使っていない番号を決めて、両端で同じにする")
        elif not link.is_infrastructure:
            lines.append(" port link-type access")
            lines.append(f"# アクセス VLAN: {TODO}")
            lines.append(" stp edged-port enable")
        lines.append("#")

    if plan.device.mgmt_ip and plan.mgmt_vlan_id is not None and plan.mgmt_netmask:
        lines.append(f"interface Vlanif{plan.mgmt_vlan_id}")
        lines.append(" description management")
        lines.append(f" ip address {plan.device.mgmt_ip} {plan.mgmt_netmask}")
        lines.append("#")
    elif plan.device.mgmt_ip:
        lines.append(f"# 管理IP: {plan.device.mgmt_ip}")
        lines.append(f"# 管理VLAN / マスク: {TODO}")
        lines.append("#")

    lines.append("# 入り口は ssh だけにする")
    lines.append("user-interface vty 0 4")
    lines.append(" protocol inbound ssh")
    lines.append("#")
    lines.append(f"# アクセスポートの割り当て: {TODO}")
    lines.append(f"# ログの送り先 / 時刻の参照先: {TODO}")
    lines.append(f"# 経路制御・ACL・認証: {TODO}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Ruckus / Brocade FastIron（ICX）
# ---------------------------------------------------------------------------


def _fastiron(plan: DesignInput) -> str:
    """VLAN の下に口を並べる書き方。IOS と向きが逆なので、そこを崩さない。

    IOS の雛形をそのまま出すと、口の下に VLAN を書く形になって入らない。
    """
    lines = _header(plan, Vendor.FASTIRON, "!")
    lines.append(f"hostname {plan.device.hostname}")
    lines.append("!")

    uplinks = [link.local_interface for link in plan.links if link.is_infrastructure]
    for vlan in plan.vlans:
        lines.append(f"vlan {vlan.vlan_id} name {plan.vlan_name(vlan.vlan_id)} by port")
        for port in uplinks:
            lines.append(f" tagged ethe {_icx_port(port)}")
        lines.append(f"! この VLAN のアクセスポート（untagged ethe ...）: {TODO}")
        lines.append("!")

    for link in plan.links:
        lines.append(f"interface ethernet {_icx_port(link.local_interface)}")
        lines.append(f" port-name {link.description}")
        if not link.is_infrastructure:
            lines.append(f"! この口を載せる VLAN は、上の vlan 側に untagged で書く: {TODO}")
        lines.append("!")

    if plan.device.mgmt_ip and plan.mgmt_vlan_id is not None and plan.mgmt_netmask:
        lines.append(f"interface ve {plan.mgmt_vlan_id}")
        lines.append(f" ip address {plan.device.mgmt_ip} {plan.mgmt_netmask}")
        lines.append("!")
    elif plan.device.mgmt_ip:
        lines.append(f"! 管理IP: {plan.device.mgmt_ip}")
        lines.append(f"! 管理VLAN / マスク: {TODO}")
        lines.append("!")

    lines.append(f"! ログの送り先 / 時刻の参照先 / 認証: {TODO}")
    lines.append(f"! 経路制御・ACL・冗長化: {TODO}")
    return "\n".join(lines) + "\n"


#: FastIron は種別と番号を空けて書く。`Ethernet1/1/1` を `1/1/1` へ
_ICX_PORT = re.compile(r"^[A-Za-z-]*\s*([\d/]+)$")


def _icx_port(name: str) -> str:
    found = _ICX_PORT.match(name.strip())
    return found.group(1) if found else name
