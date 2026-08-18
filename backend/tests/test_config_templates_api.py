from tests.conftest import API
from tests.test_documents_api import add_device, isolated_storage  # noqa: F401

IOS_VLAN_TEMPLATE = """vlan {{ vlan_id }}
 name {{ vlan_name }}
!
interface {{ interface }}
 description {{ vlan_name }} ({{ device.hostname }})
 switchport mode access
 switchport access vlan {{ vlan_id }}
 no shutdown
"""


def add_template(client, **fields):
    payload = {
        "name": "VLAN 追加（IOS-XE 17.9）",
        "vendor": "Cisco",
        "os_name": "IOS-XE",
        "category": "vlan",
        "body": IOS_VLAN_TEMPLATE,
        "variables": [
            {"name": "vlan_id", "label": "VLAN ID", "required": True},
            {"name": "vlan_name", "label": "VLAN 名", "required": True},
            {"name": "interface", "label": "対象ポート", "required": True},
        ],
    }
    payload.update(fields)
    return client.post(f"{API}/config-templates", json=payload)


def render(client, template_id, **body):
    return client.post(f"{API}/config-templates/{template_id}/render", json=body)


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


def test_template_renders_with_device_values(client, device_payload):
    template = add_template(client).json()
    device_id = add_device(
        client, device_payload, hostname="core-sw-01", os_name="IOS-XE", os_version="17.09.04a"
    )

    body = render(
        client,
        template["id"],
        device_id=device_id,
        variables={"vlan_id": "30", "vlan_name": "SERVER", "interface": "GigabitEthernet1/0/5"},
    ).json()

    assert body["device_hostname"] == "core-sw-01"
    assert "vlan 30" in body["rendered"]
    assert " name SERVER" in body["rendered"]
    # 機器の値が自動で埋まる
    assert "description SERVER (core-sw-01)" in body["rendered"]
    assert body["warnings"] == []


def test_missing_required_variable_is_rejected(client, device_payload):
    template = add_template(client).json()
    device_id = add_device(client, device_payload)

    res = render(client, template["id"], device_id=device_id, variables={"vlan_id": "30"})
    assert res.status_code == 422
    assert "VLAN 名" in res.json()["detail"]


def test_optional_variable_falls_back_to_default(client, device_payload):
    template = add_template(
        client,
        body="hostname {{ name }}\n",
        variables=[{"name": "name", "label": "名前", "required": False, "default": "sw-default"}],
    ).json()
    device_id = add_device(client, device_payload)

    body = render(client, template["id"], device_id=device_id, variables={}).json()
    assert body["rendered"] == "hostname sw-default\n"


def test_typo_in_template_fails_loudly(client, device_payload):
    """未定義の変数を黙って空にすると、壊れた config が出てしまう。"""
    template = add_template(client, body="hostname {{ typo_variable }}\n", variables=[]).json()
    device_id = add_device(client, device_payload)

    res = render(client, template["id"], device_id=device_id, variables={})
    assert res.status_code == 422


def test_template_can_loop_over_interfaces(client, device_payload):
    template = add_template(
        client,
        body=(
            "{% for i in interfaces %}interface {{ i.name }}\n"
            " description {{ i.description }}\n{% endfor %}"
        ),
        variables=[],
    ).json()
    device_id = add_device(client, device_payload, hostname="core-sw-01")

    # Config を取り込んでインターフェースを埋める
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "core-sw-01.txt"
    client.post(
        f"{API}/configs/upload",
        files=[("files", ("core-sw-01.txt", fixture.read_bytes(), "text/plain"))],
    )

    body = render(client, template["id"], device_id=device_id, variables={}).json()
    assert "interface GigabitEthernet1/0/1" in body["rendered"]
    assert "description ->edge-rt-01" in body["rendered"]


def test_sandbox_blocks_dangerous_expressions(client, device_payload):
    """テンプレートから内部構造へ触れないこと。"""
    template = add_template(client, body="{{ device.__class__.__mro__ }}", variables=[]).json()
    device_id = add_device(client, device_payload)

    res = render(client, template["id"], device_id=device_id, variables={})
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# バージョンに合ったテンプレートが選ばれる
# ---------------------------------------------------------------------------


