"""日本の海岸線データを画面用に作り直す。

    python tools/build_coastline.py path/to/ne_10m_admin_0_countries.geojson

元データは Natural Earth（1:10m 国境）。パブリックドメインで、出典表示も
再配布の制限も無い。https://www.naturalearthdata.com/

このスクリプトは開発時にだけ使う。生成した結果を frontend/src/lib/coastline.ts に
置いておくので、アプリの実行時に外へ取りに行くことはない（閉域で動かす前提）。

そのまま使うと重すぎるので、次の2つを行う。

- 日本の範囲だけ取り出す
- Douglas-Peucker で点を間引く（既定 0.01 度 ≒ 1km 弱）

間引きの度合いは見た目と大きさの折り合いで決めている。正確な海岸線が要る
用途には使わないこと。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EPSILON = 0.01  # 度。これより内側のふくらみは落とす
MIN_POINTS = 6  # これ未満になった島は出さない
MIN_SPAN = 0.05  # 度。これより小さい島は出さない（画面では点になる）


def _distance(point, start, end) -> float:
    (x, y), (x1, y1), (x2, y2) = point, start, end
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return ((x - (x1 + t * dx)) ** 2 + (y - (y1 + t * dy)) ** 2) ** 0.5


def simplify(points: list[tuple[float, float]], epsilon: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    index, worst = 0, 0.0
    for i in range(1, len(points) - 1):
        gap = _distance(points[i], points[0], points[-1])
        if gap > worst:
            index, worst = i, gap
    if worst <= epsilon:
        return [points[0], points[-1]]
    left = simplify(points[: index + 1], epsilon)
    right = simplify(points[index:], epsilon)
    return left[:-1] + right


def rings(geometry) -> list[list[tuple[float, float]]]:
    if geometry["type"] == "Polygon":
        return [[(x, y) for x, y in geometry["coordinates"][0]]]
    out = []
    for polygon in geometry["coordinates"]:
        out.append([(x, y) for x, y in polygon[0]])
    return out


def main(path: str) -> None:
    data = json.loads(Path(path).read_text())
    japan = next(
        feature
        for feature in data["features"]
        if feature["properties"].get("ADMIN") == "Japan"
        or feature["properties"].get("NAME") == "Japan"
    )

    islands = []
    for ring in rings(japan["geometry"]):
        thinned = simplify(ring, EPSILON)
        if len(thinned) < MIN_POINTS:
            continue
        span = max(
            max(p[0] for p in thinned) - min(p[0] for p in thinned),
            max(p[1] for p in thinned) - min(p[1] for p in thinned),
        )
        if span < MIN_SPAN:
            continue
        islands.append(thinned)

    islands.sort(key=len, reverse=True)
    total = sum(len(island) for island in islands)

    lines = [
        "/* 日本の海岸線。",
        " *",
        " * 出典: Natural Earth（1:10m 国境, パブリックドメイン）",
        " * https://www.naturalearthdata.com/",
        " *",
        " * tools/build_coastline.py で間引いて作った表。生成物をそのまま置いてあるので、",
        " * 画面を出すときに外へ取りに行くことはない。",
        f" * 島 {len(islands)}個 / 点 {total}個（間引き {EPSILON} 度）。",
        " * 正確な海岸線ではないので、位置合わせや面積の判断には使わないこと。",
        " */",
        "export const COASTLINE: [number, number][][] = [",
    ]
    for island in islands:
        body = ",".join(f"[{lon:.3f},{lat:.3f}]" for lon, lat in island)
        lines.append(f"  [{body}],")
    lines.append("];")

    out = Path("frontend/src/lib/coastline.ts")
    out.write_text("\n".join(lines) + "\n")
    print(f"{out}: 島 {len(islands)}個 / 点 {total}個")


if __name__ == "__main__":
    sys.setrecursionlimit(10000)
    main(sys.argv[1])
