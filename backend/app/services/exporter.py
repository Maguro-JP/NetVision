"""機器台帳の書き出し。

列見出しは取り込み側（importer.FIELD_LABELS）と揃えてあるので、
書き出したファイルをそのまま取り込み直せる（往復できる）。
Excel で編集して戻す運用や、稟議・見積依頼用の資料作成を想定している。
"""

from __future__ import annotations

import csv
import io
from datetime import date

from app.models import Device, MaintenanceContract
from app.services.importer import FIELD_LABELS, IMPORTABLE_FIELDS
from app.services.lifecycle import LifecycleStatus, Severity, evaluate

# 取り込みでは使わないが、資料として役に立つ算出列
STATUS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("maintenance_end", "保守期限(算出)"),
    ("days_to_maintenance_end", "保守期限まで(日)"),
    ("days_to_eol", "EoLまで(日)"),
    ("age_years", "経過年数"),
    ("overall_severity", "アラート"),
)

SEVERITY_LABELS: dict[Severity, str] = {
    Severity.EXPIRED: "期限切れ",
    Severity.CRITICAL: "要対応",
    Severity.WARNING: "要計画",
    Severity.OK: "問題なし",
    Severity.UNKNOWN: "情報未登録",
}

# 取り込み時に受け付ける日本語表記のうち、書き出しで使う代表表記を逆引きする
_TYPE_LABELS = {
    "switch": "スイッチ",
    "router": "ルータ",
    "firewall": "ファイアウォール",
    "load_balancer": "ロードバランサ",
    "wireless_ap": "無線AP",
    "wireless_controller": "無線コントローラ",
    "server": "サーバ",
    "other": "その他",
}
_STATUS_LABELS = {
    "active": "稼働中",
    "standby": "予備機",
    "maintenance": "保守中",
    "retired": "撤去済み",
}

# これらの表記が取り込み側でも解釈できることは tests/test_export_api.py で担保している


def build_headers(include_status: bool = True) -> list[str]:
    headers = [FIELD_LABELS[f] for f in IMPORTABLE_FIELDS]
    if include_status:
        headers += [label for _, label in STATUS_COLUMNS]
    return headers


def _latest_contract(device: Device) -> MaintenanceContract | None:
    contracts = [c for c in device.contracts if c.end_date is not None]
    return max(contracts, key=lambda c: c.end_date) if contracts else None


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.strftime("%Y/%m/%d")
    return str(value)


def build_row(device: Device, include_status: bool = True) -> list[object]:
    """1台分の行を作る。保守契約は最も終了日が遠いものを代表として書き出す。"""
    contract = _latest_contract(device)
    status: LifecycleStatus = evaluate(device)

    values: dict[str, object] = {
        "device_id": device.id,
        "code": device.code,
        "hostname": device.hostname,
        "vendor": device.vendor,
        "model": device.model,
        "serial_number": device.serial_number,
        "device_type": _TYPE_LABELS.get(str(device.device_type), ""),
        "status": _STATUS_LABELS.get(str(device.status), ""),
        "os_name": device.os_name,
        "os_version": device.os_version,
        "firmware_version": device.firmware_version,
        "site": device.site.name if device.site else None,
        "site_code": device.site.code if device.site else None,
        "rack": device.rack,
        "rack_unit": device.rack_unit,
        "mgmt_ip": device.mgmt_ip,
        "install_date": device.install_date,
        "eos_date": device.eos_date,
        "eol_date": device.eol_date,
        "notes": device.notes,
        "contract_provider": contract.provider if contract else None,
        "contract_number": contract.contract_number if contract else None,
        "contract_service_level": contract.service_level if contract else None,
        "contract_start_date": contract.start_date if contract else None,
        "contract_end_date": contract.end_date if contract else None,
        "contract_annual_cost": (
            float(contract.annual_cost) if contract and contract.annual_cost is not None else None
        ),
    }

    row: list[object] = [values[f] for f in IMPORTABLE_FIELDS]
    if include_status:
        row += [
            status.maintenance_end,
            status.days_to_maintenance_end,
            status.days_to_eol,
            status.age_years,
            SEVERITY_LABELS[status.overall_severity],
        ]
    return row


def to_csv(
    devices: list[Device], *, include_status: bool = True, encoding: str = "utf-8-sig"
) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(build_headers(include_status))
    for device in devices:
        writer.writerow([_text(v) for v in build_row(device, include_status)])
    # Shift_JIS に無い文字（絵文字や一部の記号）で落ちないよう、変換できない文字は代替する
    return buffer.getvalue().encode(encoding, errors="replace")


def to_xlsx(devices: list[Device], *, include_status: bool = True) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "機器台帳"

    headers = build_headers(include_status)
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for device in devices:
        # 日付は Excel の日付セルとして書き、数値は数値のまま入れる
        sheet.append(build_row(device, include_status))

    date_headers = {
        FIELD_LABELS[f]
        for f in (
            "install_date",
            "eos_date",
            "eol_date",
            "contract_start_date",
            "contract_end_date",
        )
    } | {STATUS_COLUMNS[0][1]}

    for index, header in enumerate(headers, start=1):
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = min(max(len(header) * 2, 12), 40)
        if header in date_headers:
            for cell in sheet[letter][1:]:
                cell.number_format = "yyyy/mm/dd"

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
