import csv
import io
import json
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.imports import ImportFieldInfo, ImportResult
from app.services import importer
from app.services.tablefile import Table, TableFileError, read_table

router = APIRouter(prefix="/import", tags=["import"])

DbSession = Annotated[Session, Depends(get_db)]

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

ERROR_COLUMN = "エラー内容"
ERROR_CSV_ENCODINGS = {"utf-8": "utf-8-sig", "shift_jis": "cp932"}


def _cell(value: object) -> str:
    """元ファイルの値をそのまま書き戻す。Excel の日付セルだけ読みやすく整える。"""
    if isinstance(value, datetime):
        return value.strftime("%Y/%m/%d")
    if isinstance(value, date):
        return value.strftime("%Y/%m/%d")
    return str(value or "")


@router.get("/fields", response_model=list[ImportFieldInfo])
def list_importable_fields() -> list[ImportFieldInfo]:
    """取り込み先として指定できる項目の一覧。画面の列マッピングUIで使う。"""
    return [
        ImportFieldInfo(
            name=name,
            label=importer.FIELD_LABELS[name],
            required=name in importer.REQUIRED_FIELDS,
            aliases=list(importer.FIELD_ALIASES.get(name, ())),
        )
        for name in importer.IMPORTABLE_FIELDS
    ]


@router.get("/template.csv")
def download_template() -> StreamingResponse:
    """記入用のテンプレートCSV。Excel でそのまま開けるよう BOM 付き UTF-8 で返す。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([importer.FIELD_LABELS[f] for f in importer.IMPORTABLE_FIELDS])
    writer.writerow(
        [
            "core-sw-01",
            "Cisco",
            "C9500-40X",
            "FOC2401A001",
            "スイッチ",
            "稼働中",
            "IOS-XE",
            "17.09.04a",
            "",
            "本社データセンター",
            "A-01",
            "U40-41",
            "10.0.0.11",
            "2021/04/01",
            "2027/01/31",
            "2030/01/31",
            "",
            "ネットワンシステムズ",
            "MC-2024-0011",
            "24x365 4時間駆付",
            "2024/04/01",
            "2027/03/31",
            "480000",
        ]
    )
    data = "﻿" + buffer.getvalue()
    return StreamingResponse(
        io.BytesIO(data.encode("utf-8")),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="netvision_import_template.csv"'},
    )


async def _run(
    db: Session,
    file: UploadFile,
    mapping: str | None,
    key_field: str,
    update_existing: bool,
    commit: bool,
) -> tuple[Table, importer.ImportOutcome]:
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "ファイルサイズが大きすぎます（上限 20MB）"
        )

    try:
        table = read_table(file.filename or "", content)
    except TableFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    mapping_override: dict[str, str] | None = None
    if mapping:
        try:
            parsed = json.loads(mapping)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "mapping は JSON で指定してください"
            ) from exc
        if not isinstance(parsed, dict):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "mapping は {列名: 項目名} の形式です"
            )
        unknown = [v for v in parsed.values() if v and v not in importer.IMPORTABLE_FIELDS]
        if unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"未知の項目が指定されています: {', '.join(unknown)}",
            )
        mapping_override = {str(k): str(v) if v else "" for k, v in parsed.items()}

    try:
        key = importer.KeyField(key_field)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "key_field は hostname または serial_number を指定してください",
        ) from exc

    outcome = importer.run_import(
        db,
        table.headers,
        table.rows,
        mapping_override=mapping_override,
        key_field=key,
        update_existing=update_existing,
        commit=commit,
    )
    return table, outcome


@router.post("/devices/preview", response_model=ImportResult)
async def preview_import(
    db: DbSession,
    file: UploadFile = File(description="CSV / TSV / Excel(.xlsx) の台帳ファイル"),
    mapping: str | None = Form(default=None, description='{"列名": "項目名"} の JSON'),
    key_field: str = Form(default="hostname"),
    update_existing: bool = Form(default=True),
) -> ImportResult:
    """取り込み内容を検証するだけで、DB は変更しない。"""
    _, outcome = await _run(db, file, mapping, key_field, update_existing, commit=False)
    return ImportResult.model_validate(outcome, from_attributes=True)


@router.post("/devices", response_model=ImportResult)
async def commit_import(
    db: DbSession,
    file: UploadFile = File(description="CSV / TSV / Excel(.xlsx) の台帳ファイル"),
    mapping: str | None = Form(default=None),
    key_field: str = Form(default="hostname"),
    update_existing: bool = Form(default=True),
) -> ImportResult:
    """取り込みを実行する。エラー行が1件でもあれば全体を反映しない（all-or-nothing）。"""
    _, outcome = await _run(db, file, mapping, key_field, update_existing, commit=True)
    return ImportResult.model_validate(outcome, from_attributes=True)


@router.post("/devices/errors.csv")
async def download_error_rows(
    db: DbSession,
    file: UploadFile = File(description="取り込みに失敗した行を抽出する対象のファイル"),
    mapping: str | None = Form(default=None),
    key_field: str = Form(default="hostname"),
    update_existing: bool = Form(default=True),
    encoding: str = Form(default="utf-8"),
) -> StreamingResponse:
    """エラーになった行だけを、元の列のまま CSV で書き出す。

    末尾に「エラー内容」列を付けるので、Excel で直してそのまま取り込み直せる。
    """
    if encoding not in ERROR_CSV_ENCODINGS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "encoding は utf-8 または shift_jis を指定してください",
        )

    table, outcome = await _run(db, file, mapping, key_field, update_existing, commit=False)

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([*table.headers, ERROR_COLUMN])

    for row in outcome.rows:
        if row.action is not importer.RowAction.ERROR:
            continue
        # row_number はヘッダを1行目とした番号なので、データ行の添字は -2
        original = table.rows[row.row_number - 2]
        messages = " / ".join(
            f"{importer.FIELD_LABELS.get(i.column or '', i.column)}: {i.message}"
            if i.column
            else i.message
            for i in row.issues
        )
        writer.writerow([_cell(original.get(h, "")) for h in table.headers] + [messages])

    data = buffer.getvalue().encode(ERROR_CSV_ENCODINGS[encoding], errors="replace")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="netvision_import_errors.csv"'},
    )
