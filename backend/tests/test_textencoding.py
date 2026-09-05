"""渡されたファイルの文字コードを、取り違えないこと。

cp932 と EUC-JP は、お互いのバイト列をエラー無しに読めてしまう。順に
試して最初に通ったものを採ると、化けた文字がそのまま通り、読めなかった
ことにも気づけない。description が化けたまま台帳に入ると、あとから
誰も直せない。
"""

from app.services.textencoding import decode

JP = "hostname sw-01\n description 会議室へ 3階の島（南）\n"


def test_each_japanese_encoding_comes_back_as_written() -> None:
    for encoding in ("utf-8", "cp932", "euc_jp"):
        assert decode(JP.encode(encoding)) == JP, encoding


def test_a_byte_order_mark_is_not_left_in_the_text() -> None:
    assert decode(JP.encode("utf-8-sig")) == JP


def test_plain_ascii_is_untouched() -> None:
    text = "hostname sw-01\ninterface GigabitEthernet1/0/1\n"
    assert decode(text.encode("ascii")) == text


def test_binary_noise_does_not_lose_the_readable_part() -> None:
    """show tech には制御文字が混ざる。丸ごと落とすより、読めた分を残す。"""
    raw = b"hostname sw-01\n\xff\xfe\x00\x01\ninterface Gi1/0/1\n"
    got = decode(raw)
    assert "hostname sw-01" in got
    assert "interface Gi1/0/1" in got


def test_a_table_says_so_rather_than_passing_broken_text() -> None:
    """表は、化けた文字が1つ入るだけで台帳として使えなくなる。

    読めた分だけ通すより、読めなかったと言って保存し直してもらう。
    """
    import pytest

    from app.services.tablefile import TableFileError, read_table

    for encoding in ("utf-8", "utf-8-sig", "cp932", "euc_jp"):
        table = read_table("ledger.csv", "ホスト名,拠点\nsw-01,本社\n".encode(encoding))
        assert table.headers[:2] == ["ホスト名", "拠点"], encoding
        assert table.rows[0]["拠点"] == "本社", encoding

    with pytest.raises(TableFileError):
        read_table("ledger.csv", b"host\x81 name")
