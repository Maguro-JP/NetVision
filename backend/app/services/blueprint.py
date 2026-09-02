"""「A と B を、この条件で繋ぐならどうなるか」を起こす。

いまある構成に足す設計（addition.py）とは別に、まだ何も無いところへ
「こう繋ぎたい」から構成を起こしたい場面がある。新しい拠点を作るとき、
既存とは別に検証環境を組むとき、客先に案を見せるとき。

そのとき決まっていないのは「間に何を挟むか」である。同じセグメントで
良ければスイッチだけで済むが、セグメントを分けるなら L3 の機器が要り、
境界で守るならファイアウォールが要る。この判断は条件から機械的に決まる
ので、ここで決めてしまう。

### 方針

- 条件から機器を決める。決めたら必ず理由を残す。「なんとなく FW を置いた」
  構成は、後から誰も外せなくなる
- 挟む機器を勝手に増やさない。1台で足りるなら1台にする。増やすほど
  止まる場所も増える
- 出す Config は命令だけ。説明は注記として別に返す（addition.py と同じ）
- ここで作るのは案であって、実機には投入しない
"""

from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Device, DeviceStatus, DeviceType, Site
from app.models.topology import ConfigSnapshot, ConfigSource, Link, LinkOrigin
from app.services.addition import netmask_of

#: 条件。画面のチェックに1つずつ対応する
SEPARATE = "separate"  # A と B を別のセグメントにする
RESTRICT = "restrict"  # A と B の間の通信を絞る
INTERNET = "internet"  # インターネットに出る
REDUNDANT = "redundant"  # 途中で1台落ちても通す
WAN = "wan"  # 別の建物どうし（拠点間）

CONDITION_LABELS = {
    SEPARATE: "別のセグメントにする",
    RESTRICT: "A と B の間の通信を絞る",
    INTERNET: "インターネットに出る",
    REDUNDANT: "途中で1台落ちても通す",
    WAN: "別の建物どうしをつなぐ",
}

#: 案で使う既定のアドレス帯。実在の網とぶつからないよう、
#: 試験用に予約されている 198.51.100.0/24 ではなく私用の帯から採る
_DEFAULT_BASE = ipaddress.ip_network("10.90.0.0/16")


@dataclass
class PlannedDevice:
    role: str  # 画面に出す肩書き（「A 側のスイッチ」など）
    hostname: str
    device_type: DeviceType
    purpose: str = ""


@dataclass
class PlannedLink:
    a_hostname: str
    a_interface: str
    b_hostname: str
    b_interface: str
    description: str = ""


@dataclass
class PlannedPort:
    """機器1つから見たポート1つ。線とセグメントの割り当てをここに落とす。"""

    hostname: str
    interface: str
    peer: str
    segment: str
    vlan_id: int | None = None
    routed: bool = False
    #: 相手側が L3 のポートかどうか。トランクで受ける相手ではないので、
    #: こちら側はアクセスポートにする
    peer_routed: bool = False


@dataclass
class PlannedAddress:
    """その機器がそのセグメントで持つアドレス。

    冗長のときは、実アドレスを機器ごとに持ち、端末が向く先は仮想IPにする。
    """

    hostname: str
    segment: str
    address: str
    netmask: str
    virtual_ip: str | None = None
    priority: int | None = None


@dataclass
class PlannedRoute:
    """中継する機器が持つ経路。直結していないセグメントへの行き方。"""

    hostname: str
    destination: str
    next_hop: str


@dataclass
class PlannedSegment:
    name: str
    vlan_id: int | None
    subnet: str
    gateway: str | None = None
    members: list[str] = field(default_factory=list)


@dataclass
class Decision:
    topic: str
    choice: str
    why: str


@dataclass
class Blueprint:
    title: str
    a_name: str
    b_name: str
    conditions: list[str] = field(default_factory=list)
    devices: list[PlannedDevice] = field(default_factory=list)
    links: list[PlannedLink] = field(default_factory=list)
    ports: list[PlannedPort] = field(default_factory=list)
    addresses: list[PlannedAddress] = field(default_factory=list)
    routes: list[PlannedRoute] = field(default_factory=list)
    segments: list[PlannedSegment] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    configs: dict[str, str] = field(default_factory=dict)
    notes: dict[str, list[str]] = field(default_factory=dict)

    def add(self, topic: str, choice: str, why: str) -> None:
        self.decisions.append(Decision(topic=topic, choice=choice, why=why))


@dataclass
class BlueprintRequest:
    a_name: str = "A"
    b_name: str = "B"
    conditions: list[str] = field(default_factory=list)
    #: 案に使うアドレス帯。空なら既定を使う
    base: str | None = None
    vendor: str = "Cisco"
    #: 機器名の頭に付ける印。テスト用に作るときに効く
    prefix: str = ""


