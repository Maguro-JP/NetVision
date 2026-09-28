"""CSV を Excel で開いても、値が式として動かない。取り込み直すと元の値に戻る。

CSV には文字の印が無いので、Excel は `=`・`+`・`-`・`@` で始まる欄を式として読む。
機器名の `=HYPERLINK(...)` がリンクとして動き、`=== 要確認 ===` が `#NAME?` になっていた。
式の形の値だけ先頭に `'` を付けて書き、取り込みではその `'` を外す（csvsafe.py）。
"""

import csv
import io
import re
from pathlib import Path

import pytest

from app.services import csvsafe
from app.services.tablefile import read_table
from tests.conftest import API

HYPERLINK = '=HYPERLINK("http://example.invalid","開く")'


@pytest.mark.parametrize(
    "value",
    [HYPERLINK, "=== 要確認 ===", "+81312345678", "@SUM(A1)", "-cmd", "- 要確認", "\tx"],
)
def test_a_value_read_as_a_formula_gets_the_text_mark(value) -> None:
    assert csvsafe.guard(value) == "'" + value


@pytest.mark.parametrize("value", ["core-sw-01", "", "-", "-5", "-1.5", "10.0.0.1", "本社", 480000])
def test_an_ordinary_value_is_left_as_it_is(value) -> None:
    """付けるのは式の形の値だけ。Excel 以外で開いたときに `'` が見える値を増やさない。"""
    assert csvsafe.guard(value) == value


@pytest.mark.parametrize(
    "value",
    [HYPERLINK, "'" + HYPERLINK, "''=x", "'abc", "it's", "'", "-", "-5", "+81", "plain"],
)
def test_writing_and_reading_back_keeps_the_value(value) -> None:
    """元々 `'` で始まる値も、書き出して取り込み直すと同じ値に戻る。"""
    assert csvsafe.unguard(csvsafe.guard(value)) == value


def test_a_quote_the_export_did_not_add_is_kept() -> None:
    assert csvsafe.unguard("'abc") == "'abc"
    assert csvsafe.unguard("it's") == "it's"


def test_the_ledger_csv_does_not_start_a_cell_with_a_formula(client) -> None:
    site = client.post(f"{API}/sites", json={"code": "S1", "name": "本社"}).json()
    created = client.post(
        f"{API}/devices",
        json={
            "hostname": HYPERLINK,
            "vendor": "Cisco",
            "model": "=== 要確認 ===",
            "site_id": site["id"],
        },
    )
    assert created.status_code == 201

    content = client.get(f"{API}/export/devices.csv").content
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline="")))
    cells = [cell for row in rows for cell in row]
    assert not [cell for cell in cells if cell[:1] in ("=", "+", "@")]
    assert "'" + HYPERLINK in cells

    # 書き出した CSV を取り込み直すと、元の値に戻る
    table = read_table("devices.csv", content)
    assert {row["ホスト名"] for row in table.rows} == {HYPERLINK}
    assert {row["型番"] for row in table.rows} == {"=== 要確認 ==="}


def test_no_csv_is_written_without_the_guard() -> None:
    """csv.writer を直接使うと守りが抜ける。書き出しは csvsafe.writer を通す。"""
    app = Path(__file__).resolve().parents[1] / "app"
    direct = [
        str(path.relative_to(app))
        for path in app.rglob("*.py")
        if path.name != "csvsafe.py" and re.search(r"\bcsv\.writer\(", path.read_text("utf-8"))
    ]
    assert direct == []
