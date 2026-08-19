"""アプリの中でモデルを動かす道。

端末に新しいソフトを入れられない職場がある。実行ファイルを1つも増やさずに
使える道が無いと、そこでは AI の話が丸ごと消える。

モデルの実体は同梱しないので、置いてあるかどうかで動きが変わる。
その切り替えが正しいことを見る。
"""

from app.services import llm, llm_inproc


def test_nothing_is_used_when_no_model_is_placed(monkeypatch, tmp_path):
    """置いていなければ、規則ベースだけで動く。"""
    monkeypatch.setenv(llm_inproc.MODEL_DIR_ENV, str(tmp_path))
    monkeypatch.delenv(llm_inproc.MODEL_PATH_ENV, raising=False)

    assert llm_inproc.available() == []
    assert llm_inproc.usable() is False
    assert llm_inproc.ask("こんにちは") is None


def test_a_placed_model_is_found(monkeypatch, tmp_path):
    """置き場所に GGUF があれば見つける。ファイル名は問わない。"""
    monkeypatch.setenv(llm_inproc.MODEL_DIR_ENV, str(tmp_path))
    (tmp_path / "qwen2.5-coder-7b-q4_k_m.gguf").write_bytes(b"dummy")

    assert [path.name for path in llm_inproc.available()] == ["qwen2.5-coder-7b-q4_k_m.gguf"]


def test_the_small_model_takes_the_light_work(monkeypatch, tmp_path):
    """要約に大きいモデルを使うと、待ち時間だけが伸びる。名前から選び分ける。"""
    monkeypatch.setenv(llm_inproc.MODEL_DIR_ENV, str(tmp_path))
    (tmp_path / "qwen2.5-3b-instruct.gguf").write_bytes(b"dummy")
    (tmp_path / "qwen2.5-coder-14b.gguf").write_bytes(b"dummy")

    assert "3b" in llm_inproc.pick_file("quick").name
    assert "14b" in llm_inproc.pick_file("deep").name


def test_the_status_shows_the_model_in_the_app(monkeypatch, tmp_path):
    """画面に、アプリの中のモデルも出す。どこで動くのかが分かるように。"""
    monkeypatch.setenv(llm_inproc.MODEL_DIR_ENV, str(tmp_path))
    (tmp_path / "qwen2.5-coder-7b.gguf").write_bytes(b"dummy")

    inside = [row for row in llm.status()["models"] if row["endpoint"] == "アプリの中"]
    assert inside, "アプリの中のモデルが出ていない"
    assert all(row["local"] for row in inside)
    assert all(not row["blocked_external"] for row in inside)


def test_a_missing_library_does_not_break_anything(monkeypatch, tmp_path):
    """ライブラリが入っていない一式でも、道具そのものは動く。"""
    monkeypatch.setenv(llm_inproc.MODEL_DIR_ENV, str(tmp_path))
    (tmp_path / "qwen2.5-coder-7b.gguf").write_bytes(b"dummy")
    monkeypatch.setattr(llm_inproc, "runtime_ready", lambda: False)

    assert llm_inproc.usable() is False
    assert llm_inproc.ask("こんにちは") is None
    assert llm.ask("こんにちは") is None


def test_the_model_can_be_switched_off(monkeypatch, tmp_path):
    """置いてあっても切れること。

    規則ベースだけのときと結果を見比べたい、遅いので一時的に止めたい、
    という場面が必ず来る。そのたびにモデルのファイルを動かすのは面倒。
    """
    from app.config import settings

    monkeypatch.setenv(llm_inproc.MODEL_DIR_ENV, str(tmp_path))
    (tmp_path / "qwen2.5-coder-7b.gguf").write_bytes(b"dummy")
    monkeypatch.setattr(settings, "llm_enabled", False)

    assert llm.enabled() is False
    assert llm.configured() is False
    assert llm.ask("こんにちは") is None
    assert llm.status()["enabled"] is False


def test_the_reason_is_shown_when_it_cannot_be_used(monkeypatch, tmp_path):
    """使えないときは理由を返す。

    「設定が足りません」だけでは、何を直せばいいのか分からない。
    ライブラリが無いのか、切ってあるのかで、やることが違う。
    """
    monkeypatch.setenv(llm_inproc.MODEL_DIR_ENV, str(tmp_path))
    (tmp_path / "qwen3-8b-q4_k_m.gguf").write_bytes(b"dummy")
    monkeypatch.setattr(llm_inproc, "runtime_ready", lambda: False)

    inside = [row for row in llm.status()["models"] if row["endpoint"] == "アプリの中"]
    assert inside
    assert all("ライブラリ" in row["note"] for row in inside)
