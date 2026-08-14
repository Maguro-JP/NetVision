from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from app.config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    """DB エンジンを作る。SQLite はノートPCの1台運用向けに調整する。

    サーバの Postgres が理想でも、導入テストと1人での運用は自分のPCで
    行う。そこで WAL と待ち時間を入れないと、取り込み中に画面が止まる。
    """
    backend = make_url(url).get_backend_name()
    if backend == "sqlite":
        memory = url in {"sqlite://", "sqlite:///:memory:"} or ":memory:" in url
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=StaticPool if memory else NullPool,
            future=True,
        )

        @event.listens_for(engine, "connect")
        def _sqlite_on_connect(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA temp_store=MEMORY")
            if not memory:
                # 取り込み中でも画面が読めるようにする。メモリDBでは WAL は使えない
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        return engine

    return create_engine(url, pool_pre_ping=True, future=True)


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    # 起動時に Alembic で表を最新にする。サーバレスでは lifespan が
    # 走る保証が無いため、最初の DB 利用でも同じ処理を見る。
    from app.bootstrap import ensure_ready

    ensure_ready()

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
