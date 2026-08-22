"""選んだところだけを直してもらう。

Config を全部作り直すのは危ない。1行直したいだけなのに全体が返ってくると、
どこが変わったのかを人が突き合わせることになり、結局読み切れない。
"""

from app.services import config_edit
from tests.conftest import API

PORT = """interface GigabitEthernet1/0/5
 description old
 switchport mode access
 switchport access vlan 10
 no shutdown"""


def test_closing_a_port_is_written_by_rule_not_guessed():
    """よく頼まれる直しは規則で書く。速く、毎回同じで、間違えたら直せる。"""
    result = config_edit.edit(PORT, "このポートは使わないので閉じて")
    assert result.source == "rule"
    assert " shutdown" in result.after
    assert " no shutdown" not in result.after
    assert result.changed


def test_opening_a_port_removes_the_opposite_line():
    result = config_edit.edit(PORT.replace(" no shutdown", " shutdown"), "このポートを開けて")
    assert " no shutdown" in result.after
    assert result.after.count("shutdown") == 1


def test_a_description_replaces_the_old_one_rather_than_piling_up():
    result = config_edit.edit(PORT, "説明を「to floor3-sw-01」にして")
    assert " description to floor3-sw-01" in result.after
    assert " description old" not in result.after


def test_a_japanese_description_is_not_written_into_the_device():
    """機器によっては日本語が化ける。化けた説明は無いより悪い。"""
    result = config_edit.edit(PORT, "説明を「3階の島」にして")
    assert "3階の島" not in result.after


def test_telnet_can_be_turned_off_by_rule():
    lines = "line vty 0 15\n transport input telnet"
    result = config_edit.edit(lines, "telnet はやめて ssh だけにして")
    assert " transport input ssh" in result.after
    assert "telnet" not in result.after


def test_the_result_comes_back_as_a_difference():
    """全体を上書きした結果だけ見せると、確認が飛ばされる。"""
    result = config_edit.edit(PORT, "このポートを閉じて")
    assert result.diff.startswith("---")
    assert "+ shutdown" in result.diff


def test_an_empty_selection_says_so(client=None):
    result = config_edit.edit("   ", "閉じて")
    assert not result.changed
    assert any("選ばれていません" in note for note in result.notes)


def test_without_a_model_it_hands_back_something_to_paste():
    """規則で書けない直しは、モデルが無ければ依頼文にして返す。"""
    result = config_edit.edit(PORT, "この構成を冗長化してほしい")
    assert result.source == "prompt"
    assert result.prompt
    assert "この範囲だけを返してください" in result.prompt
    assert result.after == result.before


def test_the_paste_prompt_hides_secrets():
    """貼る先は社外の AI かもしれない。"""
    result = config_edit.edit("snmp-server community s3cret RW", "この設定をもっと安全にしてほしい")
    assert result.prompt
    assert "s3cret" not in result.prompt
    assert "伏せています" in result.prompt


def test_the_api_returns_the_difference(client):
    res = client.post(
        f"{API}/config-check/edit",
        json={"selection": PORT, "request": "このポートを閉じて"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["changed"] is True
    assert body["source"] == "rule"
    assert "+ shutdown" in body["diff"]
    assert body["notes"]


def test_the_api_needs_to_know_what_to_do(client):
    res = client.post(f"{API}/config-check/edit", json={"selection": PORT, "request": "  "})
    assert res.status_code == 422
