"""show の出力だけを貼られたとき、プロンプトから機器名を読むこと。

`hostname` の行は running-config にしか無い。show version と show inventory だけを
貼る運用は多く、そのとき機器名が空だと台帳と突き合わせられない。
"""

from app.services import configparse


def test_the_prompt_gives_the_hostname_when_no_config_is_pasted():
    text = (
        "sw-01#show version\nCisco IOS XE Software, Version 17.09.04a\n"
        "cisco C9300-48P (X86) processor with 1331521K/6147K bytes of memory.\n"
    )
    parsed = configparse.parse(text)
    assert (parsed.hostname, parsed.model) == ("sw-01", "C9300-48P")


def test_the_uptime_line_works_too():
    text = "core-01 uptime is 1 year, 2 weeks\ncisco WS-C2960X-48FPD-L (APM86XXX) processor\n"
    assert configparse.parse(text).hostname == "core-01"


def test_asa_and_nxos_prompts_are_read_the_same_way():
    asa = (
        "fw-01# show version\nCisco Adaptive Security Appliance Software Version 9.16(4)\n"
        "Hardware:   ASA5516, 8192 MB RAM\n"
    )
    assert configparse.parse(asa).hostname == "fw-01"
    nx = "n9k-01# show version\nNXOS: version 9.3(8)\n  cisco Nexus9000 C93180YC-FX Chassis\n"
    assert configparse.parse(nx).hostname == "n9k-01"


def test_the_hostname_line_still_wins_over_the_prompt():
    text = "old-name#show running-config\nhostname new-name\ninterface Vlan1\n"
    assert configparse.parse(text).hostname == "new-name"
