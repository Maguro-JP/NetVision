"""人 → AI → ロジック → AI → ロジック → AI → 人。

AI の段が値を作らないこと、AI が出したものを必ずロジックの段が受けること、
AI が使えなくても最後まで通ることを見る。
"""

from app.models import Device
from app.services import llm, pipeline
from tests.conftest import API


def _device(db) -> Device:
    device = Device(hostname="acc-01", vendor="Cisco", model="C9300-48P")
    db.add(device)
    db.flush()
    return device


def _no_model(monkeypatch):
    monkeypatch.setattr(pipeline.llm, "ask", lambda *a, **k: None)


def _answers(monkeypatch, texts: list[str]):
    """呼ばれた順に返す。どの段がモデルを呼んだかを見るため。"""
    queue = list(texts)

    def _ask(prompt, **kwargs):
        if not queue:
            return None
        return llm.Answer(text=queue.pop(0), model="test", role=kwargs.get("role", "quick"))

    monkeypatch.setattr(pipeline.llm, "ask", _ask)
    monkeypatch.setattr(pipeline.config_draft.llm, "ask", _ask)


class TestWithoutAModel:
    def test_it_still_runs_end_to_end(self, db_session, monkeypatch):
        """端末にモデルを置けない職場でも、道具として成立する必要がある。"""
        device = _device(db_session)
        db_session.commit()
        _no_model(monkeypatch)

        result = pipeline.run(
            db_session, device, "GigabitEthernet1/0/5 を VLAN 20 のアクセスポートにして"
        )
        assert "switchport access vlan 20" in result.body
        assert result.explanation
        assert [s.name for s in result.stages] == [
            "聞き取り",
            "下ごしらえ",
            "穴埋め",
            "検め",
            "言い直し",
            "受け取り",
        ]

    def test_skipped_stages_say_why(self, db_session, monkeypatch):
        device = _device(db_session)
        db_session.commit()
        _no_model(monkeypatch)

        result = pipeline.run(db_session, device, "GigabitEthernet1/0/9 を閉じて")
        skipped = {s.name: s.skipped for s in result.stages if s.skipped}
        assert "聞き取り" in skipped
        assert all(reason for reason in skipped.values())

    def test_the_explanation_avoids_jargon_keys(self, db_session, monkeypatch):
        device = _device(db_session)
        db_session.commit()
        _no_model(monkeypatch)

        result = pipeline.run(db_session, device, "GigabitEthernet1/0/9 を閉じて")
        assert "port" not in result.explanation
        assert "ポート" in result.explanation


class TestTheAiStagesCannotDecide:
    def test_a_rewrite_that_invents_a_port_is_thrown_away(self, db_session, monkeypatch):
        """存在しないポートの設定が、最後まで通ってしまうため。"""
        device = _device(db_session)
        db_session.commit()
        _answers(monkeypatch, ["GigabitEthernet9/9/9 を閉じてください"])

        result = pipeline.run(db_session, device, "使わなくなったポートを閉じて")
        assert result.understood == "使わなくなったポートを閉じて"
        listen = next(s for s in result.stages if s.name == "聞き取り")
        assert listen.skipped == "値を作ったため"

    def test_a_rewrite_that_only_rewords_is_used(self, db_session, monkeypatch):
        device = _device(db_session)
        db_session.commit()
        _answers(monkeypatch, ["GigabitEthernet1/0/9 を閉じてください"])

        result = pipeline.run(db_session, device, "GigabitEthernet1/0/9 はもう使わない")
        assert result.understood == "GigabitEthernet1/0/9 を閉じてください"
        assert result.body.splitlines()[0] == "interface GigabitEthernet1/0/9"

    def test_commands_in_the_explanation_are_dropped(self, db_session, monkeypatch):
        """説明にコマンドが混ざると、貼れる設定に見えてしまう。"""
        device = _device(db_session)
        db_session.commit()
        _answers(
            monkeypatch,
            [
                "GigabitEthernet1/0/9 を閉じてください",
                "このポートを使えなくしました。\ninterface GigabitEthernet1/0/9\n shutdown",
            ],
        )

        result = pipeline.run(db_session, device, "GigabitEthernet1/0/9 を閉じて")
        assert "interface" not in result.explanation
        assert "使えなくしました" in result.explanation


class TestTheLogicStagesAlwaysRun:
    def test_what_the_model_wrote_is_checked(self, db_session, monkeypatch):
        device = _device(db_session)
        db_session.commit()
        _answers(monkeypatch, ["GigabitEthernet1/0/9 を閉じてください"])

        result = pipeline.run(db_session, device, "GigabitEthernet1/0/9 を閉じて")
        check = next(s for s in result.stages if s.name == "検め")
        assert check.kind == "logic"
        assert not check.skipped
        # 閉じる行は通信が止まるので、必ず言う
        assert any("止まります" in risk for risk in result.risks)

    def test_what_cannot_be_decided_is_asked_not_filled(self, db_session, monkeypatch):
        device = _device(db_session)
        db_session.commit()
        _no_model(monkeypatch)

        result = pipeline.run(db_session, device, "VLAN を足したい")
        assert {g.key for g in result.gaps} >= {"name"}
        assert all(gap.why for gap in result.gaps)


def test_the_api_returns_the_path(db_session, client, monkeypatch):
    """どの段が何をしたかまで返らないと、確かめようがない。"""
    device = _device(db_session)
    db_session.commit()
    _no_model(monkeypatch)

    response = client.post(
        f"{API}/devices/{device.id}/pipeline",
        json={"request": "GigabitEthernet1/0/5 を VLAN 20 のアクセスポートにして"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [s["name"] for s in body["stages"]][0] == "聞き取り"
    assert body["explanation"]
    assert "switchport access vlan 20" in body["body"]


def test_an_empty_request_is_refused(db_session, client):
    device = _device(db_session)
    db_session.commit()
    assert (
        client.post(f"{API}/devices/{device.id}/pipeline", json={"request": "  "}).status_code
        == 422
    )
