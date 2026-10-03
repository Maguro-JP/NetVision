"""書いていない設定の既定値（app/services/defaults.py）。

出典は defaults.py の各定数の注記を見る。
"""

from dataclasses import dataclass, field

from app.services import defaults


@dataclass
class _Entry:
    interface: str
    virtual_ip: str | None = None
    priority: int | None = None
    protocol: str = "hsrp"


@dataclass
class _Port:
    name: str
    ip_address: str | None = None
    extra_addresses: list[str] = field(default_factory=list)


def test_an_unwritten_fhrp_priority_is_100():
    assert defaults.fhrp_priority(_Entry("Vlan10"), []) == 100
    assert defaults.fhrp_priority(_Entry("Vlan10", priority=110), []) == 110


def test_a_vrrp_address_owner_is_255_even_on_a_secondary_address():
    ports = [_Port("Vlan10", "10.0.0.2/24", ["10.0.0.1/24"])]
    owner = _Entry("Vlan10", virtual_ip="10.0.0.1", protocol="vrrp")
    assert defaults.fhrp_priority(owner, ports) == 255
    # HSRP には持ち主の考えが無い
    assert defaults.fhrp_priority(_Entry("Vlan10", virtual_ip="10.0.0.1"), ports) == 100


def test_an_unwritten_static_distance_is_1():
    assert defaults.static_distance(None) == 1
    assert defaults.static_distance(200) == 200


def test_auto_is_the_same_as_not_written():
    assert defaults.negotiated(None) is None
    assert defaults.negotiated("Auto") is None
    assert defaults.negotiated("1000") == "1000"


def test_ospf_authentication_null_is_no_authentication():
    assert defaults.ospf_auth(None) is None
    assert defaults.ospf_auth("none") is None
    assert defaults.ospf_auth("md5") == "md5"


def test_the_static_distance_default_depends_on_the_vendor():
    assert defaults.static_distance(None, "huawei") == 60
    assert defaults.static_distance(None, "h3c") == 60
    assert defaults.static_distance(None, "fortinet") == 10
    assert defaults.static_distance(None, "cisco_ios") == 1
    assert defaults.static_distance(5, "huawei") == 5
