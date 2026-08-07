"""ベンダードキュメントの保管と、機器からの逆引き。

方針は「古い版を消さない」こと。現場では古いバージョンが現役で動いていることが
普通にあるため、新版が出たからといって旧版を消すと、その機器の手引きが失われる。
新旧は superseded_by で繋ぐだけで、どちらも残す。

実体はファイル領域に置き、DB にはパスとハッシュだけを持つ。
同じ内容のファイルは1つの実体を共有する（sha256 で判定）。
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Device
from app.models.document import ConfigTemplate, Document
from app.services import versions


class DocumentStorageError(RuntimeError):
    pass


def storage_root() -> Path:
    return Path(settings.document_storage_path)


def _relative_path(digest: str, filename: str) -> Path:
    """内容ハッシュで配置する。同じファイルを何度上げても実体は1つで済む。"""
    suffix = Path(filename).suffix.lower()[:16]
    return Path(digest[:2]) / f"{digest}{suffix}"


def store_file(content: bytes, filename: str) -> tuple[str, str]:
    """ファイルを保存し、(相対パス, sha256) を返す。"""
    if len(content) > settings.max_document_bytes:
        limit_mb = settings.max_document_bytes // (1024 * 1024)
        raise DocumentStorageError(f"ファイルサイズが上限（{limit_mb}MB）を超えています")

    digest = hashlib.sha256(content).hexdigest()
    relative = _relative_path(digest, filename)
    target = storage_root() / relative

    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(content)
    return str(relative), digest


def resolve_file(document: Document) -> Path | None:
    if not document.storage_path:
        return None
    path = storage_root() / document.storage_path
    return path if path.exists() else None


def delete_file_if_unused(db: Session, document: Document) -> None:
    """他のドキュメントが同じ実体を参照していなければファイルも消す。"""
    if not document.storage_path:
        return

    others = db.scalar(
        select(Document).where(
            Document.storage_path == document.storage_path, Document.id != document.id
        )
    )
    if others is not None:
        return

    path = storage_root() / document.storage_path
    if path.exists():
        path.unlink()
        # 空になった中間ディレクトリは片付ける
        try:
            path.parent.rmdir()
        except OSError:
            pass


def purge_storage() -> None:
    """テスト用。保管領域を丸ごと消す。"""
    root = storage_root()
    if root.exists():
        shutil.rmtree(root)


# ---------------------------------------------------------------------------
# バージョン範囲の適用判定
# ---------------------------------------------------------------------------


def applies_to_version(
    version: str | None, applies_from: str | None, applies_to: str | None
) -> bool:
    """資料の適用バージョン範囲に、機器のバージョンが入るか。

    範囲が未指定なら「全バージョン共通の資料」とみなして常に該当させる。
    機器のバージョンが不明な場合も、範囲指定のない資料だけは出す。
    """
    if applies_from is None and applies_to is None:
        return True
    if version is None:
        return False

    if applies_from is not None and versions.compare(version, applies_from) < 0:
        return False
    if applies_to is not None and versions.compare(version, applies_to) > 0:
        return False
    return True


@dataclass
class Applicability:
    """なぜこの資料が該当したのかの説明。根拠を出せないと使ってもらえない。"""

    matched: bool
    reason: str


def check_applicability(
    device: Device,
    *,
    vendor: str,
    model: str | None,
    os_name: str | None,
    applies_from: str | None,
    applies_to: str | None,
) -> Applicability:
    if versions.normalize_name(device.vendor) != versions.normalize_name(vendor):
        return Applicability(False, "ベンダーが異なる")
    if not versions.matches_model(model, device.model):
        return Applicability(False, "型番が対象外")
    if os_name and versions.normalize_name(device.os_name) != versions.normalize_name(os_name):
        return Applicability(False, "OS が異なる")

    if not applies_to_version(device.os_version, applies_from, applies_to):
        return Applicability(False, "バージョンが適用範囲外")

    if applies_from is None and applies_to is None:
        return Applicability(True, "バージョン共通の資料")
    span = " 〜 ".join(x for x in (applies_from, applies_to) if x)
    return Applicability(True, f"稼働中の {device.os_version} が適用範囲（{span}）に入る")


@dataclass
class DocumentMatch:
    document: Document
    reason: str


def documents_for_device(db: Session, device: Device) -> list[DocumentMatch]:
    """機器に該当するドキュメントを返す。

    古い版も消していないので、その機器のバージョンに合ったものが引ける。
    """
    matches: list[DocumentMatch] = []
    for document in db.scalars(select(Document)):
        result = check_applicability(
            device,
            vendor=document.vendor,
            model=document.model,
            os_name=document.os_name,
            applies_from=document.applies_from,
            applies_to=document.applies_to,
        )
        if result.matched:
            matches.append(DocumentMatch(document=document, reason=result.reason))

    # 型番まで指定された資料を優先し、次に新しい資料を上に出す
    matches.sort(
        key=lambda m: (
            m.document.model is None,
            -(m.document.published_at.toordinal() if m.document.published_at else 0),
            m.document.title,
        )
    )
    return matches


@dataclass
class TemplateMatch:
    template: ConfigTemplate
    reason: str


def templates_for_device(db: Session, device: Device) -> list[TemplateMatch]:
    matches: list[TemplateMatch] = []
    for template in db.scalars(select(ConfigTemplate)):
        result = check_applicability(
            device,
            vendor=template.vendor,
            model=template.model,
            os_name=template.os_name,
            applies_from=template.applies_from,
            applies_to=template.applies_to,
        )
        if result.matched:
            matches.append(TemplateMatch(template=template, reason=result.reason))

    matches.sort(key=lambda m: (m.template.category, m.template.name))
    return matches
