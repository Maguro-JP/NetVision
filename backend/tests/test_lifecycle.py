from datetime import date, timedelta

from app.models import Device, DeviceStatus, MaintenanceContract
from app.services.lifecycle import Severity, evaluate, severity_for

TODAY = date(2026, 8, 5)


def _device(**kwargs) -> Device:
    device = Device(hostname="sw-01", vendor="Cisco", model="C9300", **kwargs)
    if device.status is None:
        device.status = DeviceStatus.ACTIVE
    return device


def test_severity_thresholds():
    assert severity_for(TODAY - timedelta(days=1), TODAY) is Severity.EXPIRED
    assert severity_for(TODAY, TODAY) is Severity.CRITICAL
    assert severity_for(TODAY + timedelta(days=90), TODAY) is Severity.CRITICAL
    assert severity_for(TODAY + timedelta(days=91), TODAY) is Severity.WARNING
    assert severity_for(TODAY + timedelta(days=365), TODAY) is Severity.WARNING
    assert severity_for(TODAY + timedelta(days=366), TODAY) is Severity.OK
    assert severity_for(None, TODAY) is Severity.UNKNOWN


def test_maintenance_end_uses_latest_contract():
    device = _device(status=DeviceStatus.ACTIVE)
    device.contracts = [
        MaintenanceContract(provider="A", start_date=date(2021, 4, 1), end_date=date(2024, 3, 31)),
        MaintenanceContract(provider="A", start_date=date(2024, 4, 1), end_date=date(2027, 3, 31)),
    ]
    status = evaluate(device, TODAY)
    assert status.maintenance_end == date(2027, 3, 31)
    assert status.days_to_maintenance_end == 238
    assert status.maintenance_severity is Severity.WARNING


def test_overall_severity_is_worst_of_maintenance_and_eol():
    device = _device(status=DeviceStatus.ACTIVE, eol_date=TODAY + timedelta(days=30))
    device.contracts = [
        MaintenanceContract(
            provider="A", start_date=date(2021, 4, 1), end_date=TODAY + timedelta(days=900)
        )
    ]
    status = evaluate(device, TODAY)
    assert status.maintenance_severity is Severity.OK
    assert status.eol_severity is Severity.CRITICAL
    assert status.overall_severity is Severity.CRITICAL


def test_retired_devices_do_not_alert():
    device = _device(status=DeviceStatus.RETIRED, eol_date=TODAY - timedelta(days=500))
    assert evaluate(device, TODAY).overall_severity is Severity.OK


def test_age_years_from_install_date():
    device = _device(status=DeviceStatus.ACTIVE, install_date=date(2021, 8, 5))
    assert evaluate(device, TODAY).age_years == 5.0


def test_device_without_dates_is_unknown():
    status = evaluate(_device(status=DeviceStatus.ACTIVE), TODAY)
    assert status.maintenance_end is None
    assert status.overall_severity is Severity.UNKNOWN
