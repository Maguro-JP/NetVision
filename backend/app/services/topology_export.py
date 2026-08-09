"""トポロジ図をファイルに書き出す。

設計書・報告書・稟議資料に貼るための出力。スクリーンショットで済ませると

- 印刷や拡大で潰れる（ラスタなので）
- 画面に入りきらない範囲が切れる
- 貼り直しのたびに撮り直しになる

ので、**ベクタ（SVG）で図そのものを出す**。SVG は Word / PowerPoint / Excel が
そのまま挿入でき（Office 2016 以降）、ブラウザでも開けて印刷にも耐える。

生成はサーバ側で行う。画面側の描画ライブラリに依存しないので、閉域配布の
バンドルに余計なものが増えず、出力内容をテストできる。
"""

from __future__ import annotations

from datetime import datetime
from math import atan2, degrees
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session

from app.services import portlabel
from app.services.lifecycle import Severity
from app.services.portlabel import MEDIA_COLOR, MEDIA_LABEL, Media
from app.services.topology import TopologyGraph, get_graph

# 画面の配色に合わせる。印刷しても潰れないよう、白地でも読める濃さにしてある
SEVERITY_COLOR: dict[str, str] = {
    str(Severity.EXPIRED): "#c0392b",
    str(Severity.CRITICAL): "#e07b39",
    str(Severity.WARNING): "#c9a227",
    str(Severity.OK): "#2d8a4e",
    str(Severity.UNKNOWN): "#7a7a7a",
}
SEVERITY_LABEL: dict[str, str] = {
    str(Severity.EXPIRED): "期限切れ",
    str(Severity.CRITICAL): "90日以内",
    str(Severity.WARNING): "1年以内",
    str(Severity.OK): "余裕あり",
    str(Severity.UNKNOWN): "情報未登録",
}

NODE_W = 190.0
# ポートの種類（GigabitEthernet など）を箱の中に1行入れる分の高さを含む。
# 線のラベルを番号だけにしても、機器を見れば何のポートか分かるようにするため
NODE_H = 78.0
MARGIN = 48.0
LEGEND_H = 62.0

# 日本語が入るので、環境にありそうなものを順に並べる
FONT = "'Yu Gothic', 'Hiragino Kaku Gothic ProN', Meiryo, 'Noto Sans JP', sans-serif"


def _esc(value: object) -> str:
    return escape(str(value if value is not None else ""))


