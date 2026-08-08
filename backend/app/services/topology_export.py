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
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session

from app.services.lifecycle import Severity
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
NODE_H = 62.0
MARGIN = 48.0
LEGEND_H = 62.0

# 日本語が入るので、環境にありそうなものを順に並べる
FONT = "'Yu Gothic', 'Hiragino Kaku Gothic ProN', Meiryo, 'Noto Sans JP', sans-serif"


def _esc(value: object) -> str:
    return escape(str(value if value is not None else ""))


def _clip(value: str | None, limit: int) -> str:
    text = value or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_svg(graph: TopologyGraph, *, title: str = "ネットワーク構成図") -> str:
    """トポロジを SVG にする。位置は画面で整えたものをそのまま使う。"""
    nodes = graph.nodes
    if not nodes:
        return _empty_svg(title)

    min_x = min(n.x for n in nodes)
    min_y = min(n.y for n in nodes)
    max_x = max(n.x for n in nodes) + NODE_W
    max_y = max(n.y for n in nodes) + NODE_H

    # 図の左上を原点側へ寄せ、余白と凡例・見出しの分を確保する
    dx = MARGIN - min_x
    dy = MARGIN + 34.0 - min_y
    width = max_x - min_x + MARGIN * 2
    height = max_y - min_y + MARGIN * 2 + 34.0 + LEGEND_H

    at = {n.device_id: (n.x + dx, n.y + dy) for n in nodes}
    parts: list[str] = []

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
    parts.append(
        f'<text x="{width - MARGIN:.0f}" y="30" font-size="11" fill="#6b6b6b" '
        f'text-anchor="end">{_esc(generated)} 時点 / レイヤ: {_esc(graph.layer)} / '
        f"{len(nodes)}台 {len(graph.edges)}本</text>"
    )

    # --- 結線（ノードより先に描いて背面に回す） ---
    for index, edge in enumerate(graph.edges):
        source, target = at.get(edge.source), at.get(edge.target)
        if source is None or target is None:
            continue
        x1, y1 = source[0] + NODE_W / 2, source[1] + NODE_H / 2
        x2, y2 = target[0] + NODE_W / 2, target[1] + NODE_H / 2

        # 両側から確認できた結線は実線、片側だけのものは破線にする
        dash = "" if edge.confirmed_both_ways else ' stroke-dasharray="6 4"'
        parts.append(
            f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
            f'stroke="#8a94a6" stroke-width="1.6"{dash}/>'
        )

        label = edge.label or _interface_label(edge)
        if label:
            # 同じ機器から何本も出ていると線が重なり、ラベルも重なって読めなくなる。
            # 線に沿う位置と、線に対して垂直な向きの両方をずらして散らす。
            along = 0.5 + ((index % 3) - 1) * 0.14
            mx = x1 + (x2 - x1) * along
            my = y1 + (y2 - y1) * along
            length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1.0)
            offset = ((index % 4) - 1.5) * 13
            mx += -(y2 - y1) / length * offset
            my += (x2 - x1) / length * offset

            text = _esc(_clip(label, 28))
            parts.append(
                f'<text x="{mx:.0f}" y="{my - 4:.0f}" font-size="10" fill="#5a6270" '
                f'text-anchor="middle" paint-order="stroke" stroke="#ffffff" '
                f'stroke-width="3">{text}</text>'
            )

    # --- 機器 ---
    for node in nodes:
        x, y = at[node.device_id]
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
        parts.append("</g>")

    parts.append(_legend(MARGIN, height - LEGEND_H + 10, width))
    parts.append("</svg>")
    return "\n".join(parts)


def _interface_label(edge) -> str:
    source, target = edge.source_interface or "", edge.target_interface or ""
    if source and target:
        return f"{source} — {target}"
    return source or target


def _legend(x: float, y: float, width: float) -> str:
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


def export_svg(db: Session, layer: str, *, title: str = "ネットワーク構成図") -> str:
    return render_svg(get_graph(db, layer), title=title)
