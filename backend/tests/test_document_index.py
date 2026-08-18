"""渡した資料が、こちらで整理されて引ける状態になること。

置いておくだけでは「この機種のこのコマンドはどう書くのか」を引けない。
章に切り、章ごとのコマンドを控え、閉域の中だけで探せることを見る。
"""

from tests.conftest import API

MANUAL = """QX-S5500 シリーズ コマンドリファレンス

1. VLAN の設定
VLAN を作り、ポートに割り当てます。
 vlan 10
  name USERS
 interface GigabitEthernet1/0/2
  port link-type access
  port access vlan 10

2. リンクアグリゲーションの設定
複数のポートを束ねます。手順は次のとおりです。
 interface Bridge-Aggregation1
  port link-type trunk
 interface GigabitEthernet1/0/1
  port link-aggregation group 1
"""


def upload(client, body: str, filename: str = "qx-command-reference.txt"):
    return client.post(
        f"{API}/documents",
        data={
            "vendor": "NEC",
            "title": "QX-S コマンドリファレンス",
            "doc_type": "command_reference",
            "model": "QX-S5528P",
        },
        files={"file": (filename, body.encode("utf-8"), "text/plain")},
    )


def test_a_manual_is_split_into_sections_when_it_is_uploaded(client):
    """人が「整理する」を押さなくても、渡した時点で引ける形になる。"""
    body = upload(client, MANUAL).json()
    assert body["section_count"] >= 2
    assert body["indexed_at"] is not None
    assert body["index_note"] is None


def test_sections_keep_the_commands_that_appear_in_them(client):
    """章ごとにコマンドを控える。書き方を引くのが目的なので、そこが要。"""
    upload(client, MANUAL)
    hits = client.get(f"{API}/documents/search", params={"q": "link-aggregation"}).json()
    assert hits, hits
    commands = [line for hit in hits for line in hit["commands"]]
    assert "port link-aggregation group 1" in commands
    # 説明文はコマンドとして拾わない
    assert not any(line.endswith("。") for line in commands)


def test_search_can_be_narrowed_by_model(client):
    """型番で絞れる。別の機種の書き方を混ぜて出すと、そのまま打たれてしまう。"""
    upload(client, MANUAL)
    same = client.get(f"{API}/documents/search", params={"q": "vlan", "model": "QX-S5528P"}).json()
    other = client.get(f"{API}/documents/search", params={"q": "vlan", "model": "C9500-40X"}).json()
    assert same
    assert other == []


def test_a_document_that_cannot_be_read_says_so(client):
    """読めなかった資料は理由を残す。黙って空にすると、入れたつもりで引けない。"""
    res = client.post(
        f"{API}/documents",
        data={"vendor": "NEC", "title": "画像だけの資料", "doc_type": "other"},
        files={"file": ("scan.bin", bytes([0, 1, 2, 3, 0, 5]), "application/octet-stream")},
    )
    body = res.json()
    assert body["section_count"] == 0
    assert body["index_note"]


def test_a_device_only_sees_manuals_that_match_it(client, device_payload):
    """機種の違う書き方を混ぜて出すと、そのまま打たれてしまう。"""
    upload(client, MANUAL)
    device = client.post(
        f"{API}/devices",
        json=device_payload(hostname="aichi-sw-01", vendor="NEC", model="QX-S5528P"),
    ).json()
    other = client.post(
        f"{API}/devices",
        json=device_payload(hostname="core-sw-01", vendor="Cisco", model="C9500-40X"),
    ).json()

    mine = client.get(f"{API}/devices/{device['id']}/document-search", params={"q": "vlan"}).json()
    theirs = client.get(f"{API}/devices/{other['id']}/document-search", params={"q": "vlan"}).json()
    assert mine
    assert theirs == []