# ---------------------------------------------------------------------------
# 構成を決める
# ---------------------------------------------------------------------------


def build(request: BlueprintRequest) -> Blueprint:
    conditions = [c for c in request.conditions if c in CONDITION_LABELS]
    plan = Blueprint(
        title=f"{request.a_name} と {request.b_name} をつなぐ",
        a_name=request.a_name,
        b_name=request.b_name,
        conditions=conditions,
    )

    prefix = request.prefix
    a_switch = _name(prefix, request.a_name, "sw")
    b_switch = _name(prefix, request.b_name, "sw")

    plan.devices.append(
        PlannedDevice(
            role=f"{request.a_name} 側のスイッチ",
            hostname=a_switch,
            device_type=DeviceType.SWITCH,
            purpose=f"{request.a_name} の端末を収容する",
        )
    )
    plan.devices.append(
        PlannedDevice(
            role=f"{request.b_name} 側のスイッチ",
            hostname=b_switch,
            device_type=DeviceType.SWITCH,
            purpose=f"{request.b_name} の端末を収容する",
        )
    )

    middle = _decide_middle(plan, request, prefix)
    plan.devices.extend(middle)
    _wire(plan, request, a_switch, b_switch, middle)
    _write_configs(plan, request)
    return plan


def _name(prefix: str, base: str, suffix: str) -> str:
    """機器名を作る。機器名は ASCII に寄せる（日本語は機器によって化ける）。"""
    ascii_base = "".join(ch for ch in base if ch.isascii() and (ch.isalnum() or ch in "-_"))
    stem = (ascii_base or "site").lower()
    head = f"{prefix}-" if prefix else ""
    return f"{head}{stem}-{suffix}-01"


def _decide_middle(plan: Blueprint, request: BlueprintRequest, prefix: str) -> list[PlannedDevice]:
    """間に何を挟むかを決める。

    挟む機器は少ないほうがいい。増やすほど止まる場所も増え、設定を合わせる
    箇所も増える。条件が要求する分だけ置く。
    """
    conditions = set(plan.conditions)
    middle: list[PlannedDevice] = []

    needs_l3 = bool(conditions & {SEPARATE, INTERNET, WAN})
    needs_firewall = RESTRICT in conditions or INTERNET in conditions

    if not needs_l3 and not needs_firewall:
        plan.add(
            "間に挟む機器",
            "なし（スイッチどうしを直結）",
            "同じセグメントのままで良く、通信を絞る条件も無いので、"
            "間に機器は要りません。挟むほど止まる場所が増えます。",
        )
        if REDUNDANT in conditions:
            plan.warnings.append(
                "1台落ちても通す条件が付いていますが、間に挟む機器がない構成です。"
                "スイッチどうしの直結では、線が切れたら通りません。"
                "冗長にするなら、線を2本にして束ねる（LAG）ところから考えてください。"
            )
        return middle

    if needs_l3:
        router = PlannedDevice(
            role="境界のルータ",
            hostname=_name(prefix, "core", "rt"),
            device_type=DeviceType.ROUTER,
            purpose="セグメントをまたぐ通信を中継する",
        )
        middle.append(router)
        why = []
        if SEPARATE in conditions:
            why.append("別のセグメントにするので、またぐ通信を中継する機器が要ります")
        if INTERNET in conditions:
            why.append("インターネットに出るので、外向きの出口が要ります")
        if WAN in conditions:
            why.append("別の建物どうしなので、回線を終端する機器が要ります")
        plan.add("ルータ", router.hostname, "。".join(why) + "。")

    if needs_firewall:
        firewall = PlannedDevice(
            role="境界のファイアウォール",
            hostname=_name(prefix, "core", "fw"),
            device_type=DeviceType.FIREWALL,
            purpose="通す通信を絞る",
        )
        middle.append(firewall)
        why = []
        if RESTRICT in conditions:
            why.append(
                "A と B の間を絞る条件があるためです。"
                "スイッチの ACL でも絞れますが、行数が増えると誰も読めなくなり、"
                "変更の記録も残りません"
            )
        if INTERNET in conditions:
            why.append("インターネットに出る以上、外から入ってくるものを止める必要があります")
        plan.add("ファイアウォール", firewall.hostname, "。".join(why) + "。")

    if REDUNDANT in conditions and middle:
        doubled: list[PlannedDevice] = []
        for device in middle:
            doubled.append(device)
            partner = PlannedDevice(
                role=f"{device.role}（2台目）",
                hostname=device.hostname.replace("-01", "-02"),
                device_type=device.device_type,
                purpose=f"{device.hostname} が落ちたときに引き継ぐ",
            )
            doubled.append(partner)
        middle = doubled
        plan.add(
            "冗長",
            "間の機器を2台ずつにする",
            "1台落ちても通す条件があるためです。2台にするだけでは切り替わらないので、"
            "既定ゲートウェイの冗長（HSRP / VRRP）も入れます。"
            "優先度は必ず両側で変えてください。同じだと起動の順番で主が決まります。",
        )
        plan.warnings.append(
            "冗長にすると、片側だけ設定が入っている状態が事故のもとになります。"
            "投入後に、両方の機器で同じグループ・違う優先度になっているか確かめてください。"
        )
    elif REDUNDANT in conditions:
        plan.warnings.append(
            "1台落ちても通す条件が付いていますが、間に挟む機器がない構成です。"
            "スイッチどうしの直結では、線が切れたら通りません。"
            "冗長にするなら、線を2本にして束ねる（LAG）ところから考えてください。"
        )

    return middle


