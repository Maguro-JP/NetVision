"""show の表から、線が実際に上がっているかを読む。

Config には「止めていない」までしか書いていない。図に線があるのに実機では
上がっていないなら、それは図と実機のずれそのもの。
"""

from pathlib import Path

from app.models import Device
from app.models.topology import Interface, Link
from app.services import configparse, linkcheck
from app.services.configparse import normalize

FIXTURES = Path(__file__).parent / "fixtures"

STATUS = """
show interfaces status
Port      Name               Status       Vlan       Duplex  Speed Type
Gi1/0/1   to-acc-sw          connected    trunk      a-full  a-1000 10/100/1000BaseTX
Gi1/0/2                      notconnect   1            auto   auto 10/100/1000BaseTX
Gi1/0/24                     err-disabled 1            auto   auto 10/100/1000BaseTX
"""

BRIEF = """
show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
Vlan10                 10.0.10.1       YES NVRAM  up                    up
Vlan20                 10.0.20.1       YES NVRAM  administratively down down
GigabitEthernet1/0/1   unassigned      YES unset  up                    up
GigabitEthernet1/0/2   unassigned      YES unset  down                  down
"""

TERSE = """
show interfaces terse
Interface               Admin Link Proto    Local                 Remote
ge-0/0/0                up    up
ge-0/0/0.0              up    up   inet     10.0.0.1/24
ge-0/0/1                up    down
ge-0/0/2                down  down
"""


def _states(parsed) -> dict[str, str]:
    return {i.name: i.link_state for i in parsed.interfaces if i.link_state}


def test_show_interfaces_status_is_read_with_the_words_lined_up():
    text = (FIXTURES / "core-sw-01.txt").read_text() + STATUS
    states = _states(configparse.parse(text))
    assert states["GigabitEthernet1/0/1"] == "up"  # connected は up にそろえる
    assert states["GigabitEthernet1/0/2"] == "notconnect"


def test_show_ip_interface_brief_is_read():
    text = (FIXTURES / "core-sw-01.txt").read_text() + BRIEF
    states = _states(configparse.parse(text))
    assert states["GigabitEthernet1/0/1"] == "up"
    assert states["GigabitEthernet1/0/2"] == "down"


def test_junos_terse_is_read_on_the_physical_port():
    text = (FIXTURES / "edge-rt-01.txt").read_text() + TERSE
    states = _states(configparse.parse(text))
    assert states["ge-0/0/0"] == "up"
    assert states["ge-0/0/1"] == "down"
    assert states["ge-0/0/2"] == "disabled"


def test_a_config_without_show_output_says_nothing_about_the_link():
    parsed = configparse.parse((FIXTURES / "core-sw-01.txt").read_text())
    assert _states(parsed) == {}


def test_a_port_that_only_appears_in_show_is_not_invented():
    """show にしか出ないポートは線の材料にならないので作らない。"""
    text = "hostname sw\ninterface Gi1/0/1\n" + STATUS
    parsed = configparse.parse(text)
    assert [i.name for i in parsed.interfaces] == ["GigabitEthernet1/0/1"]


def _pair(db, left: str | None, right: str | None) -> None:
    a = Device(hostname="sw-a", vendor="Cisco")
    b = Device(hostname="sw-b", vendor="Cisco")
    db.add_all([a, b])
    db.flush()
    for device, state in ((a, left), (b, right)):
        db.add(
            Interface(
                device_id=device.id,
                name="GigabitEthernet1/0/1",
                normalized_name=normalize("GigabitEthernet1/0/1"),
                mode="trunk",
                link_state=state,
            )
        )
    db.add(
        Link(
            a_device_id=a.id,
            a_interface="GigabitEthernet1/0/1",
            b_device_id=b.id,
            b_interface="GigabitEthernet1/0/1",
        )
    )
    db.commit()


def test_a_drawn_line_that_is_down_on_the_device_is_the_heaviest(db_session):
    _pair(db_session, "up", "notconnect")
    titles = [item.finding.title for item in linkcheck.review(db_session).findings]
    assert "図には線があるのに、実機では上がっていません" in titles


def test_no_show_output_means_no_claim_about_the_line(db_session):
    _pair(db_session, None, None)
    titles = [item.finding.title for item in linkcheck.review(db_session).findings]
    assert "図には線があるのに、実機では上がっていません" not in titles


ARUBA_BRIEF = """
show interface brief
------------------------------------------------------------
Port       Native  Mode    Type   Enabled Status  Reason   Speed  Description
           VLAN                                        (Mb/s)
------------------------------------------------------------
1/1/1      1       trunk   1GbT        yes     up               1000     to-core-01-a
1/1/10     20      access  1GbT        yes     down    Waiting for link  --   server-01
1/1/11     20      access  1GbT        no      down    Administratively down  --   yobi-port
"""

COMWARE_BRIEF = """
display interface brief
Brief information on interfaces in bridge mode:
Link: ADM - administratively down; Stby - standby
Interface            Link Speed   Duplex Type PVID Description
GE1/0/1              UP   1G(a)   F(a)   T    1    to-core
GE1/0/8              ADM  auto    A      A    10
"""

ARISTA_STATUS = """
show interfaces status
Port       Name          Status       Vlan     Duplex Speed  Type        Flags Encapsulation
Et1        to-core-01         connected    trunk    full   10G    10GBASE-SR
Et2        to-server-01       notconnect   20       full   10G    10GBASE-SR
"""


def test_aruba_cx_brief_is_read_even_though_the_header_spans_two_lines():
    text = (FIXTURES / "aruba-cx-01.txt").read_text() + ARUBA_BRIEF
    states = _states(configparse.parse(text))
    assert states["1/1/1"] == "up"
    assert states["1/1/10"] == "down"
    assert states["1/1/11"] == "disabled"


def test_comware_brief_is_read_with_the_abbreviated_names():
    text = (FIXTURES / "qx-sw-01.txt").read_text() + COMWARE_BRIEF
    states = _states(configparse.parse(text))
    assert states["GigabitEthernet1/0/1"] == "up"
    assert states["GigabitEthernet1/0/8"] == "disabled"


def test_arista_status_with_the_extra_flags_column_is_read():
    text = (FIXTURES / "arista-dc-01.txt").read_text() + ARISTA_STATUS
    states = _states(configparse.parse(text))
    assert states["Ethernet1"] == "up"
    assert states["Ethernet2"] == "notconnect"


def test_a_show_header_is_not_mistaken_for_a_port():
    """`Interface  IP-Address ...` の見出しを、interface の行と取り違えない。"""
    for name, extra in (("core-sw-01.txt", BRIEF), ("qx-sw-01.txt", COMWARE_BRIEF)):
        parsed = configparse.parse((FIXTURES / name).read_text() + extra)
        names = {i.name for i in parsed.interfaces}
        assert not names & {"IP-Address", "Link", "Interface"}, name
