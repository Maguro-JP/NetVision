"""拠点をまたぐ全体図の配置を、繰り返して決める。

全体図（拠点で絞っていない図）は、120台を8段70列の帯にしていた。幅は
16000px を超え、画面に収めるには 0.08 倍まで引かないと入らない。そこまで
引いた図は、線も名前も読めない。台数の問題ではなく、並べ方の問題である。

### 考え方

紙に構成図を描くとき、人は拠点ごとに小さな図を描いて、それを並べる。
1台ずつを一列に並べたりはしない。ここでも同じにする。

1. 機器を拠点ごとのまとまり（ブロック）に分ける
2. ブロックの中は、拠点1つの図と同じ置き方で組む（既存の `_compact`）
3. ブロックを、上位（センター）から順に段へ置き、横に長くなったら折り返す

### なぜ繰り返すのか

折り返す幅をいくつにすれば良いかは、拠点の数と大きさで変わる。1回で当てる
必要はない。候補をいくつも作って、条件を満たすものの中から点の良いものを
選ぶ。条件（箱が重ならない、拠点が混ざらない、上位が上）は満たすか満たさないか
の話なので、点数と混ぜずに先に落とす。

繰り返しは打ち切る。良くならなくなったら止め、上限に達しても止める。
時間をかけてよいとはいえ、画面を開くたびに走るので、上限は要る。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import log

from app.services.topology import (
    MIN_BOX_GAP,
    TopologyEdgeView,
    TopologyGraph,
    TopologyNodeView,
    _adjacency,
    _align_redundant_pairs,
    _busiest_sides,
    _compact,
    _snap_to_grid,
    _visual_bottom,
    _visual_left,
    _visual_right,
    _visual_top,
    hierarchy_rank,
)

#: ブロックどうしのすき間。箱のすき間より広く取る。ここが詰まると、
#: どこまでが1つの拠点なのかが読めなくなる
BLOCK_GAP = 160.0

#: 目指す縦横比（幅 ÷ 高さ）。紙も画面も横長なので、少し横長にする
TARGET_RATIO = 16 / 9

#: これより横長・縦長になったら、条件を満たしていないものとして落とす
MAX_RATIO = 4.0

#: 折り返し幅の候補をいくつ試すか。多くしても答えは変わらないので上限を置く
MAX_TRIES = 32


@dataclass
class Block:
    """1拠点ぶんのまとまり。中身は組み終わっていて、あとは置くだけ。"""

    key: object
    name: str
    nodes: list[TopologyNodeView]
    #: 網全体で見た上下（小さいほど上位）。段を決めるのに使う
    rank: int
    width: float = 0.0
    height: float = 0.0
    x: float = 0.0
    y: float = 0.0

    @property
    def device_ids(self) -> set[int]:
        return {node.device_id for node in self.nodes}


@dataclass
class Report:
    """どう決まったか。図が変なときに、どこで決まったのかを追えるようにする。"""

    blocks: int = 0
    tries: int = 0
    columns: int = 0
    width: float = 0.0
    height: float = 0.0
    score: float = 0.0
    violations: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return self.width / self.height if self.height else 0.0


def should_use(nodes: list[TopologyNodeView]) -> bool:
    """この置き方が要る図か。

    拠点が2つ3つなら、1つの木として組んだ方が結線の上下が素直に出る。
    帯のように伸びるのは、拠点が増えてからである。
    """
    sites = {node.site_id for node in nodes if node.site_id is not None}
    return len(sites) >= 4 and len(nodes) >= 12


def arrange(
    nodes: list[TopologyNodeView],
    edges: list[TopologyEdgeView],
    *,
    orientation: str = "vertical",
    only: set[int] | None = None,
) -> Report:
    """拠点ごとのまとまりに分けて置く。`only` に入っている機器だけ動かす。

    戻り値は、どの候補で決まったかの記録。位置は `nodes` に書き込む。
    """
    movable = {node.device_id for node in nodes} if only is None else set(only)
    blocks = _build_blocks(nodes, edges, movable, orientation=orientation)
    report = Report(blocks=len(blocks))
    if len(blocks) < 2:
        return report

    # 箱の広がりは結線の本数で決まる。ブロックの中を組んでから測る
    tb, lr = _busiest_sides(nodes, edges)
    _measure(blocks, tb, lr)
    between = _links_between(blocks, edges)

    best: tuple[float, list[tuple[float, float]], float] | None = None
    for limit in _width_candidates(blocks):
        report.tries += 1
        placed = _place_rows(blocks, limit)
        if _violations(blocks, placed):
            continue
        score = _score(blocks, placed, between)
        if best is None or score < best[0]:
            best = (score, placed, limit)

    if best is None:
        # どの折り返し幅でも条件を満たせなかった。1段1行に落とす（必ず置ける形）
        placed = _place_rows(blocks, 0.0)
        best = (_score(blocks, placed, between), placed, 0.0)
        report.violations = _violations(blocks, placed)

    score, placed, limit = best
    for block, (x, y) in zip(blocks, placed, strict=True):
        block.x, block.y = x, y
    _apply(blocks, tb, lr)

    report.columns = max(_row_sizes(placed), default=0)
    report.score = score
    report.width, report.height = _extent(blocks, placed)
    return report


def _build_blocks(
    nodes: list[TopologyNodeView],
    edges: list[TopologyEdgeView],
    movable: set[int],
    *,
    orientation: str,
) -> list[Block]:
    """拠点ごとに分けて、それぞれの中を組む。

    ブロックの中では、その拠点の中の結線だけを見る。拠点をまたぐ線まで
    混ぜると、隣の拠点の機器に引っ張られて、まとまりが崩れる。
    """
    graph = TopologyGraph(layer="", nodes=nodes, edges=edges)
    rank = _align_redundant_pairs(
        hierarchy_rank(graph), _adjacency(graph), edges=edges, nodes=nodes
    )

    grouped: dict[object, list[TopologyNodeView]] = defaultdict(list)
    for node in nodes:
        # 拠点が付いていない機器は1台で1つのまとまりにする。まとめて置くと
        # 「拠点未登録」という大きな箱ができ、実際より繋がって見える
        key = node.site_id if node.site_id is not None else ("device", node.device_id)
        grouped[key].append(node)

    blocks: list[Block] = []
    for key, members in grouped.items():
        ids = {node.device_id for node in members}
        inner = [edge for edge in edges if edge.source in ids and edge.target in ids]
        free = [node for node in members if node.device_id in movable]
        if free:
            _compact(
                free,
                inner,
                rank={i: rank.get(i, 0) for i in ids},
                orientation=orientation,
            )
        blocks.append(
            Block(
                key=key,
                name=members[0].site_name or members[0].hostname,
                nodes=members,
                rank=min(rank.get(node.device_id, 0) for node in members),
            )
        )

    # 段（上位から）、その中は名前順。名前順にしておくと、台帳を足しても
    # 並びが飛ばない。日によって場所が変わる図は、覚えられない
    blocks.sort(key=lambda block: (block.rank, block.name))
    return blocks


def _measure(blocks: list[Block], tb: dict[int, int], lr: dict[int, int]) -> None:
    """ブロックの大きさを、画面に見える箱で測る。

    保存座標のままだと、結線の多い機器で広がった分を見落とす。そこが
    今までブロック同士の食い込みになっていた。
    """
    for block in blocks:
        left = min(_visual_left(n.x, tb.get(n.device_id, 0)) for n in block.nodes)
        right = max(_visual_right(n.x, tb.get(n.device_id, 0)) for n in block.nodes)
        top = min(_visual_top(n.y, lr.get(n.device_id, 0)) for n in block.nodes)
        bottom = max(_visual_bottom(n.y, lr.get(n.device_id, 0)) for n in block.nodes)
        block.width = right - left
        block.height = bottom - top
        # 中身を (0,0) 起点へ寄せておく。あとはブロックの位置を足すだけになる
        for node in block.nodes:
            node.x -= left
            node.y -= top


def _width_candidates(blocks: list[Block]) -> list[float]:
    """折り返す幅の候補。

    「1行に何個」ではなく「幅いくつで折り返すか」で試す。拠点の大きさは
    まちまちなので、個数で切ると、大きい拠点が並んだ行だけ極端に広くなる。

    いちばん広い拠点の幅から、全部を1行に並べた幅までを刻む。
    """
    if not blocks:
        return []
    widest = max(block.width for block in blocks)
    total = sum(block.width + BLOCK_GAP for block in blocks)
    if total <= widest:
        return [widest]
    step = (total - widest) / MAX_TRIES
    return [widest + step * i for i in range(MAX_TRIES + 1)]


def _place_rows(blocks: list[Block], limit: float) -> list[tuple[float, float]]:
    """段ごとに、指定の幅で折り返して置く。

    段（rank）が変わるところでは必ず行を変える。上位と配下が同じ行に並ぶと、
    どちらが上なのかが図から読めなくなる。

    行はそのあと中央へ寄せる。左詰めのままだと、上の段が短い図で上位が
    左端に寄り、親子の関係が読みにくくなる。
    """
    placed: list[tuple[float, float]] = []
    rows: list[list[int]] = []
    y = 0.0
    row: list[int] = []
    row_x = 0.0

    def flush() -> None:
        nonlocal y, row, row_x
        if not row:
            return
        y += max(blocks[i].height for i in row) + BLOCK_GAP
        rows.append(row)
        row = []
        row_x = 0.0

    current_rank: int | None = None
    for index, block in enumerate(blocks):
        if current_rank is not None and block.rank != current_rank:
            flush()
        elif row and row_x + block.width > limit:
            flush()
        current_rank = block.rank
        placed.append((row_x, y))
        row_x += block.width + BLOCK_GAP
        row.append(index)
    flush()

    return _center_rows(blocks, placed, rows)


def _center_rows(
    blocks: list[Block],
    placed: list[tuple[float, float]],
    rows: list[list[int]],
) -> list[tuple[float, float]]:
    """行ごとに、全体の真ん中へ寄せる。"""
    widths = [
        sum(blocks[i].width + BLOCK_GAP for i in row) - BLOCK_GAP if row else 0.0 for row in rows
    ]
    span = max(widths, default=0.0)
    out = list(placed)
    for row, width in zip(rows, widths, strict=True):
        shift = (span - width) / 2
        for i in row:
            x, y = out[i]
            out[i] = (x + shift, y)
    return out


def _row_sizes(placed: list[tuple[float, float]]) -> list[int]:
    """行ごとのブロック数。何行何列で決まったのかを記録に残す。"""
    rows: dict[float, int] = defaultdict(int)
    for _, y in placed:
        rows[y] += 1
    return list(rows.values())


def _violations(blocks: list[Block], placed: list[tuple[float, float]]) -> list[str]:
    """満たしていない条件。満たすか満たさないかなので、点数とは混ぜない。"""
    out: list[str] = []
    boxes = [
        (x, y, x + block.width, y + block.height)
        for block, (x, y) in zip(blocks, placed, strict=True)
    ]
    for i, a in enumerate(boxes):
        for j, b in enumerate(boxes[i + 1 :], start=i + 1):
            gap = max(a[0] - b[2], b[0] - a[2], a[1] - b[3], b[1] - a[3])
            if gap < MIN_BOX_GAP:
                out.append(f"{blocks[i].name} と {blocks[j].name} が近すぎる")

    width, height = _extent(blocks, placed)
    if height > 0:
        ratio = width / height
        if ratio > MAX_RATIO or ratio < 1 / MAX_RATIO:
            out.append(f"縦横比が {ratio:.1f} で、紙にも画面にも収まらない")
    return out


def _score(
    blocks: list[Block],
    placed: list[tuple[float, float]],
    between: dict[tuple[int, int], int],
) -> float:
    """小さいほど良い。

    見るのは2つだけにする。紙に収まる形か（縦横比）と、拠点をまたぐ線が
    短いか。増やすほど、なぜその配置になったのかが説明できなくなる。
    """
    width, height = _extent(blocks, placed)
    if not width or not height:
        return 0.0

    shape = abs(log((width / height) / TARGET_RATIO))

    centers = [
        (x + block.width / 2, y + block.height / 2)
        for block, (x, y) in zip(blocks, placed, strict=True)
    ]
    span = max(width, height) or 1.0
    wires = 0.0
    for (i, j), count in between.items():
        ax, ay = centers[i]
        bx, by = centers[j]
        wires += count * ((abs(ax - bx) + abs(ay - by)) / span)

    total = len(blocks) or 1
    return shape + wires / total


def _links_between(
    blocks: list[Block], edges: list[TopologyEdgeView]
) -> dict[tuple[int, int], int]:
    """ブロックをまたぐ線の本数。多いところほど近くに置きたい。"""
    owner: dict[int, int] = {}
    for index, block in enumerate(blocks):
        for device_id in block.device_ids:
            owner[device_id] = index

    out: dict[tuple[int, int], int] = defaultdict(int)
    for edge in edges:
        a, b = owner.get(edge.source), owner.get(edge.target)
        if a is None or b is None or a == b:
            continue
        out[(min(a, b), max(a, b))] += 1
    return dict(out)


def _extent(blocks: list[Block], placed: list[tuple[float, float]]) -> tuple[float, float]:
    if not blocks:
        return 0.0, 0.0
    right = max(x + block.width for block, (x, y) in zip(blocks, placed, strict=True))
    bottom = max(y + block.height for block, (x, y) in zip(blocks, placed, strict=True))
    return right, bottom


def _apply(blocks: list[Block], tb: dict[int, int], lr: dict[int, int]) -> None:
    """決めたブロックの位置を、機器の座標へ反映する。"""
    for block in blocks:
        for node in block.nodes:
            node.x = _snap_to_grid(node.x + block.x)
            node.y = _snap_to_grid(node.y + block.y)
