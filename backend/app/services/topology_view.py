"""画面が実際に描いた図の形を、書き出しへ渡すための入れ物。

書き出しの図は、画面で見ているものと同じでなければならない。人は画面で
配置と線を確かめてから「出力する」を押すのであって、そこで別のものが
出てくるなら、確かめた意味が無い。

これまでは画面（TypeScript）と書き出し（Python）が、それぞれ線を引いて
いた。同じ規則で書いてあっても、候補の選び方が少し違うだけで別の図になる。
そこで、画面が引いた結果をそのまま渡して、書き出しはそれを描くだけにする。

置き場は処理の中（メモリ）にする。図の形は、押した直後に1回使うだけで、
残しておく value が無い。台帳に書くと、図を出すたびに行が増えていく。
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

#: 預かった図を持っておく時間。押してから窓が開いて選ぶまでの間があれば足りる
TTL_SECONDS = 30 * 60
#: 同時に持っておく数。押しっぱなしにされても、際限なく増やさない
MAX_VIEWS = 64


@dataclass
class ViewBox:
    """機器1台の箱。画面での左上と、実際の大きさ。"""

    device_id: int
    x: float
    y: float
    width: float
    height: float


@dataclass
class ViewWire:
    """結線1本。画面が引いた折れ線と、どの辺から出ているか。"""

    edge_id: int
    points: list[tuple[float, float]]
    source_side: str
    target_side: str


@dataclass
class ViewGeometry:
    boxes: dict[int, ViewBox] = field(default_factory=dict)
    wires: dict[int, ViewWire] = field(default_factory=dict)


_views: dict[str, tuple[float, ViewGeometry]] = {}


def keep(view: ViewGeometry) -> str:
    """図の形を預かって、引き換えの札を返す。"""
    _sweep()
    token = secrets.token_urlsafe(12)
    _views[token] = (time.monotonic(), view)
    if len(_views) > MAX_VIEWS:
        oldest = min(_views, key=lambda key: _views[key][0])
        _views.pop(oldest, None)
    return token


def take(token: str | None) -> ViewGeometry | None:
    """札に対応する図の形。無ければ None（書き出しはいつもどおり自分で引く）。"""
    if not token:
        return None
    _sweep()
    found = _views.get(token)
    return found[1] if found else None


def _sweep() -> None:
    limit = time.monotonic() - TTL_SECONDS
    for token in [key for key, (at, _) in _views.items() if at < limit]:
        _views.pop(token, None)


def clear() -> None:
    """試験用。預かったものを空にする。"""
    _views.clear()
