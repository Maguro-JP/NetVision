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
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, DeviceStatus, DeviceType, MaintenanceContract, Site

# ---------------------------------------------------------------------------
# 取り込み可能なフィールド
# ---------------------------------------------------------------------------

DEVICE_FIELDS = (
    "hostname",
    "vendor",
    "model",
    "serial_number",
    "device_type",
    "status",
    "os_name",
    "os_version",
    "firmware_version",
    "site",
    "rack",
    "rack_unit",
    "mgmt_ip",
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

IMPORTABLE_FIELDS = DEVICE_FIELDS + CONTRACT_FIELDS

REQUIRED_FIELDS = ("hostname", "vendor", "model")

DATE_FIELDS = frozenset(
    {"install_date", "eos_date", "eol_date", "contract_start_date", "contract_end_date"}
)

FIELD_LABELS: dict[str, str] = {
    "hostname": "ホスト名",
    "vendor": "ベンダー",
    "model": "型番",
    "serial_number": "シリアル番号",
    "device_type": "種別",
    "status": "状態",
    "os_name": "OS名",
    "os_version": "OSバージョン",
    "firmware_version": "ファームウェアバージョン",
    "site": "拠点",
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
    "hostname": (
        "hostname",
        "host",
        "ホスト名",
        "機器名",
        "装置名",
        "名称",
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
    "wlc": DeviceType.WIRELESS_CONTROLLER,
    "server": DeviceType.SERVER,
    "サーバ": DeviceType.SERVER,
    "サーバー": DeviceType.SERVER,
    "other": DeviceType.OTHER,
    "その他": DeviceType.OTHER,
}

DEVICE_STATUS_ALIASES: dict[str, DeviceStatus] = {
    "active": DeviceStatus.ACTIVE,
    "稼働中": DeviceStatus.ACTIVE,
    "稼動中": DeviceStatus.ACTIVE,
    "運用中": DeviceStatus.ACTIVE,
    "使用中": DeviceStatus.ACTIVE,
    "standby": DeviceStatus.STANDBY,
    "予備": DeviceStatus.STANDBY,
    "予備機": DeviceStatus.STANDBY,
    "待機": DeviceStatus.STANDBY,
    "maintenance": DeviceStatus.MAINTENANCE,
    "保守中": DeviceStatus.MAINTENANCE,
    "保守作業中": DeviceStatus.MAINTENANCE,
    "retired": DeviceStatus.RETIRED,
    "撤去": DeviceStatus.RETIRED,
    "撤去済": DeviceStatus.RETIRED,
    "撤去済み": DeviceStatus.RETIRED,
    "廃棄": DeviceStatus.RETIRED,
    "廃止": DeviceStatus.RETIRED,
}


def normalize_header(header: str) -> str:
    """列名を突き合わせ用に正規化する（全角/半角・大小文字・記号の揺れを吸収）。"""
    text = unicodedata.normalize("NFKC", header or "").lower()
    return re.sub(r"[\s_\-/()\[\]（）【】.,:：・]", "", text)


_ALIAS_INDEX: dict[str, str] = {}
for _field, _aliases in FIELD_ALIASES.items():
    _ALIAS_INDEX[normalize_header(_field)] = _field
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

    for pattern in _DATE_PATTERNS:
        try:
            parsed = datetime.strptime(text, pattern).date()
        except ValueError:
            continue
        # 年月までの指定は月初として扱う
        return parsed
    raise CellValueError(f"日付として解釈できません: '{value}'")


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
class RowResult:
    row_number: int  # ヘッダを1行目としたときの行番号
    key: str
    action: RowAction
    issues: list[RowIssue] = field(default_factory=list)
    device_id: int | None = None
    contract_added: bool = False


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
    rows: list[RowResult]


class KeyField(enum.StrEnum):
    """既存機器を突き止めるためのキー列。"""

    HOSTNAME = "hostname"
    SERIAL_NUMBER = "serial_number"


def run_import(
    db: Session,
    table_headers: list[str],
    table_rows: list[dict[str, object]],
    *,
    mapping_override: dict[str, str] | None = None,
    key_field: KeyField = KeyField.HOSTNAME,
    update_existing: bool = True,
    commit: bool = False,
) -> ImportOutcome:
    """台帳データを取り込む。commit=False なら検証だけ行い DB は変更しない。"""

    mapping = detect_mapping(table_headers)
    for column, target in (mapping_override or {}).items():
        if target:
            mapping[column] = target
        else:
            mapping.pop(column, None)  # 空文字は「この列は使わない」の意味

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
            rows=[],
        )

    site_cache = {s.name: s for s in db.scalars(select(Site))}
    # 同一ファイル内の重複も検出したいので、投入済みキーを覚えておく
    seen_keys: dict[str, int] = {}

    results: list[RowResult] = []
    created = updated = skipped = errors = contracts_added = 0

    for index, raw_row in enumerate(table_rows, start=2):
        values, issues = _extract(raw_row, mapping)
        key_value = str(values.get(str(key_field)) or "").strip()

        result = RowResult(row_number=index, key=key_value, action=RowAction.ERROR, issues=issues)

        if not key_value:
            result.issues.append(
                RowIssue(column=str(key_field), message=f"{FIELD_LABELS[key_field]}が空です")
            )
        for required in REQUIRED_FIELDS:
            if not str(values.get(required) or "").strip():
                result.issues.append(
                    RowIssue(column=required, message=f"{FIELD_LABELS[required]}は必須です")
                )
        if key_value and key_value in seen_keys:
            result.issues.append(
                RowIssue(
                    column=str(key_field), message=f"{seen_keys[key_value]}行目と重複しています"
                )
            )

        if result.issues:
            errors += 1
            results.append(result)
            continue

        seen_keys[key_value] = index
        existing = _find_existing(db, key_field, key_value)

        if existing is not None and not update_existing:
            result.action = RowAction.SKIP
            result.device_id = existing.id
            result.issues.append(RowIssue(column=None, message="既存機器のためスキップしました"))
            skipped += 1
            results.append(result)
            continue

        device = existing or Device()
        _apply_values(device, values, db, site_cache)

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
            db.add(device)
            result.action = RowAction.CREATE
            created += 1
        else:
            result.action = RowAction.UPDATE
            updated += 1

        db.flush()
        result.device_id = device.id

        if _add_contract(db, device, values):
            contracts_added += 1
            result.contract_added = True

        results.append(result)

    if commit and errors == 0:
        db.commit()
        committed = True
    else:
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
        rows=results,
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


def _find_existing(db: Session, key_field: KeyField, key_value: str) -> Device | None:
    column = Device.hostname if key_field is KeyField.HOSTNAME else Device.serial_number
    return db.scalar(select(Device).where(column == key_value))


def _apply_values(
    device: Device, values: dict[str, object], db: Session, site_cache: dict[str, Site]
) -> None:
    """値が入っている項目だけ反映する（台帳の空欄で既存値を消さないため）。"""
    for name in DEVICE_FIELDS:
        if name == "site":
            continue
        value = values.get(name)
        if value is not None:
            setattr(device, name, value)

    site_name = values.get("site")
    if site_name:
        site = site_cache.get(str(site_name))
        if site is None:
            site = Site(name=str(site_name))
            db.add(site)
            db.flush()
            site_cache[str(site_name)] = site
        device.site_id = site.id

    if device.device_type is None:
        device.device_type = DeviceType.OTHER
    if device.status is None:
        device.status = DeviceStatus.ACTIVE


def _add_contract(db: Session, device: Device, values: dict[str, object]) -> bool:
    """保守契約列があれば契約を追加する。同じ契約が既にあれば何もしない。"""
    start = values.get("contract_start_date")
    end = values.get("contract_end_date")
    provider = values.get("contract_provider")

    if not end:
        return False  # 終了日がなければ保守期限として意味を持たない

    number = values.get("contract_number")
    for existing in device.contracts:
        same_number = number and existing.contract_number == number
        same_period = existing.end_date == end and existing.provider == (provider or "不明")
        if same_number or same_period:
            return False

    db.add(
        MaintenanceContract(
            device_id=device.id,
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
    db.flush()
    db.refresh(device)
    return True
