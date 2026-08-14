"""版とスキーマの世代が API から見えること。

版を上げたあとに「本当に入れ替わったか」を確かめる先が無いと、
上げ忘れや入れ替え漏れに気づけない。
"""

from tests.conftest import API


def test_meta_reports_the_version(client):
    body = client.get(f"{API}/meta").json()
    assert body["version"]


def test_meta_reports_the_schema_revision_key(client):
    """スキーマの世代は、無い場合も鍵ごと消さずに null で返す。

    1台完結モードも Alembic を通すので、起動後は番号が入る。
    試験のメモリ DB にはこの表が無いので null になる。
    鍵ごと無くすと、画面側が「古い版」と「使っていない」を見分けられない。
    """
    body = client.get(f"{API}/meta").json()
    assert "schema_revision" in body
    assert "schema_phase" in body


def test_the_version_matches_the_package_metadata():
    """版数を2か所に持っている以上、食い違えば /api/v1/meta が嘘をつく。

    上げたあとに版で確認する手順（docs/UPGRADE.md）が成り立たなくなるので、
    ここで揃っていることを見張る。
    """
    import tomllib
    from pathlib import Path

    from app.main import app

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]

    assert app.version == declared, (
        f"pyproject.toml は {declared}、app.main は {app.version}。どちらかの直し忘れ"
    )
