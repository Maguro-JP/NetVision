"""図の中に文字を置く場所を決める。

自動生成の図でラベルが機器の箱や他のラベルに重なるのは、単に置き場所を
考えていないだけである。人が手で直した結果ならその人の責任だが、
機械が出したものが被っているのは道具側の不備なので、ここで当たり判定を持つ。

やっていることは単純で、

1. 置きたい場所の候補を、良い順に並べる
2. 既に埋まっている矩形（機器の箱・置いたラベル）と重ならない最初の候補を採る
3. どれも駄目なら、一番重なりが小さい候補に逃がす

3 を用意してあるのは、密な構成では「どこにも置けない」が普通に起きるためで、
そこで例外にすると図そのものが出せなくなる。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians, sin

# 文字の幅はフォントに依るので測れない。半角英数を前提に、
# 実測に近い係数を置く（10px の Yu Gothic / Meiryo でおおよそ 0.55em）
_CHAR_W = 0.55
_FULL_WIDTH_MIN = 0x2E80  # ここから上は全角として扱う


def text_width(text: str, font_size: float) -> float:
    """文字列の描画幅の見積もり。全角は倍で数える。"""
    units = sum(2.0 if ord(char) >= _FULL_WIDTH_MIN else 1.0 for char in text)
    return units * font_size * _CHAR_W


@dataclass(frozen=True)
class Rect:
    x1: float
    y1: float
    x2: float
    y2: float

    def overlaps(self, other: Rect) -> bool:
        return not (
            self.x2 <= other.x1 or other.x2 <= self.x1 or self.y2 <= other.y1 or other.y2 <= self.y1
        )

    def overlap_area(self, other: Rect) -> float:
        w = min(self.x2, other.x2) - max(self.x1, other.x1)
        h = min(self.y2, other.y2) - max(self.y1, other.y1)
        return w * h if w > 0 and h > 0 else 0.0

    def grow(self, margin: float) -> Rect:
        return Rect(self.x1 - margin, self.y1 - margin, self.x2 + margin, self.y2 + margin)


def text_rect(
    x: float,
    y: float,
    text: str,
    font_size: float,
    anchor: str = "start",
    angle: float = 0.0,
) -> Rect:
    """テキストが占める矩形。回転させる場合は回した後の外接矩形を返す。

    `y` は SVG のベースライン。見た目の上端はそこから文字の高さ分だけ上になる。
    """
    width = text_width(text, font_size)
    ascent, descent = font_size * 0.8, font_size * 0.25

    if anchor == "middle":
        x1, x2 = x - width / 2, x + width / 2
    elif anchor == "end":
        x1, x2 = x - width, x
    else:
        x1, x2 = x, x + width
    y1, y2 = y - ascent, y + descent

    if not angle:
        return Rect(x1, y1, x2, y2)

    # 回転の中心は (x, y)。4隅を回してから外接矩形を取る
    radian = radians(angle)
    cos_a, sin_a = cos(radian), sin(radian)
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    rotated = [
        (
            x + (cx - x) * cos_a - (cy - y) * sin_a,
            y + (cx - x) * sin_a + (cy - y) * cos_a,
        )
        for cx, cy in corners
    ]
    xs = [p[0] for p in rotated]
    ys = [p[1] for p in rotated]
    return Rect(min(xs), min(ys), max(xs), max(ys))


def segment_hits(rect: Rect, p1: tuple[float, float], p2: tuple[float, float]) -> bool:
    """線分が矩形の内側を通るか。

    線を機器の箱の上に引くと、その機器に繋がっているように見える。
    通過なのか接続なのかは図からは区別できないので、当たったら迂回させる。
    """
    (x1, y1), (x2, y2) = p1, p2

    # 端点がそもそも中にあるなら当たり
    for x, y in (p1, p2):
        if rect.x1 <= x <= rect.x2 and rect.y1 <= y <= rect.y2:
            return True

    # 線分を媒介変数で表し、矩形の各軸について通過する区間を求める（slab 法）
    low, high = 0.0, 1.0
    for delta, start, lo, hi in (
        (x2 - x1, x1, rect.x1, rect.x2),
        (y2 - y1, y1, rect.y1, rect.y2),
    ):
        if abs(delta) < 1e-9:
            if start < lo or start > hi:
                return False
            continue
        t1, t2 = (lo - start) / delta, (hi - start) / delta
        low = max(low, min(t1, t2))
        high = min(high, max(t1, t2))
        if low > high:
            return False
    return True


class Placement:
    """置いたものを覚えておいて、次の当たり判定に使う。"""

    def __init__(self, blocked: list[Rect] | None = None) -> None:
        self._taken: list[Rect] = list(blocked or [])

    def add(self, rect: Rect) -> None:
        self._taken.append(rect)

    def collides(self, rect: Rect) -> bool:
        return any(rect.overlaps(taken) for taken in self._taken)

    def penalty(self, rect: Rect) -> float:
        return sum(rect.overlap_area(taken) for taken in self._taken)

    def choose(self, candidates: list[Rect]) -> tuple[int, bool]:
        """候補を良い順に見て、最初に空いているものを返す。

        戻り値は (選んだ番号, 重ならずに置けたか)。
        どれも空いていなければ、重なりが最小のものを選ぶ。図が出せなくなる方が困る。
        """
        for index, rect in enumerate(candidates):
            if not self.collides(rect):
                return index, True

        best = min(range(len(candidates)), key=lambda i: self.penalty(candidates[i]))
        return best, False
