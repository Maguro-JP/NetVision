"""モデルへ聞く口。繋いでよい先と、役割ごとの使い分け。

閉域で使う道具なので、設定を1つ書き換えただけで実機の Config が社外へ
出ていく、ということがあってはいけない。外向きの宛先は、人が明示したときだけ
使う。

用途で必要な賢さが違うので、モデルは役割ごとに分けられる。置いていない役割は、
置いてある方へ回す。
"""

import json

from app.config import settings
from app.services import llm


def _set(monkeypatch, **values):
    for name in ("llm_url", "llm_model", "llm_api_key", "llm_profiles"):
        monkeypatch.setattr(settings, name, values.get(name))
    monkeypatch.setattr(settings, "llm_allow_external", values.get("allow_external", False))


def test_local_endpoints_are_allowed(monkeypatch):
    _set(monkeypatch, llm_url="http://127.0.0.1:11434/v1", llm_model="qwen2.5-coder:7b")
    assert llm.configured() is True
    assert llm.status()["models"][0]["local"] is True


def test_an_office_server_is_local_too(monkeypatch):
    _set(monkeypatch, llm_url="http://10.0.0.50:8000/v1", llm_model="qwen2.5:14b")
    assert llm.configured() is True


def test_external_endpoints_need_an_explicit_yes(monkeypatch):
    _set(monkeypatch, llm_url="https://api.example.com/v1", llm_model="gpt-x")
    assert llm.configured() is False
    assert llm.status()["models"][0]["blocked_external"] is True

    _set(
        monkeypatch,
        llm_url="https://api.example.com/v1",
        llm_model="gpt-x",
        allow_external=True,
    )
    assert llm.configured() is True


def test_nothing_is_sent_when_it_is_not_configured(monkeypatch):
    _set(monkeypatch)
    assert llm.ask("この機器の Config を作って") is None


def test_the_key_is_never_returned(monkeypatch):
    _set(
        monkeypatch,
        llm_url="http://127.0.0.1:11434/v1",
        llm_model="qwen2.5-coder:7b",
        llm_api_key="secret-value",
    )
    assert "secret-value" not in json.dumps(llm.status())


def test_models_can_be_split_by_role(monkeypatch):
    """要約は小さいモデル、Config の下書きは大きいモデル。"""
    _set(
        monkeypatch,
        llm_profiles=json.dumps(
            [
                {"role": "quick", "url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:3b"},
                {"role": "deep", "url": "http://10.0.0.50:8000/v1", "model": "qwen2.5-coder:14b"},
            ]
        ),
    )
    assert llm.pick("quick").model == "qwen2.5:3b"
    assert llm.pick("deep").model == "qwen2.5-coder:14b"


def test_a_missing_role_falls_back_to_what_is_there(monkeypatch):
    """大きいモデルを置いていない環境でも、小さい方で答える。"""
    _set(
        monkeypatch,
        llm_profiles=json.dumps(
            [{"role": "quick", "url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:3b"}]
        ),
    )
    assert llm.pick("deep").model == "qwen2.5:3b"


def test_one_model_answers_every_role(monkeypatch):
    """1つだけ置いた環境では、どの役割もそれで受ける。"""
    _set(monkeypatch, llm_url="http://127.0.0.1:11434/v1", llm_model="qwen2.5-coder:7b")
    assert llm.pick("quick").model == "qwen2.5-coder:7b"
    assert llm.pick("deep").model == "qwen2.5-coder:7b"


def test_a_broken_profile_setting_does_not_stop_the_tool(monkeypatch):
    """設定が壊れていても、道具そのものは動き続ける。"""
    _set(
        monkeypatch,
        llm_profiles="{壊れた JSON",
        llm_url="http://127.0.0.1:11434/v1",
        llm_model="qwen2.5:3b",
    )
    assert llm.pick("quick").model == "qwen2.5:3b"
