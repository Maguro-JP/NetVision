"""選んで、値を入れるだけの Config 作成。

ここで見るのは、機種ごとの書き方が混ざっていないこと、おかしな値が
そのまま Config にならないこと、出せない機種で嘘を出さないこと。
"""

import pytest

from app.services import configrecipes as recipes
from app.services.configparse import parse
from app.services.configparse.base import Vendor
from tests.conftest import API


def _body(recipe: str, vendor: Vendor, **values: str) -> str:
    return recipes.render(recipe, vendor, values).body


def test_the_same_recipe_comes_out_in_each_vendors_own_words() -> None:
    """同じ用事でも、書き方は機種ごとに違う。ここが混ざると入らない。"""
    wanted = {
        Vendor.CISCO_IOS: " switchport access vlan 10",
        Vendor.NEC_QX: " port access vlan 10",
        Vendor.HUAWEI: " port default vlan 10",
        Vendor.ARUBA_CX: "    vlan access 10",
        Vendor.JUNIPER: "family ethernet-switching vlan members 10",
    }
    for vendor, line in wanted.items():
        body = _body("access_port", vendor, interface="GigabitEthernet0/5", vlan_id="10")
        assert line in body, vendor


def test_what_comes_out_can_be_read_back_as_that_vendor() -> None:
    """出したものを読み直せないなら、書き方がその機種のものになっていない。"""
    body = _body("access_port", Vendor.CISCO_IOS, interface="GigabitEthernet0/5", vlan_id="10")
    parsed = parse(body + "\nhostname sw-01\n")
    assert parsed.vendor is Vendor.CISCO_IOS
    assert any(port.vlan == 10 and port.mode == "access" for port in parsed.interfaces)


def test_a_vlan_number_out_of_range_is_refused() -> None:
    """9999 番の VLAN は作れない。それらしい Config を出す方が困る。"""
    with pytest.raises(recipes.RecipeError):
        _body("vlan", Vendor.CISCO_IOS, vlan_id="9999", name="USERS")


def test_a_vlan_name_with_spaces_is_refused() -> None:
    with pytest.raises(recipes.RecipeError):
        _body("vlan", Vendor.CISCO_IOS, vlan_id="10", name="user vlan")


def test_a_range_of_vlans_is_understood() -> None:
    """現場は `10,30,40-42` と書いて渡してくる。"""
    body = _body(
        "trunk_port", Vendor.CISCO_IOS, interface="Gi0/1", allowed="10,30,40-42", native_vlan="999"
    )
    assert " switchport trunk allowed vlan 10,30,40,41,42" in body
    assert " switchport trunk native vlan 999" in body


def test_a_native_vlan_that_is_also_tagged_is_refused() -> None:
    """同じ番号をタグ付きとタグ無しの両方に書くと、機種で解釈が割れる。"""
    with pytest.raises(recipes.RecipeError):
        _body("trunk_port", Vendor.CISCO_IOS, interface="Gi0/1", allowed="10,30", native_vlan="10")


def test_a_native_vlan_left_empty_is_left_as_a_blank_to_fill() -> None:
    """決めていないものを勝手に 1 で埋めない。"""
    body = _body("trunk_port", Vendor.CISCO_IOS, interface="Gi0/1", allowed="10,30")
    assert recipes.TODO in body
    assert "native vlan 1" not in body


def test_a_machine_that_cannot_be_written_says_so() -> None:
    """無いのと読めていないのを混ぜない。それらしい行を出さない。"""
    with pytest.raises(recipes.RecipeError, match="この機種"):
        _body("svi", Vendor.FORTINET, vlan_id="30", ip="10.0.0.1", prefixlen="24")


def test_the_vendors_a_recipe_can_be_written_for_are_listed() -> None:
    """画面で選べる機種を絞るために使う。値の間違いで機種が消えないこと。"""
    vendors = recipes.vendors_for(recipes.get("access_port"))
    assert Vendor.CISCO_IOS in vendors
    assert Vendor.FORTINET not in vendors
    assert Vendor.UNKNOWN not in vendors


def test_the_address_is_written_the_way_each_machine_wants_it() -> None:
    """マスクの書き方は機種で違う。長さで書くところと、4つ組で書くところ。"""
    assert " ip address 10.0.0.21 255.255.255.0" in _body(
        "svi", Vendor.CISCO_IOS, vlan_id="30", ip="10.0.0.21", prefixlen="24"
    )
    assert "    ip address 10.0.0.21/24" in _body(
        "svi", Vendor.ARUBA_CX, vlan_id="30", ip="10.0.0.21", prefixlen="24"
    )
    assert " ip address 10.0.0.21 24" in _body(
        "svi", Vendor.NEC_QX, vlan_id="30", ip="10.0.0.21", prefixlen="24"
    )


def test_something_that_is_not_an_address_is_refused() -> None:
    with pytest.raises(recipes.RecipeError):
        _body("svi", Vendor.CISCO_IOS, vlan_id="30", ip="10.0.0.300", prefixlen="24")


def test_every_recipe_can_be_written_for_at_least_one_machine() -> None:
    """選べるのに何も出ない献立を置かない。"""
    for recipe in recipes.RECIPES:
        assert recipes.vendors_for(recipe), recipe.name


def test_the_screen_can_list_the_recipes(client) -> None:
    response = client.get(f"{API}/config-recipes")
    assert response.status_code == 200
    names = [item["name"] for item in response.json()]
    assert "access_port" in names
    first = response.json()[0]
    assert first["fields"] and first["vendors"]


def test_the_screen_gets_a_readable_reason_when_the_value_is_wrong(client) -> None:
    response = client.post(
        f"{API}/config-recipes/vlan",
        json={"vendor": "cisco_ios", "values": {"vlan_id": "9999", "name": "USERS"}},
    )
    assert response.status_code == 422
    assert "4094" in response.json()["detail"]


def test_the_screen_gets_the_config(client) -> None:
    response = client.post(
        f"{API}/config-recipes/hardening", json={"vendor": "huawei", "values": {}}
    )
    assert response.status_code == 200
    assert "undo telnet server enable" in response.json()["body"]
