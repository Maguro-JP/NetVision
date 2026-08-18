"""モデルに聞く口の、繋いでよい先の判断。

閉域で使う道具なので、設定を1つ書き換えただけで実機の Config が社外へ
出ていく、ということがあってはいけない。外向きの宛先は、人が明示したときだけ
使う。
"""

from app.config import settings
from app.services import llm


def test_local_endpoints_are_allowed(monkeypatch):
    monkeypatch.setattr(settings, "llm_url", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(settings, "llm_model", "qwen2.5-coder:7b")
    monkeypatch.setattr(settings, "llm_allow_external", False)
    assert llm.configured() is True
    assert llm.status()["local"] is True


def test_an_office_server_is_local_too(monkeypatch):
    monkeypatch.setattr(settings, "llm_url", "http://10.0.0.50:8000/v1")
    monkeypatch.setattr(settings, "llm_model", "qwen2.5:14b")
    monkeypatch.setattr(settings, "llm_allow_external", False)
    assert llm.configured() is True


def test_external_endpoints_need_an_explicit_yes(monkeypatch):
    monkeypatch.setattr(settings, "llm_url", "https://api.example.com/v1")
    monkeypatch.setattr(settings, "llm_model", "gpt-x")
    monkeypatch.setattr(settings, "llm_allow_external", False)
    assert llm.configured() is False
    assert llm.status()["blocked_external"] is True

    monkeypatch.setattr(settings, "llm_allow_external", True)
    assert llm.configured() is True


def test_nothing_is_sent_when_it_is_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "llm_url", None)
    monkeypatch.setattr(settings, "llm_model", None)
    assert llm.ask("この機器の Config を作って") is None


def test_the_key_is_never_returned(monkeypatch):
    monkeypatch.setattr(settings, "llm_url", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(settings, "llm_model", "qwen2.5-coder:7b")
    monkeypatch.setattr(settings, "llm_api_key", "secret-value")
    assert "secret-value" not in str(llm.status())
