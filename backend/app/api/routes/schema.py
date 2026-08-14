"""表の差分を当てる専用 API。SQL は人が流さない。"""

from fastapi import APIRouter, HTTPException, status

from app.schemas.schema_status import SchemaStatusRead
from app.services import schema_gate

router = APIRouter(prefix="/schema", tags=["schema"])


def _status() -> SchemaStatusRead:
    return SchemaStatusRead.model_validate(schema_gate.schema_status())


@router.get("", response_model=SchemaStatusRead)
def get_schema_status() -> SchemaStatusRead:
    """いまの版と、控えを取る必要があるかを返す。"""
    return _status()


@router.post("/apply", response_model=SchemaStatusRead)
def apply_schema() -> SchemaStatusRead:
    """控えを取ってから表を最新にする。控えが取れなければ表は触らない。"""
    try:
        return SchemaStatusRead.model_validate(schema_gate.apply_with_backup())
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/confirm", response_model=SchemaStatusRead)
def confirm_schema() -> SchemaStatusRead:
    """起動後に問題ないことを確認した印。控えのファイルは消さない。"""
    try:
        return SchemaStatusRead.model_validate(schema_gate.confirm_backup())
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