def _wire(
    plan: Blueprint,
    request: BlueprintRequest,
    a_switch: str,
    b_switch: str,
    middle: list[PlannedDevice],
) -> None:
    """つなぐ順番を決めて、線とセグメントとアドレスを同時に割り当てる。

    別々に決めると必ずずれる。「この線はどのセグメントで、その両端の
    アドレスは何番か」は1つの決めごとなので、まとめて決める。

    並び順は A →（FW があれば FW）→ ルータ → B。絞る機器は守りたい側の
    手前に置く。奥に置くと、通ってから止めることになる。
    """
    base = _base_network(plan, request)
    subnets = list(base.subnets(new_prefix=24))
    ports = _PortAllocator(request.vendor)

    primary = [d for d in middle if not d.hostname.endswith("-02")]
    firewalls = [d for d in primary if d.device_type is DeviceType.FIREWALL]
    routers = [d for d in primary if d.device_type is DeviceType.ROUTER]
    chain_middle = firewalls + routers

    a_device = PlannedDevice(role="", hostname=a_switch, device_type=DeviceType.SWITCH)
    b_device = PlannedDevice(role="", hostname=b_switch, device_type=DeviceType.SWITCH)
    chain = [a_device, *chain_middle, b_device]

    if not chain_middle:
        network = subnets[0]
        segment = PlannedSegment(
            name=request.a_name,
            vlan_id=10,
            subnet=str(network),
            gateway=None,
            members=[a_switch, b_switch],
        )
        plan.segments.append(segment)
        plan.add(
            "セグメント",
            f"1つ（{segment.subnet}）",
            "分ける条件が無いので、同じセグメントに置きます。"
            "分けると、またぐための機器と経路が必要になります。",
        )
        _connect(plan, ports, a_device, b_device, segment, description="A と B の直結")
        return

    partners = {d.hostname.replace("-02", "-01"): d for d in middle if d.hostname.endswith("-02")}
    index = 0
    for left, right in zip(chain, chain[1:], strict=False):
        network = subnets[index]
        index += 1
        edge = left.device_type is DeviceType.SWITCH or right.device_type is DeviceType.SWITCH
        name = _segment_name(request, left, right, edge)
        hosts = network.hosts()
        virtual = str(next(hosts))  # 端末が向く先。冗長なら仮想IP、単体なら実IP

        segment = PlannedSegment(
            name=name,
            vlan_id=10 * index if edge else None,
            subnet=str(network),
            gateway=virtual,
            members=[left.hostname, right.hostname],
        )
        plan.segments.append(segment)

        if not edge:
            # 機器どうしをつなぐだけのセグメント。両端にアドレスが要る。
            # 端末は置かないので、向く先（仮想IP）は作らない
            segment.gateway = None
            _address(plan, left, segment, virtual)
            _address(plan, right, segment, str(next(hosts)))
            _connect(plan, ports, left, right, segment)
            continue

        l3_side = right if left.device_type is DeviceType.SWITCH else left
        pair = partners.get(l3_side.hostname)
        if pair is None:
            _address(plan, l3_side, segment, virtual)
        else:
            # 冗長のときは、実アドレスを2つ配って、端末が向く先は仮想IPにする
            _address(plan, l3_side, segment, str(next(hosts)), priority=110)
            _address(plan, pair, segment, str(next(hosts)), priority=100)
            segment.members.append(pair.hostname)

        _connect(plan, ports, left, right, segment)
        if pair is not None:
            other = left if l3_side is right else right
            _connect(plan, ports, other, pair, segment, description="2台目への線")

    _decide_routes(plan, chain)

    plan.add(
        "セグメント",
        " / ".join(f"{s.name} {s.subnet}" for s in plan.segments),
        "別のセグメントにする条件があるためです。"
        "機器どうしをつなぐだけのセグメントには端末を置きません。"
        "そこに端末を置くと、絞りたい通信が素通りします。",
    )


