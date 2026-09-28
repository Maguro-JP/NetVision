"""CSV / Excel の表の読み取り（台帳・拠点・結線・製品情報の取り込みが共通で使う）。"""

import pytest

from app.services.tablefile import TableFileError, read_table
from tests.conftest import API


def _csv(lines: list[str]) -> bytes:
    return "\n".join(lines).encode("utf-8")


def test_an_unclosed_quote_is_refused_instead_of_swallowing_the_rest():
    """閉じていない「"」で、後ろの行が1つの欄に飲まれて黙って消えない。

    以前は 3000 行の台帳が「5行・エラー0件」として通っていた。
    """
    rows = ["ホスト名,ベンダー,備考"] + [f"sw-{i:05d},Cisco,メモ" for i in range(3000)]
    rows[5] = 'sw-bad,Cisco,"閉じていない引用'
    with pytest.raises(TableFileError) as caught:
        read_table("devices.csv", _csv(rows))
    assert "6件目" in str(caught.value)


def test_a_huge_field_is_refused_with_a_reason():
    with pytest.raises(TableFileError):
        read_table("devices.csv", _csv(["ホスト名,備考", "sw1," + "a" * 200_000]))


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        # 改行を含む欄（Excel の書き出しで出る）は今までどおり1つの欄
        (["ホスト名,備考", 'sw1,"1行目', '2行目"', "sw2,x"], ["1行目\n2行目", "x"]),
        # 引用していない欄の途中の「"」は文字として読む
        (["ホスト名,備考", 'sw1,19" rack', "sw2,x"], ['19" rack', "x"]),
        # 閉じたあとに文字が続く手書きの書き方も、今までどおり読む
        (["ホスト名,備考", 'sw1,"本社"ビル', "sw2,x"], ["本社ビル", "x"]),
    ],
)
def test_the_usual_ways_of_writing_still_read(lines, expected):
    table = read_table("devices.csv", _csv(lines))
    assert [row["備考"] for row in table.rows] == expected


def test_the_import_screen_gets_the_reason(client):
    rows = ["ホスト名,ベンダー,型番"] + [f"sw-{i},Cisco,C1" for i in range(10)]
    rows[3] = 'sw-bad,Cisco,"C1'
    response = client.post(
        f"{API}/import/devices/preview", files={"file": ("d.csv", _csv(rows), "text/csv")}
    )
    assert response.status_code == 422
    assert "閉じていません" in response.json()["detail"]


def _xlsx(rows: list[list[object]]) -> bytes:
    import io

    from openpyxl import Workbook

    book = Workbook()
    for row in rows:
        book.active.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_a_title_above_the_headers_is_skipped_in_excel():
    """表題（と作成日）の行の下に見出しを書いた台帳も、そのまま取り込める。"""
    table = read_table(
        "d.xlsx",
        _xlsx(
            [
                ["機器台帳（2026年度）"],
                [],
                ["作成: 2026/09/01"],
                ["ホスト名", "型番"],
                ["sw1", "C1"],
            ]
        ),
    )
    assert table.headers == ["ホスト名", "型番"]
    assert table.rows == [{"ホスト名": "sw1", "型番": "C1"}]
    assert table.first_row == 5


def test_a_title_above_the_headers_is_skipped_in_csv():
    table = read_table("d.csv", _csv(["機器台帳", "", "ホスト名,型番", "sw1,C1"]))
    assert table.headers == ["ホスト名", "型番"]
    assert table.first_row == 4


def test_a_one_column_file_keeps_its_first_row_as_the_header():
    table = read_table("d.csv", _csv(["ホスト名", "sw1", "sw2"]))
    assert table.headers == ["ホスト名"]
    assert table.first_row == 2


def test_row_numbers_and_error_rows_follow_the_file(client):
    """結果の「何行目」は、表題の行を数えたファイルの行番号。エラー行の書き出しもその行。"""
    body = _xlsx(
        [
            ["機器台帳"],
            ["ホスト名", "ベンダー", "型番", "管理ID"],
            ["sw-a", "Cisco", "C1", "NW-1"],
            ["sw-b", "Cisco", "C1", "NW-1"],
        ]
    )
    result = client.post(
        f"{API}/import/devices/preview",
        files={"file": ("d.xlsx", body, "application/octet-stream")},
    ).json()
    assert [row["row_number"] for row in result["rows"]] == [3, 4]
    assert "3行目" in result["rows"][1]["issues"][0]["message"]

    errors = client.post(
        f"{API}/import/devices/errors.csv",
        files={"file": ("d.xlsx", body, "application/octet-stream")},
    )
    assert "sw-b" in errors.content.decode("utf-8-sig")
    assert "sw-a" not in errors.content.decode("utf-8-sig")
