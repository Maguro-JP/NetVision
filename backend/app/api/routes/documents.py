import io
from datetime import date
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device, DeviceStatus
from app.models.document import ConfigTemplate, Document, DocumentType, TemplateCategory
from app.schemas.documents import (
    ConfigTemplateCreate,
    ConfigTemplateMatchRead,
    ConfigTemplateRead,
    ConfigTemplateUpdate,
    DocumentMatchRead,
    DocumentRead,
    DocumentUpdate,
    RenderRequest,
    RenderResultRead,
)
from app.services import config_render
from app.services import documents as service
from app.services.device_query import DeviceFilters, query_devices

router = APIRouter(tags=["documents"])

DbSession = Annotated[Session, Depends(get_db)]


def _active_devices(db: Session) -> list[Device]:
    return [d for d in query_devices(db, DeviceFilters()) if d.status is not DeviceStatus.RETIRED]


def _to_read(document: Document, device_count: int = 0) -> DocumentRead:
    read = DocumentRead.model_validate(document, from_attributes=True)
    read.has_file = bool(document.storage_path)
    read.superseded_by_title = (
        document.superseded_by.title if document.superseded_by is not None else None
    )
    read.device_count = device_count
    return read


def _document_device_counts(db: Session, docs: list[Document]) -> dict[int, int]:
    """各資料が何台の機器に該当するか。使われていない資料を見つけるのに使う。"""
    devices = _active_devices(db)
    counts: dict[int, int] = {}
    for document in docs:
        counts[document.id] = sum(
            1
            for device in devices
            if service.check_applicability(
                device,
                vendor=document.vendor,
                model=document.model,
                os_name=document.os_name,
                applies_from=document.applies_from,
                applies_to=document.applies_to,
            ).matched
        )
    return counts


# ---------------------------------------------------------------------------
# ドキュメント
# ---------------------------------------------------------------------------


@router.get("/documents", response_model=list[DocumentRead])
def list_documents(
    db: DbSession,
    q: str | None = Query(default=None, description="タイトル / 型番 / 備考の部分一致"),
    vendor: str | None = None,
    doc_type: DocumentType | None = None,
    include_superseded: bool = Query(
        default=True, description="後継のある古い版も含める（既定で含める）"
    ),
) -> list[DocumentRead]:
    """登録済みのベンダードキュメント一覧。

    古い版も既定で表示する。現場では古いバージョンが現役なことがあり、
    既定で隠すと「無い」と誤解されるため。
    """
    stmt = select(Document).order_by(
        Document.vendor, Document.model, Document.doc_type, Document.applies_from
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Document.title.ilike(like)
            | Document.model.ilike(like)
            | Document.notes.ilike(like)
            | Document.version_label.ilike(like)
        )
    if vendor:
        stmt = stmt.where(Document.vendor == vendor)
    if doc_type:
        stmt = stmt.where(Document.doc_type == doc_type)
    if not include_superseded:
        stmt = stmt.where(Document.superseded_by_id.is_(None))

    docs = list(db.scalars(stmt))
    counts = _document_device_counts(db, docs)
    return [_to_read(d, counts.get(d.id, 0)) for d in docs]


@router.post("/documents", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def create_document(
    db: DbSession,
    vendor: str = Form(...),
    title: str = Form(...),
    doc_type: DocumentType = Form(default=DocumentType.OTHER),
    model: str | None = Form(default=None),
    os_name: str | None = Form(default=None),
    applies_from: str | None = Form(default=None),
    applies_to: str | None = Form(default=None),
    version_label: str | None = Form(default=None),
    edition: str | None = Form(default=None),
    published_at: date | None = Form(default=None),
    language: str | None = Form(default=None),
    source_url: str | None = Form(default=None),
    fetched_at: date | None = Form(default=None),
    notes: str | None = Form(default=None),
    file: UploadFile | None = File(default=None, description="PDF などの実体（省略可）"),
) -> DocumentRead:
    """ドキュメントを登録する。実体のアップロードは任意で、URL だけの登録もできる。"""
    document = Document(
        vendor=vendor,
        title=title,
        doc_type=doc_type,
        model=model or None,
        os_name=os_name or None,
        applies_from=applies_from or None,
        applies_to=applies_to or None,
        version_label=version_label or None,
        edition=edition or None,
        published_at=published_at,
        language=language or None,
        source_url=source_url or None,
        fetched_at=fetched_at or date.today(),
        notes=notes or None,
    )

    if file is not None and file.filename:
        content = await file.read()
        try:
            path, digest = service.store_file(content, file.filename)
        except service.DocumentStorageError as exc:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc)) from exc

        document.storage_path = path
        document.sha256 = digest
        document.filename = file.filename
        document.content_type = file.content_type
        document.size_bytes = len(content)

    db.add(document)
    db.commit()
    db.refresh(document)
    return _to_read(document)


def _load(db: Session, document_id: int) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"document {document_id} が見つかりません")
    return document


@router.get("/documents/{document_id}", response_model=DocumentRead)
def get_document(document_id: int, db: DbSession) -> DocumentRead:
    return _to_read(_load(db, document_id))


@router.patch("/documents/{document_id}", response_model=DocumentRead)
def update_document(document_id: int, payload: DocumentUpdate, db: DbSession) -> DocumentRead:
    document = _load(db, document_id)
    changes = payload.model_dump(exclude_unset=True)

    if changes.get("superseded_by_id") == document_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "自分自身は後継にできません")
    if changes.get("superseded_by_id") is not None:
        _load(db, changes["superseded_by_id"])

    for key, value in changes.items():
        setattr(document, key, value)
    db.commit()
    db.refresh(document)
    return _to_read(document)