def _decide_routes(plan: Blueprint, chain: list[PlannedDevice]) -> None:
    """中継する機器に、直結していないセグメントへの経路を持たせる。

    間に2台以上並ぶと、隣までは直結でも、その先へは行き方を教えないと届かない。
    ここを忘れると「片方向だけ通る」形になり、切り分けに時間がかかる。
    """
    middle = [d for d in chain if d.device_type is not DeviceType.SWITCH]
    if len(middle) < 2:
        return

    #: セグメントは繋がる順に並んでいるので、番号の大小がそのまま方向になる
    attached: dict[str, list[int]] = {}
    for index, segment in enumerate(plan.segments):
        for member in segment.members:
            attached.setdefault(member, []).append(index)

    for position, device in enumerate(middle):
        mine = attached.get(device.hostname, [])
        if not mine:
            continue
        for index, segment in enumerate(plan.segments):
            if index in mine or segment.gateway is None and not _has_hosts(segment):
                continue
            if index in mine:
                continue
            neighbor = middle[position - 1] if index < min(mine) else None
            if neighbor is None and position + 1 < len(middle):
                neighbor = middle[position + 1]
            if neighbor is None:
                continue
            shared = _shared_transit(plan, device.hostname, neighbor.hostname)
            if shared is None:
                continue
            next_hop = next(
                (
                    a.address
                    for a in plan.addresses
                    if a.hostname == neighbor.hostname and a.segment == shared
                ),
                None,
            )
            if next_hop is None:
                continue
            plan.routes.append(
                PlannedRoute(
                    hostname=device.hostname,
                    destination=segment.subnet,
                    next_hop=next_hop,
                )
            )

    if plan.routes:
        plan.add(
            "経路",
            "中継する機器どうしで、互いのセグメントへの経路を持つ",
            "隣までは直結でも、その先へは行き方を教えないと届きません。"
            "ここを忘れると片方向だけ通る形になり、切り分けに時間がかかります。",
        )


def _has_hosts(segment: PlannedSegment) -> bool:
    return segment.vlan_id is not None


def _shared_transit(plan: Blueprint, left: str, right: str) -> str | None:
    for segment in plan.segments:
        if left in segment.members and right in segment.members and segment.vlan_id is None:
            return segment.name
    return None


def _segment_name(
    request: BlueprintRequest, left: PlannedDevice, right: PlannedDevice, edge: bool
) -> str:
    if not edge:
        return "transit"
    switch = left if left.device_type is DeviceType.SWITCH else right
    return (
        request.a_name
        if "-sw-" in switch.hostname and request.a_name.lower() in switch.hostname.lower()
        else request.b_name
    )


class _PortAllocator:
    """機器ごとに、使うポートを順に配る。

    同じポートを2つの線に割り当てると、貼った時点で上書きされる。
    配る場所を1か所にまとめて、そこを起こさないようにする。
    """

    #: 機器の種類ごとの、ポート名の作り方。書式（ベンダー）で名前の形が変わる
    def __init__(self, vendor: str = "Cisco") -> None:
        self._used: dict[str, int] = {}
        self._style = _style_of(vendor)

    def take(self, device: PlannedDevice) -> str:
        count = self._used.get(device.hostname, 0)
        self._used[device.hostname] = count + 1
        switch = device.device_type is DeviceType.SWITCH
        if self._style == "aruba":
            # AOS-CX は `1/1/1` の形。上流側は後ろの番号から使う
            return f"1/1/{24 - count}" if switch else f"1/1/{1 + count}"
        if self._style == "arista":
            return f"Ethernet{48 - count}" if switch else f"Ethernet{1 + count}"
        if self._style == "allied":
            return f"port1.0.{24 - count}" if switch else f"port1.0.{1 + count}"
        if switch:
            return f"GigabitEthernet1/0/{24 - count}"
        return f"GigabitEthernet0/{count}"


def _connect(
    plan: Blueprint,
    ports: _PortAllocator,
    left: PlannedDevice,
    right: PlannedDevice,
    segment: PlannedSegment,
    *,
    description: str = "",
) -> None:
    a_port = ports.take(left)
    b_port = ports.take(right)
    plan.links.append(
        PlannedLink(
            a_hostname=left.hostname,
            a_interface=a_port,
            b_hostname=right.hostname,
            b_interface=b_port,
            description=description or segment.name,
        )
    )
    for device, port in ((left, a_port), (right, b_port)):
        plan.ports.append(
            PlannedPort(
                hostname=device.hostname,
                interface=port,
                peer=(right if device is left else left).hostname,
                segment=segment.name,
                vlan_id=segment.vlan_id,
                routed=device.device_type is not DeviceType.SWITCH,
                peer_routed=(right if device is left else left).device_type
                is not DeviceType.SWITCH,
            )
        )


