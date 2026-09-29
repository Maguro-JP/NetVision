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

NECQX_BRIEF = """
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


def test_necqx_brief_is_read_with_the_abbreviated_names():
    text = (FIXTURES / "qx-sw-01.txt").read_text() + NECQX_BRIEF
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
    for name, extra in (("core-sw-01.txt", BRIEF), ("qx-sw-01.txt", NECQX_BRIEF)):
        parsed = configparse.parse((FIXTURES / name).read_text() + extra)
        names = {i.name for i in parsed.interfaces}
        assert not names & {"IP-Address", "Link", "Interface"}, name


PANOS_ALL = """
show interface all
total configured hardware interfaces: 3
name                    id    speed/duplex/state    mac address
--------------------------------------------------------------------------------
ethernet1/1             16    1000/full/up          00:1b:17:00:01:10
ethernet1/2             17    1000/full/up          00:1b:17:00:01:11
ethernet1/3             18    ukn/ukn/down          00:1b:17:00:01:12
"""

FORTI_PHYSICAL = """
get system interface physical
==[port1]
        mode: static
        ip: 203.0.113.2 255.255.255.252
        ipv6: ::/0
        status: up
        speed: 1000Mbps (Duplex: full)
==[port2]
        mode: static
        ip: 10.1.0.1 255.255.255.0
        status: down
        speed: n/a
"""


def test_panos_show_interface_all_is_read():
    text = (FIXTURES / "panos-hq-01.txt").read_text() + PANOS_ALL
    states = _states(configparse.parse(text))
    assert states["Ethernet1/1"] == "up"
    assert states["Ethernet1/3"] == "down"


def test_fortigate_physical_status_is_read_per_paragraph():
    text = (FIXTURES / "fw-01.txt").read_text() + FORTI_PHYSICAL
    states = _states(configparse.parse(text))
    assert states["port1"] == "up"
    assert states["port2"] == "down"


SHOW_INTERFACES = """
show interfaces
GigabitEthernet1/0/1 is up, line protocol is up (connected)
  Hardware is Gigabit Ethernet, address is 0011.2233.4455 (bia 0011.2233.4455)
  Description: to-acc-sw
GigabitEthernet1/0/2 is down, line protocol is down (notconnect)
  Hardware is Gigabit Ethernet, address is 0011.2233.4456 (bia 0011.2233.4456)
