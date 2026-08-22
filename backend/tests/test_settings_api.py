"""画面から決める設定。

環境変数は起動のときに決まるもので、動かしながら切り替えられない。使う人に
環境変数を触らせると、打ち間違いが起きるし、そこで詰まる人も出る。
運用しながら変えるものは画面から決められること、決めた値がその場で効くことを見る。
"""

import pytest

API = "/api/v1"


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "document_storage_path", str(tmp_path / "documents"))
    return tmp_path


def test_the_defaults_come_from_the_environment(client):
    body = client.get(f"{API}/settings").json()
    assert body["warning_days"] == 365
    assert body["critical_days"] == 90
    assert body["display_timezone"] == "Asia/Tokyo"


def test_a_change_takes_effect_at_once(client):
    """決めた値がその場で効くこと。起動し直しを求めない。"""
    from app.config import settings
    from app.services import appsettings

    updated = client.patch(f"{API}/settings", json={"warning_days": 200}).json()
    assert updated["warning_days"] == 200
    assert appsettings.number("warning_days", settings.warning_days) == 200


def test_the_order_of_the_two_thresholds_is_kept(client):
    """「要対応」は「要計画」より短くないと、判定が入れ替わる。"""
    res = client.patch(f"{API}/settings", json={"critical_days": 400})
    assert res.status_code == 422
    assert "要対応" in res.json()["detail"]


def test_an_unknown_time_zone_is_refused(client):
    res = client.patch(f"{API}/settings", json={"display_timezone": "Asia/Nowhere"})
    assert res.status_code == 422


def test_the_model_folder_can_be_set_from_the_screen(client, tmp_path):
    """モデルの置き場所も画面から。環境変数を触らせない。"""
    from app.services import llm_inproc

    folder = tmp_path / "models"
    folder.mkdir()
    (folder / "qwen3-8b.gguf").write_bytes(b"dummy")

    client.patch(f"{API}/settings", json={"model_dir": str(folder)})
    assert [path.name for path in llm_inproc.available()] == ["qwen3-8b.gguf"]