def _address(
    plan: Blueprint,
    device: PlannedDevice,
    segment: PlannedSegment,
    address: str,
    *,
    priority: int | None = None,
) -> None:
    plan.addresses.append(
        PlannedAddress(
            hostname=device.hostname,
            segment=segment.name,
            address=address,
            netmask=netmask_of(segment.subnet),
            virtual_ip=segment.gateway if priority is not None else None,
            priority=priority,
        )
    )


def _base_network(plan: Blueprint, request: BlueprintRequest) -> ipaddress.IPv4Network:
    if not request.base:
        return _DEFAULT_BASE
    try:
        network = ipaddress.ip_network(request.base, strict=False)
    except ValueError:
        plan.warnings.append(f"アドレス帯 {request.base} を読めないので、既定の帯を使いました。")
        return _DEFAULT_BASE
    if network.version != 4 or network.prefixlen > 22:
        plan.warnings.append(
            f"{request.base} は狭すぎるので、既定の帯を使いました。"
            "/22 より広い帯を指定してください。"
        )
        return _DEFAULT_BASE
    return network


# ---------------------------------------------------------------------------
# Config を書く
# ---------------------------------------------------------------------------


def _write_configs(plan: Blueprint, request: BlueprintRequest) -> None:
    """機器ごとの Config。中身は命令だけで、説明は注記として別に返す。

    そのまま貼って使うものに説明が混ざると、貼る前に消す手間が要るうえ、
    消し忘れがそのまま機器に入る。
    """
    writer = {
        "nec": _comware,
        "aruba": _aruba_cx,
        "arista": _arista,
        "allied": _allied,
    }.get(_style_of(request.vendor), _cisco)
    for device in plan.devices:
        ports = [p for p in plan.ports if p.hostname == device.hostname]
        addresses = [a for a in plan.addresses if a.hostname == device.hostname]
        body, notes = writer(plan, device, ports, addresses)
        plan.configs[device.hostname] = body
        plan.notes[device.hostname] = notes


def _cisco(
    plan: Blueprint,
    device: PlannedDevice,
    ports: list[PlannedPort],
    addresses: list[PlannedAddress],
) -> tuple[str, list[str]]:
    lines = [f"hostname {device.hostname}", "!"]
    notes: list[str] = [device.purpose] if device.purpose else []

    vlans = {p.vlan_id: p.segment for p in ports if p.vlan_id is not None and not p.routed}
    for vlan_id, name in sorted(vlans.items()):
        lines.append(f"vlan {vlan_id}")
        lines.append(f" name {_ascii(name)}")
        lines.append("!")

    for port in ports:
        lines.append(f"interface {port.interface}")
        lines.append(f" description to {port.peer}")
        if port.routed:
            found = next((a for a in addresses if a.segment == port.segment), None)
            if found:
                lines.append(f" ip address {found.address} {found.netmask}")
                if found.virtual_ip:
                    group = port.vlan_id or 1
                    lines.append(f" standby {group} ip {found.virtual_ip}")
                    lines.append(f" standby {group} priority {found.priority}")
                    lines.append(f" standby {group} preempt")
        elif port.vlan_id is not None and port.peer_routed:
            # 相手が L3 のポートなので、タグを付けずに渡す
            lines.append(" switchport mode access")
            lines.append(f" switchport access vlan {port.vlan_id}")
        elif port.vlan_id is not None:
            lines.append(" switchport mode trunk")
            lines.append(f" switchport trunk allowed vlan {port.vlan_id}")
        lines.append(" no shutdown")
        lines.append("!")

    for route in [r for r in plan.routes if r.hostname == device.hostname]:
        network = ipaddress.ip_network(route.destination)
        lines.append(f"ip route {network.network_address} {network.netmask} {route.next_hop}")
    if any(r.hostname == device.hostname for r in plan.routes):
        lines.append("!")

    lines.append("no ip http server")
    lines.append("no ip http secure-server")
    lines.append("!")
    lines.append("line vty 0 15")
    lines.append(" transport input ssh")
    lines.append(" exec-timeout 10 0")
    lines.append("!")

    notes.extend(_notes_for(plan, device, addresses))
    return "\n".join(lines) + "\n", notes


