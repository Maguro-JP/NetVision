"""時刻の扱いを1か所にまとめる。

DB には **UTC** で持つ。拠点や利用者が増えても基準が1つで済むため。
ただし**見せるときは使う人の時刻**にする。サーバの時計が UTC のまま
「2026-08-09 14:00 時点」と書かれた資料は、日本で見ると9時間ずれる。

保存と表示を混ぜると必ずどこかで取り違えるので、境界をここに閉じ込める。
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.config import settings


def zone() -> ZoneInfo:
    """表示に使う地域。設定が壊れていても落とさず UTC に倒す。"""
    try:
        return ZoneInfo(settings.display_timezone)
    except Exception:  # 設定ミスで画面が出なくなる方が困る
        return ZoneInfo("UTC")


def as_utc(value: datetime) -> datetime:
    """タイムゾーンが付いていない値を UTC として扱う。

    DB から返る値は素の datetime になることがある（SQLite の CURRENT_TIMESTAMP など）。
    そのまま渡すと、受け取った側が**現地時刻と誤解する**。
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def to_local(value: datetime) -> datetime:
    """表示用の時刻に直す。"""
    return as_utc(value).astimezone(zone())


def now() -> datetime:
    """今の時刻（表示用）。資料に載せる「◯◯時点」はこれを使う。"""
    return datetime.now(zone())


def format(value: datetime | None, *, with_zone: bool = False) -> str:
    """画面・書き出し用の文字列。

    `with_zone` を付けると地域名も出す。書き出した資料が別の場所で読まれる
    場合、どの時刻かが分からないと突き合わせできない。
    """
    if value is None:
        return ""
    local = to_local(value)
    text = local.strftime("%Y-%m-%d %H:%M")
    return f"{text} {local.tzname()}" if with_zone else text
