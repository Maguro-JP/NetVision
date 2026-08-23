"""拠点図の配置を、線まで引いてみてから決める。

配置と線は別々に直してきた。だが図が読めるかどうかは、置いた結果に線を
引いてみるまで分からない。実際、置き方の条件（上位が上・箱が重ならない）を
全部満たしているのに、線が上流の段をまたいで長々と走る図が残っていた。
配下の対向の列が上へ伸びて、上位と同じ高さから始まっていたためである。

そこで、置き方をいくつか作り、それぞれで実際に図を描き、線の側の条件まで
見てから選ぶ。

### 条件と点数を分ける

条件は満たすか満たさないかで、点数と混ぜない。

- 箱が重ならない（最低限のすき間がある）
- 上位の対向は自拠点より上、配下の対向は自拠点より下
- 上位と配下が同じ高さに並ばない（どちらが上か読めなくなる）
- 線が箱を貫かない、切れ目の無い交差がない（描いた図で見る）

点数で選ぶのは、条件を満たしたものだけ。線の折れの合計、線の長さの合計、
図の縦横比の3つを見る。増やすほど、なぜその配置になったのかを説明できなく
なるので、この3つに絞っている。

### 置き方の候補

配下の対向の列をどう置くかだけを変える。ここが図の形をいちばん左右する。

1. いまの置き方（自拠点を中心に上下へ振り分ける）
2. 上位の段より下から始める（上へ伸ばさない）
3. 2列に折り返す（縦に長くなりすぎたとき）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.topology import (
    MIN_BOX_GAP,
    TopologyGraph,
    TopologyNodeView,
    _busiest_sides,
    _snap_to_grid,
    _visual_bottom,
    _visual_left,
    _visual_right,
    _visual_top,
)

#: 列の中の縦の間隔（対向の箱の高さ＋すき間）。_stack_outside と同じ刻み
_ROW_STEP = 80.0

#: 列と列の横の間隔。折り返したときに使う
_COLUMN_STEP = 260.0

#: 縦横比がこれを超えたら、折り返す候補の出番
_TALL = 1.2


@dataclass
class Choice:
    """選んだ置き方の記録。図が変なときに、どれで決まったのかを追える。"""

    name: str = "いまの置き方"
    tried: int = 0
    bends: int = 0
    crossings: int = 0
    ratio: float = 0.0
    violations: list[str] = field(default_factory=list)


def polish(graph: TopologyGraph) -> Choice:
    """拠点図の配置を、線まで見てから決め直す。位置は `graph` に書き込む。

    人が置いた機器がある図では何もしない。置いた位置は意思なので、
    こちらの都合で動かさない。
    """
    inside = [n for n in graph.nodes if not n.outside]
    outside = [n for n in graph.nodes if n.outside]
    if not inside or len(outside) < 2:
        return Choice(name="対向が少ないので触らない")
    if any(node.placed for node in graph.nodes):
        return Choice(name="人が置いた配置は触らない")

    start = {n.device_id: (n.x, n.y) for n in graph.nodes}
    best: tuple[tuple, dict[int, tuple[float, float]], str] | None = None
    choice = Choice()

    for name, build in (
        ("いまの置き方", _as_is),
        ("上位の段より下から始める", _below_upstream),
        ("自拠点より下から始める", _below_own),
        ("2列に折り返す", _two_columns),
    ):
        _restore(graph, start)
        if not build(graph):
            continue
        choice.tried += 1
        marks = _measure(graph)
        if marks is None:
            continue
        score, bends, crossings, ratio = marks
        if best is None or score < best[0]:
            best = (score, {n.device_id: (n.x, n.y) for n in graph.nodes}, name)
            choice.bends, choice.crossings, choice.ratio = bends, crossings, ratio

    if best is None:
        # どの置き方でも条件を満たせなかった。元のまま出す（描けないよりまし）
        _restore(graph, start)
        choice.name = "条件を満たす置き方が無かった"
        choice.violations = _violations(graph)
        return choice

    _restore(graph, best[1])
    choice.name = best[2]
    return choice


def _restore(graph: TopologyGraph, saved: dict[int, tuple[float, float]]) -> None:
    for node in graph.nodes:
        position = saved.get(node.device_id)
        if position is not None:
            node.x, node.y = position


# ---------------------------------------------------------------------------
# 置き方の候補
# ---------------------------------------------------------------------------


def _as_is(_graph: TopologyGraph) -> bool:
    return True


def _below_upstream(graph: TopologyGraph) -> bool:
    """配下の対向の列を、上位の対向より下から始める。

    列が上へ伸びると、いちばん上の1台が上位と同じ高さに並ぶ。そこへ引く線は
    上位の段を横切って走るので、どこへ繋がっているのか追えなくなる。
    """
    upstream, downstream = _split_outside(graph)
    if not upstream or not downstream:
        return False

    _tb, lr = _sides(graph)
    floor = max(_visual_bottom(n.y, lr(n)) for n in upstream) + MIN_BOX_GAP
    top = min(_visual_top(n.y, lr(n)) for n in downstream)
    if top >= floor:
        return False

    shift = _snap_to_grid(floor - top)
    for node in downstream:
        node.y += shift
    return True


def _below_own(graph: TopologyGraph) -> bool:
    """配下の対向の列を、自拠点より下から始める。

    配下が自拠点より上に並ぶと、下位の拠点が上にいる図になる。線も上へ
    向かって出るので、上位への線と見分けが付かない。
    """
    inside = [n for n in graph.nodes if not n.outside]
    _upstream, downstream = _split_outside(graph)
    if not inside or not downstream:
        return False

    _tb, lr = _sides(graph)
    floor = min(_visual_top(n.y, lr(n)) for n in inside)
    top = min(_visual_top(n.y, lr(n)) for n in downstream)
    if top >= floor:
        return False

    shift = _snap_to_grid(floor - top)
    for node in downstream:
        node.y += shift
    return True


def _two_columns(graph: TopologyGraph) -> bool:
    """配下の対向を2列に折り返す。縦に長い図は紙にも画面にも収まらない。"""
    _upstream, downstream = _split_outside(graph)
    if len(downstream) < 4:
        return False

    tb, lr = _sides(graph)
    height = max(_visual_bottom(n.y, lr(n)) for n in downstream) - min(
        _visual_top(n.y, lr(n)) for n in downstream
    )
    width = max(_visual_right(n.x, tb(n)) for n in graph.nodes) - min(
        _visual_left(n.x, tb(n)) for n in graph.nodes
    )
    if height < width * _TALL:
        return False

    ordered = sorted(downstream, key=lambda n: (n.y, n.hostname))
    half = (len(ordered) + 1) // 2
    base_x = min(n.x for n in ordered)
    base_y = min(n.y for n in ordered)
    for index, node in enumerate(ordered):
        column, row = divmod(index, half)
        node.x = base_x + column * _COLUMN_STEP
        node.y = base_y + row * _ROW_STEP
    return True


# ---------------------------------------------------------------------------
# 条件と点数
# ---------------------------------------------------------------------------


def _measure(graph: TopologyGraph) -> tuple[tuple, int, int, float] | None:
    """条件を満たしていれば点数を返す。満たしていなければ None。

    線の側は、実際に図を描いてから見る。置き方だけを見ていたのでは、
    線が箱を貫くかどうかも、交差が残るかどうかも分からない。
    """
    from app.services import topology_quality
    from app.services.topology_export import render_svg

    if _violations(graph):
        return None

    svg = render_svg(graph, title="配置の下見", style="simple")
    findings = topology_quality.evaluate_svg(svg, graph)
    if not topology_quality.is_acceptable(findings):
        return None

    wires = topology_quality._svg_polylines(svg)
    bends = sum(topology_quality._bends(wire) for wire in wires)
    crossings = sum(
        1
        for index, wire in enumerate(wires)
        for other in wires[index + 1 :]
        for a1, a2 in zip(wire, wire[1:], strict=False)
        for b1, b2 in zip(other, other[1:], strict=False)
        if topology_quality._score_cross(a1, a2, b1, b2)
    )
    length = sum(
        abs(a[0] - b[0]) + abs(a[1] - b[1])
        for wire in wires
        for a, b in zip(wire, wire[1:], strict=False)
    )
    ratio = _ratio(graph)
    # 紙に収まる形かどうかが先。縦に長い図は、折れが少なくても刷れない。
    # そのうえで、切れ目の要る交差 → 折れ → 線の長さの順に見る
    score = (_off_paper(ratio), crossings, bends, round(length / 100))
    return score, bends, crossings, ratio


#: 紙にも画面にも収まる縦横比の幅。ここを外れたら、折れが増えても直す
_PAPER = (0.9, 3.0)


def _off_paper(ratio: float) -> int:
    return 0 if _PAPER[0] <= ratio <= _PAPER[1] else 1


def _violations(graph: TopologyGraph) -> list[str]:
    """満たすか満たさないかで決まる条件。

    箱が重なっていないかは、図の良し悪しを見る側（topology_quality）に任せる。
    同じ判定をここでも書くと、対向の箱の高さのような前提がずれて、実際には
    離れている図まで「近すぎる」と落としてしまう。実際にそうなっていた。
    """
    from app.services import topology_quality

    out = [item.detail for item in topology_quality.evaluate_site_diagram(graph) if not item.ok]

    inside = [n for n in graph.nodes if not n.outside]
    upstream, downstream = _split_outside(graph)
    if inside and upstream and min(n.y for n in inside) <= max(n.y for n in upstream):
        out.append("上位の対向が自拠点より上にいない")
    if upstream and downstream:
        top = min(n.y for n in downstream)
        if any(abs(node.y - top) < 1 for node in upstream):
            out.append("上位と配下が同じ高さに並んでいる")
    return out


def _ratio(graph: TopologyGraph) -> float:
    tb, lr = _sides(graph)
    left = min(_visual_left(n.x, tb(n)) for n in graph.nodes)
    right = max(_visual_right(n.x, tb(n)) for n in graph.nodes)
    top = min(_visual_top(n.y, lr(n)) for n in graph.nodes)
    bottom = max(_visual_bottom(n.y, lr(n)) for n in graph.nodes)
    height = bottom - top
    return (right - left) / height if height else 0.0


def _split_outside(
    graph: TopologyGraph,
) -> tuple[list[TopologyNodeView], list[TopologyNodeView]]:
    """対向を、上位と配下に分ける。

    分けるのは置いた場所ではなく、上下の関係（peer_kind）で行う。場所で
    分けると、上へ伸びすぎて上位の段に並んでしまった配下が「上位」に
    数えられ、その並びを直せなくなる。直したいのはまさにそこである。
    """
    upstream = [n for n in graph.nodes if n.outside and n.peer_kind == "up"]
    downstream = [n for n in graph.nodes if n.outside and n.peer_kind != "up"]
    return upstream, downstream


def _sides(graph: TopologyGraph):
    """機器ごとの、上下辺・左右辺に来る本数を返す小さな関数の組。"""
    tb, lr = _busiest_sides(graph.nodes, graph.edges)

    def top_bottom(node: TopologyNodeView) -> int:
        return tb.get(node.device_id, 0)

    def left_right(node: TopologyNodeView) -> int:
        return lr.get(node.device_id, 0)

    return top_bottom, left_right
