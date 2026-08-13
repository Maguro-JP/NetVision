from tests.conftest import API


def test_create_and_get_device(client, device_payload):
    res = client.post(f"{API}/devices", json=device_payload())
    assert res.status_code == 201, res.text
    device_id = res.json()["id"]

    got = client.get(f"{API}/devices/{device_id}").json()
    assert got["hostname"] == "core-sw-01"
    assert got["os_version"] == "17.09.04a"
    assert got["lifecycle"]["overall_severity"] == "unknown"
    assert got["contracts"] == []


def test_duplicate_hostname_conflicts(client, device_payload):
    client.post(f"{API}/devices", json=device_payload())
    res = client.post(f"{API}/devices", json=device_payload(serial_number="X2"))
    assert res.status_code == 409


def test_eol_before_eos_is_rejected(client, device_payload):
    res = client.post(
        f"{API}/devices", json=device_payload(eos_date="2028-01-01", eol_date="2027-01-01")
    )
    assert res.status_code == 422


def test_patch_updates_only_given_fields(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload()).json()["id"]
    res = client.patch(f"{API}/devices/{device_id}", json={"os_version": "17.12.03"})
    assert res.status_code == 200
    body = res.json()
    assert body["os_version"] == "17.12.03"
    assert body["model"] == "C9500-40X"


def test_patch_rejects_inconsistent_lifecycle_dates(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload(eos_date="2028-01-01")).json()[
        "id"
    ]
    res = client.patch(f"{API}/devices/{device_id}", json={"eol_date": "2027-01-01"})
    assert res.status_code == 422


def test_search_and_filter(client, device_payload):
    client.post(f"{API}/devices", json=device_payload())
    client.post(
        f"{API}/devices",
        json=device_payload(
            hostname="fw-01",
            vendor="Fortinet",
            model="FG-100F",
            device_type="firewall",
            mgmt_ip="10.1.1.50",
            notes="東京拠点の境界",
        ),
    )

    assert len(client.get(f"{API}/devices").json()) == 2
    assert len(client.get(f"{API}/devices", params={"vendor": "Fortinet"}).json()) == 1
    assert len(client.get(f"{API}/devices", params={"q": "C9500"}).json()) == 1
    assert len(client.get(f"{API}/devices", params={"device_type": "firewall"}).json()) == 1
    # 全体検索は管理IP・メモ・種別の呼び名でも当たる
    assert len(client.get(f"{API}/devices", params={"q": "10.1.1.50"}).json()) == 1
    assert len(client.get(f"{API}/devices", params={"q": "境界"}).json()) == 1
    assert len(client.get(f"{API}/devices", params={"q": "ファイアウォール"}).json()) == 1
    # 列フィルターは部分一致
    assert len(client.get(f"{API}/devices", params={"product": "FG-"}).json()) == 1
    assert len(client.get(f"{API}/devices", params={"hostname": "fw"}).json()) == 1


def test_delete_device(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload()).json()["id"]
    assert client.delete(f"{API}/devices/{device_id}").status_code == 204
    assert client.get(f"{API}/devices/{device_id}").status_code == 404


def test_unknown_site_rejected(client, device_payload):
    res = client.post(f"{API}/devices", json=device_payload(site_id=999))
    assert res.status_code == 422


def test_a_site_can_be_named_instead_of_chosen(client, device_payload):
    """拠点を先に作らなくても、名前を書けば登録できること。

    試験や仮の入力で、拠点だけ先に作りに行くのは手が止まる。
    """
    payload = device_payload()
    payload.pop("site_id", None)
    payload["site_name"] = "検証ラック"

    created = client.post(f"{API}/devices", json=payload).json()

    assert created["site_name"] == "検証ラック"
    assert [s["name"] for s in client.get(f"{API}/sites").json()] == ["検証ラック"]


def test_naming_an_existing_site_does_not_make_a_second_one(client, device_payload):
    """表記ゆれで拠点が二重にできないこと。"""
    client.post(f"{API}/sites", json={"name": "本社データセンター"})

    payload = device_payload(hostname="sw-a")
    payload.pop("site_id", None)
    payload["site_name"] = "  本社データセンター "

    created = client.post(f"{API}/devices", json=payload).json()

    assert created["site_name"] == "本社データセンター"
    assert len(client.get(f"{API}/sites").json()) == 1


def test_a_site_can_be_named_when_editing(client, device_payload):
    payload = device_payload()
    payload.pop("site_id", None)
    device_id = client.post(f"{API}/devices", json=payload).json()["id"]

    updated = client.patch(f"{API}/devices/{device_id}", json={"site_name": "大阪ラボ"}).json()

    assert updated["site_name"] == "大阪ラボ"


def test_clearing_the_site_name_unsets_the_site(client, device_payload):
    """空にしたら未設定に戻せること（空文字で拠点を作らない）。"""
    payload = device_payload()
    payload.pop("site_id", None)
    payload["site_name"] = "一時拠点"
    device_id = client.post(f"{API}/devices", json=payload).json()["id"]

    updated = client.patch(f"{API}/devices/{device_id}", json={"site_name": ""}).json()

    assert updated["site_id"] is None
    # 空文字で拠点は増えない
    assert [s["name"] for s in client.get(f"{API}/sites").json()] == ["一時拠点"]
