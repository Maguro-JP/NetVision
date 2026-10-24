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


def test_it_can_be_switched_from_the_screen(client, tmp_path, monkeypatch):
    """画面のボタンで切り替えられること。

    環境変数だと起動し直しになる。試している最中に何度も切り替えたいので、
    その場で効いて、次の起動でも残る形にする。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "document_storage_path", str(tmp_path / "documents"))

    off = client.post("/api/v1/llm/enabled", json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["enabled"] is False
    assert llm.enabled() is False

    on = client.post("/api/v1/llm/enabled", json={"enabled": True})
    assert on.json()["enabled"] is True
    assert llm.enabled() is True


def _use_shared_home(monkeypatch, home):
    """画面と環境変数の置き場を外し、共有の置き場だけが効く形にする。"""
    from app.services import appsettings

    monkeypatch.delenv(llm_inproc.MODEL_DIR_ENV, raising=False)
    monkeypatch.delenv(llm_inproc.MODEL_PATH_ENV, raising=False)
    monkeypatch.setattr(appsettings, "text", lambda key, default="": default)
    monkeypatch.setenv(llm_inproc.SHARED_HOME_ENV, str(home))


def test_models_are_looked_for_in_the_shared_home_first(monkeypatch, tmp_path):
    """モデルは数 GB あるので、自作のアプリどうしで LLM_HOME の gguf を共有する。"""
    _use_shared_home(monkeypatch, tmp_path)

    assert llm_inproc.model_dirs()[0] == tmp_path / "gguf"
    assert llm_inproc.model_dir() == tmp_path / "gguf", "画面の案内が共有の置き場を指していない"


def test_a_model_already_in_the_shared_home_is_used_as_is(monkeypatch, tmp_path):
    """ほかのアプリが置いたモデルを、取り直さずにそのまま使う。"""
    _use_shared_home(monkeypatch, tmp_path)
    (tmp_path / "gguf").mkdir()
    (tmp_path / "gguf" / "qwen2.5-3b-instruct.gguf").write_bytes(b"dummy")

    assert [path.name for path in llm_inproc.available()] == ["qwen2.5-3b-instruct.gguf"]


def test_the_shared_home_is_prepared_without_overwriting(monkeypatch, tmp_path):
    """gguf と huggingface を用意する。既にある説明は、ほかのアプリのものなので書き換えない。"""
    _use_shared_home(monkeypatch, tmp_path)
    (tmp_path / "README.txt").write_text("ほかのアプリの説明", encoding="utf-8")

    assert llm_inproc.prepare_shared_home() == tmp_path
    assert (tmp_path / "gguf").is_dir()
    assert (tmp_path / "huggingface").is_dir()
    assert (tmp_path / "README.txt").read_text(encoding="utf-8") == "ほかのアプリの説明"


def test_windows_uses_c_llm_without_llm_home(monkeypatch):
    """Windows で LLM_HOME が無ければ C:\\LLM。ほかの OS はこれまでの置き場のまま。"""
    monkeypatch.delenv(llm_inproc.SHARED_HOME_ENV, raising=False)

    monkeypatch.setattr(llm_inproc, "_is_windows", lambda: True)
    assert str(llm_inproc.shared_gguf_dir()).replace("/", "\\") == "C:\\LLM\\gguf"

    monkeypatch.setattr(llm_inproc, "_is_windows", lambda: False)
    assert llm_inproc.shared_home() is None
    assert llm_inproc.prepare_shared_home() is None
