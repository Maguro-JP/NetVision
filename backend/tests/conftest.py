"""試験用の共通準備。

起動時 Alembic は通さない。メモリ上の SQLite にモデルから表を作る。
"""

import os

os.environ.setdefault("NETVISION_SKIP_SCHEMA", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app

API = "/api/v1"


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def device_payload():
    def _make(**overrides):
        payload = {
            "hostname": "core-sw-01",
            "vendor": "Cisco",
            "model": "C9500-40X",
            "device_type": "switch",
            "os_name": "IOS-XE",
            "os_version": "17.09.04a",
            "install_date": "2021-04-01",
        }
        payload.update(overrides)
        return payload

    return _make
