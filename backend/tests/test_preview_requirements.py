"""Vercel プレビュー用の依存が、起動に必要なものを落とさないこと。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_vercel_requirements_include_alembic():
    """#24 以降、プレビューは create_all ではなく Alembic で表を作る。"""
    body = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    names = [
        line.split(">")[0].split("=")[0].split("[")[0].strip()
        for line in body.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert "alembic" in names
