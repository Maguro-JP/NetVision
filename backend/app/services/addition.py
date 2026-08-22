"""「ここに機器を1台足したら、どう書くか」を起こす。

現場で一番多い設計は、既にあるどこかにぶら下げる形の増設である。その作業は
毎回同じ手順を踏む。空いているポートを探し、使っていない VLAN 番号と
セグメントを決め、上位と下がりの両方に同じ内容を矛盾なく書く。手で書くと、
片側だけ直っている・番号が既存とぶつかる、といった間違いが混ざる。

ここでやるのは、その手順を上位機器の Config を根拠に機械的に踏むことである。

### 方針

- 決めた値には必ず理由と根拠を付ける（`Decision`）。なぜその VLAN 番号なのか、
  なぜこの経路なのかが言えないものは、人が確認しようが無い
- 既存の流儀に合わせる。OSPF で回している構成に静的経路を足したりしない
- 決められないものは黙って埋めない。`warnings` に出して人に返す
- 生成する Config は「1. 最低限つながる分」「2. 経路」「3. セキュリティ」の
  順に並べる。急ぐときは 1 だけ入れれば繋がる、と読めるようにするため
- 実機には投入しない。ここで作るのは下書きだけ
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, DeviceType, Vlan
from app.models.topology import ConfigSnapshot
from app.services import configparse
from app.services.configparse import ParsedConfig, Vendor
from app.services.configparse.routing import to_prefix
from app.services.design import TODO, vendor_of

#: 迷ったときの VLAN 番号の刻み。現場の台帳はたいてい10刻みで採番されている
_VLAN_STEP = 10
#: 提案する VLAN 番号の上限。予約帯（1002-1005）と拡張帯は避ける
_VLAN_MAX = 999


@dataclass
class Decision:
    """1つの決めごとと、その理由。

    「なぜそうなのか」が無い提案は、人が確かめられないので使えない。
    根拠には、上位機器の Config の該当行をそのまま入れる。
    """

    topic: str
    choice: str
    why: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class AdditionRequest:
    upstream_device_id: int
    hostname: str
    device_type: DeviceType = DeviceType.SWITCH
    vendor: str = ""
    model: str = ""
    #: 用途。VLAN の名前と description に使う（「3階事務所」など）
    purpose: str = ""
    vlan_id: int | None = None
    subnet: str | None = None
    uplink_interface: str | None = None


@dataclass
class AdditionPlan:
    upstream_hostname: str
    hostname: str
    vlan_id: int | None = None
    vlan_name: str = ""
    subnet: str | None = None
    gateway_ip: str | None = None
    device_ip: str | None = None
    upstream_interface: str | None = None
    device_uplink: str = ""
    routing: str = "none"  # ospf / static / none
    decisions: list[Decision] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    upstream_config: str = ""
    device_config: str = ""
    #: Config そのものには書かない補足。画面で Config の隣に出す
    upstream_notes: list[str] = field(default_factory=list)
    device_notes: list[str] = field(default_factory=list)

    def add(self, topic: str, choice: str, why: str, evidence: list[str] | None = None) -> None:
        self.decisions.append(
            Decision(topic=topic, choice=choice, why=why, evidence=evidence or [])
        )


# ---------------------------------------------------------------------------
# 上位機器を読む
# ---------------------------------------------------------------------------


def latest_config(db: Session, device: Device) -> ConfigSnapshot | None:
    return db.scalars(
        select(ConfigSnapshot)
        .where(ConfigSnapshot.device_id == device.id)
        .order_by(ConfigSnapshot.id.desc())
        .limit(1)
    ).first()


def read_upstream(db: Session, device: Device) -> tuple[ParsedConfig | None, str]:
    """上位機器の最新 Config を読む。無ければ何も推測しない。"""
    snapshot = latest_config(db, device)
    if snapshot is None:
        return None, ""
    return configparse.parse(snapshot.content), snapshot.content


# ---------------------------------------------------------------------------
# 番号とアドレスを決める
# ---------------------------------------------------------------------------


def pick_vlan_id(used: set[int]) -> int:
    """使われていない VLAN 番号を1つ選ぶ。

    既存が10刻みなら10刻みで続ける。現場の台帳は「10=事務所 20=無線」の
    ように意味で採番されていることが多く、そこに 11 を割り込ませると
    後から読む人が困る。
    """
    numbered = sorted(v for v in used if 1 < v <= _VLAN_MAX)
    step = _VLAN_STEP
    if len(numbered) >= 2:
        gaps = {b - a for a, b in zip(numbered, numbered[1:], strict=False) if b > a}
        if gaps and min(gaps) < _VLAN_STEP:
            step = min(gaps)
    candidate = (max(numbered) + step) if numbered else _VLAN_STEP
    while candidate in used and candidate <= _VLAN_MAX:
        candidate += step
    if candidate > _VLAN_MAX:  # 埋まっているときは空き番を拾う
        candidate = next((n for n in range(2, _VLAN_MAX) if n not in used), _VLAN_MAX)
    return candidate


def pick_subnet(used: list[str], vlan_id: int) -> str | None:
    """既存の採番から、次に使えそうなセグメントを選ぶ。

    既存が 10.10.10.0/24 10.10.20.0/24 のように第3オクテットで分けているなら、
    その並びに続ける。分からなければ何も返さない（当てずっぽうで割り当てると、
    他拠点とぶつかる）。
    """
    networks = []
    for text in used:
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            continue
    private = [n for n in networks if n.version == 4 and n.is_private and n.prefixlen >= 16]
    if not private:
        return None

    length = max(n.prefixlen for n in private)
    base = min(private, key=lambda n: int(n.network_address))
    # VLAN 番号をそのまま第3オクテットに使う流儀が広いので、まずそれを試す
    octets = str(base.network_address).split(".")
    if length == 24 and vlan_id <= 255:
        candidate = ipaddress.ip_network(f"{octets[0]}.{octets[1]}.{vlan_id}.0/24")
        if all(not candidate.overlaps(n) for n in private):
            return str(candidate)

    step = 1 << (32 - length)
    address = int(max(private, key=lambda n: int(n.network_address)).network_address) + step
    for _ in range(256):
        candidate = ipaddress.ip_network((address, length))
        if all(not candidate.overlaps(n) for n in private):
            return str(candidate)
        address += step
    return None


def addresses_in(subnet: str) -> tuple[str, str]:
    """ゲートウェイと、足す機器の管理アドレス。

    ゲートウェイを先頭、機器を2番目に置く。現場の慣習として最も多く、
    後から人が見たときに迷わない。
    """
    network = ipaddress.ip_network(subnet, strict=False)
    hosts = network.hosts()
    gateway = str(next(hosts))
    device = str(next(hosts))
    return gateway, device


def netmask_of(subnet: str) -> str:
    return str(ipaddress.ip_network(subnet, strict=False).netmask)


def wildcard_of(subnet: str) -> str:
    return str(ipaddress.ip_network(subnet, strict=False).hostmask)


# ---------------------------------------------------------------------------
# 空きポート
# ---------------------------------------------------------------------------

_PHYSICAL = re.compile(
    r"^(Gigabit|TenGigabit|FastEthernet|Ethernet|GigabitEthernet|Te|Gi|Fa)", re.I
)


def free_ports(parsed: ParsedConfig) -> list[str]:
    """空いていそうな物理ポート。

    description が無く、結線も見えていないポートを候補にする。「空いている」と
    言い切れるのは実機を見た人だけなので、あくまで候補として返す。
    """
    connected = {configparse.normalize(n.local_interface) for n in parsed.neighbors}
    out = []
    for interface in parsed.interfaces:
        name = interface.name
        if not _PHYSICAL.match(name):
            continue
        if configparse.normalize(name) in connected:
            continue
        if interface.description or interface.ip_address:
            continue
        out.append(name)
    return out


# ---------------------------------------------------------------------------
# 設計を組み立てる
# ---------------------------------------------------------------------------


def build(db: Session, request: AdditionRequest) -> AdditionPlan:
    """上位機器を根拠に、足す1台ぶんの設計を起こす。"""
    upstream = db.get(Device, request.upstream_device_id)
    if upstream is None:
        raise ValueError("つなぐ先の機器が見つかりません")

    parsed, raw = read_upstream(db, upstream)
    plan = AdditionPlan(upstream_hostname=upstream.hostname, hostname=request.hostname)

    if parsed is None:
        plan.warnings.append(
            f"{upstream.hostname} の Config がまだ取り込まれていません。"
            "いまの構成が読めないので、VLAN 番号もセグメントも既存とぶつからない保証ができません。"
            "先に running-config を取り込むと、既存に合わせた形で起こせます。"
        )
        parsed = ParsedConfig()

    _decide_vlan(db, plan, parsed, request)
    _decide_subnet(plan, parsed, request)
    _decide_port(plan, parsed, request, upstream)
    _decide_routing(plan, parsed)
    _write_configs(plan, parsed, request, upstream)
    _add_checks(plan)
    return plan


def _decide_vlan(
    db: Session, plan: AdditionPlan, parsed: ParsedConfig, request: AdditionRequest
) -> None:
    used = set(parsed.vlans)
    used |= {v.vlan_id for v in db.scalars(select(Vlan))}
    for interface in parsed.interfaces:
        used |= interface.carried_vlans()

    if request.vlan_id is not None:
        plan.vlan_id = request.vlan_id
        if request.vlan_id in used:
            plan.warnings.append(
                f"VLAN {request.vlan_id} は既に使われています。"
                "同じ番号を別の用途に使うと、トランクの両端で意味が食い違います。"
            )
        plan.add("VLAN 番号", str(request.vlan_id), "指定された番号をそのまま使いました。")
    else:
        plan.vlan_id = pick_vlan_id(used)
        existing = ", ".join(str(v) for v in sorted(used)[:8]) or "（既存なし）"
        plan.add(
            "VLAN 番号",
            str(plan.vlan_id),
            "既存で使われている番号を避け、同じ刻みで続く番号にしました。"
            "刻みを変えると、番号から用途が読めなくなります。",
            [f"上位で使用中の VLAN: {existing}"],
        )

    plan.vlan_name = _vlan_name(request.purpose, plan.vlan_id)


def _vlan_name(purpose: str, vlan_id: int | None) -> str:
    """VLAN 名は ASCII に寄せる。機器によっては日本語が化けるため。"""
    text = (purpose or "").strip()
    ascii_only = re.sub(r"[^A-Za-z0-9_-]", "", text)
    if ascii_only:
        return ascii_only[:32]
    return f"VLAN{vlan_id}" if vlan_id else "VLAN"


def _decide_subnet(plan: AdditionPlan, parsed: ParsedConfig, request: AdditionRequest) -> None:
    existing = []
    for interface in parsed.interfaces:
        if interface.ip_address:
            prefix = to_prefix(interface.ip_address)
            if prefix:
                existing.append(prefix)

    if request.subnet:
        try:
            plan.subnet = str(ipaddress.ip_network(request.subnet, strict=False))
        except ValueError:
            plan.warnings.append(f"セグメント {request.subnet} を読めませんでした。")
            return
        plan.add("セグメント", plan.subnet, "指定されたセグメントをそのまま使いました。")
    else:
        plan.subnet = pick_subnet(existing, plan.vlan_id or 0)
        if plan.subnet is None:
            plan.warnings.append(
                "上位機器に IP が振られたインターフェースが見つからないため、"
                "セグメントを決められませんでした。当てずっぽうで割り当てると"
                "他拠点とぶつかるので、使ってよい範囲を指定してください。"
            )
            return
        plan.add(
            "セグメント",
            plan.subnet,
            "既存の採番の並びに続けました。VLAN 番号を第3オクテットに使う流儀に"
            "合わせているので、番号を見ればどのセグメントか分かります。",
            [f"上位の既存セグメント: {', '.join(existing[:4]) or '（なし）'}"],
        )

    if plan.subnet:
        plan.gateway_ip, plan.device_ip = addresses_in(plan.subnet)
        plan.add(
            "アドレス",
            f"ゲートウェイ {plan.gateway_ip} / {plan.hostname} {plan.device_ip}",
            "ゲートウェイを先頭、機器を2番目に置きました。現場でいちばん多い置き方で、"
            "後から見る人が迷いません。",
        )


def _decide_port(
    plan: AdditionPlan,
    parsed: ParsedConfig,
    request: AdditionRequest,
    upstream: Device,
) -> None:
    if request.uplink_interface:
        plan.upstream_interface = request.uplink_interface
        plan.add("上位のポート", request.uplink_interface, "指定されたポートを使いました。")
    else:
        candidates = free_ports(parsed)
        if candidates:
            plan.upstream_interface = candidates[0]
            plan.add(
                "上位のポート",
                candidates[0],
                "description も IP も無く、隣接も見えていないポートを候補にしました。"
                "本当に空いているかは実機で確かめてください。",
                [f"ほかの候補: {', '.join(candidates[1:4]) or 'なし'}"],
            )
        else:
            plan.warnings.append(
                f"{upstream.hostname} に空きポートの候補が見つかりませんでした。"
                "使うポートを指定してください。"
            )

    plan.device_uplink = _first_uplink(request)
    plan.add(
        f"{plan.hostname} 側のポート",
        plan.device_uplink,
        "足す機器の1番ポートを上りに使いました。設計上の決めごとなので、"
        "現場の流儀があればそちらに合わせてください。",
    )


def _first_uplink(request: AdditionRequest) -> str:
    vendor = (request.vendor or "").lower()
    if "juniper" in vendor:
        return "ge-0/0/0"
    if "nec" in vendor:
        return "GigaEthernet0"
    return "GigabitEthernet1/0/1"


def _decide_routing(plan: AdditionPlan, parsed: ParsedConfig) -> None:
    """既存の流儀に合わせる。OSPF で回している構成に静的経路を足したりしない。"""
    if parsed.ospf is not None:
        plan.routing = "ospf"
        area = parsed.ospf.areas[0].area if parsed.ospf.areas else "0"
        plan.add(
            "経路",
            f"OSPF エリア {area} に載せる",
            "上位が OSPF で回しているので、新しいセグメントも同じ流儀で広告します。"
            "ここだけ静的経路にすると、経路の出どころが2つになって追えなくなります。",
            [f"上位の OSPF: プロセス {parsed.ospf.process_id} / エリア {area}"],
        )
    elif parsed.routes:
        plan.routing = "static"
        sample = parsed.routes[0]
        plan.add(
            "経路",
            "上位に足す経路は不要（直結）",
            "上位に新しいセグメントを直接持たせるので、上位から見れば直結です。"
            "直結の経路は自動でできるため、静的経路を書き足す必要はありません。"
            "足す機器側には、上位へ向けたデフォルト経路だけ入れます。",
            [f"上位の静的経路の例: {sample.destination} → {sample.next_hop or sample.interface}"],
        )
    else:
        plan.routing = "none"
        plan.add(
            "経路",
            "足す機器にデフォルト経路のみ",
            "上位で経路制御の設定が読み取れませんでした。足す機器から上位へ向かう"
            "デフォルト経路だけを入れてあります。上位より先へ流す必要がある場合は、"
            "上位側の経路も確認してください。",
        )

    if parsed.redundancy:
        entry = parsed.redundancy[0]
        plan.warnings.append(
            f"上位は {entry.protocol.upper()} で既定ゲートウェイを冗長化しています"
            f"（{entry.interface} グループ {entry.group}）。"
            "同じ構成にするなら、対向の機器にも同じ VLAN と仮想IPの設定が要ります。"
            "片側だけ入れると、切り替わらない状態が出来上がります。"
        )


def _add_checks(plan: AdditionPlan) -> None:
    plan.checks = [
        f"VLAN {plan.vlan_id} と {plan.subnet or '（未定）'} が、"
        "台帳に載っていない機器も含めて本当に空いているか",
        f"上位の {plan.upstream_interface or '（未定）'} が本当に空いているか（実機で確認）",
        "投入後: 上位から足した機器へ ping が通るか",
        "投入後: 両端の LLDP / CDP でお互いが見えるか",
        "投入後: トランクの許可 VLAN が両端でそろっているか",
    ]


# ---------------------------------------------------------------------------
# Config を書く
# ---------------------------------------------------------------------------


def _write_configs(
    plan: AdditionPlan,
    parsed: ParsedConfig,
    request: AdditionRequest,
    upstream: Device,
) -> None:
    upstream_vendor = vendor_of(upstream)
    device_vendor = _vendor_from_name(request.vendor) if request.vendor else upstream_vendor

    plan.upstream_config, plan.upstream_notes = _upstream_config(
        plan, parsed, upstream_vendor, upstream
    )
    plan.device_config, plan.device_notes = _device_config(plan, device_vendor, request)


def _vendor_from_name(name: str) -> Vendor:
    lowered = name.strip().lower()
    if "juniper" in lowered:
        return Vendor.JUNIPER
    if "forti" in lowered:
        return Vendor.FORTINET
    if "nec" in lowered:
        return Vendor.NEC_QX
    if "cisco" in lowered:
        return Vendor.CISCO_IOS
    return Vendor.UNKNOWN


def _upstream_config(
    plan: AdditionPlan, parsed: ParsedConfig, vendor: Vendor, upstream: Device
) -> tuple[str, list[str]]:
    """上位に足す分。返すのはコマンドだけで、説明はコメントに混ぜない。

    生成した Config はそのまま貼って使う。説明や「要記入」の断り書きを
    行に混ぜると、貼る前に消す手間が要るうえ、消し忘れが機器に入る。
    伝えたいことは注記として別に返し、画面で Config の隣に出す。
    """
    if vendor in (Vendor.CISCO_IOS, Vendor.CISCO_NXOS):
        return _upstream_cisco(plan, parsed)
    if vendor is Vendor.NEC_QX:
        return _upstream_comware(plan, parsed)
    return _upstream_outline(plan, vendor, upstream)


def _upstream_cisco(plan: AdditionPlan, parsed: ParsedConfig) -> tuple[str, list[str]]:
    lines: list[str] = []
    notes = [
        f"最初の塊（vlan {plan.vlan_id} と {plan.upstream_interface or 'ポート'}）"
        "までを入れれば繋がります。",
    ]

    lines.append(f"vlan {plan.vlan_id}")
    lines.append(f" name {plan.vlan_name}")
    lines.append("!")
    if plan.upstream_interface:
        lines.append(f"interface {plan.upstream_interface}")
        lines.append(f" description to {plan.hostname}")
        lines.append(" switchport mode trunk")
        lines.append(f" switchport trunk allowed vlan add {plan.vlan_id}")
        lines.append(" no shutdown")
        lines.append("!")
    if plan.subnet and plan.gateway_ip:
        lines.append(f"interface Vlan{plan.vlan_id}")
        lines.append(f" description {plan.vlan_name}")
        lines.append(f" ip address {plan.gateway_ip} {netmask_of(plan.subnet)}")
        lines.append(" no shutdown")
        lines.append("!")

    if plan.routing == "ospf" and plan.subnet and parsed.ospf:
        area = parsed.ospf.areas[0].area if parsed.ospf.areas else "0"
        lines.append(f"router ospf {parsed.ospf.process_id or 1}")
        lines.append(
            f" network {ipaddress.ip_network(plan.subnet).network_address} "
            f"{wildcard_of(plan.subnet)} area {area}"
        )
        if parsed.ospf.passive_default:
            lines.append(f" passive-interface Vlan{plan.vlan_id}")
            notes.append(
                f"Vlan{plan.vlan_id} は passive にしています。端末しか居ないセグメントから"
                "OSPF を喋らせても、余計な隣接ができるだけだからです。"
            )
        lines.append("!")
    else:
        notes.append("上位に足す経路はありません。新しいセグメントを直接持つので直結です。")

    notes.extend(_security_notes())
    return "\n".join(lines) + "\n", notes


def _upstream_comware(plan: AdditionPlan, parsed: ParsedConfig) -> tuple[str, list[str]]:
    lines: list[str] = []
    notes = [
        f"最初の塊（vlan {plan.vlan_id} と {plan.upstream_interface or 'ポート'}）"
        "までを入れれば繋がります。",
    ]

    lines.append(f"vlan {plan.vlan_id}")
    lines.append(f" name {plan.vlan_name}")
    lines.append("#")
    if plan.upstream_interface:
        lines.append(f"interface {plan.upstream_interface}")
        lines.append(f" description to {plan.hostname}")
        lines.append(" port link-type trunk")
        lines.append(f" port trunk permit vlan {plan.vlan_id}")
        lines.append(" undo shutdown")
        lines.append("#")
    if plan.subnet and plan.gateway_ip:
        lines.append(f"interface Vlan-interface{plan.vlan_id}")
        lines.append(f" description {plan.vlan_name}")
        lines.append(f" ip address {plan.gateway_ip} {netmask_of(plan.subnet)}")
        lines.append(" undo shutdown")
        lines.append("#")

    if plan.routing == "ospf" and plan.subnet and parsed.ospf:
        area = parsed.ospf.areas[0].area if parsed.ospf.areas else "0.0.0.0"
        lines.append(f"ospf {parsed.ospf.process_id or 1}")
        lines.append(f" area {area}")
        lines.append(
            f"  network {ipaddress.ip_network(plan.subnet).network_address} "
            f"{wildcard_of(plan.subnet)}"
        )
        lines.append("#")
    else:
        notes.append("上位に足す経路はありません。新しいセグメントを直接持つので直結です。")

    notes.extend(_security_notes())
    return "\n".join(lines) + "\n", notes


def _security_notes() -> list[str]:
    return [
        "使わないポートは shutdown のままにしてください。",
        "管理アクセス（SSH / SNMP / ACL）は既存機器と同じ設定に合わせてください。"
        "この設計からは既存の値を読み取れないので、Config には入れていません。",
    ]


def _upstream_outline(
    plan: AdditionPlan, vendor: Vendor, upstream: Device
) -> tuple[str, list[str]]:
    """書式を持っていないベンダー。決めた値だけを渡す。

    それらしい別ベンダーの構文を出すと、そのまま貼られて事故になる。
    """
    label = configparse.VENDOR_LABELS.get(vendor, "判別できません")
    notes = [
        f"{label}（{upstream.vendor} {upstream.model}）の書式では起こしていません。"
        "それらしい別ベンダーの構文を出すより、決めた内容だけを渡します。",
        f"VLAN {plan.vlan_id}（{plan.vlan_name}） / セグメント {plan.subnet or TODO}",
        f"この機器のアドレス: {plan.gateway_ip or TODO}",
        f"{plan.hostname} へ向けるポート: {plan.upstream_interface or TODO}（トランク）",
    ]
    return "", notes


def _device_config(
    plan: AdditionPlan, vendor: Vendor, request: AdditionRequest
) -> tuple[str, list[str]]:
    if vendor in (Vendor.CISCO_IOS, Vendor.CISCO_NXOS):
        return _device_cisco(plan)
    if vendor is Vendor.NEC_QX:
        return _device_comware(plan)
    return _device_outline(plan, vendor, request)


def _device_cisco(plan: AdditionPlan) -> tuple[str, list[str]]:
    lines = [f"hostname {plan.hostname}", "!"]
    notes = [
        f"hostname から {plan.device_uplink} までを入れれば繋がります。",
    ]

    lines.append(f"vlan {plan.vlan_id}")
    lines.append(f" name {plan.vlan_name}")
    lines.append("!")
    lines.append(f"interface {plan.device_uplink}")
    lines.append(f" description to {plan.upstream_hostname}")
    lines.append(" switchport mode trunk")
    lines.append(f" switchport trunk allowed vlan {plan.vlan_id}")
    lines.append(" no shutdown")
    lines.append("!")
    if plan.subnet and plan.device_ip:
        lines.append(f"interface Vlan{plan.vlan_id}")
        lines.append(f" description {plan.vlan_name}")
        lines.append(f" ip address {plan.device_ip} {netmask_of(plan.subnet)}")
        lines.append(" no shutdown")
        lines.append("!")

    if plan.gateway_ip:
        lines.append(f"ip default-gateway {plan.gateway_ip}")
        lines.append("!")
        notes.append(
            f"この機器を L3 で動かすなら、ip default-gateway ではなく ip routing と "
            f"ip route 0.0.0.0 0.0.0.0 {plan.gateway_ip} にしてください。"
        )

    lines.append("no ip http server")
    lines.append("no ip http secure-server")
    lines.append("service password-encryption")
    lines.append("!")
    lines.append("line vty 0 15")
    lines.append(" transport input ssh")
    lines.append(" exec-timeout 10 0")
    lines.append("!")

    notes.append(
        "ユーザー・パスワード・SSH の鍵・SNMP・NTP・ログ送り先は入れていません。"
        "既存機器と同じ値にしてください。ここだけ違うと運用で漏れます。"
    )
    notes.append("使わないポートは shutdown にしてください。")
    return "\n".join(lines) + "\n", notes


def _device_comware(plan: AdditionPlan) -> tuple[str, list[str]]:
    lines = [f"sysname {plan.hostname}", "#"]
    notes = [f"sysname から {plan.device_uplink} までを入れれば繋がります。"]

    lines.append(f"vlan {plan.vlan_id}")
    lines.append(f" name {plan.vlan_name}")
    lines.append("#")
    lines.append(f"interface {plan.device_uplink}")
    lines.append(f" description to {plan.upstream_hostname}")
    lines.append(" port link-type trunk")
    lines.append(f" port trunk permit vlan {plan.vlan_id}")
    lines.append(" undo shutdown")
    lines.append("#")
    if plan.subnet and plan.device_ip:
        lines.append(f"interface Vlan-interface{plan.vlan_id}")
        lines.append(f" ip address {plan.device_ip} {netmask_of(plan.subnet)}")
        lines.append(" undo shutdown")
        lines.append("#")

    if plan.gateway_ip:
        lines.append(f"ip route-static 0.0.0.0 0.0.0.0 {plan.gateway_ip}")
        lines.append("#")

    lines.append("undo ip http enable")
    lines.append("undo ip https enable")
    lines.append("#")
    lines.append("line vty 0 15")
    lines.append(" protocol inbound ssh")
    lines.append(" idle-timeout 10 0")
    lines.append("#")

    notes.append(
        "ユーザー・パスワード・SSH の鍵・SNMP・NTP・ログ送り先は入れていません。"
        "既存機器と同じ値にしてください。"
    )
    notes.append("使わないポートは shutdown にしてください。")
    return "\n".join(lines) + "\n", notes


def _device_outline(
    plan: AdditionPlan, vendor: Vendor, request: AdditionRequest
) -> tuple[str, list[str]]:
    label = configparse.VENDOR_LABELS.get(vendor, "判別できません")
    notes = [
        f"{label}（{request.vendor} {request.model}）の書式では起こしていません。"
        "それらしい別ベンダーの構文を出すより、決めた内容だけを渡します。",
        f"ホスト名: {plan.hostname}",
        f"VLAN {plan.vlan_id}（{plan.vlan_name}） / アドレス {plan.device_ip or TODO}"
        f"（{plan.subnet or TODO}）",
        f"既定ゲートウェイ: {plan.gateway_ip or TODO}",
        f"上位へ向けるポート: {plan.device_uplink}（トランク）",
    ]
    return "", notes
