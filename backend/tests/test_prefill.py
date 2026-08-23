"""生成の前に、決まるものを決めておく。

ここが効くのは、モデルを呼ばずに済む場合と、呼ぶ範囲が狭くなる場合の2つ。
どちらも「決められないものを埋めない」ことが前提なので、そこを厚く見る。
"""

from app.models import Device
from app.models.topology import ConfigSnapshot, Interface
from app.models.vlan import Vlan
from app.services import config_draft, prefill


def _device(db, hostname="acc-01", vendor="Cisco") -> Device:
    device = Device(hostname=hostname, vendor=vendor, model="C9300-48P")
    db.add(device)
    db.flush()
    return device


class TestReadingTheRequest:
    def test_plain_japanese_is_understood(self):
        assert prefill.read_intent("VLAN を足したい") == "add_vlan"
        assert prefill.read_intent("Gi1/0/5 を閉じてください") == "shutdown"
        assert prefill.read_intent("Gi1/0/5 をトランクにして") == "trunk_port"

    def test_an_unknown_request_is_not_forced_into_a_shape(self):
        """読めないものを型に当てはめると、別の設定が出てくる。"""
        assert prefill.read_intent("なんとかしておいて") == ""


class TestDecidingWithoutTheModel:
    def test_a_number_that_is_free_is_chosen_and_explained(self, db_session):
        db_session.add(Vlan(vlan_id=10))
        device = _device(db_session)
        db_session.commit()

        ready = prefill.build(db_session, device, "VLAN を足したい 名前は SERVERS")
        chosen = {d.key: d for d in ready.decided}
        assert chosen["vlan_id"].value != "10"
        assert chosen["vlan_id"].why
        assert chosen["name"].value == "SERVERS"

    def test_a_number_already_in_use_is_refused(self, db_session):
        """使用中の番号に重ねると、別の用途のセグメントが混ざる。"""
        db_session.add(Vlan(vlan_id=30))
        device = _device(db_session)
        db_session.commit()

        ready = prefill.build(db_session, device, "VLAN 30 を足したい")
        assert [g.key for g in ready.gaps] == ["vlan_id"]
        assert not ready.complete

    def test_what_only_a_person_knows_is_left_as_a_gap(self, db_session):
        """用途は台帳に書いていない。埋めずに聞く。"""
        device = _device(db_session)
        db_session.commit()

        ready = prefill.build(db_session, device, "VLAN を足したい")
        assert "name" in {g.key for g in ready.gaps}
        assert all(gap.why for gap in ready.gaps)

    def test_an_access_port_is_written_without_the_model(self, db_session):
        device = _device(db_session)
        db_session.commit()

        ready = prefill.build(
            db_session, device, "GigabitEthernet1/0/5 を VLAN 20 のアクセスポートにして"
        )
        assert ready.complete
        assert ready.lines[0] == "interface GigabitEthernet1/0/5"
        assert " switchport access vlan 20" in ready.lines

    def test_a_port_that_was_not_named_is_asked_about(self, db_session):
        device = _device(db_session)
        db_session.commit()

        ready = prefill.build(db_session, device, "アクセスポートにして VLAN 20")
        assert "port" in {g.key for g in ready.gaps}

    def test_closing_a_port_says_what_it_costs(self, db_session):
        device = _device(db_session)
        db_session.commit()

        ready = prefill.build(db_session, device, "GigabitEthernet1/0/9 を閉じて")
        assert ready.lines == ["interface GigabitEthernet1/0/9", " shutdown"]
        assert any("止まります" in note for note in ready.notes)

    def test_a_vendor_we_cannot_write_yet_keeps_the_values_but_not_the_lines(self, db_session):
        """書き方を知らないまま並べるより、値だけ渡してモデルに書かせる。"""
        device = _device(db_session, hostname="fw-01", vendor="Fortinet")
        db_session.commit()

        ready = prefill.build(db_session, device, "GigabitEthernet1/0/9 を閉じて")
        assert ready.lines == []
        assert {d.key for d in ready.decided} == {"port"}
        assert not ready.complete


class TestTheDraftPath:
    def test_the_model_is_not_called_when_nothing_is_left_to_decide(self, db_session, monkeypatch):
        device = _device(db_session)
        db_session.add(
            ConfigSnapshot(
                device_id=device.id,
                filename="acc-01.txt",
                content="hostname acc-01\n",
                sha256="a" * 64,
                vendor="cisco",
            )
        )
        db_session.commit()

        def _never(*args, **kwargs):
            raise AssertionError("決まっているのにモデルを呼んでいます")

        monkeypatch.setattr(config_draft.llm, "ask", _never)

        result = config_draft.draft(
            db_session, device, "GigabitEthernet1/0/5 を VLAN 20 のアクセスポートにして"
        )
        assert "switchport access vlan 20" in result.body
        assert result.model is None
        assert any("AI は使っていません" in note for note in result.notes)
        assert result.decided

    def test_only_the_gap_is_handed_to_the_model(self, db_session, monkeypatch):
        device = _device(db_session)
        db_session.commit()
        asked: dict[str, str] = {}

        def _capture(prompt, **kwargs):
            asked["prompt"] = prompt
            return config_draft.llm.Answer(text=" description UPLINK", model="test", role="deep")

        monkeypatch.setattr(config_draft.llm, "ask", _capture)

        result = config_draft.draft(db_session, device, "GigabitEthernet1/0/5 に説明を入れて")
        assert "足りないところ" in asked["prompt"]
        assert "変えないでください" in asked["prompt"]
        # 決まっていた行は残り、モデルの行が足されている
        assert result.body.splitlines()[0] == "interface GigabitEthernet1/0/5"
        assert " description UPLINK" in result.body

    def test_the_decided_lines_are_not_overwritten_by_the_model(self, db_session, monkeypatch):
        """返ってきた本文で置き換えると、確定していた行まで書き換わる。"""
        device = _device(db_session)
        db_session.commit()

        monkeypatch.setattr(
            config_draft.llm,
            "ask",
            lambda prompt, **kwargs: config_draft.llm.Answer(
                text="interface GigabitEthernet9/9/9\n description X", model="test", role="deep"
            ),
        )

        result = config_draft.draft(db_session, device, "GigabitEthernet1/0/5 に説明を入れて")
        assert result.body.splitlines()[0] == "interface GigabitEthernet1/0/5"


def test_interfaces_are_not_needed_for_any_of_this(db_session):
    """台帳が空でも動く。空のときに落ちると、最初の1台で使えない。"""
    device = _device(db_session)
    db_session.add(
        Interface(device_id=device.id, name="Gi1/0/1", normalized_name="gigabitethernet1/0/1")
    )
    db_session.commit()

    ready = prefill.build(db_session, device, "なんとかして")
    assert ready.intent == ""
    assert ready.notes