"""


def test_show_interfaces_paragraphs_are_read_from_their_first_line():
    text = (FIXTURES / "core-sw-01.txt").read_text() + SHOW_INTERFACES
    states = _states(configparse.parse(text))
    assert states["GigabitEthernet1/0/1"] == "up"
    assert states["GigabitEthernet1/0/2"] == "down"


NXOS_BRIEF = """
show interface brief
--------------------------------------------------------------------------------
Ethernet      VLAN    Type Mode   Status  Reason                 Speed     Port
Interface                                                                  Ch #
--------------------------------------------------------------------------------
Eth1/1        1       eth  trunk  up      none                    10G(D) --
Eth1/2        1       eth  access down    Link not connected      auto(D) --
Eth1/3        1       eth  access down    Administratively down   auto(D) --
"""


def test_nxos_brief_is_read_with_the_reason_column():
    text = (
        "hostname nx-01\nfeature lldp\n"
        "interface Ethernet1/1\ninterface Ethernet1/2\ninterface Ethernet1/3\n"
    )
    states = _states(configparse.parse(text + NXOS_BRIEF))
    assert states["Ethernet1/1"] == "up"
    assert states["Ethernet1/2"] == "down"
    assert states["Ethernet1/3"] == "disabled"


def test_the_name_column_fills_a_missing_description() -> None:
    """show interfaces status の Name 列は、そのポートの説明そのもの。

    config に description が無いポートでも、ここに書いてあれば分かる。
    config 側に書いてあるほうが新しいとは限らないが、書いてあれば動かさない。
    """
    text = "\n".join(
        [
            "hostname sw1",
            "interface GigabitEthernet1/0/1",
            " description UPLINK-CORE-A",
            "interface GigabitEthernet1/0/3",
            " switchport access vlan 20",
            "interface GigabitEthernet1/0/4",
            " switchport access vlan 20",
            "!",
            "show interfaces status",
            "Port      Name               Status       Vlan       Duplex  Speed Type",
            "Gi1/0/1   UPLINK-CORE        connected    trunk        full    1000 10/100/1000BaseTX",
            "Gi1/0/3   PRINTER 1F         err-disabled 20           full     100 10/100/1000BaseTX",
            "Gi1/0/4                      notconnect   20           auto    auto 10/100/1000BaseTX",
        ]
    )
    ports = {i.name: i for i in configparse.parse(text).interfaces}
    # config に書いてあるほうが勝つ
    assert ports["GigabitEthernet1/0/1"].description == "UPLINK-CORE-A"
    # 空白を含む説明も、Status 列の手前までを1つとして読む
    assert ports["GigabitEthernet1/0/3"].description == "PRINTER 1F"
    # Name 列が空なら、無いままにする
    assert ports["GigabitEthernet1/0/4"].description is None


def test_the_added_vendors_say_whether_the_link_is_up() -> None:
    """図に線があるのに実機で上がっていない、を出せること。

    3ベンダーとも表の形が違う。読めないと、そのベンダーの機器だけ黙る。
    """
    huawei = "\n".join(
        [
            "VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)",
            "sysname hw1",
            "#",
            "interface 10GE1/0/1",
            " port link-type trunk",
            "#",
            "interface 10GE1/0/2",
            " port link-type access",
            "#",
            "Interface                   PHY   Protocol InUti OutUti",
            "10GE1/0/1                   up    up          1%     1%",
            "10GE1/0/2                   down  down        0%     0%",
        ]
    )
    fastiron = "\n".join(
        [
            "  HW: Stackable ICX7150-48P",
            "hostname icx1",
            "interface ethernet 1/1/1",
            " port-name to-core",
            "interface ethernet 1/1/2",
            " port-name spare",
            "Port    Link    State   Dupl Speed Trunk Tag Pvid Pri MAC             Name",
            "1/1/1   Up      Forward Full 1G    None  Yes N/A  0   0000.1111.2222  to-core",
            "1/1/2   Down    None    None None  None  No  10   0   0000.1111.2223",
        ]
    )
    alaxala = "\n".join(
        [
            "Model AX3650S-24T4XW",
            'hostname "ax01"',
            "interface gigabitethernet 0/1",
            "  switchport mode trunk",
            "interface gigabitethernet 0/2",
            "  switchport mode access",
            "  Port 0/1  Link: Up    Speed: 1000BASE-T",
            "  Port 0/2  Link: Down  Speed: -",
        ]
    )
    for text in (huawei, fastiron, alaxala):
        states = [item.link_state for item in configparse.parse(text).interfaces]
        assert states == ["up", "down"], text.splitlines()[0]


def test_broken_frames_are_counted_on_necqx_and_vrp() -> None:
    """壊れて届いた数（CRC）を、NEC QX / VRP の書き方でも読むこと。

    線は上がっているのに通信が遅い、の手掛かりになる。Cisco の並びだけを
    見ていると、その機器だけ「壊れていない」ことになる。
    """
    necqx = "\n".join(
        [
            "sysname qx1",
            "#",
            "interface GigabitEthernet1/0/1",
            " port link-type access",
            "GigabitEthernet1/0/1 current state: UP",
            "  Input: 0 input errors, 0 runts, 0 giants",
            "         55 CRC, 0 frame, 0 overruns",
        ]
    )
    vrp = "\n".join(
        [
            "VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)",
            "sysname hw1",
            "#",
            "interface 10GE1/0/1",
            " port link-type trunk",
            "10GE1/0/1 current state : UP",
            "    CRC: 42,  Overrun: 0",
        ]
    )
    for text, expected in ((necqx, 55), (vrp, 42)):
        port = configparse.parse(text).interfaces[0]
        assert port.link_state == "up"
        assert port.input_crc_errors == expected


def test_a_port_shut_by_a_person_is_not_called_a_broken_link() -> None:
    text = "\n".join(
        [
            "VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)",
            "sysname hw1",
            "#",
            "interface 10GE1/0/2",
            " port link-type access",
            "10GE1/0/2 current state : Administratively DOWN",
        ]
    )
    assert configparse.parse(text).interfaces[0].link_state == "disabled"


def test_vrp_phy_marks_do_not_stop_the_table() -> None:
    """VRP の PHY 列の印（^down、#down、up(s) など）で、表の読み取りを止めない。

    出典: Huawei「Troubleshooting S Series Switch Port and Interface Problems」の
    display interface brief の凡例（*down: administratively down、^down: standby、
    ~down: LDT down、#down: LBDT down、(s): spoofing、(e): ETHOAM down）。
    """
    names = [f"GigabitEthernet0/0/{n}" for n in range(1, 7)]
    config = ["sysname hw1", "#"]
    for name in names:
        config += [f"interface {name}", " port link-type access", "#"]
    table = [
        "PHY: Physical",
        "*down: administratively down",
        "^down: standby",
        "Interface                   PHY   Protocol InUti OutUti   inErrors  outErrors",
        "GigabitEthernet0/0/1        ^down down        0%     0%          0          0",
        "GigabitEthernet0/0/2        up(s) up          0%     0%          0          0",
        "GigabitEthernet0/0/3        #down down        0%     0%          0          0",
        "GigabitEthernet0/0/4        *down down        0%     0%          0          0",
        "GigabitEthernet0/0/5        down(e) down      0%     0%          0          0",
        "GigabitEthernet0/0/6        up    up          0%     0%          0          0",
    ]
    text = "\n".join(["VRP (R) software, Version 5.170 (S5720 V200R011C10SPC500)"] + config + table)
    ports = {normalize(item.name): item.link_state for item in configparse.parse(text).interfaces}
    assert [ports[normalize(name)] for name in names] == [
        "down",
        "up",
        "err-disabled",
        "disabled",
        "down",
        "up",
    ]


def test_an_unknown_status_word_does_not_stop_the_status_table() -> None:
    """知らない言葉の行があっても、その後ろの口の上下は読む。

    出典: Cisco IOS XE Interface and Hardware Components Configuration Guide
    （Catalyst 9400、16.11.x）「Checking Port Status and Connectivity」の show interface status。
    知らない言葉（ここでは sfpAbsent）の意味は決めず、空のままにする。
    """
    config = "\n".join(
        [
            "hostname sw1",
            "interface GigabitEthernet1/0/1",
            "interface GigabitEthernet1/0/2",
            "interface GigabitEthernet1/0/3",
            "Switch# show interface status",
            "Port      Name               Status       Vlan       Duplex  Speed Type",
            "Gi1/0/1                      connected    1          a-full a-1000 10/100/1000BaseTX",
            "Gi1/0/2                      sfpAbsent    1            auto   auto unknown",
            "Gi1/0/3                      notconnect   1            auto   auto 10/100/1000BaseTX",
            "Switch#",
        ]
    )
    ports = {item.name: item.link_state for item in configparse.parse(config).interfaces}
    assert list(ports.values()) == ["up", None, "notconnect"]


def test_junos_terse_keeps_reading_past_ready_and_address_only_rows() -> None:
    """Link が `ready` の行と、アドレスだけが続く行で、表の読み取りを止めない。

    出典: Junos Software CLI Reference「show interfaces」の show interfaces terse の
    サンプル出力（`st0.1  up  ready inet`、字下げした `10.0.0.16  --> 0/0` の行）と、
    Junos OS CLI Reference「show interfaces terse」の出力欄の説明。
    表そのものが字下げされていても読むこと。
    """
    config = [
        "set system host-name srx1",
        "set interfaces ge-0/0/0 unit 0 family inet address 10.209.4.61/18",
        "set interfaces ae0 unit 0 family inet address 10.2.2.1/30",
        "set interfaces ge-0/0/1 unit 0 family inet address 10.1.1.1/24",
    ]
    table = [
        "user@host> show interfaces terse",
        "    Interface               Admin Link Proto    Local                 Remote",
        "    ge-0/0/0                up    up",
        "    ge-0/0/0.0              up    up   inet     10.209.4.61/18",
        "    st0.1                   up    ready inet",
        "    lo0.16385               up    up   inet     10.0.0.1            --> 0/0",
        "                                                10.0.0.16           --> 0/0",
        "    ae0                     up    up",
        "    ae0.0                   up    up   inet     10.2.2.1/30",
        "    ge-0/0/1                up    down",
        "    ge-0/0/1.0              up    down inet     10.1.1.1/24",
    ]
    parsed = configparse.parse("\n".join(config + table))
    states = {normalize(item.name): item.link_state for item in parsed.interfaces}
    assert states[normalize("ae0")] == "up"
    assert states[normalize("ge-0/0/1")] == "down"


def test_nxos_brief_follows_the_official_sample() -> None:
    """NX-OS の show interface brief を、公式の出力例の形で読む。

    出典: Cisco Nexus 6000 Series Layer 2 Command Reference の show interface brief
    （見出し2行と罫線、`Eth1/5.2` の枝の口、Reason の SFP not inserted など）。
    出力例に無い Status の言葉（ここでは sfpAbsent）の行でも、表は読み続ける。
    """
    config = [
        "hostname nx1",
        "feature interface-vlan",
        "interface Ethernet1/1",
        "interface Ethernet1/2",
        "interface Ethernet1/3",
        "interface Ethernet1/5.2",
        "interface Ethernet1/6",
    ]
    table = [
        "switch# show interface brief",
        "-" * 80,
        "Ethernet      VLAN   Type Mode   Status  Reason                   Speed     Port",
        "Interface                                                                   Ch #",
        "-" * 80,
        "Eth1/1        1      eth  access down    Link not connected          10G(D) --",
        "Eth1/2        1      eth  trunk  up      none                        10G(D) --",
        "Eth1/3        1      eth  access sfpAbsent SFP not inserted          10G(D) --",
        "Eth1/5.2      --     eth  routed down    Configuration Incomplete    10G(D) --",
        "Eth1/6        1      eth  access up      none                        10G(D) --",
    ]
    parsed = configparse.parse("\n".join(config + table))
    states = {normalize(item.name): item.link_state for item in parsed.interfaces}
    assert states[normalize("Ethernet1/1")] == "down"
    assert states[normalize("Ethernet1/2")] == "up"
    assert states[normalize("Ethernet1/3")] is None
    assert states[normalize("Ethernet1/5.2")] == "down"
    assert states[normalize("Ethernet1/6")] == "up"


def test_ip_interface_brief_keeps_reading_past_an_unknown_protocol_value() -> None:
    """Protocol 列に一覧に無い値が出ても、その下の口の上下は読む。

    出典: Cisco IOS Interface and Hardware Component Command Reference（IOS XE 16）の
    show ip interface brief の出力例と Field Descriptions（Status は up / down /
    administratively down）。Protocol の値は一覧が無いので、知らない値の口は空のままにする。
    """
    config = [
        "hostname r1",
        "interface GigabitEthernet0/0/0",
        "interface GigabitEthernet0/0/1",
        "interface Serial1/0/0",
        "interface Loopback0",
    ]
    table = [
        "Router#show ip interface brief",
        "Interface              IP-Address      OK? Method Status                Protocol",
        "GigabitEthernet0/0/0   192.0.2.1       YES NVRAM  up                    up",
        "GigabitEthernet0/0/1   unassigned      YES NVRAM  down                  "
        "down (err-disabled)",
        "Serial1/0/0            unassigned      YES unset  administratively down down",
        "Loopback0              10.108.200.5    YES NVRAM  up                    up",
    ]
    parsed = configparse.parse("\n".join(config + table))
    states = {item.name: item.link_state for item in parsed.interfaces}
    assert list(states.values()) == ["up", None, "disabled", "up"]


def test_panos_state_with_autoneg_and_na_is_read() -> None:
    """PAN-OS の state 欄の `down(autoneg)`、`down((autoneg))`、`[n/a]/[n/a]/up` を読む。

    出典: Palo Alto Networks Knowledge Base（kA14u000000ToqhCAC、kA14u000000sapmCAA、
    kA1Ki000000XZB5KAO）の show interface all の出力。
    """
    config = [
        "set deviceconfig system hostname pa1",
        "set network interface ethernet ethernet1/1 layer3 ip 10.0.0.1/24",
        "set network interface ethernet ethernet1/2 layer3 ip 10.0.1.1/24",
        "set network interface ethernet ethernet1/3 layer3 ip 10.0.2.1/24",
        "set network interface ethernet ethernet1/4 layer3 ip 10.0.3.1/24",
    ]
    table = [
        "admin@PA-3410> show interface all",
        "",
        "total configured hardware interfaces: 5",
        "",
        "name                    id    speed/duplex/state            mac address",
        "-" * 80,
        "ethernet1/1             16    ukn/ukn/down(autoneg)         60:15:2b:8d:b1:10",
        "ethernet1/2             17    ukn/ukn/down((autoneg))       00:00:00:00:00:00",
        "tunnel                  4     [n/a]/[n/a]/up                60:15:2b:8d:b1:04",
        "ethernet1/3             18    1000/full/up                  60:15:2b:8d:b1:12",
        "ethernet1/4             19    auto/auto/up                  06:62:db:00:00:01",
    ]
    parsed = configparse.parse("\n".join(config + table))
    states = {normalize(item.name): item.link_state for item in parsed.interfaces}
    assert [states[normalize(f"ethernet1/{n}")] for n in range(1, 5)] == [
        "down",
        "down",
        "up",
        "up",
    ]


def test_fastiron_disable_does_not_stop_the_table() -> None:
    """FastIron の止めた口は Link 列に `Disable` と出る。その下の口も読む。

    出典: Brocade FastIron Ethernet Switch Platform and Layer 2 Switching Configuration Guide の
    show interface brief の出力例（`1/1/3  Disable  None ...`）。
    """
    config = [
        "  HW: Stackable ICX7150-48P",
        "hostname icx1",
        "interface ethernet 1/1/1",
        "interface ethernet 1/1/3",
        "interface ethernet 1/1/4",
        "interface ethernet 1/1/5",
    ]
    table = [
        "device# show interface brief",
        "Port    Link    State   Dupl Speed Trunk Tag Pvid Pri MAC             Name",
        "1/1/1   Down    None    None None  None  No  1    0   cc4e.2438.8080",
        "1/1/3   Disable None    None None  6     Yes N/A  0   cc4e.2438.8080",
        "1/1/4   Down    None    None None  None  No  1    0   cc4e.2438.8083",
        "1/1/5   Up      Forward Full 1G    10    Yes N/A  0   cc4e.2438.8080  VXL1",
        "mgmt1   Up      None    Full 1G    None  No  None 0   cc4e.2438.8080",
    ]
    parsed = configparse.parse("\n".join(config + table))
    states = [item.link_state for item in parsed.interfaces]
    assert states == ["down", "disabled", "down", "up"]
