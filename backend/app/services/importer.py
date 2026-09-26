"""既存の Excel / CSV 台帳を取り込むためのマッピングと検証。

現場の台帳は列名も表記も揺れるため、次の方針で作っている。

1. よくある列名（日本語・英語）を別名辞書で自動マッピングする
2. 自動判別できない列は、利用者が画面で明示的に対応付けできる
3. 取り込み前に必ずプレビュー（dry-run）を返し、行ごとの結果とエラーを見せる
4. 更新は上書きではなく「値が入っている列だけ」反映する（台帳の空欄で既存値を消さない）
"""

from __future__ import annotations

import enum
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Device, DeviceStatus, DeviceType, MaintenanceContract, Site
from app.models.change_log import ChangeSource
from app.services import configparse
from app.services.audit import audit_context
from app.services.configparse import normalize_hostname

# ---------------------------------------------------------------------------
# 取り込み可能なフィールド
# ---------------------------------------------------------------------------

# 並びは、人が台帳を読むときの順にしてある。
#   どの機器か（管理ID・名前）→ 何か（種別・製品）→ どういう状態か
#   → どこにあるか（拠点・ラック・IP）→ 中身（OS）→ 日付 → 備考
# 拠点は名前を先に置く。ID は突き合わせ用で、人が読む列ではない
DEVICE_FIELDS = (
    "code",
    "label",
    "hostname",
    "device_type",
    "vendor",
    "model",
    "serial_number",
    "status",
    "site",
    "site_code",
    "rack",
    "rack_unit",
    "mgmt_ip",
    "os_name",
    "os_version",
    "firmware_version",
    "install_date",
    "eos_date",
    "eol_date",
    "notes",
)

CONTRACT_FIELDS = (
    "contract_provider",
    "contract_number",
    "contract_service_level",
    "contract_start_date",
    "contract_end_date",
    "contract_annual_cost",
)

# 機器ID は取り込みでも受け取る。書き出して直して戻すとき、この列があるから
# ホスト名を書き換えても同じ機器として直せる（無いと別名の機器が増える）。
# 人が読む列ではないので、並びは最後にする
IMPORTABLE_FIELDS = DEVICE_FIELDS + CONTRACT_FIELDS + ("device_id",)

# 取り込みで最低限いるのはホスト名だけ。ベンダーと型番は Config を読ませれば
# 埋まるので、台帳を起こす時点で必須にすると、分かっている機器まで登録できない
REQUIRED_FIELDS = ("hostname",)

DATE_FIELDS = frozenset(
    {"install_date", "eos_date", "eol_date", "contract_start_date", "contract_end_date"}
)

FIELD_LABELS: dict[str, str] = {
    "device_id": "内部ID（書き出した行を戻すとき用）",
    "code": "管理ID",
    "hostname": "ホスト名",
    "label": "機器名",
    "vendor": "ベンダー",
    "model": "型番",
    "serial_number": "シリアル番号",
    "device_type": "種別",
    "status": "状態",
    "os_name": "OS名",
    "os_version": "OSバージョン",
    "firmware_version": "ファームウェアバージョン",
    "site": "拠点名",
    "site_code": "拠点ID（任意）",
    "rack": "ラック",
    "rack_unit": "搭載U",
    "mgmt_ip": "管理IP",
    "install_date": "導入日",
    "eos_date": "販売終了(EoS)",
    "eol_date": "サポート終了(EoL)",
    "notes": "備考",
    "contract_provider": "保守ベンダー",
    "contract_number": "契約番号",
    "contract_service_level": "サービスレベル",
    "contract_start_date": "保守開始日",
    "contract_end_date": "保守終了日",
    "contract_annual_cost": "保守年額",
}

# 列名の別名。正規化（小文字化・空白と記号の除去）した文字列で突き合わせる。
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "device_id": (
        "内部id",
        "内部ID",
        "機器id",
        "機器ID",
        "id",
        "device_id",
        "deviceid",
        "台帳id",
    ),
    "code": (
        "code",
        "管理id",
        "管理ID",
        "管理番号",
        "資産id",
        "資産ID",
        "資産番号",
        "managementid",
        "assetid",
        "assettag",
    ),
    "label": (
        "label",
        "機器名",
        "機器名称",
        "名称",
        "呼称",
        "表示名",
        "devicename",
        "displayname",
    ),
    # 「機器名」「名称」は label（人が付けた名称）に振る。ホスト名は機械の名前で、
    # 現場の呼び名と食い違うことがあるので、同じ列として扱わない
    "hostname": (
        "hostname",
        "host",
        "ホスト名",
        "装置名",
        "デバイス名",
        "nodename",
    ),
    "vendor": (
        "vendor",
        "maker",
        "manufacturer",
        "ベンダー",
        "ベンダ",
        "メーカー",
        "メーカ",
        "製造元",
    ),
    "model": ("model", "modelnumber", "producttype", "型番", "型式", "機種", "品名", "製品名"),
    "serial_number": (
        "serial",
        "serialnumber",
        "sn",
        "シリアル",
        "シリアル番号",
        "製造番号",
        "号機番号",
    ),
    "device_type": ("devicetype", "type", "種別", "機器種別", "装置種別", "区分", "分類"),
    "status": ("status", "state", "状態", "稼働状態", "ステータス", "運用状態"),
    "os_name": ("osname", "os", "ソフトウェア", "os名"),
    "os_version": (
        "osversion",
        "version",
        "firmware",
        "バージョン",
        "osバージョン",
        "ソフトウェアバージョン",
        "現行バージョン",
    ),
    "firmware_version": (
        "firmwareversion",
        "fwversion",
        "ファームウェア",
        "ファームウェアバージョン",
    ),
    "site": (
        "拠点名",
        "site",
        "location",
        "sitename",
        "拠点",
        "設置場所",
        "設置拠点",
        "事業所",
        "ロケーション",
        "建屋",
    ),
    "site_code": (
        "拠点id（任意）",
        "sitecode",
        "site_code",
        "siteid",
        "拠点id",
        "拠点ID",
        "拠点コード",
        "拠点番号",
    ),
    "rack": ("rack", "rackname", "ラック", "ラック番号", "ラック名"),
    "rack_unit": ("rackunit", "unit", "u", "搭載u", "ユニット", "u位置", "搭載位置"),
    "mgmt_ip": (
        "mgmtip",
        "ip",
        "ipaddress",
        "managementip",
        "管理ip",
        "ipアドレス",
        "管理アドレス",
    ),
    "install_date": (
        "installdate",
        "installedat",
        "導入日",
        "導入年月日",
        "導入時期",
        "設置日",
        "納入日",
        "運用開始日",
    ),
    "eos_date": ("eos", "eosdate", "endofsale", "販売終了", "販売終了日", "eos日"),
    "eol_date": (
        "eol",
        "eoldate",
        "endoflife",
        "endofsupport",
        "eosl",
        "サポート終了",
        "サポート終了日",
        "保守終了予定",
        "eol日",
    ),
    "notes": ("notes", "note", "remarks", "memo", "備考", "メモ", "特記事項"),
    "contract_provider": (
        "contractprovider",
        "provider",
        "maintenancevendor",
        "保守ベンダー",
        "保守業者",
        "保守会社",
        "保守委託先",
    ),
    "contract_number": ("contractnumber", "contractno", "契約番号", "保守契約番号"),
    "contract_service_level": (
        "servicelevel",
        "sla",
        "サービスレベル",
        "保守レベル",
        "保守区分",
        "保守内容",
    ),
    "contract_start_date": ("contractstart", "startdate", "保守開始日", "契約開始日", "保守開始"),
    "contract_end_date": (
        "contractend",
        "enddate",
        "保守終了日",
        "契約終了日",
        "保守期限",
        "保守切れ",
        "保守終了",
    ),
    "contract_annual_cost": ("annualcost", "cost", "保守年額", "年額", "保守費用", "年間保守費"),
}