def _comware(
    plan: Blueprint,
    device: PlannedDevice,
    ports: list[PlannedPort],
    addresses: list[PlannedAddress],
) -> tuple[str, list[str]]:
    lines = [f"sysname {device.hostname}", "#"]
    notes: list[str] = [device.purpose] if device.purpose else []

    vlans = {p.vlan_id: p.segment for p in ports if p.vlan_id is not None and not p.routed}
    for vlan_id, name in sorted(vlans.items()):
        lines.append(f"vlan {vlan_id}")
        lines.append(f" name {_ascii(name)}")
        lines.append("#")

    for port in ports:
        lines.append(f"interface {port.interface}")
        lines.append(f" description to {port.peer}")
        if port.routed:
            found = next((a for a in addresses if a.segment == port.segment), None)
            if found:
                lines.append(f" ip address {found.address} {found.netmask}")
                if found.virtual_ip:
                    group = port.vlan_id or 1
                    lines.append(f" vrrp vrid {group} virtual-ip {found.virtual_ip}")
                    lines.append(f" vrrp vrid {group} priority {found.priority}")
        elif port.vlan_id is not None and port.peer_routed:
            lines.append(" port link-type access")
            lines.append(f" port access vlan {port.vlan_id}")
        elif port.vlan_id is not None:
            lines.append(" port link-type trunk")
            lines.append(f" port trunk permit vlan {port.vlan_id}")
        lines.append(" undo shutdown")
        lines.append("#")

    for route in [r for r in plan.routes if r.hostname == device.hostname]:
        network = ipaddress.ip_network(route.destination)
        lines.append(
            f"ip route-static {network.network_address} {network.netmask} {route.next_hop}"
        )
    if any(r.hostname == device.hostname for r in plan.routes):
        lines.append("#")

    lines.append("undo ip http enable")
    lines.append("undo ip https enable")
    lines.append("#")

    notes.extend(_notes_for(plan, device, addresses))
    return "\n".join(lines) + "\n", notes


def _style_of(vendor: str) -> str:
    lowered = vendor.lower()
    for key in ("nec", "aruba", "arista", "allied"):
        if key in lowered:
            return key
    if "アライド" in vendor:
        return "allied"
    return "cisco"


def _arista(
    plan: Blueprint,
    device: PlannedDevice,
    ports: list[PlannedPort],
    addresses: list[PlannedAddress],
) -> tuple[str, list[str]]:
    """EOS。IOS 風だが、アドレスと経路は長さ付き。冗長は VRRP。"""
    lines = [f"hostname {device.hostname}", "!"]
    notes: list[str] = [device.purpose] if device.purpose else []

    vlans = {p.vlan_id: p.segment for p in ports if p.vlan_id is not None and not p.routed}
    for vlan_id, name in sorted(vlans.items()):
        lines.append(f"vlan {vlan_id}")
        lines.append(f"   name {_ascii(name)}")
        lines.append("!")

    for port in ports:
        lines.append(f"interface {port.interface}")
        lines.append(f"   description to {port.peer}")
        if port.routed:
            found = next((a for a in addresses if a.segment == port.segment), None)
            if found:
                prefixlen = ipaddress.ip_network(f"0.0.0.0/{found.netmask}").prefixlen
                lines.append("   no switchport")
                lines.append(f"   ip address {found.address}/{prefixlen}")
                if found.virtual_ip:
                    group = port.vlan_id or 1
                    lines.append(f"   vrrp {group} ipv4 {found.virtual_ip}")
                    lines.append(f"   vrrp {group} priority-level {found.priority}")
        elif port.vlan_id is not None and port.peer_routed:
            lines.append("   switchport mode access")
            lines.append(f"   switchport access vlan {port.vlan_id}")
        elif port.vlan_id is not None:
            lines.append("   switchport mode trunk")
            lines.append(f"   switchport trunk allowed vlan {port.vlan_id}")
        lines.append("   no shutdown")
        lines.append("!")

    for route in [r for r in plan.routes if r.hostname == device.hostname]:
        lines.append(f"ip route {route.destination} {route.next_hop}")
    if any(r.hostname == device.hostname for r in plan.routes):
        lines.append("!")

    lines.append("management ssh")
    lines.append("!")
    notes.extend(_notes_for(plan, device, addresses))
    return "\n".join(lines) + "\n", notes


