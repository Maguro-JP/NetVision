"""拠点台帳の取り込み。

拠点は数が多い。2万を超える拠点を画面から1つずつ直すのは現実的ではないので、
書き出した表を直して戻せることを確かめる。機器を巻き込まないことも見る。
"""

API = "/api/v1"


def _csv(*lines: str) -> bytes:
    return "\n".join(lines).encode("utf-8-sig")


def test_sites_can_be_updated_from_a_table(client):
    """書き出した並びのまま取り込める。住所と状態が入る。"""
    site = client.post(f"{API}/sites", json={"name": "青森県営業所", "code": "AOM-1"}).json()

    body = _csv(
        "拠点ID,拠点名,状態,住所,開設日,閉鎖日,メモ",
        "AOM-1,青森県営業所,一時閉鎖,青森県青森市,2019/4/1,,改装中",
    )
    res = client.post(
        f"{API}/import/sites",
        files={"file": ("sites.csv", body, "text/csv")},
    )
    assert res.status_code == 200, res.text
    assert res.json()["updated"] == 1

    after = client.get(f"{API}/sites").json()
    updated = next(row for row in after if row["id"] == site["id"])
    assert updated["status"] == "suspended"
    assert updated["address"] == "青森県青森市"
    assert updated["opened_on"] == "2019-04-01"
    assert updated["notes"] == "改装中"


def test_a_preview_changes_nothing(client):
    """取り込む前に中身だけ見られること。"""
    body = _csv("拠点ID,拠点名,住所", "TOK-1,本社,東京都千代田区")
    res = client.post(
        f"{API}/import/sites/preview",
        files={"file": ("sites.csv", body, "text/csv")},
    )
    assert res.status_code == 200
    assert res.json()["created"] == 1
    assert res.json()["committed"] is False
    assert client.get(f"{API}/sites").json() == []


def test_new_sites_are_created(client):
    body = _csv("拠点ID,拠点名", "S-001,大手町ビル", "S-002,大手町ビル")
    res = client.post(f"{API}/import/sites", files={"file": ("sites.csv", body, "text/csv")})
    assert res.json()["created"] == 2

    names = [row["name"] for row in client.get(f"{API}/sites").json()]
    assert names.count("大手町ビル") == 2


def test_devices_are_left_alone(client):
    """拠点を直しても、その拠点の機器は触らない。"""
    site = client.post(f"{API}/sites", json={"name": "千葉県営業所", "code": "CHB-1"}).json()
    client.post(f"{API}/devices", json={"hostname": "chiba-rt-01", "site_id": site["id"]})

    body = _csv("拠点ID,拠点名,住所", "CHB-1,千葉県営業所,千葉県千葉市中央区")
    client.post(f"{API}/import/sites", files={"file": ("sites.csv", body, "text/csv")})

    devices = client.get(f"{API}/devices").json()
    assert [d["hostname"] for d in devices] == ["chiba-rt-01"]


def test_a_bad_date_is_reported_by_row(client):
    """読めない値は、その行の話として返す。丸ごと落とさない。"""
    body = _csv("拠点ID,拠点名,開設日", "S-001,本社,きのう", "S-002,支社,2020-04-01")
    res = client.post(f"{API}/import/sites", files={"file": ("sites.csv", body, "text/csv")})
    result = res.json()
    assert result["created"] == 1
    assert result["issues"][0]["row_number"] == 2
    assert "日付" in result["issues"][0]["message"]


def test_an_exported_excel_ledger_goes_back_unchanged(client):
    """書き出した拠点台帳の Excel を、何も変えずに戻せる。

    Excel の日付のセルは datetime で来るので、文字にしてから読むと
    「2018-04-01 00:00:00」になり、開設日・閉鎖日のある行が読めなかった。
    """
    client.post(
        f"{API}/sites",
        json={"code": "HQ", "name": "本社", "opened_on": "2018-04-01", "closed_on": "2030-03-31"},
    )
    exported = client.get(f"{API}/export/sites.xlsx")
    assert exported.status_code == 200
    response = client.post(
        f"{API}/import/sites/preview",
        files={"file": ("sites.xlsx", exported.content, "application/octet-stream")},
    )
    result = response.json()
    assert result["issues"] == []
    assert result["unchanged"] == 1
