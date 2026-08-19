"""資料からテンプレートを起こす。

コマンドの書式は機種とバージョンで変わる。人が手で書き写すと、写し間違いと
版ずれが必ず入る。取り込んだ資料の中に正しい書き方があるので、そこから起こす。
"""

import pytest

from app.models.document import Document, DocumentSection, DocumentType
from app.services import template_from_docs

API = "/api/v1"


@pytest.fixture
def reference(db_session):
    document = Document(
        title="QX コマンドリファレンス",
        vendor="NEC",
        model="QX-S5500G",
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
            commands="vlan 120\n name office\n quit\ninterface GigabitEthernet1/0/1\n port access vlan 120",
        )
    )
    db_session.commit()
    db_session.refresh(document)
    return document


def test_values_become_variables(db_session, reference):
    """番号やポート名は変数にする。そのままだと使い回せない。"""
    drafts = template_from_docs.drafts_for_document(db_session, reference)
    assert len(drafts) == 1
    draft = drafts[0]

    assert "{{ vlan_id }}" in draft.body
    assert "{{ interface }}" in draft.body
    names = {item["name"] for item in draft.variables}
    assert {"vlan_id", "interface"} <= names


def test_the_same_value_becomes_the_same_variable(db_session, reference):
    """同じ VLAN 番号が2回出てくるなら、変数も1つ。別々にすると食い違う。"""
    draft = template_from_docs.drafts_for_document(db_session, reference)[0]
    assert draft.body.count("{{ vlan_id }}") == 2


def test_the_value_in_the_document_becomes_the_default(db_session, reference):
    """資料に書いてあった値を初期値にする。何を入れる欄なのかが分かる。"""
    draft = template_from_docs.drafts_for_document(db_session, reference)[0]
    vlan = next(item for item in draft.variables if item["name"] == "vlan_id")
    assert vlan["default"] == "120"


def test_it_remembers_where_it_came_from(db_session, reference):
    """どの資料から起こしたかを残す。版が変わったときに元の章へ戻れる。"""
    draft = template_from_docs.drafts_for_document(db_session, reference)[0]
    assert draft.source_document_id == reference.id
    assert draft.vendor == "NEC"
    assert draft.model == "QX-S5500G"

    template = template_from_docs.save(db_session, draft)
    assert template.source_document_id == reference.id


def test_drafts_are_reachable_from_the_api(client, db_session, reference):
    res = client.get(f"{API}/documents/{reference.id}/template-drafts")
    assert res.status_code == 200
    drafts = res.json()
    assert drafts[0]["category"] == "vlan"

    created = client.post(f"{API}/documents/{reference.id}/template-drafts", json={"index": 0})
    assert created.status_code == 201
    assert created.json()["vendor"] == "NEC"
