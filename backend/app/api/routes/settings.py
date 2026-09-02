"""画面から決める設定。

環境変数は起動のときに決まるもので、動かしながら切り替えられない。使う人に
環境変数を触らせるのは、面倒なだけでなく、打ち間違いが起きるし、そこで
詰まる人も出る。運用しながら変えたいものは、ここから決められるようにする。

ここに出さないものもある。DB の接続先や待ち受けアドレスのように、起動時に
しか決められないものと、間違えると外から見える状態になるものは、環境変数の
ままにしておく。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.config import settings
from app.services import appsettings, exposure_schedule, llm_inproc

router = APIRouter(prefix="/settings", tags=["settings"])


class AppSettingsRead(BaseModel):
    """いま効いている値。環境変数の値は初期値として扱う。"""

    warning_days: int = Field(description="この日数以内の期限を「要計画」とする")
    critical_days: int = Field(description="この日数以内の期限を「要対応」とする")
    reference_stale_days: int = Field(description="リファレンスが古いと見なす日数")
    display_timezone: str = Field(description="画面と書き出しに出す時刻の地域")
    model_dir: str = Field(description="アプリの中で動かすモデル（GGUF）の置き場所")
    exposure_sync_hours: int = Field(description="該当状況を裏で洗い直す間隔（時間）。0 で止める")


class AppSettingsUpdate(BaseModel):
    warning_days: int | None = None
    critical_days: int | None = None
    reference_stale_days: int | None = None
    display_timezone: str | None = None
    model_dir: str | None = None
    exposure_sync_hours: int | None = None


def _current() -> AppSettingsRead:
    return AppSettingsRead(
        warning_days=appsettings.number("warning_days", settings.warning_days),
        critical_days=appsettings.number("critical_days", settings.critical_days),
        reference_stale_days=appsettings.number(
            "reference_stale_days", settings.reference_stale_days
        ),
        display_timezone=appsettings.text("display_timezone", settings.display_timezone),
        model_dir=appsettings.text("model_dir", str(llm_inproc.model_dir())),
        exposure_sync_hours=exposure_schedule.interval_hours(),
    )


@router.get("", response_model=AppSettingsRead)
def read_settings() -> AppSettingsRead:
    return _current()


@router.patch("", response_model=AppSettingsRead)
def update_settings(payload: AppSettingsUpdate) -> AppSettingsRead:
    """渡された項目だけを書き換える。"""
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)

    for key in ("warning_days", "critical_days", "reference_stale_days"):
        if key in changes and not 1 <= changes[key] <= 3650:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "日数は 1〜3650 で指定してください"
            )
    if "exposure_sync_hours" in changes and not (
        0 <= changes["exposure_sync_hours"] <= exposure_schedule.MAX_HOURS
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"洗い直しの間隔は 0〜{exposure_schedule.MAX_HOURS} 時間で指定してください",
        )
    if "display_timezone" in changes:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(changes["display_timezone"])
        except Exception as error:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"時刻の地域として読めません: {changes['display_timezone']}",
            ) from error
    if "warning_days" in changes or "critical_days" in changes:
        warning = changes.get("warning_days", _current().warning_days)
        critical = changes.get("critical_days", _current().critical_days)
        if critical > warning:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "「要対応」は「要計画」より短い日数にしてください",
            )

    try:
        for key, value in changes.items():
            appsettings.set_value(key, value)
    except RuntimeError as error:
        raise HTTPException(status.HTTP_507_INSUFFICIENT_STORAGE, str(error)) from error
    if "exposure_sync_hours" in changes:
        exposure_schedule.reschedule()
    return _current()
