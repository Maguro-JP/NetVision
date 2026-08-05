import csv
import io
import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.imports import ImportFieldInfo, ImportResult
from app.services import importer
from app.services.tablefile import TableFileError, read_table

router = APIRouter(prefix="/import", tags=["import"])

DbSession = Annotated[Session, Depends(get_db)]

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


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
) -> ImportResult:
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
    return ImportResult.model_validate(outcome, from_attributes=True)


@router.post("/devices/preview", response_model=ImportResult)
async def preview_import(
    db: DbSession,
    file: UploadFile = File(description="CSV / TSV / Excel(.xlsx) の台帳ファイル"),
    mapping: str | None = Form(default=None, description='{"列名": "項目名"} の JSON'),
    key_field: str = Form(default="hostname"),
    update_existing: bool = Form(default=True),
) -> ImportResult:
    """取り込み内容を検証するだけで、DB は変更しない。"""
    return await _run(db, file, mapping, key_field, update_existing, commit=False)


@router.post("/devices", response_model=ImportResult)
async def commit_import(
    db: DbSession,
    file: UploadFile = File(description="CSV / TSV / Excel(.xlsx) の台帳ファイル"),
    mapping: str | None = Form(default=None),
    key_field: str = Form(default="hostname"),
    update_existing: bool = Form(default=True),
) -> ImportResult:
    """取り込みを実行する。エラー行が1件でもあれば全体を反映しない（all-or-nothing）。"""
    return await _run(db, file, mapping, key_field, update_existing, commit=True)
