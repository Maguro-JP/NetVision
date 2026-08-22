from pydantic import BaseModel, Field

from app.services.importer import RowAction


class ImportFieldInfo(BaseModel):
    """画面で列を対応付けるための選択肢。"""

    name: str
    label: str
    required: bool
    aliases: list[str]
    #: 決まった書き方しか受け付けない項目の候補（種別・状態）。空なら自由入力
    choices: list[str] = []


class ImportRowIssue(BaseModel):
    column: str | None = None
    message: str


class ImportFieldChange(BaseModel):
    """1つの項目が、いまの値から何に変わるか。"""

    field: str
    label: str
    before: str
    after: str


class ImportRowResult(BaseModel):
    row_number: int
    key: str
    action: RowAction
    issues: list[ImportRowIssue] = []
    device_id: int | None = None
    contract_added: bool = False
    #: 既存を直す行で、実際に変わる項目だけ。変わらない行は空
    changes: list[ImportFieldChange] = []


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
    column_samples: dict[str, list[str]] = Field(
        default_factory=dict, description="{列名: 実データの例}。列の対応付けを判断する材料"
    )
    #: 行ごとの結果。多いときはエラー優先で一部だけ（rows_total が実数）
    rows: list[ImportRowResult]
    rows_total: int = Field(
        default=0, description="行結果の実数。rows は画面が固まらないよう一部だけ返す"
    )
