"""ログイン時に見せる文言（banner）を、設定と間違えないこと。

注意書きに設定例を書いてある機器は多い。そのまま読むと、無い口が増え、
無い合言葉が指摘に出る。「無いものを有ると言う」いちばん困る間違い方。
"""

from app.services import configcheck, configparse
from app.services.configparse import banners

WITH_BANNER = """hostname sw-01
!
banner motd ^C
この機器は許可された者だけが使えます。
例) interface GigabitEthernet0/99 は使用禁止
snmp-server community public RO
^C
!
interface GigabitEthernet0/1
 description users
 switchport mode access
 switchport access vlan 10
!
snmp-server community Str0ngOne RO
logging host 10.0.0.9
ntp server 10.0.0.8
line vty 0 4
 transport input ssh
"""


def test_a_port_written_in_the_banner_does_not_become_a_port() -> None:
    parsed = configparse.parse(WITH_BANNER)
    assert [port.name for port in parsed.interfaces] == ["GigabitEthernet0/1"]


def test_a_line_in_the_banner_is_not_pointed_at() -> None:
    """文言の中の `public` を見て、合言葉が既定のままだと言わない。"""
    parsed = configparse.parse(WITH_BANNER)
    titles = [f.title for f in configcheck._findings(parsed, banners.strip(WITH_BANNER))]
    assert not [title for title in titles if "合言葉" in title]


def test_the_setting_after_the_banner_is_still_read() -> None:
    """外しすぎて、後ろの設定まで落とさないこと。"""
    parsed = configparse.parse(WITH_BANNER)
    assert parsed.hostname == "sw-01"
    assert parsed.interfaces[0].vlan == 10


def test_a_banner_that_is_never_closed_leaves_the_config_alone() -> None:
    """閉じを探しに行った先が設定なら、設定ぜんぶを捨てることになる。"""
    text = "hostname sw-01\nbanner motd ^C\ninterface GigabitEthernet0/1\n"
    assert banners.strip(text) == text
    assert configparse.parse(text).interfaces


def test_a_banner_without_a_mark_is_left_alone() -> None:
    """どこまでが文言かを決められないものを、当てずっぽうで切らない。"""
    text = "hostname sw-01\nbanner motd\ninterface GigabitEthernet0/1\n"
    assert banners.strip(text) == text


def test_the_lines_do_not_move() -> None:
    """行番号で場所を示している読み手とずれないこと。"""
    assert len(banners.strip(WITH_BANNER).splitlines()) == len(WITH_BANNER.splitlines())