def _allied(
    plan: Blueprint,
    device: PlannedDevice,
    ports: list[PlannedPort],
    addresses: list[PlannedAddress],
) -> tuple[str, list[str]]:
    """AlliedWare Plus。VLAN の名前は vlan database の下、アドレスは長さ付き。"""
    lines = [f"hostname {device.hostname}", "!", "no service telnet", "service ssh", "!"]
    notes: list[str] = [device.purpose] if device.purpose else []

    vlans = {p.vlan_id: p.segment for p in ports if p.vlan_id is not None and not p.routed}
    if vlans:
        lines.append("vlan database")
        for vlan_id, name in sorted(vlans.items()):
            lines.append(f" vlan {vlan_id} name {_ascii(name)}")
        lines.append("!")

    for port in ports:
        lines.append(f"interface {port.interface}")
        lines.append(f" description to {port.peer}")
        if port.routed:
            found = next((a for a in addresses if a.segment == port.segment), None)
            if found:
                prefixlen = ipaddress.ip_network(f"0.0.0.0/{found.netmask}").prefixlen
                lines.append(f" ip address {found.address}/{prefixlen}")
                if found.virtual_ip:
                    group = port.vlan_id or 1
                    lines.append(f" vrrp {group} address-family ipv4")
                    lines.append(f"  virtual-ip {found.virtual_ip} backup")
                    lines.append(f"  priority {found.priority}")
        elif port.vlan_id is not None and port.peer_routed:
            lines.append(" switchport")
            lines.append(" switchport mode access")
            lines.append(f" switchport access vlan {port.vlan_id}")
        elif port.vlan_id is not None:
            lines.append(" switchport")
            lines.append(" switchport mode trunk")
            lines.append(f" switchport trunk allowed vlan add {port.vlan_id}")
        lines.append("!")

    for route in [r for r in plan.routes if r.hostname == device.hostname]:
        lines.append(f"ip route {route.destination} {route.next_hop}")
    if any(r.hostname == device.hostname for r in plan.routes):
        lines.append("!")

    lines.append("line vty 0 4")
    lines.append(" exec-timeout 10 0")
    lines.append("!")
    notes.extend(_notes_for(plan, device, addresses))
    return "\n".join(lines) + "\n", notes


def _aruba_cx(
    plan: Blueprint,
    device: PlannedDevice,
    ports: list[PlannedPort],
    addresses: list[PlannedAddress],
) -> tuple[str, list[str]]:
    """AOS-CX。ポートは既定で落ちているので no shutdown を書く。アドレスは長さ付き。"""
    lines = [f"hostname {device.hostname}", "!"]
    notes: list[str] = [device.purpose] if device.purpose else []

    vlans = {p.vlan_id: p.segment for p in ports if p.vlan_id is not None and not p.routed}
    for vlan_id, name in sorted(vlans.items()):
        lines.append(f"vlan {vlan_id}")
        lines.append(f"    name {_ascii(name)}")
    if vlans:
        lines.append("!")

    for port in ports:
        lines.append(f"interface {port.interface}")
        lines.append("    no shutdown")
        lines.append(f"    description to {port.peer}")
        if port.routed:
            found = next((a for a in addresses if a.segment == port.segment), None)
            if found:
                lines.append("    routing")
                prefixlen = ipaddress.ip_network(f"0.0.0.0/{found.netmask}").prefixlen
                lines.append(f"    ip address {found.address}/{prefixlen}")
                if found.virtual_ip:
                    group = port.vlan_id or 1
                    lines.append(f"    vrrp {group} address-family ipv4")
                    lines.append(f"        address {found.virtual_ip} primary")
                    lines.append(f"        priority {found.priority}")
                    lines.append("        no shutdown")
        elif port.vlan_id is not None and port.peer_routed:
            lines.append("    no routing")
            lines.append(f"    vlan access {port.vlan_id}")
        elif port.vlan_id is not None:
            lines.append("    no routing")
            lines.append(f"    vlan trunk allowed {port.vlan_id}")
        lines.append("!")

    for route in [r for r in plan.routes if r.hostname == device.hostname]:
        lines.append(f"ip route {route.destination} {route.next_hop}")
    if any(r.hostname == device.hostname for r in plan.routes):
        lines.append("!")

    lines.append("ssh server vrf default")
    lines.append("no https-server vrf default")
    lines.append("!")

    notes.extend(_notes_for(plan, device, addresses))
    return "\n".join(lines) + "\n", notes


def _notes_for(
    plan: Blueprint, device: PlannedDevice, addresses: list[PlannedAddress]
) -> list[str]:
    notes: list[str] = []
    if device.device_type is DeviceType.SWITCH:
        notes.append(
            "端末を挿すポートの設定は入れていません。何台をどこに挿すかは、"
            "この条件からは決まらないためです。"
        )
    if device.device_type is DeviceType.FIREWALL:
        notes.append(
            "通す通信の中身（どのポートを、どちら向きに開けるか）は入れていません。"
            "業務が決まらないと決められないところで、当てずっぽうで開けると"
            "ファイアウォールを置いた意味が無くなります。"
        )
    if any(a.virtual_ip for a in addresses):
        notes.append(
            "端末が向く先は仮想IPです。この機器の実アドレスとは別なので、"
            "端末側の既定ゲートウェイには仮想IPを入れてください。"
        )
    if INTERNET in plan.conditions and device.device_type is DeviceType.ROUTER:
        notes.append(
            "インターネット側の回線設定（IP・NAT・既定経路）は入れていません。"
            "回線事業者から渡される値が決まらないと書けないためです。"
        )
    notes.append(
        "ユーザー・パスワード・SSH の鍵・SNMP・NTP・ログ送り先は入れていません。"
        "既存機器と同じ値にしてください。"
    )
    return notes


