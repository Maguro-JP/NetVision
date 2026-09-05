"""線の両端で、設定がそろっているかを見る。

1台ずつの Config を見ても分からない壊れ方がある。片側だけトランク、片側だけ
大きい MTU、片側だけ速度を固定。どれも「片方の機器の中では正しい」ので、
1台ずつ見ている限り見つからない。

この手の食い違いは、通信が止まらないのが厄介なところである。だいたい動く。
大きい荷物のときだけ落ちる、混んだときだけ遅い、片方の VLAN だけ通らない。
止まってくれれば気づくが、止まらないので何か月も残る。

線が引けていることが前提になる。図が出ていない現場では、まず結線表を
取り込むか、経路から推定する（l3links）。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device
from app.models.topology import Interface, Link
from app.services.configparse import expand, normalize, vlanlist
from app.services.l3review import HIGH, LOW, MEDIUM, Finding

#: トランクの許可 VLAN は、この数までなら食い違いを並べて出す。
#: 何十個も並べると、どれが問題なのか読めなくなる
_SHOW = 6


@dataclass
class LinkFinding:
    """1本の線についての指摘。どの線かが分からないと直しに行けない。"""

    link_id: int
    a_hostname: str
    a_interface: str
    b_hostname: str
    b_interface: str
    finding: Finding


@dataclass
class LinkReport:
    links_seen: int = 0
    links_with_config: int = 0
    findings: list[LinkFinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def review(db: Session, *, limit: int = 2000) -> LinkReport:
    """引けている線を、両端の設定で突き合わせる。"""
    report = LinkReport()
    links = list(db.scalars(select(Link).order_by(Link.id).limit(limit)))
    report.links_seen = len(links)
    if not links:
        report.notes.append(
            "線が1本も引かれていません。結線表を取り込むか、経路から推定すると、"
            "両端の突き合わせができるようになります。"
        )
        return report

    hostnames = dict(db.execute(select(Device.id, Device.hostname)).all())
    ports = _ports_by_device(
        db, {link.a_device_id for link in links} | {link.b_device_id for link in links}
    )

    for link in links:
        a = _find_port(ports, link.a_device_id, link.a_interface)
        b = _find_port(ports, link.b_device_id, link.b_interface)
        if a is None or b is None:
            continue
        report.links_with_config += 1

        for finding in _compare(a, b):
            report.findings.append(
                LinkFinding(
                    link_id=link.id,
                    a_hostname=hostnames.get(link.a_device_id, "?"),
                    a_interface=link.a_interface or a.name,
                    b_hostname=hostnames.get(link.b_device_id, "?"),
                    b_interface=link.b_interface or b.name,
                    finding=finding,
                )
            )

    if report.links_with_config == 0:
        report.notes.append(
            "線はありますが、両端のポートの設定が読めていません。"
            "Config を取り込むと、両端の突き合わせができるようになります。"
        )
    elif report.links_with_config < report.links_seen:
        # 見ていない線があるのに指摘が0件だと、全部そろっていると読まれる。
        # 「食い違いが無い」と「まだ見ていない」は違う
        unseen = report.links_seen - report.links_with_config
        report.notes.append(
            f"突き合わせたのは {report.links_seen} 本のうち {report.links_with_config} 本です。"
            f"残り {unseen} 本は、両端のどちらかの Config が無いため見ていません。"
        )

    weight = {HIGH: 0, MEDIUM: 1, LOW: 2}
    report.findings.sort(key=lambda item: weight.get(item.finding.level, 3))
    return report


def _ports_by_device(db: Session, device_ids: set[int]) -> dict[int, dict[str, Interface]]:
    """機器ごとのポート表。線の本数だけ問い合わせると、16万台で終わらない。"""
    if not device_ids:
        return {}
    out: dict[int, dict[str, Interface]] = {}
    for interface in db.scalars(select(Interface).where(Interface.device_id.in_(device_ids))):
        out.setdefault(interface.device_id, {})[interface.normalized_name] = interface
    return out


def _find_port(
    ports: dict[int, dict[str, Interface]], device_id: int, name: str | None
) -> Interface | None:
    table = ports.get(device_id)
    if not table:
        return None
    if not name:
        return None
    # 結線表から引いた線は `Gi1/0/1` のように略して書かれていることがある。
    # ポート表は正式名で持っているので、略称を伸ばしてから引く
    for candidate in (normalize(expand(name)), normalize(name)):
        found = table.get(candidate)
        if found is not None:
            return found
    return None


def _compare(a: Interface, b: Interface) -> list[Finding]:
    found: list[Finding] = []
    found.extend(_mode(a, b))
    found.extend(_trunk_vlans(a, b))
    found.extend(_native_vlan(a, b))
    found.extend(_mtu(a, b))
    found.extend(_speed_duplex(a, b))
    found.extend(_lag(a, b))
    found.extend(_enabled(a, b))
    found.extend(_link_state(a, b))
    found.extend(_addresses(a, b))
    found.extend(_ospf(a, b))
    return found


def _enabled(a: Interface, b: Interface) -> list[Finding]:
    """片側だけ落としてある線。

    線としては引けているのに、片側が shutdown だと通らない。図の上では
    つながって見えるので、いちばん気づきにくい形になる。両側とも落として
    あるなら、それは「まだ使っていない線」なので言わない。
    """
    left, right = _both(a, b, "enabled")
    if left == right:
        return []
    down = a if not left else b
    up = b if not left else a
    return [
        Finding(
            level=HIGH,
            title="片側だけポートが落ちています",
            why="図の上ではつながって見えますが、片側が落ちているので通りません。"
            "使う線なら落ちている側を上げ、使わない線なら両端を落として"
            "「使っていない」と分かる形にしてください。",
            evidence=[f"{down.name}: 落ちている", f"{up.name}: 上がっている"],
        )
    ]


def _link_state(a: Interface, b: Interface) -> list[Finding]:
    """図には線があるのに、実機では線が上がっていない。

    show の表を取り込んだときだけ言える。片側だけでも down / notconnect なら、
    その線は図の上だけのものになっている。
    """
    down = [
        port
        for port in (a, b)
        if port.link_state in ("down", "notconnect", "err-disabled", "inactive")
    ]
    if not down:
        return []
    return [
        Finding(
            level=HIGH,
            title="図には線があるのに、実機では上がっていません",
            why="結線表か図が実機と違っています。抜けているか、別のポートに"
            "挿さっているか、相手が落ちています。図を直すか、線を確かめてください。",
            evidence=[f"{port.name}: {port.link_state}" for port in down],
        )
    ]


def _both(a: Interface, b: Interface, name: str) -> tuple:
    return getattr(a, name), getattr(b, name)


def _mode(a: Interface, b: Interface) -> list[Finding]:
    left, right = _both(a, b, "mode")
    if not left or not right or left == right:
        return []
    if {left, right} == {"trunk", "access"}:
        return [
            Finding(
                level=HIGH,
                title="片側がトランク、片側がアクセスです",
                why="タグを付けて渡す側と、付けずに渡す側がつながっています。"
                "1つの VLAN だけがたまたま通り、残りは通りません。"
                "「一部だけ通らない」という形になり、切り分けに時間がかかります。",
                evidence=[f"{a.name}: {left}", f"{b.name}: {right}"],
            )
        ]
    return [
        Finding(
            level=MEDIUM,
            title=f"ポートの使い方が違います（{left} と {right}）",
            why="両端で渡し方が違います。意図してそうしているなら問題ありませんが、"
            "片方だけ直したまま忘れている場合は、通らない VLAN が出ます。",
            evidence=[f"{a.name}: {left}", f"{b.name}: {right}"],
        )
    ]


def _trunk_vlans(a: Interface, b: Interface) -> list[Finding]:
    if a.mode != "trunk" or b.mode != "trunk":
        return []
    if not a.trunk_vlans or not b.trunk_vlans:
        return []  # 書いていない側は「全部通す」の意味になりうる。ここでは言わない
    if _is_all(a.trunk_vlans) or _is_all(b.trunk_vlans):
        # 「全部通す」は、その機器に定義してある VLAN 次第で中身が変わる。
        # 両端の定義を持ち出して比べる話になるので、この層では言わない
        return []
    left = vlanlist.expand_column(a.trunk_vlans, set())
    right = vlanlist.expand_column(b.trunk_vlans, set())
    if not left or not right:
        return []
    if left == right:
        return []

    only_a = sorted(left - right)
    only_b = sorted(right - left)
    return [
        Finding(
            level=HIGH,
            title="トランクで通す VLAN が両端でそろっていません",
            why="片側だけが通している VLAN は、相手に着いた時点で捨てられます。"
            "その VLAN の通信だけが通らないので、機器を見ても正常に見えます。",
            evidence=[
                f"{a.name} だけが通す: {_join(only_a)}",
                f"{b.name} だけが通す: {_join(only_b)}",
            ],
        )
    ]


def _is_all(text: str | None) -> bool:
    return bool(text) and text.strip().lower().startswith("all")


def _join(values: list[int]) -> str:
    if not values:
        return "なし"
    shown = ", ".join(str(v) for v in values[:_SHOW])
    return shown + (f" ほか {len(values) - _SHOW} 個" if len(values) > _SHOW else "")


def _native_vlan(a: Interface, b: Interface) -> list[Finding]:
    if a.mode != "trunk" or b.mode != "trunk":
        return []
    left, right = _both(a, b, "native_vlan")
    if left is None or right is None or left == right:
        return []
    return [
        Finding(
            level=HIGH,
            title="タグ無しで渡す VLAN が両端で違います",
            why="タグを付けずに渡した通信が、相手側では別の VLAN として扱われます。"
            "本来つながらないはずの2つのセグメントが、そこだけつながります。"
            "分けたつもりのものが混ざるので、気づきにくく、影響は大きいところです。",
            evidence=[f"{a.name}: VLAN {left}", f"{b.name}: VLAN {right}"],
        )
    ]


def _addresses(a: Interface, b: Interface) -> list[Finding]:
    """両端のアドレスが、同じセグメントに乗っているか。

    機器どうしを直接つなぐところは、両端が同じセグメントでなければ通じない。
    片側だけ別の区画にしてあっても、機器の中では正しい設定に見えるので、
    1台ずつ見ている限り見つからない。経路だけ入れて確かめると「相手が
    見えない」とだけ出て、原因はここにある、というのがよくある形。

    片側にアドセスが無い場合は何も言わない。L2 の口として使っていることも、
    取り込んだ Config が抜粋なことも、どちらもあるため。
    """
    left, right = _both(a, b, "ip_address")
    if not left or not right:
        return []
    a_net, a_addr = _network_of(left)
    b_net, b_addr = _network_of(right)
    if a_net is None or b_net is None:
        return []

    if a_addr == b_addr:
        return [
            Finding(
                level=HIGH,
                title="両端に同じアドレスが入っています",
                why="どちらが応えているのか決まりません。片方は届き、片方は届かない、"
                "という形になります。",
                evidence=[f"{a.name}: {left}", f"{b.name}: {right}"],
            )
        ]

    # 区切りが違っても、互いに相手を「同じ線の上」と見ているなら通じている。
    # 別のセグメントと言い切ると、直す必要の無いところを直させてしまう
    reaches = b_addr in a_net and a_addr in b_net
    if not reaches:
        return [
            Finding(
                level=HIGH,
                title="両端が別のセグメントです",
                why="同じ線でつながっていても、別の区画にいるので直接は通じません。"
                "経路を入れても「相手が見えない」とだけ出て、原因はここにあります。",
                evidence=[f"{a.name}: {left}（{a_net}）", f"{b.name}: {right}（{b_net}）"],
            )
        ]

    if a_net != b_net or a_net.prefixlen != b_net.prefixlen:
        return [
            Finding(
                level=MEDIUM,
                title="両端でセグメントの区切りが違います",
                why="いまは通じていても、区切りの外にいる相手へは片側だけが直接"
                "届く形になります。増設したときに、そこだけ通らなくなります。",
                evidence=[f"{a.name}: {left}", f"{b.name}: {right}"],
            )
        ]
    return []


#: OSPF の決めごとのうち、両端でそろっていないと隣にならないもの
_OSPF_MUST_MATCH = (
    ("ospf_area", "エリア"),
    ("ospf_hello", "あいさつの間隔（hello）"),
    ("ospf_dead", "見切りの時間（dead）"),
    ("ospf_network", "線の見立て（network type）"),
)


def _ospf(a: Interface, b: Interface) -> list[Finding]:
    """OSPF の決めごとが、両端でそろっているか。

    1つでも違うと隣どうしとして認識しない。上がらないので気づきはするが、
    「なぜ上がらないのか」を Config から追うと時間がかかる。ここで並べて
    出しておけば、どこが違うかは一目で分かる。

    片側にしか OSPF が入っていない場合は言わない。片側を意図的に外して
    いること（受け側だけ静的経路にする）があるため。
    """
    out: list[Finding] = []
    if not _runs_ospf(a) or not _runs_ospf(b):
        return out

    for name, label in _OSPF_MUST_MATCH:
        left, right = _both(a, b, name)
        if left is None or right is None or left == right:
            continue
        out.append(
            Finding(
                level=HIGH,
                title=f"OSPF の{label}が両端で違います",
                why="1つでも違うと、隣どうしとして認識しません。"
                "線はつながっているのに経路が流れないので、"
                "配線や故障を先に疑って時間を使うことになります。",
                evidence=[f"{a.name}: {left}", f"{b.name}: {right}"],
            )
        )

    left_auth, right_auth = _both(a, b, "ospf_auth")
    if (left_auth or "none") != (right_auth or "none"):
        out.append(
            Finding(
                level=HIGH,
                title="OSPF の合言葉の掛け方が両端で違います",
                why="片側だけ合言葉を求めていると、隣になりません。"
                "掛けるなら両端で、掛けないなら両端で外してください。",
                evidence=[
                    f"{a.name}: {left_auth or '掛けていない'}",
                    f"{b.name}: {right_auth or '掛けていない'}",
                ],
            )
        )
    return out


def _runs_ospf(interface: Interface) -> bool:
    """そのポートで OSPF を話しているか（読み取れた範囲で）。"""
    return any(
        getattr(interface, name) is not None
        for name in ("ospf_area", "ospf_hello", "ospf_dead", "ospf_network", "ospf_auth")
    )


def _network_of(value: str):  # noqa: ANN201
    """`10.0.0.1/30` を (ネットワーク, アドレス) にする。読めなければ (None, None)。"""
    text = value.strip().split()[0]
    if "/" not in text:
        return None, None
    try:
        interface = ipaddress.ip_interface(text)
    except ValueError:
        return None, None
    return interface.network, interface.ip


def _mtu(a: Interface, b: Interface) -> list[Finding]:
    left, right = _both(a, b, "mtu")
    if left is None or right is None or left == right:
        return []
    return [
        Finding(
            level=MEDIUM,
            title="1回に送れる大きさ（MTU）が両端で違います",
            why="小さい荷物は通り、大きい荷物だけが落ちます。"
            "普段は動いていて、ファイル転送や特定の業務のときだけ固まる、"
            "という形になります。切り分けにいちばん時間がかかる壊れ方です。",
            evidence=[f"{a.name}: {left}", f"{b.name}: {right}"],
        )
    ]


def _speed_duplex(a: Interface, b: Interface) -> list[Finding]:
    out: list[Finding] = []
    for name, label in (("speed", "速度"), ("duplex", "二重")):
        left, right = _both(a, b, name)
        if left is None and right is None:
            continue
        if left == right:
            continue
        if left is None or right is None:
            out.append(
                Finding(
                    level=MEDIUM,
                    title=f"片側だけ{label}を固定しています",
                    why="片側が固定で、もう片側が自動だと、話し合いが成立しません。"
                    "つながりはしますが、遅い側に落ちたり、エラーが出続けたりします。"
                    "両方固定するか、両方自動にしてください。",
                    evidence=[
                        f"{a.name}: {left or '自動'}",
                        f"{b.name}: {right or '自動'}",
                    ],
                )
            )
        else:
            out.append(
                Finding(
                    level=MEDIUM,
                    title=f"{label}の設定が両端で違います",
                    why="食い違ったまま通信すると、エラーが出続けるか、遅い側に落ちます。",
                    evidence=[f"{a.name}: {left}", f"{b.name}: {right}"],
                )
            )
    return out


def _lag(a: Interface, b: Interface) -> list[Finding]:
    left, right = _both(a, b, "lag_group")
    if left is None and right is None:
        return []
    if (left is None) != (right is None):
        return [
            Finding(
                level=HIGH,
                title="片側だけポートを束ねています",
                why="片側が束ねるつもりで、もう片側がそのつもりでないと、"
                "束にならないばかりか、線が輪になって通信が回り続けることがあります。"
                "束ねるなら両端で、束ねないなら両端で外してください。",
                evidence=[
                    f"{a.name}: {'束ねている' if left is not None else '束ねていない'}",
                    f"{b.name}: {'束ねている' if right is not None else '束ねていない'}",
                ],
            )
        ]

    left_mode, right_mode = _both(a, b, "lag_mode")
    if left_mode and right_mode and left_mode != right_mode:
        return [
            Finding(
                level=MEDIUM,
                title="ポートの束ね方が両端で違います",
                why="片側が話し合う方式、もう片側が決め打ちだと、束が組めません。"
                "組めないまま片方の線だけで動くことがあり、帯域が出ません。",
                evidence=[f"{a.name}: {left_mode}", f"{b.name}: {right_mode}"],
            )
        ]
    return []
