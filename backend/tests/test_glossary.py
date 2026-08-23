"""画面に出てくる言葉の説明。

このツールを使う人がネットワークの用語を知っているとは限らない。
知らないまま使えるほうが正しい。
"""

from app.services import glossary
from tests.conftest import API


def test_every_term_says_what_it_is_and_what_goes_wrong():
    """意味だけ分かっても、画面の指摘が何を言っているのかは分からない。"""
    for term in glossary.TERMS:
        assert term.plain, term.term
        assert term.matters, term.term
        assert term.reading, term.term


def test_the_explanations_do_not_lean_on_other_jargon():
    """説明の中に知らない言葉が出ると、振り出しに戻る。"""
    jargon = ("ブロードキャストドメイン", "カプセル化", "ネクストホップ解決", "AS 番号")
    for term in glossary.TERMS:
        for word in jargon:
            assert word not in term.plain, f"{term.term} の説明に {word} が出ている"


def test_a_word_can_be_found_by_the_name_people_actually_use():
    assert glossary.find("スタティックルート").term == "静的経路"
    assert glossary.find("デフォルトゲートウェイ").term == "ゲートウェイ"
    assert glossary.find("ポートチャネル").term == "LAG"
    assert glossary.find("そんな言葉") is None


def test_it_can_be_searched_from_the_symptom_not_the_word():
    """言葉を知らない人は、起きていることのほうから探す。"""
    assert [t.term for t in glossary.search("切り替わ")] == ["HSRP"]
    assert any(t.term == "トランク" for t in glossary.search("片方だけ落ち"))


def test_the_terms_the_screens_use_are_covered():
    """画面に出しておいて、説明が無い言葉があるのはいちばん困る。"""
    used = [
        "セグメント",
        "VLAN",
        "トランク",
        "静的経路",
        "ネクストホップ",
        "OSPF",
        "BGP",
        "VRF",
        "HSRP",
        "VRRP",
        "ACL",
        "NAT",
        "DHCP",
        "STP",
        "LAG",
        "EoL",
        "LLDP",
    ]
    for word in used:
        assert glossary.find(word) is not None, word


def test_the_api_returns_the_list(client):
    body = client.get(f"{API}/glossary").json()
    assert len(body) == len(glossary.TERMS)
    assert all(item["plain"] and item["matters"] for item in body)


def test_the_api_can_narrow_it_down(client):
    body = client.get(f"{API}/glossary", params={"q": "VLAN"}).json()
    assert body
    assert any(item["term"] == "VLAN" for item in body)


def test_every_term_belongs_to_a_field():
    """数が増えると一覧では探せない。分野で束ねられること。"""
    for term in glossary.TERMS:
        assert term.category, term.term


def test_the_words_a_certification_asks_about_are_covered():
    """資格で必ず出るところは、聞かれたときに引けるようにしておく。

    現場で「この言葉が分からない」と止まるのは、たいていこの辺りの言葉。
    """
    expected = [
        "OSI参照モデル",
        "MACアドレス",
        "ARP",
        "TCP",
        "UDP",
        "ポート番号",
        "DNS",
        "MTU",
        "CIDR",
        "ネイティブVLAN",
        "タグVLAN",
        "SVI",
        "ルートブリッジ",
        "ポートファスト",
        "BPDUガード",
        "動的経路",
        "メトリック",
        "アドミニストレーティブディスタンス",
        "ロンゲストマッチ",
        "経路集約",
        "EIGRP",
        "RIP",
        "再配布",
        "ECMP",
        "暗黙のdeny",
        "PAT",
        "DMZ",
        "802.1X",
        "AAA",
        "IPsec",
        "SSH",
        "SNMP",
        "Syslog",
        "NTP",
        "QoS",
        "PoE",
        "MPLS",
        "VXLAN",
    ]
    for word in expected:
        assert glossary.find(word) is not None, word


def test_the_api_hands_over_the_field(client):
    body = client.get(f"{API}/glossary").json()
    assert all(item["category"] for item in body)