# 値の別名（日本語表記でも取り込めるようにする）
DEVICE_TYPE_ALIASES: dict[str, DeviceType] = {
    "router": DeviceType.ROUTER,
    "ルータ": DeviceType.ROUTER,
    "ルーター": DeviceType.ROUTER,
    "l3": DeviceType.ROUTER,
    "switch": DeviceType.SWITCH,
    "スイッチ": DeviceType.SWITCH,
    "sw": DeviceType.SWITCH,
    "l2": DeviceType.SWITCH,
    "l2sw": DeviceType.SWITCH,
    "l3sw": DeviceType.SWITCH,
    "firewall": DeviceType.FIREWALL,
    "ファイアウォール": DeviceType.FIREWALL,
    "ファイアーウォール": DeviceType.FIREWALL,
    "fw": DeviceType.FIREWALL,
    "loadbalancer": DeviceType.LOAD_BALANCER,
    "ロードバランサ": DeviceType.LOAD_BALANCER,
    "ロードバランサー": DeviceType.LOAD_BALANCER,
    "lb": DeviceType.LOAD_BALANCER,
    "wirelessap": DeviceType.WIRELESS_AP,
    "無線ap": DeviceType.WIRELESS_AP,
    "ap": DeviceType.WIRELESS_AP,
    "アクセスポイント": DeviceType.WIRELESS_AP,
    "wirelesscontroller": DeviceType.WIRELESS_CONTROLLER,
    "無線コントローラ": DeviceType.WIRELESS_CONTROLLER,
    "無線コントローラー": DeviceType.WIRELESS_CONTROLLER,
    "wlc": DeviceType.WIRELESS_CONTROLLER,
    "server": DeviceType.SERVER,
    "サーバ": DeviceType.SERVER,
    "サーバー": DeviceType.SERVER,
    "other": DeviceType.OTHER,
    "その他": DeviceType.OTHER,
}

# 決まった書き方しか受け付けない項目の候補。取り込み画面と台帳の入力欄に出す。
# 別名（router / sw / ルータ など）も受け付けるが、人に見せるのは代表表記だけ
FIELD_CHOICES: dict[str, tuple[str, ...]] = {
    "device_type": (
        "ルーター",
        "スイッチ",
        "ファイアウォール",
        "ロードバランサー",
        "無線AP",
        "無線コントローラー",
        "サーバー",
        "その他",
    ),
    "status": ("稼働中", "予備機", "保守中", "撤去済み"),
}


DEVICE_STATUS_ALIASES: dict[str, DeviceStatus] = {
    "active": DeviceStatus.ACTIVE,
    "稼働": DeviceStatus.ACTIVE,
    "稼動": DeviceStatus.ACTIVE,
    "稼働中": DeviceStatus.ACTIVE,
    "稼動中": DeviceStatus.ACTIVE,
    "運用": DeviceStatus.ACTIVE,
    "運用中": DeviceStatus.ACTIVE,
    "使用中": DeviceStatus.ACTIVE,
    "現用": DeviceStatus.ACTIVE,
    "本番": DeviceStatus.ACTIVE,
    "standby": DeviceStatus.STANDBY,
    "予備": DeviceStatus.STANDBY,
    "予備機": DeviceStatus.STANDBY,
    "待機": DeviceStatus.STANDBY,
    "待機中": DeviceStatus.STANDBY,
    "maintenance": DeviceStatus.MAINTENANCE,
    "保守": DeviceStatus.MAINTENANCE,
    "保守中": DeviceStatus.MAINTENANCE,
    "保守作業中": DeviceStatus.MAINTENANCE,
    "retired": DeviceStatus.RETIRED,
    "撤去": DeviceStatus.RETIRED,
    "撤去済": DeviceStatus.RETIRED,
    "撤去済み": DeviceStatus.RETIRED,
    "廃棄": DeviceStatus.RETIRED,
    "廃棄済": DeviceStatus.RETIRED,
    "廃止": DeviceStatus.RETIRED,
    "返却": DeviceStatus.RETIRED,
    # 「停止」は入れない。一時的に止めているのか、外したのかが読めない。
    # 撤去として扱うと、期限の判定からその機器が消える
}