def _clip(value: str | None, limit: int) -> str:
    text = value or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_svg(
    graph: TopologyGraph,
    *,
    title: str = "ネットワーク構成図",
    vlan: int | None = None,
    vrf: str | None = None,
    site_id: int | None = None,
    style: str = "detail",
    port_style: str = "short",
    along_line: bool = False,
) -> str:
    """トポロジを SVG にする。位置は画面で整えたものをそのまま使う。

    `vlan` / `vrf` / `site_id` を指定すると、それに載っている機器だけを描く。
    セグメント単位・VRF 単位・拠点単位で資料を作るときに使う。

    `style="simple"` は**機器名と結線だけ**の図。型番・IP・保守状態・VLAN を
    一切載せない。作業手順書や現場での指差し確認では、余計な情報が入っている方が
    読み違いのもとになるため、用途で図を分けられるようにしてある。
    """
    simple = style == "simple"
    nodes = graph.nodes
    if vlan is not None:
        nodes = [n for n in nodes if vlan in n.vlans]
    if vrf is not None:
        nodes = [n for n in nodes if vrf in n.vrfs]
    if site_id is not None:
        nodes = [n for n in nodes if n.site_id == site_id]
    if nodes is not graph.nodes:
        keep = {n.device_id for n in nodes}
        graph = TopologyGraph(
            layer=graph.layer,
            nodes=nodes,
            edges=[e for e in graph.edges if e.source in keep and e.target in keep],
        )
    if not nodes:
        return _empty_svg(title)

    min_x = min(n.x for n in nodes)
    min_y = min(n.y for n in nodes)
    max_x = max(n.x for n in nodes) + NODE_W
    max_y = max(n.y for n in nodes) + NODE_H

    # 図の左上を原点側へ寄せ、余白と凡例・見出しの分を確保する
    legend_h = 0.0 if simple else LEGEND_H
    dx = MARGIN - min_x
    dy = MARGIN + 34.0 - min_y
    width = max_x - min_x + MARGIN * 2
    height = max_y - min_y + MARGIN * 2 + 34.0 + legend_h

    at = {n.device_id: (n.x + dx, n.y + dy) for n in nodes}
    parts: list[str] = []
    # 凡例に出すのは実際に使われている種別だけ。使っていない色を並べても読み手が困る
    used_media: set[Media] = set()

    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="{FONT}">'
    )
    parts.append('<rect width="100%" height="100%" fill="#ffffff"/>')

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts.append(
        f'<text x="{MARGIN:.0f}" y="30" font-size="18" font-weight="700" fill="#1a1a1a">'
        f"{_esc(title)}</text>"
    )
    scope = f"{f' / VLAN {vlan}' if vlan is not None else ''}"
    scope += f"{f' / VRF {vrf}' if vrf else ''}"
    if site_id is not None:
        site_name = next((n.site_name for n in nodes if n.site_name), None)
        scope += f" / {site_name}" if site_name else ""
    if simple:
        # シンプル図でも「いつ時点の何台か」だけは残す。日付の無い構成図は
        # あとから見たときに信用できないため
        parts.append(
            f'<text x="{width - MARGIN:.0f}" y="30" font-size="11" fill="#6b6b6b" '
            f'text-anchor="end">{_esc(generated)} 時点{_esc(scope)} / '
            f"{len(nodes)}台 {len(graph.edges)}本</text>"
        )
    else:
        parts.append(
            f'<text x="{width - MARGIN:.0f}" y="30" font-size="11" fill="#6b6b6b" '
            f'text-anchor="end">{_esc(generated)} 時点 / レイヤ: {_esc(graph.layer)}'
            f"{_esc(scope)} / {len(nodes)}台 {len(graph.edges)}本</text>"
        )

    # --- 結線（ノードより先に描いて背面に回す） ---
    for index, edge in enumerate(graph.edges):
        source, target = at.get(edge.source), at.get(edge.target)
        if source is None or target is None:
            continue
        x1, y1 = source[0] + NODE_W / 2, source[1] + NODE_H / 2
        x2, y2 = target[0] + NODE_W / 2, target[1] + NODE_H / 2

        # 両側から確認できた結線は実線、片側だけのものは破線にする。
        # シンプル図では全部実線にする。破線は「取り込みの確からしさ」の話で、
        # 現場で見る図に混ぜると「未接続」と読み違えられるため
        dash = "" if (simple or edge.confirmed_both_ways) else ' stroke-dasharray="6 4"'
        stroke = "#4a5364" if simple else "#8a94a6"
        parts.append(
            f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
            f'stroke="{stroke}" stroke-width="1.6"{dash}/>'
        )

        if edge.label:
            # 人が付けた名前は線の中央に置く。ポート名とは性質が違うため
            parts.append(_center_label(x1, y1, x2, y2, edge.label, index))
        else:
            # ポート名は**それぞれの機器側**に置く。1本の線に2つ並べると
            # 「左が上位」のような規則を覚える必要が出て、図の向きが変われば
            # 逆に読んでしまう。ポートの隣にあれば規則は要らない
            parts.append(_port_label(x1, y1, x2, y2, edge.source_interface, port_style, along_line))
            parts.append(_port_label(x2, y2, x1, y1, edge.target_interface, port_style, along_line))
            for name in (edge.source_interface, edge.target_interface):
                if name:
                    used_media.add(portlabel.media_of(name))

    # --- 機器 ---
    for node in nodes:
        x, y = at[node.device_id]
        if simple:
            # 名前だけ。枠は輪郭が分かる程度の細さに留める
            parts.append(
                f'<g><rect x="{x:.0f}" y="{y:.0f}" width="{NODE_W:.0f}" '
                f'height="{NODE_H:.0f}" rx="7" fill="#ffffff" stroke="#4a5364" '
                f'stroke-width="1.6"/>'
                f'<text x="{x + NODE_W / 2:.0f}" y="{y + NODE_H / 2 + 5:.0f}" font-size="14" '
                f'font-weight="700" fill="#1a1a1a" text-anchor="middle">'
                f"{_esc(_clip(node.hostname, 22))}</text></g>"
            )
            continue

        color = SEVERITY_COLOR.get(str(node.severity), SEVERITY_COLOR[str(Severity.UNKNOWN)])
        parts.append(
            f'<g><rect x="{x:.0f}" y="{y:.0f}" width="{NODE_W:.0f}" height="{NODE_H:.0f}" '
            f'rx="7" fill="#f7f9fc" stroke="{color}" stroke-width="2"/>'
            # 状態が一目で分かるよう左端に色帯を置く（白黒印刷でも位置で区別できる）
            f'<rect x="{x:.0f}" y="{y:.0f}" width="5" height="{NODE_H:.0f}" '
            f'rx="2" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{x + 14:.0f}" y="{y + 22:.0f}" font-size="13" font-weight="700" '
            f'fill="#1a1a1a">{_esc(_clip(node.hostname, 22))}</text>'
        )
        parts.append(
            f'<text x="{x + 14:.0f}" y="{y + 38:.0f}" font-size="10" fill="#5a6270">'
            f"{_esc(_clip(f'{node.vendor} {node.model}'.strip(), 26))}</text>"
        )
        detail = node.mgmt_ip or node.site_name or ""
        if detail:
            parts.append(
                f'<text x="{x + 14:.0f}" y="{y + 52:.0f}" font-size="10" fill="#7a828f">'
                f"{_esc(_clip(detail, 26))}</text>"
            )
        if node.port_kinds:
            parts.append(_port_kinds_line(x + 14, y + 68, node.port_kinds))
        parts.append("</g>")

    if not simple:
        parts.append(_legend(MARGIN, height - LEGEND_H + 10, width, used_media))
    parts.append("</svg>")
    return "\n".join(parts)