def test_templates_are_filtered_by_version(client, device_payload):
    add_template(client, name="17.9 用", applies_from="17.9.0", applies_to="17.9.99")
    add_template(client, name="17.12 用", applies_from="17.12.0", applies_to="17.12.99")
    add_template(client, name="バージョン共通")

    device_id = add_device(client, device_payload, os_name="IOS-XE", os_version="17.09.04a")

    names = [t["name"] for t in client.get(f"{API}/devices/{device_id}/config-templates").json()]
    assert set(names) == {"17.9 用", "バージョン共通"}


def test_old_version_template_is_kept(client, device_payload):
    """新しい書式のテンプレートを足しても、古い版で動く機器用は残る。"""
    old = add_template(client, name="17.9 用", applies_from="17.9.0", applies_to="17.9.99").json()
    new = add_template(
        client, name="17.12 用", applies_from="17.12.0", applies_to="17.12.99"
    ).json()
    client.patch(f"{API}/config-templates/{old['id']}", json={"superseded_by_id": new["id"]})

    device_id = add_device(client, device_payload, os_name="IOS-XE", os_version="17.09.04a")

    names = [t["name"] for t in client.get(f"{API}/devices/{device_id}/config-templates").json()]
    assert "17.9 用" in names


def test_rendering_for_an_out_of_scope_device_warns(client, device_payload):
    template = add_template(client, applies_from="17.12.0", applies_to="17.12.99").json()
    device_id = add_device(client, device_payload, os_name="IOS-XE", os_version="15.2(7)E6")

    body = render(
        client,
        template["id"],
        device_id=device_id,
        variables={"vlan_id": "30", "vlan_name": "SERVER", "interface": "Gi0/1"},
    ).json()

    assert any("適用対象" in w for w in body["warnings"])


def test_device_dependent_template_requires_a_device(client):
    """機器の値を使うテンプレートは、機器を選ばないと展開できないと明示する。"""
    template = add_template(client).json()
    res = render(
        client,
        template["id"],
        variables={"vlan_id": "30", "vlan_name": "SERVER", "interface": "Gi0/1"},
    )

    assert res.status_code == 422
    assert "対象の機器を選んでください" in res.json()["detail"]


def test_device_independent_template_renders_without_a_device(client):
    template = add_template(
        client,
        body="ntp server {{ ntp_server }}\n",
        variables=[{"name": "ntp_server", "label": "NTPサーバー", "required": True}],
    ).json()

    body = render(client, template["id"], variables={"ntp_server": "10.0.0.1"}).json()

    assert body["rendered"] == "ntp server 10.0.0.1\n"
    assert body["device_hostname"] is None
    assert any("機器に依存しない" in w for w in body["warnings"])


# ---------------------------------------------------------------------------
# 管理
# ---------------------------------------------------------------------------


def test_template_can_cite_the_manual_it_came_from(client):
    from tests.test_documents_api import add_document

    document = add_document(client, title="コマンドリファレンス 17.9").json()
    template = add_template(client, source_document_id=document["id"]).json()

    assert template["source_document_title"] == "コマンドリファレンス 17.9"


def test_variables_are_detected_when_not_declared(client):
    template = add_template(
        client, body="vlan {{ vlan_id }}\n name {{ vlan_name }}\n", variables=[]
    ).json()

    detected = {v["name"] for v in template["detected_variables"]}
    assert detected == {"vlan_id", "vlan_name"}


def test_template_crud(client):
    template = add_template(client).json()

    updated = client.patch(
        f"{API}/config-templates/{template['id']}", json={"name": "改訂版"}
    ).json()
    assert updated["name"] == "改訂版"

    assert len(client.get(f"{API}/config-templates").json()) == 1
    assert client.delete(f"{API}/config-templates/{template['id']}").status_code == 204
    assert client.get(f"{API}/config-templates").json() == []


def test_unknown_device_is_rejected_on_render(client):
    template = add_template(client).json()
    res = render(client, template["id"], device_id=999, variables={})
    assert res.status_code == 422
