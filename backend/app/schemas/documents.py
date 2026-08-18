from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentType, TemplateCategory


class DocumentBase(BaseModel):
    vendor: str = Field(min_length=1, max_length=80)
    model: str | None = None
    os_name: str | None = None
    doc_type: DocumentType = DocumentType.OTHER
    title: str = Field(min_length=1, max_length=300)
    language: str | None = None
    applies_from: str | None = None
    applies_to: str | None = None
    version_label: str | None = None
    edition: str | None = None
    published_at: date | None = None
    source_url: str | None = None
    fetched_at: date | None = None
    notes: str | None = None


class DocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor: str | None = Field(default=None, min_length=1, max_length=80)
    model: str | None = None
    os_name: str | None = None
    doc_type: DocumentType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    language: str | None = None
    applies_from: str | None = None
    applies_to: str | None = None
    version_label: str | None = None
    edition: str | None = None
    published_at: date | None = None
    source_url: str | None = None
    fetched_at: date | None = None
    notes: str | None = None
    superseded_by_id: int | None = None


class DocumentRead(DocumentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    #: 中身を章に切った日時。読めなかった資料は index_note に理由が入る
    indexed_at: datetime | None = None
    index_note: str | None = None
    section_count: int = 0
    filename: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    has_file: bool = False
    superseded_by_id: int | None = None
    superseded_by_title: str | None = None
    device_count: int = 0


class DocumentHitRead(BaseModel):
    """資料の中を探した結果。1件が1つの章。"""

    document_id: int
    title: str
    vendor: str
    model: str | None = None
    heading: str | None = None
    body: str
    #: その章に出てくるコマンド。書き方をそのまま拾ったもの
    commands: list[str] = []


class DocumentMatchRead(DocumentRead):
    """機器から引いたときの、該当理由付き。"""

    reason: str


class ConfigTemplateBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    category: TemplateCategory = TemplateCategory.OTHER
    vendor: str = Field(min_length=1, max_length=80)
    model: str | None = None
    os_name: str | None = None
    applies_from: str | None = None
    applies_to: str | None = None
    version_label: str | None = None
    body: str = Field(min_length=1)
    variables: list[dict] = []
    source_document_id: int | None = None


class ConfigTemplateCreate(ConfigTemplateBase):
    pass


class ConfigTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    category: TemplateCategory | None = None
    vendor: str | None = Field(default=None, min_length=1, max_length=80)
    model: str | None = None
    os_name: str | None = None
    applies_from: str | None = None
    applies_to: str | None = None
    version_label: str | None = None
    body: str | None = Field(default=None, min_length=1)
    variables: list[dict] | None = None
    source_document_id: int | None = None
    superseded_by_id: int | None = None


class ConfigTemplateRead(ConfigTemplateBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    superseded_by_id: int | None = None
    source_document_title: str | None = None
    detected_variables: list[dict] = []


class ConfigTemplateMatchRead(ConfigTemplateRead):
    reason: str


class RenderRequest(BaseModel):
    device_id: int | None = None
    variables: dict[str, str] = {}


class RenderResultRead(BaseModel):
    rendered: str
    device_hostname: str | None
    used_variables: dict[str, str]
    warnings: list[str]
