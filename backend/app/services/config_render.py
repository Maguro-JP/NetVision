"""設定投入用 Config の生成。

生成するだけで、実機には一切投入しない。 出来上がった内容を人が確認し、
自分の手で流し込む前提にしている。自動投入は事故の影響が大きすぎるため、
この段階では持たせない。

テンプレートは Jinja2 のサンドボックス環境で描画する。社内ツールとはいえ、
テンプレートから任意のコードが動く状態にはしない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jinja2 import StrictUndefined, TemplateError, meta
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device
from app.models.document import ConfigTemplate
from app.models.topology import Interface

_env = SandboxedEnvironment(
    undefined=StrictUndefined,  # 変数の書き間違いは黙って空にせず、エラーにする
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


class RenderError(ValueError):
    pass


@dataclass
class RenderResult:
    rendered: str
    device_hostname: str | None
    used_variables: dict[str, str]
    warnings: list[str] = field(default_factory=list)


def declared_variables(template: ConfigTemplate) -> list[dict]:
    """テンプレートに定義された変数。未定義なら本文から推測する。"""
    if template.variables:
        return list(template.variables)

    try:
        parsed = _env.parse(template.body)
    except TemplateError:
        return []

    device_keys = {"device", "interfaces"}
    return [
        {"name": name, "label": name, "required": True, "default": "", "help": ""}
        for name in sorted(meta.find_undeclared_variables(parsed) - device_keys)
    ]


def _device_references(template: ConfigTemplate) -> list[str]:
    """テンプレートが機器由来の値を参照しているか。"""
    try:
        parsed = _env.parse(template.body)
    except TemplateError:
        return []
    return sorted({"device", "interfaces"} & meta.find_undeclared_variables(parsed))


def device_context(db: Session, device: Device) -> dict:
    """機器から自動で埋まる値。手入力を減らすほど間違いも減る。"""
    interfaces = db.scalars(
        select(Interface)
        .where(Interface.device_id == device.id)
        .order_by(Interface.normalized_name)
    )

    return {
        "device": {
            "hostname": device.hostname,
            "vendor": device.vendor,
            "model": device.model,
            "serial_number": device.serial_number or "",
            "os_name": device.os_name or "",
            "os_version": device.os_version or "",
            "mgmt_ip": device.mgmt_ip or "",
            "site": device.site.name if device.site else "",
            "rack": device.rack or "",
        },
        "interfaces": [
            {
                "name": i.name,
                "description": i.description or "",
                "ip_address": i.ip_address or "",
                "vlan": i.vlan,
                "mode": i.mode or "",
                "enabled": i.enabled,
            }
            for i in interfaces
        ],
    }


def render(
    db: Session,
    template: ConfigTemplate,
    device: Device | None,
    variables: dict[str, str] | None = None,
) -> RenderResult:
    values = dict(variables or {})
    warnings: list[str] = []

    declared = declared_variables(template)
    for declaration in declared:
        name = declaration.get("name")
        if not name:
            continue
        if name not in values or values[name] == "":
            default = declaration.get("default", "")
            if default:
                values[name] = default
            elif declaration.get("required", True):
                raise RenderError(
                    f"必須の変数「{declaration.get('label') or name}」が入力されていません"
                )
            else:
                values[name] = ""

    context: dict = dict(values)
    if device is not None:
        context.update(device_context(db, device))
    else:
        # 機器の値を参照しているテンプレートは、機器を選ばないと展開できない。
        # Jinja の生エラーを見せるより、何をすればよいかを伝える。
        needed = _device_references(template)
        if needed:
            raise RenderError(
                f"このテンプレートは機器の値（{', '.join(needed)}）を使うため、"
                "対象の機器を選んでください。"
            )
        warnings.append("機器を指定していないため、機器に依存しない内容だけを生成しました。")
        context.setdefault("device", {})
        context.setdefault("interfaces", [])

    try:
        rendered = _env.from_string(template.body).render(**context)
    except TemplateError as exc:
        raise RenderError(f"テンプレートの展開に失敗しました: {exc}") from exc

    if device is not None:
        applicable = _is_applicable(template, device)
        if not applicable:
            warnings.append(
                "この機器はテンプレートの適用対象（ベンダー / 型番 / バージョン）から外れています。"
                "コマンド書式が合わない可能性があります。"
            )

    return RenderResult(
        rendered=rendered,
        device_hostname=device.hostname if device else None,
        used_variables=values,
        warnings=warnings,
    )


def _is_applicable(template: ConfigTemplate, device: Device) -> bool:
    from app.services.documents import check_applicability

    return check_applicability(
        device,
        vendor=template.vendor,
        model=template.model,
        os_name=template.os_name,
        applies_from=template.applies_from,
        applies_to=template.applies_to,
    ).matched
