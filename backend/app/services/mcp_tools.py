"""NetVision を、外の AI から「道具」として呼べるようにするための口。

MCP（Model Context Protocol）は、AI の側から道具を呼び出すための取り決め。
ここでは台帳を読むだけの道具を並べる。書き換えの口は置かない。台帳を
壊す事故は取り返しがつかないうえ、誰が書いたのかも残らないため。

引数と戻り値は、そのまま JSON にできる形にしてある。処理の中身は画面と
同じ層（device_query など）を通す。同じ問いに画面と違う答えが出ると、
どちらが正しいのか誰にも分からなくなる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Device, Site
from app.models.topology import Link
from app.services import device_query, lifecycle
from app.services.device_query import DeviceFilters

#: 一度に返す上限。AI に渡す文字数には限りがあるので、多く求められても切る
MAX_LIMIT = 200
DEFAULT_LIMIT = 20


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]


def _limit(value: Any, default: int = DEFAULT_LIMIT) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, MAX_LIMIT))


def _device_brief(device: Device) -> dict[str, Any]:
    status = lifecycle.evaluate(device)
    return {
        "id": device.id,
        "code": device.code,
        "hostname": device.hostname,
        "label": device.label,
        "vendor": device.vendor,
        "model": device.model,
        "device_type": device.device_type.value,
        "status": device.status.value,
        "site": device.site.name if device.site else None,
        "mgmt_ip": device.mgmt_ip,
        "severity": status.overall_severity.value,
    }


def _device_detail(db: Session, device: Device) -> dict[str, Any]:
    status = lifecycle.evaluate(device)
    detail = _device_brief(device)
    detail.update(
        {
            "serial_number": device.serial_number,
            "os_name": device.os_name,
            "os_version": device.os_version,
            "firmware_version": device.firmware_version,
            "rack": device.rack,
            "rack_unit": device.rack_unit,
            "install_date": _as_text(device.install_date),
            "maintenance_end": _as_text(status.maintenance_end),
            "eos_date": _as_text(status.effective_eos_date),
            "eol_date": _as_text(status.effective_eol_date),
            "notes": device.notes,
            "links": _links_of(db, device.id),
        }
    )
    return detail


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _links_of(db: Session, device_id: int, limit: int = MAX_LIMIT) -> list[dict[str, Any]]:
    stmt = (
        select(Link)
        .where(or_(Link.a_device_id == device_id, Link.b_device_id == device_id))
        .order_by(Link.id)
        .limit(limit)
    )
    names = _hostnames(db)
    out: list[dict[str, Any]] = []
    for link in db.scalars(stmt):
        near, far = link.a_device_id, link.b_device_id
        near_if, far_if = link.a_interface, link.b_interface
        if far == device_id:
            near, far = far, near
            near_if, far_if = far_if, near_if
        out.append(
            {
                "interface": near_if,
                "peer": names.get(far, str(far)),
                "peer_interface": far_if,
                "origin": link.origin.value,
                "confirmed_both_ways": link.confirmed_both_ways,
            }
        )
    return out


def _hostnames(db: Session) -> dict[int, str]:
    return dict(db.execute(select(Device.id, Device.hostname)).all())


def _find_device(db: Session, key: str) -> Device | None:
    """管理ID・ホスト名・呼び名のどれで呼ばれても引けるようにする。

    人は台帳の番号で呼ぶこともホスト名で呼ぶこともある。AI に「どちらで
    渡すか」を覚えさせるより、こちらで受け止めたほうが間違いが少ない。
    """
    key = (key or "").strip()
    if not key:
        return None
    stmt = (
        select(Device)
        .options(selectinload(Device.contracts), selectinload(Device.site))
        .where(or_(Device.code == key, Device.hostname == key, Device.label == key))
        .order_by(Device.id)
    )
    found = db.scalars(stmt).first()
    if found is not None:
        return found
    if key.isdigit():
        return db.get(Device, int(key))
    return None


# ---- 道具の中身 -------------------------------------------------------


def list_devices(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    filters = DeviceFilters(q=(args.get("q") or None), incomplete=bool(args.get("incomplete")))
    limit = _limit(args.get("limit"))
    total = device_query.count_devices(db, filters)
    devices = device_query.query_devices(db, filters, limit=limit)
    return {
        "total": total,
        "shown": len(devices),
        "devices": [_device_brief(d) for d in devices],
    }


def get_device(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    device = _find_device(db, str(args.get("device") or ""))
    if device is None:
        return {"found": False, "message": "その名前や管理IDの機器は台帳にありません"}
    return {"found": True, "device": _device_detail(db, device)}


def device_summary(db: Session, _args: dict[str, Any]) -> dict[str, Any]:
    return {
        "total": device_query.count_devices(db, DeviceFilters()),
        "by_severity": device_query.count_by_overall_severity(db),
        "by_maintenance": device_query.count_by_maintenance_severity(db),
        "by_eol": device_query.count_by_eol_severity(db),
        "by_vendor": [
            {"vendor": v or "(未入力)", "count": c} for v, c in device_query.count_by_vendor(db)
        ],
        "without_lifecycle_data": device_query.count_without_lifecycle_data(db),
    }


def list_links(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    device = _find_device(db, str(args.get("device") or ""))
    if device is None:
        return {"found": False, "message": "その名前や管理IDの機器は台帳にありません"}
    return {"found": True, "hostname": device.hostname, "links": _links_of(db, device.id)}


def list_sites(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    limit = _limit(args.get("limit"), default=MAX_LIMIT)
    counts = dict(
        db.execute(select(Device.site_id, func.count(Device.id)).group_by(Device.site_id)).all()
    )
    sites = db.scalars(select(Site).order_by(Site.name).limit(limit))
    return {
        "sites": [
            {
                "id": s.id,
                "code": s.code,
                "name": s.name,
                "status": str(s.status),
                "devices": counts.get(s.id, 0),
            }
            for s in sites
        ]
    }


TOOLS: dict[str, tuple[Tool, Any]] = {}


def _register(name: str, description: str, schema: dict[str, Any], func: Any) -> None:
    TOOLS[name] = (Tool(name=name, description=description, schema=schema), func)


_register(
    "list_devices",
    "機器台帳を探す。語を渡すと、ホスト名・管理ID・メーカー・型番・拠点などから当てる。",
    {
        "type": "object",
        "properties": {
            "q": {"type": "string", "description": "探す語。空なら先頭から並べる"},
            "limit": {"type": "integer", "description": f"返す件数（既定 {DEFAULT_LIMIT}）"},
            "incomplete": {"type": "boolean", "description": "未入力のある機器だけに絞る"},
        },
    },
    list_devices,
)
_register(
    "get_device",
    "機器を1台ぶん詳しく見る。管理ID・ホスト名・呼び名のどれでも引ける。結線も付く。",
    {
        "type": "object",
        "properties": {"device": {"type": "string", "description": "管理ID / ホスト名 / 呼び名"}},
        "required": ["device"],
    },
    get_device,
)
_register(
    "device_summary",
    "台帳全体の内訳。総数、重要度ごとの件数、メーカーごとの件数を返す。",
    {"type": "object", "properties": {}},
    device_summary,
)
_register(
    "list_links",
    "その機器につながっている相手と、使っているポートを返す。",
    {
        "type": "object",
        "properties": {"device": {"type": "string", "description": "管理ID / ホスト名 / 呼び名"}},
        "required": ["device"],
    },
    list_links,
)
_register(
    "list_sites",
    "拠点の一覧。拠点ごとの機器台数も返す。",
    {"type": "object", "properties": {"limit": {"type": "integer"}}},
    list_sites,
)


def descriptors() -> list[dict[str, Any]]:
    """MCP の tools/list に載せる形。"""
    return [
        {"name": t.name, "description": t.description, "inputSchema": t.schema}
        for t, _ in TOOLS.values()
    ]


def call(db: Session, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    entry = TOOLS.get(name)
    if entry is None:
        raise KeyError(name)
    return entry[1](db, args or {})