def normalize_header(header: str) -> str:
    """列名を突き合わせ用に正規化する（全角/半角・大小文字・記号の揺れを吸収）。"""
    text = unicodedata.normalize("NFKC", header or "").lower()
    return re.sub(r"[\s_\-/()\[\]（）【】.,:：・]", "", text)


_ALIAS_INDEX: dict[str, str] = {}
# 書き出したファイルをそのまま取り込み直せるよう、表示ラベル自体も必ず別名に含める
for _field, _label in FIELD_LABELS.items():
    _ALIAS_INDEX[normalize_header(_label)] = _field
for _field, _aliases in FIELD_ALIASES.items():
    _ALIAS_INDEX.setdefault(normalize_header(_field), _field)
    for _alias in _aliases:
        _ALIAS_INDEX.setdefault(normalize_header(_alias), _field)


def detect_mapping(headers: list[str]) -> dict[str, str]:
    """ヘッダ行から {列名: フィールド名} を推定する。同じフィールドは最初の列を採用。"""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for header in headers:
        target = _ALIAS_INDEX.get(normalize_header(header))
        if target and target not in used:
            mapping[header] = target
            used.add(target)
    return mapping


# ---------------------------------------------------------------------------
# 値の変換
# ---------------------------------------------------------------------------

_DATE_PATTERNS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y年%m月%d日",
    "%d/%m/%Y",
    "%Y-%m",
    "%Y/%m",
    "%Y年%m月",
)


#: 和暦の元号。台帳、とくに官公庁や古くからある社内様式では和暦がふつうに
#: 出てくる。読めないと、その行がまるごと取り込めない。
#: 値は「元年が西暦何年か」から1を引いた数（令和1年 = 2019年）
_ERAS = {
    "令和": 2018,
    "令": 2018,
    "R": 2018,
    "平成": 1988,
    "平": 1988,
    "H": 1988,
    "昭和": 1925,
    "昭": 1925,
    "S": 1925,
}
#: 令和5年4月1日 / R5.4.1 / H31/3/31 / 令和元年4月1日
_WAREKI = re.compile(
    r"^(?P<era>令和|令|R|平成|平|H|昭和|昭|S)\s*(?P<year>元|\d{1,2})\s*"
    r"(?:年|[./-])\s*(?P<month>\d{1,2})\s*"
    r"(?:(?:月|[./-])\s*(?P<day>\d{1,2})\s*日?)?$",
    re.IGNORECASE,
)


def _parse_wareki(text: str) -> date | None:
    """和暦を西暦にする。読めなければ None（読めないものを当てずっぽうで埋めない）。"""
    match = _WAREKI.match(text)
    if match is None:
        return None
    offset = _ERAS.get(match.group("era")) or _ERAS.get(match.group("era").upper())
    if offset is None:
        return None
    era_year = 1 if match.group("year") == "元" else int(match.group("year"))
    if era_year < 1:
        return None
    day = int(match.group("day")) if match.group("day") else 1
    try:
        return date(offset + era_year, int(match.group("month")), day)
    except ValueError:
        return None


class CellValueError(ValueError):
    """セル値の変換に失敗したことを表す。"""


def parse_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        return None

    # よくある形を先に処理する。strptime を総当たりすると、16万行では
    # それだけで数秒溶ける（列が日付2つあるだけで行数の十数倍呼ばれる）
    fast = _parse_date_fast(text)
    if fast is not None:
        return fast

    for pattern in _DATE_PATTERNS:
        try:
            parsed = datetime.strptime(text, pattern).date()
        except ValueError:
            continue
        # 年月までの指定は月初として扱う
        return parsed

    wareki = _parse_wareki(text)
    if wareki is not None:
        return wareki
    raise CellValueError(f"日付として解釈できません: '{value}'")


def _parse_date_fast(text: str) -> date | None:
    """YYYY-MM-DD / YYYY/M/D / YYYY.MM.DD と、年月だけの形を速く読む。"""
    if len(text) >= 8 and text[4] in "-/.":
        sep = text[4]
        parts = text.split(sep)
        if len(parts) == 3:
            try:
                year, month, day = (int(parts[0]), int(parts[1]), int(parts[2]))
                return date(year, month, day)
            except ValueError:
                return None
        if len(parts) == 2:
            try:
                return date(int(parts[0]), int(parts[1]), 1)
            except ValueError:
                return None
    if text.endswith("日") and "年" in text and "月" in text:
        try:
            return datetime.strptime(text, "%Y年%m月%d日").date()
        except ValueError:
            try:
                return datetime.strptime(text, "%Y年%m月").date()
            except ValueError:
                return None
    return None


def parse_decimal(value: object) -> float | None:
    if value in (None, ""):
        return None
    text = unicodedata.normalize("NFKC", str(value))
    text = re.sub(r"[,¥￥円\s]", "", text)
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise CellValueError(f"金額として解釈できません: '{value}'") from exc


def parse_device_type(value: object) -> DeviceType | None:
    if value in (None, ""):
        return None
    key = normalize_header(str(value))
    if key in DEVICE_TYPE_ALIASES:
        return DEVICE_TYPE_ALIASES[key]
    raise CellValueError(f"種別として解釈できません: '{value}'")


def parse_device_status(value: object) -> DeviceStatus | None:
    if value in (None, ""):
        return None
    key = normalize_header(str(value))
    if key in DEVICE_STATUS_ALIASES:
        return DEVICE_STATUS_ALIASES[key]
    raise CellValueError(f"状態として解釈できません: '{value}'")


