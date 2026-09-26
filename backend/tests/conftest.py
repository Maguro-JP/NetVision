"""試験用の共通準備。

起動時 Alembic は通さない。メモリ上の SQLite にモデルから表を作る。
"""

import os
import tempfile
import time

os.environ.setdefault("NETVISION_SKIP_SCHEMA", "true")
# 設定（settings.json）・バックアップ・利用記録は、資料フォルダの隣に置かれる。
# 既定のままだと手元の backend/data を読み書きし、そこに残った設定で結果が変わる
# （実際に、手元で LLM を切った設定が残って LLM の試験が落ちた）。
# 試験ごとの一時フォルダに向け、手元の状態に左右されないようにする
os.environ["NETVISION_DOCUMENT_STORAGE_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="netvision-test-"), "documents"
)

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
    # 裏で走らせる処理は、その場限りの接続を開く。試験ではそれも同じ DB を
    # 指すようにする（本物の接続先を触らせない）
    import app.db as db_module

    original_new_session = db_module.new_session
    # 同じ DB を指す別のつなぎを返す。db_session そのものを返すと、
    # 裏の処理が閉じたときに試験側のつなぎまで閉じてしまう
    db_module.new_session = lambda: sessionmaker(
        bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
    )()
    with TestClient(app) as c:
        yield c
    _wait_for_background_jobs()
    app.dependency_overrides.clear()
    db_module.new_session = original_new_session


def _wait_for_background_jobs(timeout: float = 5.0) -> None:
    """裏で走っている処理が終わるまで待つ。

    試験が終わると db_session の後始末が表を落とす。裏の取り込みがまだ
    書いている最中に落とすと、SQLite が "not an error" という分かりにくい
    形で失敗する。終わったのを見てから落とす。
    """
    from app.services import jobs

    limit = time.time() + timeout
    while time.time() < limit:
        running = [j for j in jobs.recent(limit=100) if j.status in {"pending", "running"}]
        if not running:
            return
        time.sleep(0.01)


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