def _ascii(text: str) -> str:
    """名前は ASCII に寄せる。機器によっては日本語が化ける。"""
    cleaned = "".join(ch for ch in text if ch.isascii() and (ch.isalnum() or ch in "-_"))
    return (cleaned or "SEGMENT")[:32]


# ---------------------------------------------------------------------------
# 試しに置いてみる（テスト拠点として台帳に作る）
# ---------------------------------------------------------------------------

#: テスト用に作った拠点の目印。名前の頭に付ける。
#: まとめて消せるようにするため、印は名前だけでなく備考にも残す
TEST_PREFIX = "テスト"
TEST_NOTE = "NetVision が案として作ったテスト構成です。実機はありません。"


def apply_to_ledger(db: Session, plan: Blueprint, site_name: str) -> Site:
    """起こした構成を、テスト拠点として台帳に作る。

    案は図で見ないと判断できない。図を出すために別の描画を作るより、
    実際に台帳へ入れて、いつものトポロジー図で見るほうが早いし正しい
    （見ている図と、後で本番に入れるものが同じ形になる）。

    本物と混ざらないよう、拠点名に印を付け、備考にも残す。まとめて消せる。
    """
    name = site_name if site_name.startswith(TEST_PREFIX) else f"{TEST_PREFIX}-{site_name}"
    site = Site(name=name, status="planned", notes=TEST_NOTE)
    db.add(site)
    db.flush()

    created: dict[str, Device] = {}
    for planned in plan.devices:
        device = Device(
            hostname=planned.hostname,
            vendor="Cisco",
            model="",
            device_type=planned.device_type,
            status=DeviceStatus.STANDBY,
            site_id=site.id,
            notes=f"{planned.role} / {planned.purpose}".strip(" /") + f"\n{TEST_NOTE}",
        )
        db.add(device)
        created[planned.hostname] = device
    db.flush()

    for link in plan.links:
        left = created.get(link.a_hostname)
        right = created.get(link.b_hostname)
        if left is None or right is None:
            continue
        db.add(
            Link(
                a_device_id=left.id,
                a_interface=link.a_interface,
                b_device_id=right.id,
                b_interface=link.b_interface,
                origin=LinkOrigin.MANUAL,
                label=link.description or None,
                confirmed_both_ways=True,
            )
        )

    # 起こした Config も一緒に残す。図から機器を開けば中身が見えるほうが、
    # 案を確かめやすい
    for planned in plan.devices:
        device = created.get(planned.hostname)
        body = plan.configs.get(planned.hostname)
        if device is None or not body:
            continue
        db.add(
            ConfigSnapshot(
                device_id=device.id,
                hostname=device.hostname,
                filename=f"{device.hostname}-案.txt",
                content=body,
                sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                source=ConfigSource.UPLOAD,
            )
        )

    db.commit()
    return site


def remove_test_site(db: Session, site_id: int) -> int:
    """テスト拠点を、機器ごと消す。

    消すのは印の付いた拠点だけにする。本物の拠点をこの口から消せると、
    取り違えたときに取り返しがつかない。
    """
    site = db.get(Site, site_id)
    if site is None or not site.name.startswith(TEST_PREFIX):
        raise ValueError("テスト用に作った拠点ではありません")

    devices = list(db.scalars(select(Device).where(Device.site_id == site.id)))
    ids = [device.id for device in devices]
    if ids:
        # 結線は自分で消す。DB 任せの連鎖削除は、外部キーの設定が入っていない
        # 環境だと黙って残る（残った線は、消えた機器を指したまま図に出る）
        for link in db.scalars(
            select(Link).where(or_(Link.a_device_id.in_(ids), Link.b_device_id.in_(ids)))
        ):
            db.delete(link)
    for device in devices:
        db.delete(device)
    db.delete(site)
    db.commit()
    return len(devices)


def test_sites(db: Session) -> list[Site]:
    return list(
        db.scalars(select(Site).where(Site.name.startswith(TEST_PREFIX)).order_by(Site.id.desc()))
    )