# ---------------------------------------------------------------------------
# 取り込み結果
# ---------------------------------------------------------------------------


class RowAction(enum.StrEnum):
    CREATE = "create"
    UPDATE = "update"
    SKIP = "skip"
    ERROR = "error"


@dataclass
class RowIssue:
    column: str | None
    message: str


@dataclass
class FieldChange:
    """1つの項目が、いまの値から何に変わるか。"""

    field: str
    label: str
    before: str
    after: str


@dataclass
class RowResult:
    row_number: int  # ヘッダを1行目としたときの行番号
    key: str
    action: RowAction
    issues: list[RowIssue] = field(default_factory=list)
    device_id: int | None = None
    contract_added: bool = False
    #: 既存の機器を直す行で、実際に変わる項目だけ。変わらない行は空
    changes: list[FieldChange] = field(default_factory=list)


#: 行ごとの結果を返す上限。件数・内訳は全件から出すが、中身を全部返すと
#: 16万行で応答が数十MBになり、画面側が固まる。エラー行を優先して残す
_ROW_SAMPLE = 200
#: 経過を知らせる間隔。細かすぎると取り込みそのものが遅くなる
_PROGRESS_STEP = 500


@dataclass
class ImportOutcome:
    committed: bool
    total_rows: int
    created: int
    updated: int
    skipped: int
    errors: int
    contracts_added: int
    mapping: dict[str, str]
    unmapped_columns: list[str]
    missing_required_fields: list[str]
    column_samples: dict[str, list[str]]
    rows: list[RowResult]
    #: 行結果の実数。rows はサンプルなので、多いときはこちらが正
    rows_total: int = 0


def _sample_rows(results: list[RowResult], limit: int | None = _ROW_SAMPLE) -> list[RowResult]:
    """返す行結果を絞る。エラーを先に出し、残りは出てきた順で埋める。"""
    if limit is None or len(results) <= limit:
        return results
    errors = [r for r in results if r.action is RowAction.ERROR]
    if len(errors) >= limit:
        return errors[:limit]
    chosen = list(errors)
    seen = {id(r) for r in chosen}
    for row in results:
        if id(row) in seen:
            continue
        chosen.append(row)
        if len(chosen) >= limit:
            break
    return chosen


class KeyField(enum.StrEnum):
    """既存機器を突き止めるためのキー列。

    社内台帳では管理IDで突合するのが一番やりやすい。ホスト名は変わりうるし、
    シリアルは空のことが多い。内部ID（書き出しの先頭列）がある行は、
    キーよりそちらを優先する。
    """

    CODE = "code"
    HOSTNAME = "hostname"
    SERIAL_NUMBER = "serial_number"


#: キー列に対応する Device のカラム
_KEY_COLUMNS = {
    KeyField.CODE: Device.code,
    KeyField.HOSTNAME: Device.hostname,
    KeyField.SERIAL_NUMBER: Device.serial_number,
}


def _sample_text(value: object) -> str:
    """Excel の日付セルは datetime で返るので、時刻を落として見やすくする。"""
    if isinstance(value, datetime):
        return value.strftime("%Y/%m/%d")
    if isinstance(value, date):
        return value.strftime("%Y/%m/%d")
    return str(value or "").strip()


def collect_samples(
    headers: list[str], rows: list[dict[str, object]], limit: int = 3
) -> dict[str, list[str]]:
    """列ごとの実データの例。画面で列を対応付けるとき、列名だけでは判断しづらいため添える。"""
    samples: dict[str, list[str]] = {}
    for header in headers:
        values: list[str] = []
        for row in rows:
            text = _sample_text(row.get(header, ""))
            if text and text not in values:
                values.append(text)
            if len(values) >= limit:
                break
        samples[header] = values
    return samples


def run_import(
    db: Session,
    table_headers: list[str],
    table_rows: list[dict[str, object]],
    *,
    mapping_override: dict[str, str] | None = None,
    key_field: KeyField = KeyField.CODE,
    update_existing: bool = True,
    commit: bool = False,
    keep_rows: int | None = _ROW_SAMPLE,
    on_progress: Callable[[int, int, str], None] | None = None,
    first_row: int = 2,
) -> ImportOutcome:
    """台帳データを取り込む。commit=False なら検証だけ行い DB は変更しない。

    この取り込みで発生した変更は、まとめて1つのバッチとして履歴に残る。

    `keep_rows` は返す行結果の上限。件数や内訳は全件から出す。エラー行の
    CSV 書き出しなど、全行が必要な呼び出しでは None を渡す。

    `on_progress` を渡すと、区切りごとに（済んだ行数, 全行数, いま何をして
    いるか）で呼ぶ。裏で走らせるときに、画面へ経過を見せるために使う。
    """
    with audit_context(ChangeSource.IMPORT, note="台帳の取り込み"):
        return _run_import(
            db,
            table_headers,
            table_rows,
            mapping_override=mapping_override,
            key_field=key_field,
            update_existing=update_existing,
            commit=commit,
            keep_rows=keep_rows,
            on_progress=on_progress,
            first_row=first_row,
        )


