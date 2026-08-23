"""数えれば分かることを、数えて出す。

ネットワークの設計でよく聞かれることの半分は、実のところ計算です。
このセグメントはあと何台入るのか。空いている VLAN 番号はどれか。
このスイッチのポートはあと何本残っているのか。束ねた線は片方落ちても足りるのか。

こういうものを人に考えさせたり、AI に推測させたりする理由はありません。
答えが一つに決まるからです。決まらないもの（本当に空きポートか、など）は
ここでは断定せず、何を根拠にそう数えたかを添えて返します。

数え方は台帳と、Config から落とした観測値（interfaces / device_vlans）だけを見ます。
実機には触れません。
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device
from app.models.topology import Interface, Link
from app.models.vlan import DeviceVlan, Vlan
from app.services.configparse import normalize
from app.services.configparse.routing import to_prefix

#: 使用率がこれを超えたら、増設の前に採番を見直す話になる
CROWDED = 80.0

#: 予約されていて使えない VLAN 番号。0 と 4095 は番号として使えず、
#: 1002-1005 は古い規格の名残で機器が勝手に持っている
RESERVED_VLANS = frozenset({0, 1002, 1003, 1004, 1005, 4095})

#: 物理ポートらしい名前。Vlan10 や Loopback0 は本数に数えない
_PHYSICAL = re.compile(
    r"^(?:GigabitEthernet|TenGigabitEthernet|TwentyFiveGigE|FortyGigabitEthernet|"
    r"HundredGigE|FastEthernet|Ethernet|XGigabitEthernet|GE|XGE|ge-|xe-|et-|port)",
    re.IGNORECASE,
)

_SPEED = re.compile(r"^(\d+(?:\.\d+)?)\s*([gmkt]?)", re.IGNORECASE)
_UNIT = {"": 1, "k": 0.001, "m": 1, "g": 1000, "t": 1000000}


# ---------------------------------------------------------------------------
# 計算そのもの（DB を見ない。ここが正しければ、あとは数を渡すだけ）


def usable_hosts(subnet: str) -> int | None:
    """そのセグメントに置ける機器の数。

    ネットワークアドレスとブロードキャストアドレスは機器に付けられないので、
    /24 なら 256 ではなく 254 です。/31 は 2 つとも使う決まり（対向2台むけ）、
    /32 は1つだけを指すので 1 として数えます。
    """
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        return None
    if network.version != 4:
        return None
    if network.prefixlen >= 32:
        return 1
    if network.prefixlen == 31:
        return 2
    return network.num_addresses - 2


def usage_ratio(used: int, capacity: int) -> float:
    """使用率（％）。小数第1位まで。分母が 0 なら 0 とする。"""
    if capacity <= 0:
        return 0.0
    return round(used * 100 / capacity, 1)


def free_vlan_ids(used: set[int], *, want: int = 10, start: int = 2) -> list[int]:
    """空いている VLAN 番号を、若い順に。

    予約番号は避けます。1 は初期値として全ポートに付いているので、
    新しく採るときの候補にはしません。
    """
    out: list[int] = []
    number = max(start, 2)
    while number < 4095 and len(out) < want:
        if number not in used and number not in RESERVED_VLANS:
            out.append(number)
        number += 1
    return out


def speed_mbps(speed: str | None) -> int | None:
    """`1000` `10G` `auto` のような書き方を Mbps に直す。

    `auto` は相手と決めるので、数としては扱えません（None を返す）。
    """
    if not speed:
        return None
    text = speed.strip().lower()
    if text in {"auto", "nonegotiate", "negotiate"}:
        return None
    matched = _SPEED.match(text)
    if not matched:
        return None
    unit = _UNIT.get(matched.group(2).lower())
    if unit is None:
        return None
    value = float(matched.group(1)) * unit
    return int(value) if value >= 1 else None


def address_and_network(address: str | None) -> tuple[str, str] | None:
    """`10.0.0.1/24` や `10.0.0.1 255.255.255.0` を、機器のアドレスと
    そのセグメントに分ける。

    セグメントだけに寄せてしまうと、同じアドレスが二か所に付いていることに
    気づけません。ここでは両方を残します。
    """
    if not address:
        return None
    network = to_prefix(address)
    if network is None:
        return None
    text = address.strip().replace(" ", "/", 1) if " " in address.strip() else address.strip()
    try:
        host = ipaddress.ip_interface(text if "/" in text else f"{text}/32").ip
    except ValueError:
        return None
    return str(host), network


def is_physical(name: str) -> bool:
    """物理ポートらしい名前か。Vlan / Loopback / Port-channel は外す。"""
    return bool(_PHYSICAL.match(name.strip()))


# ---------------------------------------------------------------------------
# 台帳から数える


@dataclass
class SubnetUsage:
    subnet: str
    capacity: int
    used: int
    free: int
    ratio: float
    crowded: bool
    vlan_id: int | None = None
    vlan_name: str | None = None
    devices: list[str] = field(default_factory=list)
    basis: str = ""


@dataclass
class VlanNumbers:
    used: list[int]
    free_next: list[int]
    used_count: int
    basis: str = ""


@dataclass
class PortUsage:
    device_id: int
    hostname: str
    total: int
    in_use: int
    free: int
    ratio: float
    basis: str = ""


@dataclass
class LagUsage:
    device_id: int
    hostname: str
    group: int
    members: int
    total_mbps: int | None
    remaining_mbps: int | None
    note: str = ""


@dataclass
class DuplicateAddress:
    address: str
    places: list[str]


@dataclass
class CalcReport:
    subnets: list[SubnetUsage] = field(default_factory=list)
    vlans: VlanNumbers | None = None
    ports: list[PortUsage] = field(default_factory=list)
    lags: list[LagUsage] = field(default_factory=list)
    duplicates: list[DuplicateAddress] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def report(db: Session, *, limit: int = 200) -> CalcReport:
    """数えられるものを一通り数える。"""
    rows = _interface_rows(db)
    out = CalcReport()
    out.subnets = subnet_usage(db, rows)[:limit]
    out.vlans = vlan_numbers(db, rows)
    out.ports = port_usage(db, rows)[:limit]
    out.lags = lag_usage(db, rows)[:limit]
    out.duplicates = duplicate_addresses(rows)[:limit]

    if not rows:
        out.notes.append("Config をまだ読ませていないので、数えられるものがありません")
    crowded = [s for s in out.subnets if s.crowded]
    if crowded:
        out.notes.append(
            f"使用率が {CROWDED:.0f}% を超えているセグメントが {len(crowded)} 本あります。"
            "ここに機器を足すなら、先に採番を分ける相談が要ります"
        )
    return out


@dataclass
class _Row:
    device_id: int
    hostname: str
    name: str
    address: str | None
    vlan: int | None
    native_vlan: int | None
    description: str | None
    lag_group: int | None
    speed: str | None
    enabled: bool


def _interface_rows(db: Session) -> list[_Row]:
    found = db.execute(
        select(
            Interface.device_id,
            Device.hostname,
            Interface.name,
            Interface.ip_address,
            Interface.vlan,
            Interface.native_vlan,
            Interface.description,
            Interface.lag_group,
            Interface.speed,
            Interface.enabled,
        ).join(Device, Device.id == Interface.device_id)
    ).all()
    return [_Row(*row) for row in found]


def subnet_usage(db: Session, rows: list[_Row] | None = None) -> list[SubnetUsage]:
    """セグメントごとに、あと何台入るか。

    数えているのは「Config でアドレスを振ってあるポート」です。実際に机の上で
    動いている PC の数までは分かりません。分かるのは、機器が持っている枠のうち
    どれだけ埋まっているか。増設の前に見るのはここです。
    """
    rows = _interface_rows(db) if rows is None else rows
    named = {v.subnet: v for v in db.execute(select(Vlan)).scalars() if v.subnet}

    groups: dict[str, set[tuple[str, str]]] = {}
    for row in rows:
        found = address_and_network(row.address)
        if found is None:
            continue
        host, prefix = found
        network = ipaddress.ip_network(prefix, strict=False)
        if network.version != 4 or network.prefixlen >= 32:
            continue
        groups.setdefault(str(network), set()).add((host, row.hostname))

    out: list[SubnetUsage] = []
    for subnet, holders in groups.items():
        capacity = usable_hosts(subnet)
        if capacity is None:
            continue
        used = len({address for address, _ in holders})
        ratio = usage_ratio(used, capacity)
        vlan = _vlan_for(subnet, named)
        out.append(
            SubnetUsage(
                subnet=subnet,
                capacity=capacity,
                used=used,
                free=max(capacity - used, 0),
                ratio=ratio,
                crowded=ratio >= CROWDED,
                vlan_id=vlan.vlan_id if vlan else None,
                vlan_name=vlan.name if vlan else None,
                devices=sorted({hostname for _, hostname in holders}),
                basis=(
                    f"/{subnet.split('/')[1]} なので置ける数は {capacity}。"
                    f"Config でアドレスが振ってあるのが {used} なので、残りは"
                    f" {max(capacity - used, 0)}"
                ),
            )
        )
    out.sort(key=lambda s: (-s.ratio, s.subnet))
    return out


def _vlan_for(subnet: str, named: dict[str, Vlan]) -> Vlan | None:
    for text, vlan in named.items():
        try:
            if ipaddress.ip_network(text, strict=False) == ipaddress.ip_network(
                subnet, strict=False
            ):
                return vlan
        except ValueError:
            continue
    return None


def vlan_numbers(db: Session, rows: list[_Row] | None = None) -> VlanNumbers:
    """使われている VLAN 番号と、次に使える番号。

    台帳に登録したものだけでなく、機器に定義されているもの、ポートに付いて
    いるものも「使用中」として数えます。台帳に無いから空いている、として
    採ってしまうと、現場でぶつかります。
    """
    rows = _interface_rows(db) if rows is None else rows
    used: set[int] = set()
    used |= {v for v in db.execute(select(Vlan.vlan_id)).scalars() if v}
    used |= {v for v in db.execute(select(DeviceVlan.vlan_id)).scalars() if v}
    for row in rows:
        if row.vlan:
            used.add(row.vlan)
        if row.native_vlan:
            used.add(row.native_vlan)
    return VlanNumbers(
        used=sorted(used),
        free_next=free_vlan_ids(used),
        used_count=len(used),
        basis="台帳・機器の定義・ポートに付いている番号を、すべて使用中として数えています",
    )


def port_usage(db: Session, rows: list[_Row] | None = None) -> list[PortUsage]:
    """機器ごとに、物理ポートが何本あって、何本残っているか。

    使っていると数えるのは、線が引いてある・アドレスや VLAN が付いている・
    説明が書いてある、のどれかに当たるポートです。実際に線が挿さっているかは
    行って見ないと分かりませんが、残り本数の当たりはこれで付きます。
    """
    rows = _interface_rows(db) if rows is None else rows
    linked = _linked_ports(db)

    counts: dict[int, list[int]] = {}
    names: dict[int, str] = {}
    for row in rows:
        if not is_physical(row.name):
            continue
        names[row.device_id] = row.hostname
        slot = counts.setdefault(row.device_id, [0, 0])
        slot[0] += 1
        in_use = bool(
            row.address
            or row.vlan
            or row.description
            or (row.device_id, normalize(row.name)) in linked
        )
        if in_use:
            slot[1] += 1

    out = []
    for device_id, (total, in_use) in counts.items():
        free = max(total - in_use, 0)
        out.append(
            PortUsage(
                device_id=device_id,
                hostname=names[device_id],
                total=total,
                in_use=in_use,
                free=free,
                ratio=usage_ratio(in_use, total),
                basis=(
                    f"物理ポート {total} 本のうち、線・アドレス・VLAN・説明の"
                    f"どれかが付いているのが {in_use} 本。残りは {free} 本"
                ),
            )
        )
    out.sort(key=lambda p: (-p.ratio, p.hostname))
    return out


def _linked_ports(db: Session) -> set[tuple[int, str]]:
    out: set[tuple[int, str]] = set()
    for a_id, a_if, b_id, b_if in db.execute(
        select(Link.a_device_id, Link.a_interface, Link.b_device_id, Link.b_interface)
    ).all():
        if a_if:
            out.add((a_id, normalize(a_if)))
        if b_if:
            out.add((b_id, normalize(b_if)))
    return out


def lag_usage(db: Session, rows: list[_Row] | None = None) -> list[LagUsage]:
    """束ねた線が、1本落ちても足りるか。

    2本を束ねて 2Gbps 出ている構成は、1本切れると 1Gbps です。足りるかどうかは
    流れている量しだいなので断定はしませんが、落ちたあと何が残るかは数えられます。
    """
    rows = _interface_rows(db) if rows is None else rows
    groups: dict[tuple[int, int], list[_Row]] = {}
    for row in rows:
        if row.lag_group is None:
            continue
        groups.setdefault((row.device_id, row.lag_group), []).append(row)

    out = []
    for (device_id, group), members in sorted(groups.items()):
        speeds = [speed_mbps(m.speed) for m in members]
        known = [s for s in speeds if s]
        total = sum(known) if len(known) == len(members) else None
        slowest = min(known) if known else None
        remaining = total - slowest if total is not None and slowest is not None else None
        if total is None:
            note = "速度が書いていないポートがあるので、合計は出せません（本数だけ確かです）"
        elif len(members) == 1:
            note = "束ねているのが1本だけです。これでは片方落ちたときに残りません"
        else:
            note = f"1本落ちると {remaining} Mbps になります（{len(members) - 1} 本残る）"
        out.append(
            LagUsage(
                device_id=device_id,
                hostname=members[0].hostname,
                group=group,
                members=len(members),
                total_mbps=total,
                remaining_mbps=remaining,
                note=note,
            )
        )
    return out


def duplicate_addresses(rows: list[_Row]) -> list[DuplicateAddress]:
    """同じアドレスが二か所に付いていないか。

    片方が通信できなくなる形の事故で、しかも設定を1台ずつ見ている限り
    気づけません。突き合わせれば分かるので、ここで出します。
    """
    places: dict[str, list[str]] = {}
    for row in rows:
        found = address_and_network(row.address)
        if found is None:
            continue
        places.setdefault(found[0], []).append(f"{row.hostname} {row.name}")

    out = [
        DuplicateAddress(address=address, places=sorted(set(where)))
        for address, where in places.items()
        if len(set(where)) > 1
    ]
    out.sort(key=lambda d: d.address)
    return out
