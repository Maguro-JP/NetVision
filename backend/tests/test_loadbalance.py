"""「機器は動いているのにサービスが応答しない」の形を Config から言えること。

何台置くか、どう振り分けるかという方針は要件が要るので見ない。
"""

from app.services import configparse, loadbalancereview
from app.services.reviewnote import HIGH, LOW, MEDIUM

F5 = """ltm virtual /Common/web_vs {
    destination /Common/10.1.1.100:80
    pool /Common/web_pool
}
ltm pool /Common/web_pool {
    members {
        /Common/10.1.1.11:80 {
            address 10.1.1.11
        }
        /Common/10.1.1.12:80 {
            address 10.1.1.12
        }
    }
    monitor /Common/http
}
ltm monitor http /Common/http {
    interval 5
}
"""


def _review(text: str):
    return loadbalancereview.review(configparse.parse(text))


def _one(findings, word: str):
    hits = [f for f in findings if word in f.title]
    assert len(hits) == 1, [f.title for f in findings]
    return hits[0]


def test_a_healthy_setup_says_nothing():
    assert _review(F5) == []


def test_the_members_and_the_monitor_are_read():
    balance = configparse.parse(F5).load_balance
    pool = balance.pool("web_pool")
    assert [m.address for m in pool.members] == ["10.1.1.11", "10.1.1.12"]
    assert pool.monitors == ["http"]
    assert balance.virtual_servers[0].address == "10.1.1.100"
    assert balance.virtual_servers[0].port == "80"


def test_an_empty_pool_is_called_out():
    text = """ltm virtual /Common/web_vs {
    destination /Common/10.1.1.100:80
    pool /Common/web_pool
}
ltm pool /Common/web_pool {
    monitor /Common/http
}
"""
    assert _one(_review(text), "1台も入っていません").level == HIGH


def test_a_virtual_server_pointing_at_a_missing_pool_is_called_out():
    text = """ltm virtual /Common/web_vs {
    destination /Common/10.1.1.100:80
    pool /Common/gone_pool
}
"""
    item = _one(_review(text), "がありません")
    assert item.level == HIGH
    assert "gone_pool" in item.title


def test_a_virtual_server_with_no_pool_at_all_is_called_out():
    text = "ltm virtual /Common/web_vs {\n    destination /Common/10.1.1.100:80\n}\n"
    assert _one(_review(text), "振り分け先が指定されていません").level == HIGH


def test_a_pool_without_a_monitor_is_called_out():
    text = F5.replace("    monitor /Common/http\n", "")
    item = _one(_review(text), "健全性監視が付いていません")
    assert item.level == MEDIUM
    assert "10.1.1.11:80" in item.evidence


def _stop(text: str, address: str) -> str:
    """その1台を、手で止めた状態にする。"""
    return text.replace(
        f"            address {address}\n",
        f"            address {address}\n            state user-down\n",
    )


def test_members_left_disabled_are_called_out():
    text = _stop(_stop(F5, "10.1.1.11"), "10.1.1.12")
    assert _one(_review(text), "全部止めてあります").level == HIGH


def test_only_one_live_member_is_noted():
    assert _one(_review(_stop(F5, "10.1.1.12")), "1台だけです").level == LOW


def test_a_pool_nobody_uses_is_noted():
    text = (
        F5
        + """ltm pool /Common/old_pool {
    members {
        /Common/10.1.1.99:80 {
            address 10.1.1.99
        }
    }
    monitor /Common/http
}
"""
    )
    assert _one(_review(text), "使われていません").level == LOW


def test_fortigate_load_balancing_is_read():
    text = """config system global
    set hostname fg-01
end
config firewall vip
    edit "web-vip"
        set type server-load-balance
        set extip 203.0.113.10
        set extport 443
        set ldb-method round-robin
        set monitor "https-check"
        config realservers
            edit 1
                set ip 10.1.1.11
                set port 443
            next
            edit 2
                set ip 10.1.1.12
                set port 443
            next
        end
    next
end
"""
    balance = configparse.parse(text).load_balance
    assert balance.virtual_servers[0].address == "203.0.113.10"
    pool = balance.pool("web-vip")
    assert [m.address for m in pool.members] == ["10.1.1.11", "10.1.1.12"]
    assert pool.monitors == ["https-check"]
    assert _review(text) == []


def test_slb_style_is_read():
    text = """slb server web1 10.2.2.11
slb server web2 10.2.2.12
slb service-group web-group
   method round-robin
   health-check ping
   member web1 80
   member web2 80
slb virtual-server web-vip 10.2.2.100
   port 80
   service-group web-group
"""
    balance = configparse.parse(text).load_balance
    pool = balance.pool("web-group")
    assert [m.address for m in pool.members] == ["10.2.2.11", "10.2.2.12"]
    assert pool.monitors == ["ping"]
    assert _review(text) == []


def test_nothing_written_means_nothing_read():
    parsed = configparse.parse(
        "hostname sw-1\ninterface Vlan10\n ip address 10.1.1.1 255.255.255.0\n"
    )
    assert parsed.load_balance is None
    assert loadbalancereview.review(parsed) == []