def _run_import(
    db: Session,
    table_headers: list[str],
    table_rows: list[dict[str, object]],
    *,
    mapping_override: dict[str, str] | None = None,
    key_field: KeyField = KeyField.CODE,
    update_existing: bool = True,
    commit: bool = False,
    keep_rows: int | None = _ROW_SAMPLE,
    on_progress: Callable[[int, int, str], None] | None = None,
    first_row: int = 2,
) -> ImportOutcome:
    total_rows = len(table_rows)

    def progress(done: int, note: str) -> None:
        if on_progress is not None:
            on_progress(done, total_rows, note)

    progress(0, "列の対応を見ています")
    mapping = detect_mapping(table_headers)
    for column, target in (mapping_override or {}).items():
        if target:
            mapping[column] = target
        else:
            mapping.pop(column, None)  # 空文字は「この列は使わない」の意味

    samples = collect_samples(table_headers, table_rows)
    mapped_fields = set(mapping.values())
    missing_required = [f for f in REQUIRED_FIELDS if f not in mapped_fields]
    unmapped = [h for h in table_headers if h not in mapping]

    if missing_required or str(key_field) not in mapped_fields:
        if str(key_field) not in mapped_fields and str(key_field) not in missing_required:
            missing_required.append(str(key_field))
        return ImportOutcome(
            committed=False,
            total_rows=len(table_rows),
            created=0,
            updated=0,
            skipped=0,
            errors=0,
            contracts_added=0,
            mapping=mapping,
            unmapped_columns=unmapped,
            missing_required_fields=missing_required,
            column_samples=samples,
            rows=[],
            rows_total=0,
        )

    # 行の変換は1回だけやる。以前は既存引きのたびにやり直しており、
    # 日付列がある台帳では行数の数倍だけ strptime が走っていた
    progress(0, "行を読み取っています")
    extracted: list[tuple[dict[str, object], list[RowIssue]]] = [
        _extract(raw, mapping) for raw in table_rows
    ]

    progress(0, "台帳の既存分を引いています")

    site_cache = {s.name: s for s in db.scalars(select(Site))}
    for site in list(site_cache.values()):
        if site.code:
            site_cache[site.code] = site

    # ファイルに出てくる鍵の分だけ、既存機器を先にまとめて引いておく。
    # 1行ずつ問い合わせると行数分の往復になり、取り込みが終わらなくなる
    existing_by_key = _existing_by_key(
        db,
        key_field,
        [str(values.get(str(key_field)) or "").strip() for values, _ in extracted],
    )

    # 機器ID が書かれている行は、その ID の機器を直す。ホスト名を書き換えたい
    # ときにこれが無いと、別名の機器が新しく作られるだけで直せない。
    # 書き出したファイルには ID が入っているので、Excel で直して戻せば通る
    wanted_ids: set[int] = set()
    for values, _ in extracted:
        number = _device_id_of(values)
        if number is not None:
            wanted_ids.add(number)
    by_id = _devices_by_ids(db, wanted_ids)

    # 重複判定に使う表。ファイルに出てくる名前の分だけ台帳を引く
    # （全件読むと、台数が増えたときに取り込みが終わらなくなる）
    wanted_names = [str(values.get("hostname") or "").strip() for values, _ in extracted]
    existing_by_name = _devices_by_hostnames(db, wanted_names)
    # 管理ID とシリアル番号も一意。DB の重複エラーで全体を落とさず、どの行かを
    # 返すために先に引く
    existing_by_unique = {
        name: _devices_by_unique(
            db, name, [str(values.get(name) or "").strip() for values, _ in extracted]
        )
        for name in _UNIQUE_FIELDS
    }
    #: このファイルで使った値と、その行番号（項目ごと）
    seen_unique: dict[str, dict[str, int]] = {name: {} for name in _UNIQUE_FIELDS}

    # このファイルで名前が変わる機器。入れ替えは正しい操作なので弾かない
    renaming_ids: set[int] = set()
    for values, _ in extracted:
        number = _device_id_of(values)
        if number is not None and number in by_id:
            renaming_ids.add(number)

    #: 行の結果と、その行で作った・更新した機器。id は最後にまとめて入れる
    pending: list[tuple[RowResult, Device]] = []
    #: 保守契約は機器の id が決まってからまとめて付ける（行ごとの flush を避ける）
    pending_contracts: list[tuple[RowResult, Device, dict[str, object]]] = []

    # 同一ファイル内の重複も検出したいので、投入済みキーを覚えておく
    seen_keys: dict[str, int] = {}

    results: list[RowResult] = []
    created = updated = skipped = errors = contracts_added = 0

    for position, (values, issues) in enumerate(extracted):
        # 結果に出す行番号はファイルの行番号。進み具合は何行目まで見たかで数える
        index = first_row + position
        if (position + 1) % _PROGRESS_STEP == 0:
            progress(position + 1, "1行ずつ見ています")
        key_value = str(values.get(str(key_field)) or "").strip()

        # 機器ID が書かれていれば、それがその行の身元。ホスト名が変わっていても
        # 同じ機器として扱う。これが無いと、名前を直したいのに別名の機器が
        # 新しく作られるだけで、書き出して直して戻す運用が成り立たない
        id_text = str(values.get("device_id") or "").strip()
        by_id_device: Device | None = None
        id_issue: RowIssue | None = None
        if id_text:
            number = _device_id_of(values)
            if number is None:
                id_issue = RowIssue(column="device_id", message="機器IDは数字で書いてください")
            else:
                by_id_device = by_id.get(number)
                if by_id_device is None:
                    id_issue = RowIssue(
                        column="device_id",
                        message=f"機器ID {id_text} は台帳にありません",
                    )
            if by_id_device is not None and not key_value:
                # 名前を空にして ID だけ書いた行も通す
                key_value = by_id_device.hostname

        result = RowResult(
            row_number=index,
            key=key_value or id_text,
            action=RowAction.ERROR,
            issues=list(issues),
        )

        if id_issue is not None:
            result.issues.append(id_issue)
        elif by_id_device is None and not key_value:
            result.issues.append(
                RowIssue(column=str(key_field), message=f"{FIELD_LABELS[key_field]}が空です")
            )
        for required in REQUIRED_FIELDS:
            if not str(values.get(required) or "").strip():
                result.issues.append(
                    RowIssue(column=required, message=f"{FIELD_LABELS[required]}は必須です")
                )
        # 同じ機器を1つのファイルで2度直そうとしていないか。ID があるときは
        # ID で、無いときはキー列で見る（名前を入れ替える行があっても弾かない）
        dedupe_key = f"id:{id_text}" if by_id_device is not None else key_value
        if dedupe_key and dedupe_key in seen_keys:
            result.issues.append(
                RowIssue(
                    column="device_id" if by_id_device is not None else str(key_field),
                    message=f"{seen_keys[dedupe_key]}行目と重複しています",
                )
            )

        if result.issues:
            errors += 1
            results.append(result)
            continue

        seen_keys[dedupe_key] = index
        existing = by_id_device if by_id_device is not None else existing_by_key.get(key_value)

        if existing is not None and not update_existing:
            result.action = RowAction.SKIP
            result.device_id = existing.id
            result.issues.append(RowIssue(column=None, message="既存機器のためスキップしました"))
            skipped += 1
            results.append(result)
            continue

        device = existing or Device()

        # 他の機器が既に使っている名前にしようとしていないか。DB の重複エラーを
        # そのまま出すと、何行目の何が悪いのか分からない
        wanted_name = str(values.get("hostname") or "").strip()
        if wanted_name:
            owner = existing_by_name.get(normalize_hostname(wanted_name))
            if owner is not None and owner is not device and owner.id not in renaming_ids:
                result.issues.append(
                    RowIssue(
                        column="hostname",
                        message=f"ホスト名「{wanted_name}」は他の機器が使っています",
                    )
                )
                errors += 1
                results.append(result)
                continue

        # 管理ID とシリアル番号も、他の機器のものや、このファイルの別の行と重ならないか見る
        wanted_unique = {name: str(values.get(name) or "").strip() for name in _UNIQUE_FIELDS}
        for name, wanted in wanted_unique.items():
            if not wanted:
                continue
            label = FIELD_LABELS[name]
            owner_id = existing_by_unique[name].get(wanted)
            if owner_id is not None and owner_id != device.id:
                result.issues.append(
                    RowIssue(column=name, message=f"{label}「{wanted}」は他の機器が使っています")
                )
            elif wanted in seen_unique[name]:
                result.issues.append(
                    RowIssue(
                        column=name,
                        message=f"{label}「{wanted}」が{seen_unique[name][wanted]}行目と重複しています",
                    )
                )
        if result.issues:
            errors += 1
            results.append(result)
            continue
        for name, wanted in wanted_unique.items():
            if wanted:
                seen_unique[name][wanted] = index

        # 既存を直す行は、変わる前の値を控える。どこが変わるのかを人が
        # 見てから取り込めるようにするため（変わらない行は出さない）
        before = _snapshot(device, values) if existing is not None else {}

        _apply_values(device, values, db, site_cache)

        if existing is not None:
            result.changes = _changes_between(before, _snapshot(device, values))

        if device.eos_date and device.eol_date and device.eol_date < device.eos_date:
            result.issues.append(
                RowIssue(column="eol_date", message="EoL が EoS より前の日付になっています")
            )
            errors += 1
            if existing is not None:
                db.expire(device)  # 既存機器への変更を破棄する
            results.append(result)
            continue

        if existing is None:
            if commit:
                db.add(device)
            result.action = RowAction.CREATE
            created += 1
            if wanted_name:
                existing_by_name[normalize_hostname(wanted_name)] = device
        else:
            result.action = RowAction.UPDATE
            result.device_id = existing.id
            updated += 1
            if wanted_name:
                existing_by_name[normalize_hostname(wanted_name)] = device

        if _has_contract(values) and _contract_would_be_added(device, values):
            contracts_added += 1
            result.contract_added = True
            if commit:
                pending_contracts.append((result, device, values))

        if commit:
            pending.append((result, device))

        results.append(result)

    progress(total_rows, "台帳に書いています" if commit else "結果をまとめています")

    if commit:
        # 問題のある行だけを落とし、残りは取り込む。
        #
        # 以前は1行でも問題があれば全部を取り込まない作りだった。数十行の表なら
        # それで直せるが、16万行の台帳では、重複が1組あるだけで丸ごと入らない。
        # しかも「◯件 作成」と返るので、入ったように見えて実際は空、という
        # いちばん困る形になっていた。
        #
        # 確認だけなら、16万件を flush してから rollback する必要はない。
        # 件数と行結果はすでに揃っている。反映するときだけまとめて書く。
        if pending:
            _flush_renames(db, [device for _, device in pending])
            db.flush()
            for result, device in pending:
                result.device_id = device.id
            for _result, device, values in pending_contracts:
                _add_contract(db, device, values)
            if pending_contracts:
                db.flush()
        # 結線表を先に取り込んでいた場合、機器が揃った行をここで結ぶ
        from app.services.linkimport import resolve_pending

        resolve_pending(db)
        db.commit()
        committed = True
    else:
        # 下見のときは、既存機器への途中変更を捨てる
        db.rollback()
        committed = False

    return ImportOutcome(
        committed=committed,
        total_rows=len(table_rows),
        created=created,
        updated=updated,
        skipped=skipped,
        errors=errors,
        contracts_added=contracts_added,
        mapping=mapping,
        unmapped_columns=unmapped,
        missing_required_fields=[],
        column_samples=samples,
        rows=_sample_rows(results, keep_rows) if keep_rows is not None else results,
        rows_total=len(results),
    )


