"""構成図と表を1つの Excel にまとめる。

図は、画面や SVG と同じものを Excel の図形として置く。以前はセルの罫線で
別の図（上位を左、下位を右に並べ直したもの）を描いていたが、それだと同じ
台帳から2つの違う図が出ることになる。どちらを信じればいいのか分からない図は
現場では使われない。

図形にしておけば、Excel の上で掴んで動かせるし、色も変えられる。罫線と違って
行を足しても図は崩れない。図の中身は `topology_export` が書いた SVG を
`xlsxdraw` が図形へ置き換えたもので、線1本まで同じものが入る。

表（結線表・ポート一覧）は、図では読めないことのために付ける。並べ替えと
絞り込みが要る場面（作業指示・工事依頼・検収の突合）で使う。
"""

from __future__ import annotations

import io

from sqlalchemy.orm import Session

from app.services import (
    topology_export,
    topology_ports,
    topology_table,
    topology_view,
    xlsxdraw,
)
from app.services.topology import DEFAULT_LAYER, auto_scoped_ids, load_diagram
from app.services.xlsxsafe import row_values


def to_xlsx(
    db: Session,
    layer: str = DEFAULT_LAYER,
    *,
    site_id: int | None = None,
    scope: str = "peers",
    view: topology_view.ViewGeometry | None = None,
) -> bytes:
    """構成図・結線表・ポート一覧・ポート割付を1つのファイルにまとめる。

    ボタンを分けるより、1つ開けば全部入っている方が配りやすい。
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    # 絞らずに頼まれた全体図は、画面と同じく描ける範囲に絞る（全台だと終わらない）
    only = auto_scoped_ids(db, layer, site_id=site_id)
    graph = load_diagram(db, layer, site_id=site_id, scope=scope, only=only)
    # 図は SVG と同じものを使う。ここでもう一度並べ直すと、同じ台帳から
    # 違う図が2つ出ることになる
    scope_name = ""
    if site_id is not None:
        scope_name = next((n.site_name for n in graph.nodes if not n.outside and n.site_name), "")
    svg = topology_export.render_svg(
        graph,
        title=f"構成図{f' - {scope_name}' if scope_name else ''}",
        site_id=site_id,
        view=view,
    )
    shapes = xlsxdraw.shapes_from_svg(svg)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "構成図"
    # 図形は紙の上に浮くので、升目は消しておく。見出しも時点も図の中にある
    sheet.sheet_view.showGridLines = False
    _fit_to_page(sheet, shapes, get_column_letter)

    # --- 表のシート。同じファイルに入れておけば、配るのは1つで済む ---
    _append_table(
        workbook.create_sheet("結線表"),
        topology_table.HEADERS,
        topology_table.rows(db, layer, site_id=site_id),
        Font,
        get_column_letter,
    )
    _append_table(
        workbook.create_sheet("ポート一覧"),
        topology_ports.HEADERS,
        topology_ports.rows(db, site_id=site_id),
        Font,
        get_column_letter,
    )

    buffer = io.BytesIO()
    workbook.save(buffer)
    # openpyxl は自由な図形を書けないので、出来上がったファイルへ足す
    return xlsxdraw.attach(buffer.getvalue(), shapes)


#: 既定のセルの大きさ（px）。図がはみ出さない印刷範囲を決めるのに使う
_CELL_W = 64.0
_CELL_H = 20.0


def _fit_to_page(sheet, shapes, get_column_letter) -> None:
    """図が1枚に収まるようにする。

    構成図は「画面で確かめて、印刷して配る」ために出す。既定のままだと
    A4 の幅で切られて、線が次の紙へ渡ってしまう。図の入る範囲を印刷範囲に
    決めて、1枚に縮める。
    """
    from openpyxl.worksheet.properties import PageSetupProperties

    if not shapes:
        return
    right = max(shape.x + shape.width for shape in shapes)
    bottom = max(shape.y + shape.height for shape in shapes)
    columns = max(1, int(right // _CELL_W) + 1)
    rows = max(1, int(bottom // _CELL_H) + 1)
    # 印刷範囲を決めると、Excel は普通の画面に点線の枠を出す。図の紙に
    # 大きな枠が乗っているように見えるので、範囲は決めない。代わりに、
    # 図の右下にあたるセルを作って「ここまでが中身」と伝える
    sheet.cell(row=rows, column=columns).value = ""
    # 横に長い図が多い。縦置きだと、縮めた結果が読めない大きさになる
    sheet.page_setup.orientation = "landscape" if right >= bottom else "portrait"
    sheet.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 1


def _append_table(sheet, headers, rows, Font, get_column_letter) -> None:
    sheet.append(list(headers))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(row_values(row))
    for index, header in enumerate(headers, start=1):
        width = max(len(header) * 2 + 2, 12)
        for row in rows:
            width = max(width, min(len(str(row[index - 1])) + 2, 40))
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "A2"
    if rows:
        sheet.auto_filter.ref = sheet.dimensions
