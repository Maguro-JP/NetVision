"""ノートPCの SQLite が、取り込み中でも画面を止めないこと。"""

from sqlalchemy import text

from app.db import make_engine


def test_file_sqlite_uses_wal(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'netvision.db'}")
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
        fks = conn.execute(text("PRAGMA foreign_keys")).scalar()
    assert mode.lower() == "wal"
    assert int(timeout) >= 30000
    assert int(fks) == 1


def test_memory_sqlite_does_not_require_wal():
    engine = make_engine("sqlite://")
    with engine.connect() as conn:
        fks = conn.execute(text("PRAGMA foreign_keys")).scalar()
        timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
    assert int(fks) == 1
    assert int(timeout) >= 30000
