from app.models import Device
from app.schemas.device import DeviceDetail, DeviceRead, LifecycleStatusRead
from app.services.lifecycle import evaluate


def to_device_read(device: Device) -> DeviceRead:
    return DeviceRead(
        **{k: getattr(device, k) for k in DeviceRead.model_fields if hasattr(device, k)},
        site_name=device.site.name if device.site else None,
        lifecycle=LifecycleStatusRead(**evaluate(device).__dict__),
    )


def to_device_detail(device: Device) -> DeviceDetail:
    base = to_device_read(device)
    return DeviceDetail(**base.model_dump(), contracts=list(device.contracts))
