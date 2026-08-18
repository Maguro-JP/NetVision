"""資料を根拠にした Config の下書き。

モデルを置いていない環境が既定なので、まず「モデル無しでも使える」ことを
確かめる。そのうえで、モデルの答えをそのまま信じず、資料と突き合わせて
裏付けの無い行に印が付くことを見る。ここが緩いと、それらしいだけの
コマンドが実機へ持ち込まれる。
"""

import pytest

from app.models import Device
from app.models.document import Document, DocumentSection, DocumentType
from app.services import config_draft, llm

API = "/api/v1"


@pytest.fixture
def reference(db_session):
    document = Document(
        title="QX コマンドリファレンス",
        vendor="NEC",
        doc_type=DocumentType.COMMAND_REFERENCE,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        DocumentSection(
            document_id=document.id,
            position=0,
            heading="VLAN の作成",
            body="VLAN を作る手順",
            commands="vlan 120\n name office\n quit",
        )
    )
    db_session.commit()
    return document


def test_it_answers_from_documents_without_a_model(db_session, reference):
    """モデルが無くても、資料から拾ったコマンドを手がかりとして返す。"""
    device = Device(hostname="qx-01", vendor="NEC")
    db_session.add(device)
    db_session.commit()

    result = config_draft.draft(db_session, device, "vlan")
    assert result.model is None
    assert "vlan 120" in result.body
    assert [source.title for source in result.sources] == ["QX コマンドリファレンス"]
    assert any("モデルが使えない" in note for note in result.notes)


def test_lines_without_a_document_behind_them_are_marked(db_session, reference, monkeypatch):
    """資料に無い書き方の行には印を付ける。丸ごと信じない。"""
    monkeypatch.setattr(
        llm,
        "ask",
        lambda *args, **kwargs: llm.Answer(
            text="```\nvlan 120\n name office\nsnmp-server community public rw\n```",
            model="qwen",
            role=llm.DEEP,
        ),
    )
    device = Device(hostname="qx-02", vendor="NEC")
    db_session.add(device)
    db_session.commit()

    result = config_draft.draft(db_session, device, "vlan")
    assert result.model == "qwen"
    assert "```" not in result.body
    assert result.ungrounded == ["snmp-server community public rw"]
    assert any("確かめて" in note for note in result.notes)


def test_it_says_so_when_no_document_matches(db_session):
    """資料が無いときは、その旨を言う。黙って空を返さない。"""
    device = Device(hostname="qx-03", vendor="NEC")
    db_session.add(device)
    db_session.commit()

    result = config_draft.draft(db_session, device, "vlan")
    assert result.body == ""
    assert any("資料が見つかりません" in note for note in result.notes)


def test_draft_is_reachable_from_the_api(client, reference):
    device = client.post(f"{API}/devices", json={"hostname": "qx-04", "vendor": "NEC"}).json()
    res = client.post(f"{API}/devices/{device['id']}/config-draft", json={"request": "vlan"})
    assert res.status_code == 200
    body = res.json()
    assert "vlan 120" in body["body"]
    assert body["sources"][0]["title"] == "QX コマンドリファレンス"

    status = client.get(f"{API}/llm/status").json()
    assert status["configured"] is False
