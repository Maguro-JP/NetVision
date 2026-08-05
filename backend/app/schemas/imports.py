from pydantic import BaseModel, Field

from app.services.importer import RowAction


class ImportFieldInfo(BaseModel):
    """画面で列を対応付けるための選択肢。"""

    name: str
    label: str
    required: bool
    aliases: list[str]


class ImportRowIssue(BaseModel):
    column: str | None = None
    message: str


class ImportRowResult(BaseModel):
    row_number: int
    key: str
    action: RowAction
    issues: list[ImportRowIssue] = []
    device_id: int | None = None
    contract_added: bool = False


class ImportResult(BaseModel):
    """取り込みのプレビュー / 実行結果。両方で同じ形を返す。"""

    committed: bool = Field(description="True なら DB に反映済み。False は検証のみ。")
    total_rows: int
    created: int
    updated: int
    skipped: int
    errors: int
    contracts_added: int
    mapping: dict[str, str] = Field(description="{ファイルの列名: 取り込み先フィールド}")
    unmapped_columns: list[str] = Field(description="対応付けできなかった列")
    missing_required_fields: list[str] = Field(description="必須項目のうち未対応付けのもの")
    rows: list[ImportRowResult]
