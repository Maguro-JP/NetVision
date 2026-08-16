"""機器の箱の高さと、同じ相手への束の置き方。

箱は中身の行数から決める。行が枠から切れると、そこに何が書いてあったのか
図からは分からない。高さは画面と図（SVG）で同じでなければ、同じ配置なのに
線の出る位置が食い違う。

束（ブリッジやポートチャネル）は近づけて置く。1本ずつ辺いっぱいに散らすと、
どれが同じ相手行きなのか読めない。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.topology import _DETAIL_HEIGHT
from app.services.topology_export import _space_face_ts

_TSX = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "TopologyPage.tsx"


def _screen_row_heights() -> dict[str, int]:
    source = _TSX.read_text(encoding="utf-8")
    return {
        name: int(value)
        for name, value in re.findall(r"^const (\w+_ROWS?|PAD_Y) = (\d+);$", source, re.M)
    }


def test_box_height_matches_the_screen():
    """図の箱の高さは画面と同じ。ずれると口の位置が食い違う。"""
    rows = _screen_row_heights()
    expected = (
        rows["PAD_Y"]
        + rows["TOP_ROW"]
        + rows["HEAD_ROW"]
        + rows["SUB_ROW"]
        + rows["BODY_ROW"] * rows["BODY_ROWS"]
        + rows["PORTS_ROW"]
        + rows["FOOT_ROW"]
    )
    assert _DETAIL_HEIGHT == float(expected)


def test_box_height_covers_every_row_the_card_can_show():
    """出る可能性のある行を全部足しても収まること。"""
    rows = _screen_row_heights()
    # 管理IDと種別・機器名・ホスト名・型番・シリアル・ポート種・管理IPと状態
    tallest = (
        rows["PAD_Y"]
        + rows["TOP_ROW"]
        + rows["HEAD_ROW"]
        + rows["SUB_ROW"]
        + rows["BODY_ROW"] * rows["BODY_ROWS"]
        + rows["PORTS_ROW"]
        + rows["FOOT_ROW"]
    )
    assert _DETAIL_HEIGHT >= float(tallest)


def test_a_bundle_to_the_same_peer_stays_together_in_the_middle():
    """同じ相手への2本は、辺の端へ分かれず中央に並ぶ。"""
    spaced = _space_face_ts([0.5, 0.5], 190.0, [7, 7])
    assert len(spaced) == 2
    for t in spaced:
        assert 0.25 < t < 0.75, spaced
    # 読める間隔は空けたうえで、辺の幅いっぱいには広げない
    gap = abs(spaced[1] - spaced[0]) * 190.0
    assert 11.9 <= gap <= 70, gap


def test_a_bundle_on_a_side_face_can_sit_closer():
    """左右の辺はポート名を横書きしても縦に重ならないので、より詰められる。"""
    side = _space_face_ts([0.5, 0.5], 190.0, [7, 7])
    top = _space_face_ts([0.5, 0.5], 190.0, [7, 7], horizontal=True)
    assert abs(side[1] - side[0]) < abs(top[1] - top[0])


def test_a_fan_to_different_peers_still_uses_the_whole_face():
    """相手がばらばらの扇は、辺の上の順で読むので全体を使う。"""
    # 対向がみな箱の外にいて、射影が端に潰れている状態
    spaced = _space_face_ts([-2.0, -1.0, 3.0, 4.0], 190.0, [1, 2, 3, 4])
    assert spaced[0] < 0.1
    assert spaced[-1] > 0.9


def test_order_follows_the_projection():
    """並びは相手の方角どおり。入れ替わると線が交差する。"""
    spaced = _space_face_ts([0.2, 0.5, 0.8], 190.0, [1, 2, 3])
    assert spaced == sorted(spaced)


def test_port_order_puts_the_lowest_number_first():
    """束の中の並びはポート番号順。若い番号が上（左）に来る。"""
    from app.services.topology_export import _port_key

    names = ["Gi0/10", "Gi0/2", "Gi0/1"]
    assert sorted(names, key=_port_key) == ["Gi0/1", "Gi0/2", "Gi0/10"]


def test_port_order_matches_the_screen_rule():
    """画面と同じ規則であること。ずれると図と画面で束の並びが食い違う。"""
    source = (
        Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "TopologyPage.tsx"
    ).read_text(encoding="utf-8")
    assert "export function comparePortNames" in source
    assert "comparePortNames(a.port, b.port)" in source


def test_a_port_name_clears_its_own_wire():
    """ポート名は線の上に乗らない。線を跨ぐと、細い線も文字も読みにくい。"""
    from app.services.topology_export import _LABEL_FONT, _SIDE_OFFSET

    # 文字の高さの半分ぶん離れていないと、線が文字の真ん中を通る
    assert _SIDE_OFFSET >= _LABEL_FONT / 2 + 3
