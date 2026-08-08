"""API 層で使い回す小さな変換。"""

from __future__ import annotations

from app.schemas.config_plans import CommandRead, DiffRead
from app.services.configdiff import DiffResult


def diff_read(diff: DiffResult) -> DiffRead:
    return DiffRead(
        verified=diff.verified,
        commands=[
            CommandRead(text=c.text, depth=c.depth, kind=c.kind, rendered=c.rendered)
            for c in diff.commands
        ],
        script=diff.script,
        rollback_script=diff.rollback_script,
        mismatches=diff.mismatches,
        warnings=diff.warnings,
        risks=diff.risks,
        added=diff.added,
        removed=diff.removed,
        changed=diff.changed,
    )