def _extract(
    raw_row: dict[str, object], mapping: dict[str, str]
) -> tuple[dict[str, object], list[RowIssue]]:
    """1行分を {フィールド: 変換済みの値} に変換する。変換エラーは issue として返す。"""
    values: dict[str, object] = {}
    issues: list[RowIssue] = []

    for column, target in mapping.items():
        raw = raw_row.get(column, "")
        try:
            if target in DATE_FIELDS:
                values[target] = parse_date(raw)
            elif target == "contract_annual_cost":
                values[target] = parse_decimal(raw)
            elif target == "device_type":
                values[target] = parse_device_type(raw)
            elif target == "status":
                values[target] = parse_device_status(raw)
            else:
                text = str(raw).strip() if raw is not None else ""
                values[target] = text or None
        except CellValueError as exc:
            issues.append(RowIssue(column=target, message=str(exc)))

    return values, issues


def _snapshot(device: Device, values: dict[str, object]) -> dict[str, str]:
    """その行が触る項目の、いまの値。"""
    taken: dict[str, str] = {}
    for name in values:
        if name in {"site", "site_code"}:
            site = getattr(device, "site", None)
            taken["site"] = site.name if site is not None else ""
            continue
        if not hasattr(device, name):
            continue
        taken[name] = _as_text(getattr(device, name))
    return taken


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, enum.Enum):
        return str(value.value)
    return str(value)