def _port_kinds_line(x: float, y: float, kinds: list[str]) -> str:
    """機器の箱に、その機器で使われているポートの種類を書く。

    線のラベルを番号だけにすると何のポートか分からなくなる。色でも表せるが、
    色は凡例を探しに行かないと読めない。**機器の隣に名前があれば視線が動かない**。
    ラベルと同じ色を使うので、種類が複数あっても線と対応が付く。
    """
    spans: list[str] = []
    budget = 36  # 箱の幅（190px）に 9px の文字で収まるおおよその数
    for index, kind in enumerate(kinds):
        if budget <= 0:
            spans.append('<tspan fill="#7a828f"> ほか</tspan>')
            break
        color = MEDIA_COLOR.get(portlabel.media_of(kind), "#5a6270")
        separator = " / " if index else ""
        text = _clip(kind, budget)
        budget -= len(text) + len(separator)
        spans.append(f'<tspan fill="{color}">{_esc(separator + text)}</tspan>')

    return f'<text x="{x:.0f}" y="{y:.0f}" font-size="9">{"".join(spans)}</text>'


# ノード枠から、その先どのくらい離してラベルを置くか
_LABEL_GAP = 10.0
# 線と垂直な向きへのずらし幅。線に文字が重なると両方読めなくなる
_SIDE_OFFSET = 7.0


def _port_label(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    name: str,
    style: str,
    along_line: bool,
) -> str:
    """(x1,y1) 側の機器のポート名を、その機器のすぐ外に置く。

    線の向きは (x1,y1) → (x2,y2)。ノードの矩形と線の交点まで進んでから
    少し外に出した位置に置くので、**どのポートがどちらの機器のものか**が
    位置だけで分かる。左右・上下の規則を覚える必要が無くなる。

    ずらす向きは**その端から見た左側**で固定する。両端では線の向きが逆になるので、
    結果として2つのラベルは線の反対側に出る。機器の間隔が狭くて両端のラベルが
    近づいても重ならない。
    """
    if not name:
        return ""

    dx, dy = x2 - x1, y2 - y1
    length = max((dx * dx + dy * dy) ** 0.5, 1.0)
    ux, uy = dx / length, dy / length

    # ノードの枠を出るまでの距離。矩形の中心から見た交点までを求める
    edge_distance = _exit_distance(ux, uy)
    px = x1 + ux * (edge_distance + _LABEL_GAP)
    py = y1 + uy * (edge_distance + _LABEL_GAP)

    # 線に重ならないよう、線と垂直な向きへずらす
    px += -uy * _SIDE_OFFSET
    py += ux * _SIDE_OFFSET

    # そのまま出す指定のときは切り詰めを緩める。長いと分かっていて選んだ表示なので
    limit = 30 if style == "full" else 18
    text = _esc(_clip(portlabel.format(name, style), limit))
    color = MEDIA_COLOR.get(portlabel.media_of(name), "#5a6270")

    if along_line:
        # 線に沿わせる。ただし上下逆さまにはしない（読めなくなるため半回転させる）
        angle = (degrees(atan2(dy, dx)) + 180) % 360 - 180  # -180〜180 に正規化
        flipped = angle > 90 or angle <= -90
        if flipped:
            angle += -180 if angle > 0 else 180
        transform = f' transform="rotate({angle:.1f} {px:.1f} {py:.1f})"'
        # 半回転させた側は、回した後の座標系では逆向きに伸ばすことになる
        anchor = "end" if flipped else "start"
    else:
        transform = ""
        # 線が右向きならラベルは右へ、左向きなら左へ出す
        anchor = "start" if ux > 0.2 else ("end" if ux < -0.2 else "middle")

    return (
        f'<text x="{px:.1f}" y="{py - 3:.1f}" font-size="10" fill="{color}" '
        f'text-anchor="{anchor}" paint-order="stroke" stroke="#ffffff" '
        f'stroke-width="3"{transform}>{text}</text>'
    )


