"""控えの一覧と、いま取る操作。

戻す操作は持たない。データを上書きする作業なので、人が手順を見て行う。
どこに何があるかと、それが開けるかまでをここで見せる。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.schemas.backups import BackupRead, BackupStatusRead
from app.services import backup

router = APIRouter(prefix="/backups", tags=["backups"])


def _row(item: dict) -> BackupRead:
    return BackupRead(
        id=str(item.get("id", "")),
        path=str(item.get("path", "")),
        created_at=str(item.get("created_at", "")),
        reason=item.get("to_revision"),
        bytes=int(item.get("bytes") or 0),
        exists=bool(item.get("exists")),
        verified=item.get("verified"),
        verify_message=str(item.get("verify_message") or ""),
        before_schema_change=item.get("from_revision") is not None
        or str(item.get("to_revision") or "").startswith("rev:"),
    )


def _status() -> BackupStatusRead:
    items = backup.listing()
    newest = next((item for item in items if item.get("exists")), None)
    return BackupStatusRead(
        interval_hours=backup.interval_hours(),
        keep=backup.keep(),
        running=backup.running(),
        next_run_at=backup.next_run_at(),
        latest=_row(newest) if newest else None,
        backups=[_row(item) for item in items[:50]],
        restore_hint="戻すときは、このツールを止めてから控えのフォルダの中身を"
        "データの置き場所へ戻してください。手順は docs/OPERATIONS.md にあります。",
    )


@router.get("", response_model=BackupStatusRead)
def read_backups() -> BackupStatusRead:
    """いつのものがどこにあるか、開ける状態か。"""
    return _status()


@router.post("", response_model=BackupStatusRead)
def create_backup() -> BackupStatusRead:
    """いま取る。取ったあと、開けるところまで確かめる。"""
    try:
        backup.run("手動")
    except RuntimeError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except OSError as error:
        raise HTTPException(status.HTTP_507_INSUFFICIENT_STORAGE, str(error)) from error
    return _status()