def _changes_between(before: dict[str, str], after: dict[str, str]) -> list[FieldChange]:
    """変わった項目だけを並べる。順番は台帳の並びに合わせる。"""
    order = list(FIELD_LABELS)
    changed = [
        FieldChange(
            field=name,
            label=FIELD_LABELS.get(name, name),
            before=before.get(name, ""),
            after=after.get(name, ""),
        )
        for name in after
        if before.get(name, "") != after.get(name, "")
    ]
    changed.sort(key=lambda item: order.index(item.field) if item.field in order else len(order))
    return changed


def _flush_renames(db: Session, devices: list[Device]) -> None:
    """名前を変える機器を、いったん仮の名前にしてから書き込む。

    ホスト名には重複禁止が掛かっている。1つのファイルで2台の名前を
    入れ替えると、書き込む順番によっては途中で一時的に重複し、DB に
    弾かれる（利用者から見れば理由の分からない失敗になる）。

    先に全部を衝突しない仮の名前にしてから、本来の名前を入れ直す。
    こうすると順番に関係なく通る。
    """
    from sqlalchemy import inspect as sa_inspect

    intended: dict[Device, str] = {}
    for device in devices:
        if device.id is None:
            continue  # 新規はまだ誰とも重ならない
        history = sa_inspect(device).attrs.hostname.history
        if history.has_changes() and history.deleted:
            intended[device] = device.hostname

    if not intended:
        return

    for device in intended:
        device.hostname = f"__netvision_renaming_{device.id}__"
    db.flush()
    for device, name in intended.items():
        device.hostname = name
    db.flush()


def _find_existing(db: Session, key_field: KeyField, key_value: str) -> Device | None:
    column = _KEY_COLUMNS[key_field]
    return db.scalar(select(Device).where(column == key_value))


#: 既存機器を先に引くときの1回あたりの件数。IN 句が長くなりすぎないように分ける
_LOOKUP_CHUNK = 500


def _device_lookup():
    """既存機器を引くときの共通オプション。

    保守契約列があると、契約の有無を1台ずつ見に行って行数分の問い合わせに
    なる。先に契約も載せておけば、16万行でも問い合わせは塊の数だけになる。
    """
    return selectinload(Device.contracts)


def _existing_by_key(db: Session, key_field: KeyField, keys: list[str]) -> dict[str, Device]:
    """ファイルに出てくる鍵の分だけ、既存機器をまとめて引く。

    以前は1行ごとに1回ずつ問い合わせていた。1万行なら1万回で、行が増える
    ほど時間が伸びる。取り込みが終わらないという症状の主因がこれだった。
    ファイルに出てくる鍵は先に分かるので、まとめて引いて表に載せておく。
    """
    column = _KEY_COLUMNS[key_field]
    found: dict[str, Device] = {}
    unique = [key for key in dict.fromkeys(keys) if key]
    for start in range(0, len(unique), _LOOKUP_CHUNK):
        chunk = unique[start : start + _LOOKUP_CHUNK]
        stmt = select(Device).where(column.in_(chunk)).options(_device_lookup())
        for device in db.scalars(stmt):
            value = getattr(device, column.key)
            if value is not None:
                found[str(value)] = device
    return found


#: 機器ID として読む上限。Postgres の integer（int4）の最大値。これを超える数で
#: 台帳を引くと DB が値の範囲外として落ちる（SQLite でも 64bit を超えると落ちる）
_MAX_DEVICE_ID = 2**31 - 1


def _device_id_of(values: dict[str, object]) -> int | None:
    """行に書かれた機器ID。ID として読めなければ None。

    別の列（シリアル番号など）を誤って割り当てると、桁の多い数が入る。
    `str.isdigit()` は「²」のような文字も数字とみなすので、ASCII の数字だけを見る。
    """
    text = str(values.get("device_id") or "").strip()
    if not (text.isascii() and text.isdigit()):
        return None
    number = int(text)
    return number if 0 < number <= _MAX_DEVICE_ID else None


def _devices_by_ids(db: Session, wanted_ids: set[int]) -> dict[int, Device]:
    by_id: dict[int, Device] = {}
    if not wanted_ids:
        return by_id
    ids = sorted(wanted_ids)
    for start in range(0, len(ids), _LOOKUP_CHUNK):
        chunk = ids[start : start + _LOOKUP_CHUNK]
        stmt = select(Device).where(Device.id.in_(chunk)).options(_device_lookup())
        for device in db.scalars(stmt):
            by_id[device.id] = device
    return by_id


def _devices_by_hostnames(db: Session, names: list[str]) -> dict[str, Device]:
    found: dict[str, Device] = {}
    unique = [n for n in dict.fromkeys(names) if n]
    for start in range(0, len(unique), _LOOKUP_CHUNK):
        chunk = unique[start : start + _LOOKUP_CHUNK]
        stmt = select(Device).where(Device.hostname.in_(chunk)).options(_device_lookup())
        for device in db.scalars(stmt):
            found[normalize_hostname(device.hostname)] = device
    return found


#: 台帳で一意な項目のうち、ホスト名のほかに取り込みで書けるもの。
#: ここが重なると DB の一意制約で取り込み全体が落ちるので、行のエラーとして先に返す
_UNIQUE_FIELDS = ("code", "serial_number")


