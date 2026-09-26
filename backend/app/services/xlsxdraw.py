"""図を Excel の図形として置く。

Excel に構成図を入れるとき、セルの罫線で描くやり方もある。だが罫線の図は
画面や SVG とは別物になり、同じ台帳から2つの違う図が出ることになる。
どちらを信じればいいのか分からない図は、現場では使われない。

そこで、SVG に描いた図をそのまま図形（DrawingML）に置き換える。線は線、
箱は箱として入るので、Excel の上で掴んで動かせるし、色も変えられる。
セルの罫線と違い、行を足しても図は崩れない。

読み取るのは、こちらが書いた SVG だけである。どんな SVG でも読める必要は
無いので、使っている書き方（rect / line / polyline / text）に絞る。
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field

#: 1px を EMU（Office の長さの単位）へ。96dpi のときの決まった比
EMU = 9525

_SVG_NS = "{http://www.w3.org/2000/svg}"


@dataclass
class Shape:
    """図形1つ。位置は px（SVG と同じ座標）で持ち、書き出すときに EMU へ直す。"""

    kind: str  # rect / line / text
    x: float
    y: float
    width: float
    height: float
    fill: str | None = None
    stroke: str | None = None
    stroke_width: float = 1.0
    dashed: bool = False
    #: 角の丸み（px）。0 なら角のある箱
    radius: float = 0.0
    #: line のときの折れ点（x, y の並び）。箱の左上からの相対にして持つ
    points: list[tuple[float, float]] = field(default_factory=list)
    text: str = ""
    font_size: float = 11.0
    bold: bool = False
    align: str = "l"  # l / ctr / r
    #: 回転（度）。ポート名を線に沿わせたときに付く
    rotation: float = 0.0


def shapes_from_svg(svg: str) -> list[Shape]:
    """こちらが書いた SVG を、図形の並びにする。"""
    root = ET.fromstring(svg)
    shapes: list[Shape] = []
    for element in root.iter():
        tag = element.tag.replace(_SVG_NS, "")
        if tag == "rect":
            shape = _rect(element)
        elif tag == "polyline":
            shape = _polyline(element)
        elif tag == "line":
            shape = _line(element)
        elif tag == "text":
            shape = _text(element)
        else:
            continue
        if shape is not None:
            shapes.append(shape)
    return shapes


def _number(element: ET.Element, name: str, fallback: float = 0.0) -> float:
    value = element.get(name)
    try:
        return float(value) if value is not None else fallback
    except ValueError:
        return fallback


def _color(value: str | None) -> str | None:
    """`#1a1a1a` を Office の `1A1A1A` に。読めない書き方は色なしにする。"""
    if not value or value == "none":
        return None
    found = re.fullmatch(r"#([0-9a-fA-F]{6})", value.strip())
    return found.group(1).upper() if found else None


def _rect(element: ET.Element) -> Shape | None:
    width = element.get("width") or ""
    if width.endswith("%"):
        return None  # 下地の白。Excel の背景は元から白い
    return Shape(
        kind="rect",
        x=_number(element, "x"),
        y=_number(element, "y"),
        width=_number(element, "width"),
        height=_number(element, "height"),
        fill=_color(element.get("fill")),
        stroke=_color(element.get("stroke")),
        stroke_width=_number(element, "stroke-width", 1.0),
        dashed=bool(element.get("stroke-dasharray")),
        radius=_number(element, "rx"),
    )


def _polyline(element: ET.Element) -> Shape | None:
    raw = element.get("points") or ""
    points: list[tuple[float, float]] = []
    for pair in raw.split():
        if "," not in pair:
            continue
        x, _, y = pair.partition(",")
        try:
            points.append((float(x), float(y)))
        except ValueError:
            continue
    if len(points) < 2:
        return None
    return _as_line(points, element)


def _line(element: ET.Element) -> Shape | None:
    points = [
        (_number(element, "x1"), _number(element, "y1")),
        (_number(element, "x2"), _number(element, "y2")),
    ]
    return _as_line(points, element)


def _as_line(points: list[tuple[float, float]], element: ET.Element) -> Shape:
    min_x = min(x for x, _ in points)
    min_y = min(y for _, y in points)
    return Shape(
        kind="line",
        x=min_x,
        y=min_y,
        # 太さ0の箱は Excel が潰すので、線1本ぶんだけ持たせる
        width=max(max(x for x, _ in points) - min_x, 1.0),
        height=max(max(y for _, y in points) - min_y, 1.0),
        stroke=_color(element.get("stroke")) or "4A5364",
        stroke_width=_number(element, "stroke-width", 1.0),
        dashed=bool(element.get("stroke-dasharray")),
        points=[(x - min_x, y - min_y) for x, y in points],
    )


_ANCHOR = {"start": "l", "middle": "ctr", "end": "r"}


def _text_of(element: ET.Element) -> str:
    """文字の中身。`<title>`（マウスを乗せたときの説明）は図には出さない。

    ポート名は `<title>GigabitEthernet0/1</title>Gi0/1` の形で書いてある。
    element.text だけ見ると空になり、線のそばの名前が丸ごと落ちる。
    """
    parts = [element.text or ""]
    for child in element:
        if not child.tag.endswith("title"):
            parts.append(child.text or "")
        parts.append(child.tail or "")
    return "".join(parts).strip()


def _text(element: ET.Element) -> Shape | None:
    text = _text_of(element)
    if not text:
        return None
    size = _number(element, "font-size", 11.0)
    align = _ANCHOR.get(element.get("text-anchor") or "start", "l")
    x = _number(element, "x")
    y = _number(element, "y")
    # 文字の幅は測れない。見込みで箱を作ると、font が違うだけでずれる。
    # 広い箱を置いて、寄せ方（左端・中央・右端）で位置を決める。箱には枠も
    # 塗りも無いので、隣と重なっても見た目には出ない。
    # 図の左端に近いところでは、はみ出さない幅に詰める。負の座標に置くと、
    # その文字だけ丸ごと消える
    room = 600.0
    if align == "ctr":
        room = max(2.0, min(room, x * 2))
        left = x - room / 2
    elif align == "r":
        room = max(2.0, min(room, x))
        left = x - room
    else:
        left = x
    return Shape(
        kind="text",
        x=left,
        # SVG の y は文字の下端。箱は上端で置くので、1行ぶん上へ
        y=y - size,
        width=room,
        height=size * 1.4,
        text=text,
        font_size=size,
        bold=(element.get("font-weight") or "") in {"600", "700", "bold"},
        align=align,
        fill=_color(element.get("fill")),
        stroke=None,
        rotation=_rotation(element),
    )


def _rotation(element: ET.Element) -> float:
    found = re.search(r"rotate\(\s*(-?[\d.]+)", element.get("transform") or "")
    return float(found.group(1)) if found else 0.0


# ---------------------------------------------------------------------------
# Excel の図形として書き出す
# ---------------------------------------------------------------------------


#: XML 1.0 に書けない文字。SVG 側で落としてあるが、図形の XML も同じ理由で壊れるので重ねて落とす
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _escape(value: str) -> str:
    return (
        _NOT_XML.sub("", value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _px(value: float) -> int:
    return int(round(value * EMU))


def _outline(shape: Shape) -> str:
    if not shape.stroke:
        return "<a:ln><a:noFill/></a:ln>"
        # 枠線なし。塗りだけの帯（機器の色分け）で使う
    dash = '<a:prstDash val="dash"/>' if shape.dashed else ""
    return (
        f'<a:ln w="{_px(shape.stroke_width)}" cap="flat">'
        f'<a:solidFill><a:srgbClr val="{shape.stroke}"/></a:solidFill>{dash}</a:ln>'
    )


def _fill(shape: Shape) -> str:
    if not shape.fill:
        return "<a:noFill/>"
    return f'<a:solidFill><a:srgbClr val="{shape.fill}"/></a:solidFill>'


def _body(shape: Shape) -> str:
    if shape.kind != "text":
        return ""
    color = shape.fill or "1A1A1A"
    return (
        '<xdr:txBody><a:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0" '
        'anchor="t"><a:noAutofit/></a:bodyPr><a:lstStyle/>'
        f'<a:p><a:pPr algn="{shape.align}"/><a:r><a:rPr lang="ja-JP" '
        f'sz="{int(shape.font_size * 100)}" b="{1 if shape.bold else 0}">'
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
        '<a:latin typeface="Yu Gothic"/><a:ea typeface="Yu Gothic"/></a:rPr>'
        f"<a:t>{_escape(shape.text)}</a:t></a:r></a:p></xdr:txBody>"
    )


def _geometry(shape: Shape) -> str:
    if shape.kind == "line":
        width = max(_px(shape.width), 1)
        height = max(_px(shape.height), 1)
        moves = [
            f"<a:{'moveTo' if index == 0 else 'lnTo'}>"
            f'<a:pt x="{_px(x)}" y="{_px(y)}"/></a:{"moveTo" if index == 0 else "lnTo"}>'
            for index, (x, y) in enumerate(shape.points)
        ]
        return (
            "<a:custGeom><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>"
            f'<a:rect l="0" t="0" r="r" b="b"/><a:pathLst>'
            f'<a:path w="{width}" h="{height}" fill="none">{"".join(moves)}</a:path>'
            "</a:pathLst></a:custGeom>"
        )
    if shape.kind == "text":
        return '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
    if not shape.radius:
        return '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
    # 丸みは、短い辺に対する割合で指定する（10万分率）
    short = max(min(shape.width, shape.height), 1.0)
    adj = min(50000, int(shape.radius / short * 100000))
    return (
        '<a:prstGeom prst="roundRect"><a:avLst>'
        f'<a:gd name="adj" fmla="val {adj}"/></a:avLst></a:prstGeom>'
    )


def _shape_xml(shape: Shape, index: int) -> str:
    width = max(_px(shape.width), 1)
    height = max(_px(shape.height), 1)
    rotation = f' rot="{int(shape.rotation * 60000)}"' if shape.rotation else ""
    text_box = ' txBox="1"' if shape.kind == "text" else ""
    fill = "<a:noFill/>" if shape.kind == "text" else _fill(shape)
    line = "<a:ln><a:noFill/></a:ln>" if shape.kind == "text" else _outline(shape)
    return (
        "<xdr:absoluteAnchor>"
        f'<xdr:pos x="{_px(shape.x)}" y="{_px(shape.y)}"/>'
        f'<xdr:ext cx="{width}" cy="{height}"/>'
        '<xdr:sp macro="" textlink="">'
        f'<xdr:nvSpPr><xdr:cNvPr id="{index + 2}" name="{shape.kind} {index + 1}"/>'
        f"<xdr:cNvSpPr{text_box}/></xdr:nvSpPr>"
        f'<xdr:spPr><a:xfrm{rotation}><a:off x="{_px(shape.x)}" y="{_px(shape.y)}"/>'
        f'<a:ext cx="{width}" cy="{height}"/></a:xfrm>'
        f"{_geometry(shape)}{fill}{line}</xdr:spPr>"
        f"{_body(shape)}"
        "</xdr:sp><xdr:clientData/></xdr:absoluteAnchor>"
    )


def drawing_xml(shapes: list[Shape]) -> str:
    body = "".join(_shape_xml(shape, index) for index, shape in enumerate(shapes))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f"{body}</xdr:wsDr>"
    )


#: 図形を載せる worksheet の名前（zip の中の位置）
_SHEET_PATH = "xl/worksheets/sheet1.xml"


def attach(book: bytes, shapes: list[Shape], sheet_path: str = _SHEET_PATH) -> bytes:
    """openpyxl が書いたファイルに、図形の載った紙を足す。

    openpyxl は自由な図形を書けないので、出来上がった zip に足す。
    足すのは3か所（図形そのもの・紙からの参照・中身の一覧）だけで、
    ほかの紙（結線表・ポート一覧）はそのまま残る。
    """
    if not shapes:
        return book

    with zipfile.ZipFile(io.BytesIO(book)) as source:
        items = {name: source.read(name) for name in source.namelist()}

    number = 1
    while f"xl/drawings/drawing{number}.xml" in items:
        number += 1
    drawing = f"xl/drawings/drawing{number}.xml"
    items[drawing] = drawing_xml(shapes).encode("utf-8")

    # 紙から図形への参照
    rels_path = sheet_path.replace("worksheets/", "worksheets/_rels/") + ".rels"
    rels = items.get(rels_path)
    relation_id = "rIdDrawing1"
    if rels is None:
        items[rels_path] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="{relation_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" '
            f'Target="../drawings/drawing{number}.xml"/></Relationships>'
        ).encode()
    else:
        text = rels.decode("utf-8")
        items[rels_path] = text.replace(
            "</Relationships>",
            f'<Relationship Id="{relation_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" '
            f'Target="../drawings/drawing{number}.xml"/></Relationships>',
        ).encode("utf-8")

    sheet = items[sheet_path].decode("utf-8")
    if "<drawing " not in sheet:
        # r: の名前空間は、openpyxl が図や画像を書いていない紙には無い。
        # 宣言せずに r:id を書くと、Excel も LibreOffice もファイルごと開けない
        if "xmlns:r=" not in sheet:
            sheet = sheet.replace(
                "<worksheet ",
                '<worksheet xmlns:r="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships" ',
                1,
            )
        sheet = sheet.replace("</worksheet>", f'<drawing r:id="{relation_id}"/></worksheet>')
        items[sheet_path] = sheet.encode("utf-8")

    types = items["[Content_Types].xml"].decode("utf-8")
    if drawing not in types:
        types = types.replace(
            "</Types>",
            f'<Override PartName="/{drawing}" '
            'ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/></Types>',
        )
        items["[Content_Types].xml"] = types.encode("utf-8")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        # 中身の一覧は先頭に置く。後ろにあると、読めないと言う道具がある
        order = ["[Content_Types].xml"] + [n for n in items if n != "[Content_Types].xml"]
        for name in order:
            target.writestr(name, items[name])
    return out.getvalue()
