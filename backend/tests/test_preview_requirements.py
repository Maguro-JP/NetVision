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


def test_offline_bundles_carry_the_schema_steps():
    """持ち出し用の一式に、表を作る手順（Alembic）が入っていること。

    起動時に DB を作るのは Alembic なので、ライブラリと alembic/ の両方が
    要る。どちらかが欠けると、閉域の端末で初回起動が落ちる。手元では気づかない。
    """
    scripts = [
        ROOT / "packaging" / "make-bundle.sh",
        ROOT / "packaging" / "make-bundle-windows.sh",
        ROOT / "packaging" / "make-bundle-windows.ps1",
    ]
    for script in scripts:
        body = script.read_text(encoding="utf-8")
        assert "alembic.ini" in body, script.name
        assert "alembic" in body, script.name
        # ライブラリとして入れているか（コピーだけでは動かない）
        assert "sqlalchemy alembic" in body or '"sqlalchemy", "alembic"' in body or (
            "pyproject.toml" in body
        ), script.name
