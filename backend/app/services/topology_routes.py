"""図の線と箱を1か所で決める。

線の引き方が画面（TypeScript）と書き出し（Python）に分かれていると、同じ
規則で書いてあっても候補の選び方が少し違うだけで別の図になる。人は画面で
確かめてから印刷するので、そこで違う図が出てはいけない。

そこで、線を引くのは書き出しの側だけにして、画面はその結果を描く。ここは
その受け渡し口である。図を描く手順の途中から取り出すので、引き方が2つに
分かれることが無い。

同じ台帳・同じ配置なら結果も同じなので、覚えておいて使い回す。全体図
（120台）で0.9秒かかる計算を、画面を開き直すたびに走らせない。
"""

from __future__ import annotations

from collections import OrderedDict

from app.services import topology_export
from app.services.topology import TopologyGraph

#: 覚えておく図の数。拠点をいくつか行き来しても足りる程度
_KEEP = 12

_cache: OrderedDict[tuple, dict] = OrderedDict()


def _fingerprint(graph: TopologyGraph, simple: bool) -> tuple:
    """同じ図かどうかの見分け。位置か結線が変われば別物になる。"""
    return (
        graph.layer,
        simple,
        tuple(sorted((n.device_id, n.x, n.y, n.outside) for n in graph.nodes)),
        tuple(
            sorted(
                (e.id, e.source, e.target, e.source_side or "", e.target_side or "")
                for e in graph.edges
            )
        ),
        tuple(sorted((g.id, tuple(sorted(g.device_ids))) for g in graph.groups)),
    )


def compute(graph: TopologyGraph, *, simple: bool = False) -> dict:
    """箱の大きさと、線の折れ点を返す。

    返す座標は台帳と同じ（図の余白は含まない）。画面はそのまま置ける。
    """
    key = _fingerprint(graph, simple)
    found = _cache.get(key)
    if found is not None:
        _cache.move_to_end(key)
        return found

    collected: dict = {}
    topology_export.render_svg(
        graph,
        style="simple" if simple else "detail",
        collect=collected,
    )
    _cache[key] = collected
    if len(_cache) > _KEEP:
        _cache.popitem(last=False)
    return collected


def clear() -> None:
    """試験用。覚えているものを空にする。"""
    _cache.clear()
