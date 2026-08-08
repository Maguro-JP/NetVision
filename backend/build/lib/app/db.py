from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    # デモモードのときだけ、初回アクセスでスキーマとデモデータを用意する。
    # サーバレスでは startup イベントが走る保証が無いため、ここで面倒を見る。
    from app.bootstrap import ensure_ready

    ensure_ready()

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
