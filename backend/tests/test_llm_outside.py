"""外の事業者へ出すときの守り。

どの事業者を使うにせよ、実機の Config には管理IP・鍵・コミュニティ名が
入っている。そのまま送ってよいものではない。送る前に伏せること、許可した
宛先以外へは出さないこと、送った事実が残ることを見る。
"""

import json

import pytest

from app.config import settings
from app.services import llm, llm_audit, llm_safety


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "document_storage_path", str(tmp_path / "documents"))
    return tmp_path


def test_addresses_and_secrets_are_hidden():
    out = llm_safety.mask(
        "ip address 10.20.30.40 255.255.255.0\n"
        "snmp-server community public RO\n"
        "username admin password 0 Hunter2"
    )
    assert "10.20.30.40" not in out.text
    assert "public" not in out.text
    assert "Hunter2" not in out.text
    # マスクは残す。伏せると /24 なのか /30 なのかが消え、答えが使えなくなる
    assert "255.255.255.0" in out.text
    assert out.counts == {"secret": 2, "ipv4": 1}


def test_the_same_value_gets_the_same_label():
    """同じアドレスは同じ印にする。全部を同じ印にすると関係が読めなくなる。"""
    out = llm_safety.mask("ip address 10.0.0.1\nneighbor 10.0.0.1\nneighbor 10.0.0.2")
    assert out.text.count("<ipv4-1>") == 2
    assert "<ipv4-2>" in out.text


def test_only_the_listed_hosts_are_allowed(monkeypatch):
    """「外向き可」を1つ立てただけで、どこへでも送れる状態にしない。"""
    monkeypatch.setattr(settings, "llm_allowed_hosts", "ai.example.co.jp")

    assert llm.host_allowed("https://ai.example.co.jp/v1") is True
    assert llm.host_allowed("https://tokyo.ai.example.co.jp/v1") is True
    assert llm.host_allowed("https://somewhere-else.com/v1") is False

    monkeypatch.setattr(settings, "llm_allowed_hosts", None)
    assert llm.host_allowed("https://somewhere-else.com/v1") is True


def test_an_unlisted_host_is_not_usable(monkeypatch):
    monkeypatch.setattr(settings, "llm_allow_external", True)
    monkeypatch.setattr(settings, "llm_allowed_hosts", "ai.example.co.jp")
    monkeypatch.setattr(
        settings,
        "llm_profiles",
        json.dumps([{"role": "deep", "url": "https://other.com/v1", "model": "m"}]),
    )
    assert llm.pick("deep") is None


def test_the_record_keeps_the_fact_but_not_the_content(monkeypatch, tmp_path):
    """送った事実は残す。中身は残さない（記録が漏れ口になるため）。"""
    monkeypatch.setattr(settings, "llm_allow_external", True)
    monkeypatch.setattr(settings, "llm_allowed_hosts", None)
    monkeypatch.setattr(
        settings,
        "llm_profiles",
        json.dumps([{"role": "deep", "url": "https://ai.example.co.jp/v1", "model": "m"}]),
    )
    monkeypatch.setitem(llm._PROVIDERS, "openai", lambda profile, messages, temperature: "ok")

    answer = llm.ask("ip address 10.20.30.40", role="deep")
    assert answer is not None

    entries = llm_audit.recent()
    assert len(entries) == 1
    assert entries[0]["host"] == "ai.example.co.jp"
    assert entries[0]["masked"] == {"ipv4": 1}
    assert entries[0]["answered"] is True
    assert "10.20.30.40" not in json.dumps(entries[0])


def test_what_goes_out_is_masked(monkeypatch):
    """外向きの宛先には、伏せた文が渡ること。"""
    monkeypatch.setattr(settings, "llm_allow_external", True)
    monkeypatch.setattr(settings, "llm_allowed_hosts", None)
    monkeypatch.setattr(
        settings,
        "llm_profiles",
        json.dumps([{"role": "deep", "url": "https://ai.example.co.jp/v1", "model": "m"}]),
    )
    seen: list[str] = []

    def spy(profile, messages, temperature):
        seen.append(messages[-1]["content"])
        return "ok"

    monkeypatch.setitem(llm._PROVIDERS, "openai", spy)
    llm.ask("snmp-server community public RO", role="deep")

    assert seen and "public" not in seen[0]


def test_a_new_vendor_can_be_added_without_touching_the_callers(monkeypatch):
    """事業者を足すのは、作法を1つ登録するだけで済むこと。"""
    monkeypatch.setattr(settings, "llm_allow_external", True)
    monkeypatch.setattr(settings, "llm_allowed_hosts", None)
    monkeypatch.setattr(
        settings,
        "llm_profiles",
        json.dumps(
            [
                {
                    "role": "deep",
                    "url": "https://ai.example.co.jp/api",
                    "model": "m",
                    "provider": "sample",
                }
            ]
        ),
    )
    monkeypatch.setitem(llm._PROVIDERS, "sample", lambda profile, messages, temperature: "はい")

    answer = llm.ask("こんにちは", role="deep")
    assert answer is not None
    assert answer.text == "はい"
