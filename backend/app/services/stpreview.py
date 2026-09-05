"""ループを止める木（STP）の根が、どこにあるかを見る。

1台の Config だけでは何も言えない。優先度は相手と比べてはじめて意味を持つ
から、根がどこに立つかは機器をまたいで見るしかない。ここはそれだけを行う。

現場でよく効くのは、次の3つ。

- 誰も優先度を書いていない。根は MAC アドレスの若い機器に立つ。つまり、
  たいてい古い機器か、末端のスイッチが根になる。動いてはいるが、
  1台足すたびに木の形が変わる
- 根が VLAN ごとにばらばら。番号によって上流が変わるので、
  「この VLAN だけ遅い」という切り分けの難しい形になる
- 予備の根（secondary）が決まっていない。根が落ちたとき、次にどこへ
  移るかが運任せになる

優先度の書き方はベンダーで違う。Cisco / Arista / アライドテレシスは
4096 の倍数、Aruba CX は 0〜15 の段（4096 を掛けた値になる）。
比べられるように、読んだ時点で実際の値へそろえる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device
from app.services.addition import latest_config
from app.services.reviewnote import HIGH, LOW, MEDIUM, Finding

#: 書かなかったときの優先度。ここが並ぶと、根は MAC の若い順で決まる
DEFAULT_PRIORITY = 32768

_MODE = re.compile(r"^spanning-tree\s+mode\s+(\S+)", re.IGNORECASE)
#: `spanning-tree vlan 10,20 priority 4096`（Cisco / Arista / アライドテレシス）
_VLAN_PRIORITY = re.compile(
    r"^spanning-tree\s+vlan\s+([\d,\-\s]+?)\s+priority\s+(\d+)", re.IGNORECASE
)
#: `spanning-tree vlan 10 root primary`（実際の優先度は機器が決める）
_VLAN_ROOT = re.compile(
    r"^spanning-tree\s+vlan\s+([\d,\-\s]+?)\s+root\s+(primary|secondary)", re.IGNORECASE
)
#: 機器ぜんぶに効く書き方。Aruba CX は段（0〜15）で書く
_PRIORITY = re.compile(r"^spanning-tree\s+priority\s+(\d+)", re.IGNORECASE)
#: `spanning-tree instance 1 priority 4`（AOS-CX の MSTP）
_INSTANCE_PRIORITY = re.compile(
    r"^spanning-tree\s+instance\s+(\d+)\s+priority\s+(\d+)", re.IGNORECASE
)

#: root primary / secondary が実際に置く値。機器が決めるので、比べる用の目安
ROOT_PRIMARY = 24576
ROOT_SECONDARY = 28672


@dataclass
class DeviceStp:
    """1台分の、木の根まわりの設定。"""

    device_id: int
    hostname: str
    device_type: str
    mode: str | None = None
    #: VLAN 番号 → 優先度。すべての VLAN に効く書き方は None を鍵にする
    priorities: dict[int | None, int] = field(default_factory=dict)
    #: root primary / secondary と書いた VLAN
    primary_vlans: set[int] = field(default_factory=set)
    secondary_vlans: set[int] = field(default_factory=set)
    #: 段で書く機器か（Aruba CX）。読み取りの根拠を残すために持つ
    stepped: bool = False

    def priority_for(self, vlan: int | None) -> int:
        if vlan is not None and vlan in self.priorities:
            return self.priorities[vlan]
        return self.priorities.get(None, DEFAULT_PRIORITY)


@dataclass
class StpReport:
    devices_seen: int = 0
    roots: dict[int | None, str] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def review(db: Session) -> StpReport:
    report = StpReport()
    devices = list(db.scalars(select(Device)))
    states = [state for state in (_read(db, device) for device in devices) if state is not None]
    if not states:
        report.notes.append(
            "STP の設定を読み取れる Config がありません。取り込むと、"
            "木の根がどこに立つかを機器をまたいで見られます。"
        )
        return report

    report.devices_seen = len(states)
    report.findings.extend(_nobody_decides(states))
    report.findings.extend(_root_per_vlan(states, report))
    report.findings.extend(_no_backup(states))
    report.findings.extend(_root_on_the_edge(states))
    report.findings.extend(_modes_differ(states))

    # 見ていない機器があるのに指摘が0件だと、全部そろっていると読まれる。
    # STP は輪が1つできるだけで拠点ごと止まるので、範囲を言わずに黙らない
    switches = [d for d in devices if str(d.device_type) == "switch"]
    if switches and len(states) < len(switches):
        report.notes.append(
            f"見たのは、台帳のスイッチ {len(switches)} 台のうち {len(states)} 台です。"
            "残りは STP の設定を読み取れる Config がないため見ていません。"
        )
    return report


def _read(db: Session, device: Device) -> DeviceStp | None:
    snapshot = latest_config(db, device)
    if snapshot is None:
        return None
    text = snapshot.content
    if not re.search(r"^\s*spanning-tree\b", text, re.IGNORECASE | re.MULTILINE):
        return None

    state = DeviceStp(
        device_id=device.id,
        hostname=device.hostname,
        device_type=str(device.device_type),
    )
    stepped = _is_stepped(text)
    state.stepped = stepped

    for raw in text.splitlines():
        body = raw.strip()

        mode = _MODE.match(body)
        if mode:
            state.mode = mode.group(1).lower()
            continue

        found = _VLAN_PRIORITY.match(body)
        if found:
            value = _value(found.group(2), stepped)
            for vlan in _vlans(found.group(1)):
                state.priorities[vlan] = value
            continue

        found = _VLAN_ROOT.match(body)
        if found:
            where = found.group(2).lower()
            for vlan in _vlans(found.group(1)):
                if where == "primary":
                    state.primary_vlans.add(vlan)
                    state.priorities.setdefault(vlan, ROOT_PRIMARY)
                else:
                    state.secondary_vlans.add(vlan)
                    state.priorities.setdefault(vlan, ROOT_SECONDARY)
            continue

        found = _INSTANCE_PRIORITY.match(body)
        if found:
            # インスタンスは VLAN そのものではないが、根の位置の話としては同じ。
            # 番号をそのまま鍵にすると VLAN と混ざるので、全体扱いにする
            state.priorities.setdefault(None, _value(found.group(2), stepped))
            continue

        found = _PRIORITY.match(body)
        if found:
            state.priorities[None] = _value(found.group(1), stepped)

    return state


def _is_stepped(text: str) -> bool:
    """優先度を段（0〜15）で書く機器か。Aruba CX がこの書き方をする。"""
    return bool(re.search(r"^!\s*Version\s+ArubaOS-CX", text, re.IGNORECASE | re.MULTILINE))


def _value(written: str, stepped: bool) -> int:
    number = int(written)
    return number * 4096 if stepped else number


def _vlans(written: str) -> list[int]:
    values: list[int] = []
    for part in written.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            if start.isdigit() and end.isdigit():
                values.extend(range(int(start), int(end) + 1))
        elif part.isdigit():
            values.append(int(part))
    return values


def _note(level: str, title: str, why: str, evidence: list[str]) -> Finding:
    return Finding(level=level, title=title, why=why, evidence=evidence)


def _nobody_decides(states: list[DeviceStp]) -> list[Finding]:
    if any(state.priorities or state.primary_vlans for state in states):
        return []
    return [
        _note(
            MEDIUM,
            "木の根を誰も決めていません",
            "優先度がどの機器にも書かれていないので、根は MAC アドレスの"
            "若い機器に立ちます。たいていは古い機器か末端のスイッチです。"
            "上流の機器に priority を入れて、根の場所を決めてください。",
            [f"{state.hostname}: priority の行なし" for state in states[:5]],
        )
    ]


def _root_per_vlan(states: list[DeviceStp], report: StpReport) -> list[Finding]:
    """VLAN ごとに、いちばん優先度の低い（=強い）機器を根として並べる。"""
    vlans = sorted({vlan for state in states for vlan in state.priorities if vlan is not None})
    keys: list[int | None] = list(vlans) if vlans else [None]

    roots: dict[int | None, str] = {}
    for vlan in keys:
        ranked = sorted(states, key=lambda state: (state.priority_for(vlan), state.hostname))
        best = ranked[0]
        tied = [state for state in ranked if state.priority_for(vlan) == best.priority_for(vlan)]
        roots[vlan] = best.hostname if len(tied) == 1 else "決まりません"
    report.roots = roots

    named = {host for host in roots.values() if host != "決まりません"}
    if len(named) > 1:
        return [
            _note(
                MEDIUM,
                "VLAN によって木の根が違います",
                "番号ごとに上流が変わるので、同じ線を通っていても VLAN に"
                "よって経路が変わります。「この VLAN だけ遅い」という、"
                "切り分けのいちばん難しい形になります。",
                [f"VLAN {vlan}: {host}" for vlan, host in sorted(roots.items(), key=_order)][:5],
            )
        ]
    return []


def _order(item: tuple[int | None, str]) -> tuple[int, int]:
    vlan, _ = item
    return (0, vlan) if vlan is not None else (1, 0)


def _no_backup(states: list[DeviceStp]) -> list[Finding]:
    """根はあるが、次に立つ機器が決まっていない。"""
    has_root = [state for state in states if state.primary_vlans or state.priorities]
    if not has_root:
        return []
    if any(state.secondary_vlans for state in states):
        return []
    # 優先度を書いた機器が2台以上あれば、落ちたときの次は決まっている
    wrote = [state for state in states if state.priorities]
    if len(wrote) > 1:
        return []
    return [
        _note(
            LOW,
            "根が落ちたときに、次にどこへ移るかが決まっていません",
            "予備の根（secondary）を決めていないので、いまの根が落ちると"
            "残りの機器の MAC アドレスで決まります。切り替わったあとの"
            "経路が読めません。2台目にも優先度を入れてください。",
            [f"{state.hostname}: priority あり" for state in wrote[:3]],
        )
    ]


def _root_on_the_edge(states: list[DeviceStp]) -> list[Finding]:
    """末端のスイッチが根になっている。台帳の種別が分かるときだけ言う。"""
    findings: list[Finding] = []
    for state in states:
        if not (state.primary_vlans or state.priorities):
            continue
        strongest = min(state.priorities.values(), default=DEFAULT_PRIORITY)
        if strongest >= DEFAULT_PRIORITY:
            continue
        if state.device_type in ("wireless_ap", "server", "other"):
            findings.append(
                _note(
                    HIGH,
                    f"{state.hostname} が木の根になっています",
                    "末端の機器を根にすると、機器どうしの通信がそこへ集まります。"
                    "上流の機器より強い優先度が入っていないか確かめてください。",
                    [f"{state.hostname}: priority {strongest}（種別 {state.device_type}）"],
                )
            )
    return findings


def _modes_differ(states: list[DeviceStp]) -> list[Finding]:
    modes = {state.mode for state in states if state.mode}
    if len(modes) <= 1:
        return []
    return [
        _note(
            MEDIUM,
            "STP のモードがそろっていません",
            "モードが違うと、VLAN ごとの木の作られ方も収束の速さもそろいません。"
            "境目の機器で思ったとおりに止まらないことがあります。",
            [f"{state.hostname}: {state.mode}" for state in states if state.mode][:5],
        )
    ]
