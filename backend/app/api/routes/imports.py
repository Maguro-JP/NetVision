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
from app.schemas.imports import (
    ImportFieldInfo,
    ImportResult,
    LinkImportResultRead,
)
from app.schemas.jobs import JobRead, job_read
from app.services import csvsafe, importer, jobs, linkimport, siteimport
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
            choices=list(importer.FIELD_CHOICES.get(name, ())),
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
    writer = csvsafe.writer(buffer)
    writer.writerow([importer.FIELD_LABELS[f] for f in importer.IMPORTABLE_FIELDS])
    # 列と値の位置をずらさない。内部IDは新規では空、管理IDはサンプルを入れる
    sample = {name: "" for name in importer.IMPORTABLE_FIELDS}
    sample.update(
        {
            "code": "ASSET-1001",
            "hostname": "core-sw-01",
            "label": "本社コア1号機",
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
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, _too_large_message())

    try:
        table = read_table(file.filename or "", content)
    except TableFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    mapping_override: dict[str, str] | None = None
    if mapping:
        try:
            parsed = json.loads(mapping)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "mapping は JSON で指定してください"
            ) from exc
        if not isinstance(parsed, dict):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "mapping は {列名: 項目名} の形式です"
            )
        unknown = [v for v in parsed.values() if v and v not in importer.IMPORTABLE_FIELDS]
        if unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"未知の項目が指定されています: {', '.join(unknown)}",
            )
        mapping_override = {str(k): str(v) if v else "" for k, v in parsed.items()}

    try:
        key = importer.KeyField(key_field)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
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
        first_row=table.first_row,
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
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "encoding は utf-8 または shift_jis を指定してください",
        )

    table, outcome = await _run(
        db, file, mapping, key_field, update_existing, commit=False, keep_rows=None
    )

    buffer = io.StringIO(newline="")
    writer = csvsafe.writer(buffer, lineterminator="\r\n")
    writer.writerow([*table.headers, ERROR_COLUMN])

    for row in outcome.rows:
        if row.action is not importer.RowAction.ERROR:
            continue
        # row_number はファイルの行番号。最初のデータ行（first_row）からの差が添字になる
        original = table.rows[row.row_number - table.first_row]
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
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, _too_large_message())
    try:
        outcome = linkimport.run_import(db, file.filename or "links", content, commit=commit)
    except TableFileError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
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


# ---------------------------------------------------------------------------
# 拠点台帳
# ---------------------------------------------------------------------------


class SiteImportIssueRead(BaseModel):
    row_number: int
    message: str


class SiteImportResultRead(BaseModel):
    total_rows: int
    created: int
    updated: int
    unchanged: int
    issues: list[SiteImportIssueRead]
    unmapped_columns: list[str]
    committed: bool


async def _run_sites(db: Session, file: UploadFile, commit: bool) -> SiteImportResultRead:
    content = await file.read()
    if len(content) > _max_upload_bytes():
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, _too_large_message())
    try:
        outcome = siteimport.run_import(db, file.filename or "sites", content, commit=commit)
    except TableFileError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return SiteImportResultRead.model_validate(outcome, from_attributes=True)


@router.post("/sites/preview", response_model=SiteImportResultRead)
async def preview_site_import(
    db: DbSession,
    file: UploadFile = File(description="拠点台帳（CSV / Excel）"),
) -> SiteImportResultRead:
    """拠点台帳の中身を確認するだけで、DB は変更しない。"""
    return await _run_sites(db, file, commit=False)


@router.post("/sites", response_model=SiteImportResultRead)
async def commit_site_import(
    db: DbSession,
    file: UploadFile = File(description="拠点台帳（CSV / Excel）"),
) -> SiteImportResultRead:
    """拠点台帳を取り込む。

    突き合わせの鍵は拠点ID。IDが無い行は拠点名で探し、それも無ければ
    新しく起こす。書き出した表をそのまま直して戻せる。

    拠点に紐づく機器は触らない。
    """
    return await _run_sites(db, file, commit=True)


# ---------------------------------------------------------------------------
# 裏で走らせる（16万行の台帳は数十秒かかる）
# ---------------------------------------------------------------------------


def _parse_options(
    mapping: str | None, key_field: str
) -> tuple[dict[str, str] | None, importer.KeyField]:
    """下見と同じ検査を、受け付けの時点で通す。

    裏に回してから「列名が違う」と分かっても、待ったぶんが無駄になる。
    形の誤りはその場で返す。
    """
    mapping_override: dict[str, str] | None = None
    if mapping:
        try:
            parsed = json.loads(mapping)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "mapping は JSON で指定してください"
            ) from exc
        if not isinstance(parsed, dict):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "mapping は {列名: 項目名} の形式です"
            )
        unknown = [v for v in parsed.values() if v and v not in importer.IMPORTABLE_FIELDS]
        if unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"未知の項目が指定されています: {', '.join(unknown)}",
            )
        mapping_override = {str(k): str(v) if v else "" for k, v in parsed.items()}
    try:
        key = importer.KeyField(key_field)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "key_field は code / hostname / serial_number を指定してください",
        ) from exc
    return mapping_override, key


@router.post("/devices/async", response_model=JobRead)
async def commit_import_in_background(
    file: UploadFile = File(description="CSV / TSV / Excel(.xlsx) の台帳ファイル"),
    mapping: str | None = Form(default=None),
    key_field: str = Form(default="hostname"),
    update_existing: bool = Form(default=True),
) -> JobRead:
    """台帳の取り込みを裏で走らせる。受け付けたらすぐ番号を返す。

    16万行の台帳は数十秒かかる。そのあいだ画面を止めておくと、押しても
    反応が無いように見えるうえ、ブラウザ側で先に切れることもある。
    様子は `/jobs/{id}` で聞く。
    """
    content = await file.read()
    if len(content) > _max_upload_bytes():
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, _too_large_message())
    mapping_override, key = _parse_options(mapping, key_field)
    filename = file.filename or "devices"

    def work(job) -> ImportResult:  # noqa: ANN001
        from app.db import new_session

        with new_session() as session:
            try:
                table = read_table(filename, content)
            except TableFileError as exc:
                raise RuntimeError(str(exc)) from exc
            job.advance(0, len(table.rows), "読み込みました")
            outcome = importer.run_import(
                session,
                table.headers,
                table.rows,
                mapping_override=mapping_override,
                key_field=key,
                update_existing=update_existing,
                commit=True,
                on_progress=job.advance,
                first_row=table.first_row,
            )
            return ImportResult.model_validate(outcome, from_attributes=True)

    return job_read(jobs.start("device-import", work, label=filename))


@router.post("/links/async", response_model=JobRead)
async def commit_link_import_in_background(
    file: UploadFile = File(description="結線表（CSV / Excel）"),
) -> JobRead:
    """結線表の取り込みを裏で走らせる。様子は `/jobs/{id}` で聞く。"""
    content = await file.read()
    if len(content) > _max_upload_bytes():
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, _too_large_message())
    filename = file.filename or "links"

    def work(job) -> LinkImportResultRead:  # noqa: ANN001
        from app.db import new_session

        with new_session() as session:
            try:
                outcome = linkimport.run_import(
                    session, filename, content, commit=True, on_progress=job.advance
                )
            except TableFileError as exc:
                raise RuntimeError(str(exc)) from exc
            return LinkImportResultRead.model_validate(outcome, from_attributes=True)

    return job_read(jobs.start("link-import", work, label=filename))