def _devices_by_unique(db: Session, field_name: str, values: list[str]) -> dict[str, int]:
    """一意な項目（管理ID・シリアル番号）の値から、それを持つ機器の id を引く。

    重なりを見るだけなので、機器そのもの（関連ごと）は読まない。読むと、
    書き出した台帳を戻すような全行更新で、取り込みが3割ほど遅くなる。
    """
    column = getattr(Device, field_name)
    found: dict[str, int] = {}
    unique = [value for value in dict.fromkeys(values) if value]
    for start in range(0, len(unique), _LOOKUP_CHUNK):
        chunk = unique[start : start + _LOOKUP_CHUNK]
        for held, device_id in db.execute(select(column, Device.id).where(column.in_(chunk))):
            if held:
                found[held] = device_id
    return found


#: 大文字小文字の違いだけで別のメーカーとして数えないための対応表。
#: 台帳は人が書くので Cisco / cisco / CISCO が混ざる。分かれて数えられると、
#: メーカー別の台数も、そこから引く保守費の見積もりも割れる
_VENDOR_CANONICAL = {
    name.casefold(): name for name in dict.fromkeys(configparse.VENDOR_NAMES.values())
}


def _canonical_vendor(text: str, db: Session) -> str:
    """書き方のゆれだけをそろえる。知らない名前は書き換えない。"""
    key = text.casefold()
    known = _VENDOR_CANONICAL.get(key)
    if known is not None:
        return known
    # 台帳にすでにある綴りに合わせる。どちらが正しいかはこちらでは決められない
    # ので、先に入っているほうを残す
    for existing in db.scalars(select(Device.vendor).distinct()):
        if existing and existing.casefold() == key:
            return existing
    return text


def _apply_values(
    device: Device, values: dict[str, object], db: Session, site_cache: dict[str, Site]
) -> None:
    """値が入っている項目だけ反映する（台帳の空欄で既存値を消さないため）。"""
    for name in DEVICE_FIELDS:
        if name in {"site", "site_code"}:
            continue
        value = values.get(name)
        if value is not None:
            if name == "code":
                text = str(value).strip()
                setattr(device, name, text or None)
            elif name == "vendor":
                setattr(device, name, _canonical_vendor(str(value).strip(), db))
            else:
                setattr(device, name, value)

    site_code = values.get("site_code")
    site_name = values.get("site")
    if site_code:
        key = str(site_code).strip()
        site = site_cache.get(key)
        if site is not None and site_name:
            # 拠点IDが同じなら同じ拠点。名前はその拠点の呼び名なので、
            # 違う名前で来たら書き換える。打ち間違いを直す手段が
            # 拠点の台帳しか無いと、機器の台帳を直しても直らない
            new_name = str(site_name).strip()
            if new_name and new_name != site.name:
                site_cache.pop(site.name, None)
                site.name = new_name
                site_cache[new_name] = site
        if site is None:
            # 拠点IDは人が決めるものなので、無いときは名前なしで起こさずエラーにする
            # （呼び出し側の issues に載せるのは難しいので、ここでは名前列があれば併用）
            if site_name:
                known = site_cache.get(str(site_name))
                # 名前が同じでも、別の拠点IDが振ってあるなら別の拠点。
                # 同名のビル・同名の営業所は普通にあるので、混ぜてはいけない
                if known is not None and not known.code:
                    known.code = key
                    site_cache[key] = known
                    site = known
                else:
                    site = Site(name=str(site_name), code=key)
                    db.add(site)
                    site_cache.setdefault(str(site_name), site)
                    site_cache[key] = site
            else:
                site = Site(name=key, code=key)
                db.add(site)
                site_cache[key] = site
        device.site = site
    elif site_name:
        site = site_cache.get(str(site_name))
        if site is None:
            site = Site(name=str(site_name))
            db.add(site)
            site_cache[str(site_name)] = site
        # site_id をその場で採番するために flush すると、溜まっている機器まで
        # まとめて書かれてしまう。関係を渡せば、最後の flush で順番に載る
        device.site = site

    if device.device_type is None:
        device.device_type = DeviceType.OTHER
    if device.status is None:
        device.status = DeviceStatus.ACTIVE


def _has_contract(values: dict[str, object]) -> bool:
    """この行に保守契約の列が入っているか（_add_contract の判定と揃えること）。"""
    return bool(values.get("contract_end_date"))


def _contract_would_be_added(device: Device, values: dict[str, object]) -> bool:
    """この行の保守契約が新規として付くか。書かずに判定する（プレビュー用）。"""
    end = values.get("contract_end_date")
    if not end:
        return False
    provider = values.get("contract_provider")
    number = values.get("contract_number")
    for existing in device.contracts:
        same_number = number and existing.contract_number == number
        same_period = existing.end_date == end and existing.provider == (provider or "不明")
        if same_number or same_period:
            return False
    return True


def _add_contract(db: Session, device: Device, values: dict[str, object]) -> bool:
    """保守契約列があれば契約を追加する。同じ契約が既にあれば何もしない。"""
    if not _contract_would_be_added(device, values):
        return False

    start = values.get("contract_start_date")
    end = values.get("contract_end_date")
    provider = values.get("contract_provider")
    number = values.get("contract_number")

    # device= で渡す。device_id だけだと、同一セッション内の
    # device.contracts が空のまま残り、再取り込みで二重に付いてしまう
    db.add(
        MaintenanceContract(
            device=device,
            provider=str(provider) if provider else "不明",
            contract_number=str(number) if number else None,
            service_level=(
                str(values["contract_service_level"])
                if values.get("contract_service_level")
                else None
            ),
            # 開始日が台帳にない台帳も多いので、その場合は終了日を暫定値にする
            start_date=start or end,
            end_date=end,
            annual_cost=values.get("contract_annual_cost"),
        )
    )
    return True