@router.post("/documents/{document_id}/supersede/{successor_id}", response_model=DocumentRead)
def supersede_document(document_id: int, successor_id: int, db: DbSession) -> DocumentRead:
    """新しい版を「後継」として結び付ける。古い版は消さずに残す。"""
    if document_id == successor_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "自分自身は後継にできません")

    document = _load(db, document_id)
    _load(db, successor_id)
    document.superseded_by_id = successor_id
    db.commit()
    db.refresh(document)
    return _to_read(document)


@router.get("/documents/{document_id}/download")
def download_document(document_id: int, db: DbSession) -> StreamingResponse:
    document = _load(db, document_id)
    path = service.resolve_file(document)
    if path is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "この資料には実体が登録されていません（URL のみ）"
        )

    filename = document.filename or f"document-{document.id}"
    return StreamingResponse(
        io.BytesIO(path.read_bytes()),
        media_type=document.content_type or "application/octet-stream",
        headers={"Content-Disposition": (f"attachment; filename*=UTF-8''{quote(filename)}")},
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: int, db: DbSession) -> None:
    """本当に不要なときだけ使う。版が新しくなっただけなら supersede を使うこと。"""
    document = _load(db, document_id)
    service.delete_file_if_unused(db, document)
    db.delete(document)
    db.commit()


@router.get("/devices/{device_id}/documents", response_model=list[DocumentMatchRead])
def documents_for_device(device_id: int, db: DbSession) -> list[DocumentMatchRead]:
    """この機器に該当する資料。稼働中のバージョンに合った版が出る。"""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"device {device_id} が見つかりません")

    results = []
    for match in service.documents_for_device(db, device):
        read = _to_read(match.document)
        results.append(DocumentMatchRead(**read.model_dump(), reason=match.reason))
    return results


# ---------------------------------------------------------------------------
# Config テンプレート
# ---------------------------------------------------------------------------


def _to_template_read(template: ConfigTemplate) -> ConfigTemplateRead:
    read = ConfigTemplateRead.model_validate(template, from_attributes=True)
    read.source_document_title = (
        template.source_document.title if template.source_document is not None else None
    )
    read.detected_variables = config_render.declared_variables(template)
    return read


@router.get("/config-templates", response_model=list[ConfigTemplateRead])
def list_templates(
    db: DbSession,
    q: str | None = None,
    vendor: str | None = None,
    category: TemplateCategory | None = None,
) -> list[ConfigTemplateRead]:
    stmt = select(ConfigTemplate).order_by(ConfigTemplate.vendor, ConfigTemplate.name)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(ConfigTemplate.name.ilike(like) | ConfigTemplate.description.ilike(like))
    if vendor:
        stmt = stmt.where(ConfigTemplate.vendor == vendor)
    if category:
        stmt = stmt.where(ConfigTemplate.category == category)
    return [_to_template_read(t) for t in db.scalars(stmt)]


@router.post(
    "/config-templates", response_model=ConfigTemplateRead, status_code=status.HTTP_201_CREATED
)
def create_template(payload: ConfigTemplateCreate, db: DbSession) -> ConfigTemplateRead:
    if payload.source_document_id is not None:
        _load(db, payload.source_document_id)

    template = ConfigTemplate(**payload.model_dump())
    db.add(template)
    db.commit()
    db.refresh(template)
    return _to_template_read(template)


def _load_template(db: Session, template_id: int) -> ConfigTemplate:
    template = db.get(ConfigTemplate, template_id)
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"template {template_id} が見つかりません")
    return template


@router.get("/config-templates/{template_id}", response_model=ConfigTemplateRead)
def get_template(template_id: int, db: DbSession) -> ConfigTemplateRead:
    return _to_template_read(_load_template(db, template_id))


@router.patch("/config-templates/{template_id}", response_model=ConfigTemplateRead)
def update_template(
    template_id: int, payload: ConfigTemplateUpdate, db: DbSession
) -> ConfigTemplateRead:
    template = _load_template(db, template_id)
    changes = payload.model_dump(exclude_unset=True)

    if changes.get("superseded_by_id") == template_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "自分自身は後継にできません")

    for key, value in changes.items():
        setattr(template, key, value)
    db.commit()
    db.refresh(template)
    return _to_template_read(template)


@router.delete("/config-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: int, db: DbSession) -> None:
    db.delete(_load_template(db, template_id))
    db.commit()


@router.get("/devices/{device_id}/config-templates", response_model=list[ConfigTemplateMatchRead])
def templates_for_device(device_id: int, db: DbSession) -> list[ConfigTemplateMatchRead]:
    """この機器に使えるテンプレート。バージョンに合った書式のものが出る。"""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"device {device_id} が見つかりません")

    results = []
    for match in service.templates_for_device(db, device):
        read = _to_template_read(match.template)
        results.append(ConfigTemplateMatchRead(**read.model_dump(), reason=match.reason))
    return results


@router.post("/config-templates/{template_id}/render", response_model=RenderResultRead)
def render_template(template_id: int, payload: RenderRequest, db: DbSession) -> RenderResultRead:
    """テンプレートから設定内容を生成する。

    生成するだけで、実機には投入しない。 内容を確認して手で流し込む前提。
    """
    template = _load_template(db, template_id)

    device = None
    if payload.device_id is not None:
        device = db.get(Device, payload.device_id)
        if device is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"device {payload.device_id} が存在しません"
            )

    try:
        result = config_render.render(db, template, device, payload.variables)
    except config_render.RenderError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    return RenderResultRead(
        rendered=result.rendered,
        device_hostname=result.device_hostname,
        used_variables=result.used_variables,
        warnings=result.warnings,
    )