def _exit_distance(ux: float, uy: float) -> float:
    """ノードの中心から、その向きで枠を出るまでの距離。"""
    half_w, half_h = NODE_W / 2, NODE_H / 2
    tx = half_w / abs(ux) if abs(ux) > 1e-6 else float("inf")
    ty = half_h / abs(uy) if abs(uy) > 1e-6 else float("inf")
    return min(tx, ty)


def _center_label(x1: float, y1: float, x2: float, y2: float, label: str, index: int) -> str:
    """人が付けた線の名前。同じ機器から何本も出ると重なるので散らす。"""
    along = 0.5 + ((index % 3) - 1) * 0.14
    mx = x1 + (x2 - x1) * along
    my = y1 + (y2 - y1) * along
    length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1.0)
    offset = ((index % 4) - 1.5) * 13
    mx += -(y2 - y1) / length * offset
    my += (x2 - x1) / length * offset

    return (
        f'<text x="{mx:.0f}" y="{my - 4:.0f}" font-size="10" fill="#5a6270" '
        f'text-anchor="middle" paint-order="stroke" stroke="#ffffff" '
        f'stroke-width="3">{_esc(_clip(label, 28))}</text>'
    )


def _legend(x: float, y: float, width: float, media: set[Media] | None = None) -> str:
    parts = [
        f'<line x1="{x:.0f}" y1="{y - 12:.0f}" x2="{width - x:.0f}" y2="{y - 12:.0f}" '
        f'stroke="#dfe3ea" stroke-width="1"/>',
        f'<text x="{x:.0f}" y="{y + 4:.0f}" font-size="10" fill="#6b6b6b">保守・EoL の状態</text>',
    ]
    cursor = x + 96
    for key, label in SEVERITY_LABEL.items():
        color = SEVERITY_COLOR[key]
        parts.append(
            f'<rect x="{cursor:.0f}" y="{y - 5:.0f}" width="10" height="10" rx="2" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{cursor + 15:.0f}" y="{y + 4:.0f}" font-size="10" fill="#4a4a4a">'
            f"{_esc(label)}</text>"
        )
        cursor += 22 + len(label) * 11

    parts.append(
        f'<text x="{x:.0f}" y="{y + 24:.0f}" font-size="10" fill="#6b6b6b">'
        "実線 = 両側の機器から確認できた結線 / 破線 = 片側のみ</text>"
    )

    # ポート名を色分けしているので、何色が何かを図の中に残す。
    # 凡例の無い色分けは、その図だけ見た人には読めない
    if media:
        cursor = x + 250
        parts.append(
            f'<text x="{cursor:.0f}" y="{y + 24:.0f}" font-size="10" fill="#6b6b6b">'
            "ポート種別</text>"
        )
        cursor += 60
        for item in sorted(media, key=lambda m: list(MEDIA_LABEL).index(m)):
            label = MEDIA_LABEL[item]
            parts.append(
                f'<text x="{cursor:.0f}" y="{y + 24:.0f}" font-size="10" '
                f'fill="{MEDIA_COLOR[item]}" font-weight="700">{_esc(label)}</text>'
            )
            cursor += 18 + len(label) * 8

    return "\n".join(parts)


def _empty_svg(title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="520" height="120" '
        f'viewBox="0 0 520 120" font-family="{FONT}">'
        '<rect width="100%" height="100%" fill="#ffffff"/>'
        f'<text x="24" y="42" font-size="16" font-weight="700">{_esc(title)}</text>'
        '<text x="24" y="70" font-size="12" fill="#6b6b6b">'
        "表示できる機器がありません。Config を取り込むと結線が推定されます。</text>"
        "</svg>"
    )


def export_svg(
    db: Session,
    layer: str,
    *,
    title: str = "ネットワーク構成図",
    vlan: int | None = None,
    vrf: str | None = None,
    site_id: int | None = None,
    style: str = "detail",
    port_style: str = "short",
    along_line: bool = False,
) -> str:
    return render_svg(
        get_graph(db, layer),
        title=title,
        vlan=vlan,
        vrf=vrf,
        site_id=site_id,
        style=style,
        port_style=port_style,
        along_line=along_line,
    )
