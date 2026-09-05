"""CSV / Excel ファイルを「ヘッダ + 行の辞書」に読み込むユーティリティ。

既存の Excel 台帳をそのまま取り込めるようにするのが目的なので、
日本語環境で実際に出てくる形式（CP932 の CSV、BOM 付き UTF-8、.xlsx）を許容する。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime

from app.services import textencoding

#: 判別は textencoding にまとめてある。cp932 と EUC-JP はお互いのバイト列を
#: エラー無しに読めるので、順に試して最初に通ったものを採ると化ける
CSV_ENCODINGS = textencoding.ENCODINGS


class TableFileError(ValueError):
    """ファイルとして読めなかった場合に送出する。"""


@dataclass
class Table:
    headers: list[str]
    rows: list[dict[str, object]] = field(default_factory=list)


ZIP_MAGIC = b"PK\x03\x04"


def read_table(filename: str, content: bytes) -> Table:
    name = (filename or "").lower()
    # .xlsx は ZIP なので、拡張子が付いていない/変えられていても中身で判別できる
    if content.startswith(ZIP_MAGIC) or name.endswith((".xlsx", ".xlsm")):
        return _read_excel(content)
    if name.endswith((".csv", ".txt", ".tsv")):
        return _read_csv(content, delimiter="\t" if name.endswith(".tsv") else None)
    raise TableFileError("対応していない拡張子です（.csv / .tsv / .xlsx に対応しています）")


def _decode(content: bytes) -> str:
    # 表は、化けた文字が1つ入るだけで台帳として使えなくなる。読めた分だけ
    # 通すより、読めなかったと言って保存し直してもらうほうがよい
    text = textencoding.best_effort(content)
    if text is None:
        raise TableFileError(
            "文字コードを判別できませんでした（UTF-8 または Shift_JIS で保存してください）"
        )
    return text


def _read_csv(content: bytes, delimiter: str | None = None) -> Table:
    text = _decode(content)
    if not text.strip():
        raise TableFileError("ファイルが空です")

    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",\t;").delimiter
        except csv.Error:
            delimiter = ","

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    records = [row for row in reader if any(cell.strip() for cell in row)]
    if not records:
        raise TableFileError("データ行がありません")

    headers = [cell.strip() for cell in records[0]]
    rows: list[dict[str, object]] = []
    for record in records[1:]:
        row: dict[str, object] = {}
        for index, header in enumerate(headers):
            row[header] = record[index].strip() if index < len(record) else ""
        rows.append(row)
    return Table(headers=headers, rows=rows)


def _read_excel(content: bytes) -> Table:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - 依存が入っていない環境向け
        raise TableFileError("Excel の読み込みには openpyxl が必要です") from exc

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise TableFileError(f"Excel ファイルを読めませんでした: {exc}") from exc

    sheet = workbook.active
    if sheet is None:
        raise TableFileError("シートが見つかりません")

    records = [row for row in sheet.iter_rows(values_only=True) if any(_clean(c) for c in row)]
    workbook.close()
    if not records:
        raise TableFileError("データ行がありません")

    headers = [_clean(cell) for cell in records[0]]
    rows: list[dict[str, object]] = []
    for record in records[1:]:
        row: dict[str, object] = {}
        for index, header in enumerate(headers):
            if not header:
                continue
            value = record[index] if index < len(record) else None
            # Excel の日付セルは datetime で返るので、日付として扱えるようそのまま渡す
            row[header] = value if isinstance(value, (datetime, date)) else _clean(value)
        rows.append(row)
    return Table(headers=[h for h in headers if h], rows=rows)


def _clean(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
