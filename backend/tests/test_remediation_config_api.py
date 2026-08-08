"""対処から「対応版 Config」を起こす。

投入コマンドは Config 計画と同じエンジンを通すので、
対応版フルに収束することを検証してからでないと出ない。
"""

from pathlib import Path

from tests.conftest import API
from tests.test_exposure_api import add_device, import_advisory

FIXTURE = Path(__file__).parent / "fixtures" / "core-sw-01.txt"


def upload(client, extra=b""):
    return client.post(
        f"{API}/configs/upload",
        files=[("files", ("core-sw-01.txt", FIXTURE.read_bytes() + extra, "text/plain"))],
    )


def make_remediation(client, **fields):
    payload = {
        "cve_id": "CVE-2026-0001",
        "kind": "workaround",
        "summary": "Web UI を無効化",
        "vendor": "Cisco",
        "model": "C9500*",
    }
    payload.update(fields)
    return client.post(f"{API}/remediations", json=payload).json()


def with_template(client):
    template = client.post(
        f"{API}/config-templates",
        json={
            "name": "Web UI 無効化",
            "vendor": "Cisco",
            "category": "security",
            "body": "no ip http server\nno ip http secure-server\n",
            "variables": [],
        },
    ).json()
    return make_remediation(client, config_template_id=template["id"])


def setup(client, device_payload, extra=b"\nip http server\nip http secure-server\n"):
    import_advisory(client)
    device_id = add_device(client, device_payload, hostname="core-sw-01")
    upload(client, extra)
    return device_id


# ---------------------------------------------------------------------------
# 4つの成果物
# ---------------------------------------------------------------------------


def test_fix_config_produces_current_fixed_and_diff(client, device_payload):
    device_id = setup(client, device_payload)
    remediation = with_template(client)

    fix = client.get(f"{API}/devices/{device_id}/remediations/{remediation['id']}/config").json()

    assert "ip http server" in fix["current_full"]
    # 対応版では塞がれている
    assert "ip http server" not in fix["fixed_full"]
    assert fix["diff"]["verified"] is True
    assert "no ip http server" in fix["diff"]["script"]
    assert fix["already_applied"] is False


def test_diff_converges_to_the_fixed_config(client, device_payload):
    from app.services.configdiff.dialects import for_vendor
    from app.services.configdiff.engine import apply_script, equivalent, normalize
    from app.services.configdiff.tree import parse

    device_id = setup(client, device_payload)
    remediation = with_template(client)
    fix = client.get(f"{API}/devices/{device_id}/remediations/{remediation['id']}/config").json()

    dialect = for_vendor(fix["vendor"])
    applied = apply_script(
        normalize(parse(fix["current_full"], dialect), dialect), fix["diff"]["script"], dialect
    )
    expected = normalize(parse(fix["fixed_full"], dialect), dialect)
    assert equivalent(applied, expected, dialect, ())


def test_rollback_restores_the_vulnerable_state(client, device_payload):
    """切り戻しコマンドも出る（投入して問題が出たとき戻せないと困る）。"""
    device_id = setup(client, device_payload)
    remediation = with_template(client)

    fix = client.get(f"{API}/devices/{device_id}/remediations/{remediation['id']}/config").json()
    assert "ip http server" in fix["diff"]["rollback_script"]


def test_already_applied_is_reported(client, device_payload):
    """既に塞いである機器に投入コマンドを出さない。"""
    device_id = setup(client, device_payload, extra=b"\n")  # http server が無い
    remediation = with_template(client)

    fix = client.get(f"{API}/devices/{device_id}/remediations/{remediation['id']}/config").json()

    assert fix["already_applied"] is True
    assert fix["diff"]["script"] == ""
    assert any("既に入っています" in n for n in fix["notes"])


def test_each_output_can_be_downloaded(client, device_payload):
    device_id = setup(client, device_payload)
    remediation = with_template(client)
    base = f"{API}/devices/{device_id}/remediations/{remediation['id']}/config/download"

    for kind in ("current", "fixed", "diff", "rollback"):
        res = client.get(f"{base}/{kind}")
        assert res.status_code == 200, kind
        assert "attachment" in res.headers["content-disposition"]

    assert "CVE-2026-0001" in client.get(f"{base}/fixed").headers["content-disposition"]


# ---------------------------------------------------------------------------
# 出せないときは理由を返す
# ---------------------------------------------------------------------------


def test_remediation_without_commands_is_rejected(client, device_payload):
    device_id = setup(client, device_payload)
    remediation = make_remediation(client)  # テンプレートも手順も無い

    res = client.get(f"{API}/devices/{device_id}/remediations/{remediation['id']}/config")
    assert res.status_code == 422
    assert "登録されていません" in res.json()["detail"]


def test_device_without_config_is_rejected(client, device_payload):
    import_advisory(client)
    device_id = add_device(client, device_payload, hostname="no-config-sw")
    remediation = with_template(client)

    res = client.get(f"{API}/devices/{device_id}/remediations/{remediation['id']}/config")
    assert res.status_code == 422
    assert "取り込まれていない" in res.json()["detail"]


def test_procedure_text_is_usable_but_warned_about(client, device_payload):
    """テンプレートが無くても手順から出せる。ただし注意を添える。"""
    device_id = setup(client, device_payload)
    remediation = make_remediation(
        client, procedure="no ip http server\nno ip http secure-server\n"
    )

    fix = client.get(f"{API}/devices/{device_id}/remediations/{remediation['id']}/config").json()

    assert fix["source"] == "procedure"
    assert fix["diff"]["verified"] is True
    assert any("文章が混ざっていないか" in n for n in fix["notes"])


def test_unknown_ids_are_404(client, device_payload):
    device_id = setup(client, device_payload)
    remediation = with_template(client)

    missing_device = client.get(f"{API}/devices/999/remediations/{remediation['id']}/config")
    assert missing_device.status_code == 404
    assert client.get(f"{API}/devices/{device_id}/remediations/999/config").status_code == 404
