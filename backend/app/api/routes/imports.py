import csv
import io
import json
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.schemas.imports import ImportFieldInfo, ImportResult
from app.services import importer, linkimport
from app.services.tablefile import Table, TableFileError, read_table

router = APIRouter(prefix="/import", tags=["import"])

DbSession = Annotated[Session, Depends(get_db)]

ERROR_COLUMN = "エラー内容"
ERROR_CSV_ENCODINGS = {"utf-8": "utf-8-sig", "shift_jis": "cp932"}


def _max_upload_bytes() -> int:
    return settings.max_import_bytes


def _too_large_message() -> str:
    limit_mb = max(1, _max_upload_bytes() // (1024 * 1024))
    return f"ファイルサイズが大きすぎます（上限 {limit_mb}MB）"


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
    """記入用のテンプレートCSV。Excel でそのまま開けるよう BOM 付き UTF-8 で返す。

    列の並びは IMPORTABLE_FIELDS と同じ。管理IDを入れておけば、次から
    ホスト名を変えても同じ機器として更新できる。
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([importer.FIELD_LABELS[f] for f in importer.IMPORTABLE_FIELDS])
    # 列と値の位置をずらさない。内部IDは新規では空、管理IDはサンプルを入れる
    sample = {name: "" for name in importer.IMPORTABLE_FIELDS}
    sample.update(
        {
            "code": "ASSET-1001",
            "hostname": "core-sw-01",
            "vendor": "Cisco",
            "model": "C9500-40X",
            "serial_number": "FOC2401A001",
            "device_type": "スイッチ",
            "status": "稼働中",
            "os_name": "IOS-XE",
            "os_version": "17.09.04a",
            "site": "本社データセンター",
            "site_code": "TOK-DC1",
            "rack": "A-01",
            "rack_unit": "U40-41",
            "mgmt_ip": "10.0.0.11",
            "install_date": "2021/04/01",
            "eos_date": "2027/01/31",
            "eol_date": "2030/01/31",
            "contract_provider": "ネットワンシステムズ",
            "contract_number": "MC-2024-0011",
            "contract_service_level": "24x365 4時間駆付",
            "contract_start_date": "2024/04/01",
            "contract_end_date": "2027/03/31",
            "contract_annual_cost": "480000",
        }
    )
    writer.writerow([sample[name] for name in importer.IMPORTABLE_FIELDS])
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
    *,
    keep_rows: int | None = importer._ROW_SAMPLE,
) -> tuple[Table, importer.ImportOutcome]:
    content = await file.read()
    if len(content) > _max_upload_bytes():
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, _too_large_message())

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
            "key_field は code / hostname / serial_number を指定してください",
        ) from exc

    outcome = importer.run_import(
        db,
        table.headers,
        table.rows,
        mapping_override=mapping_override,
        key_field=key,
        update_existing=update_existing,
        commit=commit,
        keep_rows=keep_rows,
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

    table, outcome = await _run(
        db, file, mapping, key_field, update_existing, commit=False, keep_rows=None
    )

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


class LinkImportIssueRead(BaseModel):
    row_number: int
    message: str


class LinkImportResultRead(BaseModel):
    total_rows: int
    linked: int
    already: int
    pending: int
    issues: list[LinkImportIssueRead]
    unmapped_columns: list[str]
    committed: bool


@router.get("/links/template.xlsx")
def link_template() -> Response:
    """結線表の記入用ひな形。

    導入のとき、これに書いて取り込めば図が立ち上がる。何をどう書くかは
    シートの中に書いてあるので、この画面を離れても分かる。
    """
    return Response(
        content=linkimport.template_xlsx(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="netvision-links-template.xlsx"'},
    )


async def _run_links(db: Session, file: UploadFile, commit: bool) -> LinkImportResultRead:
    content = await file.read()
    if len(content) > _max_upload_bytes():
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, _too_large_message())
    try:
        outcome = linkimport.run_import(db, file.filename or "links", content, commit=commit)
    except TableFileError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    return LinkImportResultRead.model_validate(outcome, from_attributes=True)


@router.post("/links/preview", response_model=LinkImportResultRead)
async def preview_link_import(
    db: DbSession,
    file: UploadFile = File(description="結線表（CSV / Excel）"),
) -> LinkImportResultRead:
    """結線表の中身を確認するだけで、DB は変更しない。"""
    return await _run_links(db, file, commit=False)


@router.post("/links", response_model=LinkImportResultRead)
async def commit_link_import(
    db: DbSession,
    file: UploadFile = File(description="結線表（CSV / Excel）"),
) -> LinkImportResultRead:
    """結線表を取り込む。

    台帳にまだ無い機器の行は「未接続」として預かる。その機器を登録した
    時点で自動的に結線に変わるので、取り込み直しは要らない。
    """
    return await _run_links(db, file, commit=True)
