"""起動のときに、ターミナルへ何を出すか。

まっさらな DB を作るときは、全部の版が順に当たる。その一覧を出しても、
初めて使う人には何の情報でもない。本当に更新が要るときの1行まで、
一緒に読み飛ばされる。

（試験の中では ensure_ready 自体は動かない。表を作る処理は試験用の DB を
壊しうるので、もともと止めてある。ここでは部品を直接見る）
"""

import logging

from sqlalchemy import create_engine, text

from app import bootstrap
from app.schema_alembic import apply_schema, current_revision, is_empty, pending_steps


def test_making_a_fresh_database_prints_no_version_list(tmp_path, caplog, monkeypatch):
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    engine = create_engine(url)
    monkeypatch.setattr("app.db.engine", engine)

    with caplog.at_level(logging.INFO):
        apply_schema(quiet=True)

    assert not is_empty(engine)
    assert [r for r in caplog.records if "Running upgrade" in r.getMessage()] == []


def test_the_versions_are_still_applied_even_though_it_is_quiet(tmp_path, monkeypatch):
    """黙らせたせいで表が作られていない、では困る。"""
    url = f"sqlite:///{tmp_path / 'quiet.db'}"
    engine = create_engine(url)
    monkeypatch.setattr("app.db.engine", engine)

    apply_schema(quiet=True)
    assert current_revision(engine) is not None
    assert "devices" in {
        row[0]
        for row in engine.connect().execute(
            text("select name from sqlite_master where type='table'")
        )
    }


def test_nothing_is_said_when_nothing_needs_updating(tmp_path, caplog, monkeypatch):
    """何も要らないときに何か出すと、要るときの1行が埋もれる。"""
    url = f"sqlite:///{tmp_path / 'current.db'}"
    engine = create_engine(url)
    monkeypatch.setattr("app.db.engine", engine)
    apply_schema(quiet=True)

    with caplog.at_level(logging.INFO):
        bootstrap._announce_pending(engine)
    assert caplog.records == []


def test_a_pending_update_is_announced_by_what_it_does(tmp_path, caplog, monkeypatch):
    """要るときは、当たる分だけを、何をするものかで出す。"""
    url = f"sqlite:///{tmp_path / 'old.db'}"
    engine = create_engine(url)
    monkeypatch.setattr("app.db.engine", engine)
    apply_schema(quiet=True)

    # 版の記録を古いものに戻して、更新が残っている状態を作る
    with engine.begin() as connection:
        connection.execute(text("delete from alembic_version"))
        connection.execute(
            text("insert into alembic_version (version_num) values ('b724b443b51f')")
        )

    steps = pending_steps(engine)
    assert steps

    with caplog.at_level(logging.INFO):
        bootstrap._announce_pending(engine)

    messages = " / ".join(r.getMessage() for r in caplog.records)
    assert f"{len(steps)} 件" in messages
    assert steps[0]["summary"] in messages
    # 版の名前（16進の並び）そのものは出さない。人には読めない
    assert "b724b443b51f" not in messages
