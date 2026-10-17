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


def test_a_banner_whose_text_starts_on_the_first_line_is_still_removed() -> None:
    """`banner motd ^CWelcome` のように、印の直後に文言が続いても閉じを見つける。

    出典: Cisco IOS Command Reference の banner motd（`banner motd d message d`。
    d は1文字で、2回目に出たところで文言が終わる）。
    """
    text = (
        "hostname sw-01\nbanner motd ^CWelcome\ninterface GigabitEthernet0/99\n^C\n"
        "interface GigabitEthernet0/1\n"
    )
    stripped = banners.strip(text)
    assert "GigabitEthernet0/99" not in stripped
    assert "interface GigabitEthernet0/1" in stripped
    assert len(stripped.splitlines()) == len(text.splitlines())


def test_a_delimiter_of_your_choice_is_followed() -> None:
    """区切りは好きな1文字。公式の例は `#` と `%` と `/`。"""
    text = (
        "hostname sw-01\nbanner exec /\n interface GigabitEthernet0/99\n /\n"
        "banner motd %You have entered $(hostname) %\n"
        "interface GigabitEthernet0/1\n"
    )
    parsed = configparse.parse(text)
    assert [port.name for port in parsed.interfaces] == ["GigabitEthernet0/1"]


def test_a_banner_without_a_delimiter_is_left_alone() -> None:
    """ASA は `banner motd 文言` と印を使わない。1文字目を印にすると設定を捨てる。"""
    text = "hostname fw-01\nbanner motd Authorized use only\ninterface GigabitEthernet0/1\n"
    assert banners.strip(text) == text


def test_saved_banners_follow_the_cisco_network_as_code_sample() -> None:
    """見張り。保存した設定の banner は、区切りが `^C` と出る。4種類とも外れることを見張る。

    出典: Cisco「Network as Code」の IOS-XE Banner（Sample Configuration。`banner login ^C` の
    次の行から `^C` まで、`banner motd ^C文言` と同じ行に続く形、`banner prompt-timeout ^C文言^C`
    と1行で閉じる形）。区切りの考え方は System Management Configuration Guide, Cisco IOS XE
    （Catalyst 9300 17.15）の Configuring a Message-of-the-Day Login Banner。
    """
    text = (
        "hostname rt-01\n"
        "banner exec ^C\n"
        "Welcome to Device1\n"
        "interface GigabitEthernet0/99\n"
        "^C\n"
        "banner login ^C\n"
        "* WARNING: Unauthorized access is *\n"
        "^C\n"
        "banner motd ^CSystem maintenance scheduled for Sunday 2AM-4AM\n"
        "All users will be disconnected during this time\n"
        "^C\n"
        "banner prompt-timeout ^CSession will timeout in 5 minutes due to inactivity^C\n"
        "interface GigabitEthernet0/1\n"
        " description uplink\n"
    )
    stripped = banners.strip(text).splitlines()
    assert len(stripped) == len(text.splitlines())
    assert [line for line in stripped if line] == [
        "hostname rt-01",
        "interface GigabitEthernet0/1",
        " description uplink",
    ]
