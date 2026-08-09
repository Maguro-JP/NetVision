"""対処から「対応版 Config」を起こす。

対処には回避コマンドが書いてあるが、そのままでは投入できない。
その機器の現行 config に当てたら何になるのか、何を投入すればいいのかが要る。

出すものは Config 計画（フェーズ4.7）と同じ4つ。

- 現行フル   … 取り込んだ running-config
- 対応版フル … 現行に対処を当てた結果。更改・再構築時にそのまま入れる
- 差分     … 既存機器に当てる投入コマンド
- 切り戻し   … 戻すためのコマンド

差分は Config 計画と同じエンジンを通すので、対応版フルに収束することを
機械で検証してからでないと出ない。

副産物として重要なのが「差分が空かどうか」。空なら対処の設定は既に現行
config に入っているということで、「やったつもり」ではない裏付けになる。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import ConfigSnapshot, Device, Remediation
from app.services import config_plans, config_render
from app.services.configdiff import DiffResult, build
from app.services.configdiff.dialects import for_vendor
from app.services.configdiff.engine import apply_script, normalize
from app.services.configdiff.tree import parse


@dataclass
class FixConfig:
    device_id: int
    hostname: str
    cve_id: str
    remediation_id: int
    remediation_summary: str
    vendor: str
    source: str  # template（テンプレート） / procedure（手順の本文）
    current_full: str
    fixed_full: str
    diff: DiffResult
    already_applied: bool  # 対処の設定が現行 config に既に入っている
    notes: list[str] = field(default_factory=list)


class FixError(Exception):
    """対応版 Config を作れない。理由をそのまま画面に出す。"""


def _fragment(db: Session, device: Device, remediation: Remediation) -> tuple[str, str, list[str]]:
    """投入する設定の断片を決める。テンプレートがあればそれを優先する。"""
    notes: list[str] = []

    if remediation.config_template is not None:
        rendered = config_render.render(db, remediation.config_template, device, {})
        notes.extend(rendered.warnings)
        return rendered.rendered, "template", notes

    if remediation.procedure and remediation.procedure.strip():
        notes.append(
            "対処用テンプレートが紐付いていないため、手順の本文をそのままコマンドとして"
            "扱っています。文章が混ざっていないか確認してください"
        )
        return remediation.procedure, "procedure", notes

    raise FixError(
        "この対処には投入する設定が登録されていません。"
        "対処用テンプレートを紐付けるか、手順にコマンドを書いてください"
    )


def _snapshot(db: Session, device: Device) -> ConfigSnapshot:
    snapshot = config_plans.latest_snapshot(db, device.id)
    if snapshot is None:
        raise FixError(
            "この機器の config が取り込まれていないため、対応版 Config を作れません。"
            "「Configの取り込み」から running-config を登録してください"
        )
    return snapshot


def build_fix(db: Session, device: Device, remediation: Remediation) -> FixConfig:
    snapshot = _snapshot(db, device)
    fragment, source, notes = _fragment(db, device, remediation)

    vendor = snapshot.vendor or config_plans.vendor_from_name(device.vendor)
    dialect = for_vendor(vendor)

    # 現行に対処を当てた姿を作る。当て方は差分エンジンの適用処理と同じものを使う
    current_tree = normalize(parse(snapshot.content, dialect), dialect)
    fixed_tree = apply_script(current_tree, fragment, dialect)
    fixed_full = fixed_tree.to_text()

    # 投入コマンドは通常の差分として作り直し、収束を検証させる
    diff = build(snapshot.content, fixed_full, vendor)
    already_applied = diff.verified and diff.is_empty

    if already_applied:
        notes.append(
            "この対処の設定は、取り込み済みの現行 config に既に入っています"
            "（投入するものはありません）"
        )
    elif not diff.verified:
        notes.append(
            "投入コマンドが対応版 config に収束しないため出力していません。"
            "対処の内容がこの機器の書式に合っているか確認してください"
        )

    return FixConfig(
        device_id=device.id,
        hostname=device.hostname,
        cve_id=remediation.vulnerability.cve_id if remediation.vulnerability else "—",
        remediation_id=remediation.id,
        remediation_summary=remediation.summary,
        vendor=vendor,
        source=source,
        current_full=snapshot.content,
        fixed_full=fixed_full,
        diff=diff,
        already_applied=already_applied,
        notes=notes,
    )
