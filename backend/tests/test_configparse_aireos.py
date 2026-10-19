"""Cisco AireOS の無線コントローラを、show 出力から読めること。

AireOS は設定ファイルではなく画面を貼ったものが来る。IOS-XE の C9800 とは
別物なので、IOS の読み手に渡すと何も入らない。型番が読めないと保守期限も
脆弱性も引けないので、そこがいちばん効く。
"""

from __future__ import annotations

from app.services import configparse
from app.services.configparse.base import Vendor
from app.services.configparse.ifname import normalize

_SHOW = "\n".join(
    [
        "(Cisco Controller) >show sysinfo",
        "",
        "Manufacturer's Name.............................. Cisco Systems Inc.",
        "Product Version.................................. 8.10.185.0",
        "System Name...................................... wlc-01",
        "IP Address....................................... 10.0.0.31",
        "Configured Country............................... JP  - Japan",
        "",
        "(Cisco Controller) >show inventory",
        "",
        'NAME: "Chassis", DESCR: "Cisco 5520 Series Wireless Controller"',
        "PID: AIR-CT5520-K9,  VID: V01,  SN: FCH2020V0AB",
        "",
        "(Cisco Controller) >show interface summary",
        "",
        "Interface Name       Port Vlan Id  IP Address      Type    Ap Mgr Guest",
        "-------------------- ---- -------- --------------- ------- ------ -----",
        "management           1    30       10.0.0.31       Static  Yes    No",
        "corp                 1    40       10.0.40.5       Dynamic No     No",
        "",
        "(Cisco Controller) >show wlan summary",
        "",
        "WLAN ID  WLAN Profile Name / SSID               Status    Interface Name",
        "1        Corp / Corp-WiFi                       Enabled   corp",
        "2        Guest / Guest-WiFi                     Disabled  management",
        "",
        "(Cisco Controller) >show ap summary",
        "",
        "AP Name    Slots  AP Model          Ethernet MAC       Location  Country IP Address",
        "AP-01      2      AIR-AP2802I-Q-K9  00:11:22:33:44:55  Floor1    JP      10.0.40.11",
        "AP-02      2      AIR-AP2802I-Q-K9  00:11:22:33:44:66  Floor2    JP      10.0.40.12",
    ]
)


def test_the_controller_itself_is_read() -> None:
    parsed = configparse.parse(_SHOW)
    assert parsed.vendor is Vendor.CISCO_WLC
    assert parsed.hostname == "wlc-01"
    assert parsed.model == "AIR-CT5520-K9"
    assert parsed.serial_number == "FCH2020V0AB"
    assert parsed.os_version == "8.10.185.0"


def test_the_interfaces_and_the_radios_are_read() -> None:
    parsed = configparse.parse(_SHOW)
    ports = {item.name.lower(): item for item in parsed.interfaces}
    assert ports["management"].vlan == 30
    assert ports["management"].ip_address == "10.0.0.31"
    assert ports["corp"].vlan == 40

    wlans = {item.ssid: item for item in parsed.wireless.wlans}
    assert wlans["Corp-WiFi"].enabled is True
    assert wlans["Guest-WiFi"].enabled is False
    assert parsed.wireless.country == "JP"

    aps = {item.name: item for item in parsed.wireless.aps}
    assert aps["AP-01"].model == "AIR-AP2802I-Q-K9"
    assert aps["AP-01"].ip_address == "10.0.40.11"
    assert len(aps) == 2


def test_only_sysinfo_still_reads_the_name_and_version() -> None:
    # 表を貼らずに sysinfo だけ来ることがある。無いものは作らない
    text = "\n".join(
        [
            "Product Version.................................. 8.5.182.0",
            "System Name...................................... wlc-02",
            "IP Address....................................... 10.0.0.32",
        ]
    )
    parsed = configparse.parse(text)
    assert parsed.vendor is Vendor.CISCO_WLC
    assert parsed.hostname == "wlc-02"
    assert parsed.model is None
    assert [item.ip_address for item in parsed.interfaces] == ["10.0.0.31".replace("31", "32")]


def test_an_ios_xe_controller_is_still_read_as_ios() -> None:
    text = "\n".join(
        [
            "version 8.10",
            "hostname wlc-01",
            "wlan Corp 1 Corp-WiFi",
            " client vlan 40",
        ]
    )
    assert configparse.detect_vendor(text) is Vendor.CISCO_IOS


def test_the_summary_table_does_not_claim_the_radio_is_open() -> None:
    """一覧の表には守り方が出てこない。読めていないのと、無いのは別。

    掛けてあるのに「誰でも入れます」と言うと、直しようのない指摘になる。
    """
    from app.services import wirelessreview

    parsed = configparse.parse(_SHOW)
    titles = [item.title for item in wirelessreview.review(parsed)]
    assert not [title for title in titles if "誰でも入れます" in title]
    assert [title for title in titles if "読めません" in title]


#: 公式の作例の形。WLC Port and Interface Command Reference 7.4、
#: Mobility Express Command Reference 8.10、「Configure VLANs on Wireless LAN Controllers」
OFFICIAL_INTERFACES = """(Cisco Controller) >show interface summary
Number of Interfaces.......................... 7

Interface Name                   Port Vlan Id  IP Address      Type    Ap Mgr Guest
-------------------------------- ---- -------- --------------- ------- ------ -----
management                       1    untagged 192.168.1.11    Static  Yes    No
dyn59                            LAG  59       9.10.59.66      Dynamic No     No
redundancy-port                  -    untagged 0.0.0.0         Static  No     No
service-port                     N/A  N/A      2.2.2.2         Static  No     No
virtual                          N/A  N/A      1.2.3.4         Static  No     No
vlan 81                          1    81       192.168.81.46   Dynamic No     No
171                              1    171      192.168.171.30  Dynamic No     No
"""


def _official_ports():
    # 名前は別の処理で揃えられる（management → Management、vlan 81 → Vlan81）。
    # ここでは揃え方を見ないので、揃えた後の鍵で引く
    return {i.key: i for i in configparse.parse(OFFICIAL_INTERFACES).interfaces}


def _port(name: str):
    return _official_ports()[normalize(name)]


def test_every_row_of_the_official_table_is_a_port():
    """`untagged`・`N/A` の行を読まないと、管理の口そのものが台帳から消える。"""
    names = ["management", "dyn59", "redundancy-port", "service-port", "virtual", "vlan 81", "171"]
    assert set(_official_ports()) == {normalize(name) for name in names}


def test_an_untagged_port_has_its_address_but_no_vlan_number():
    port = _port("management")
    assert port.ip_address == "192.168.1.11"
    assert port.vlan is None


def test_a_name_with_a_space_keeps_its_vlan():
    assert _port("vlan 81").vlan == 81
    assert _port("171").vlan == 171
    assert _port("dyn59").vlan == 59


#: 公式の作例。「Configure VLANs on Wireless LAN Controllers」の `show wlan summary`
#: （Interface Name に `vlan 81` と空白が入り、末尾に PMIPv6 Mobility の列が付く）
OFFICIAL_WLANS = """(Cisco Controller) >show wlan summary
Number of WLANs.................................. 2

WLAN ID  WLAN Profile Name / SSID               Status    Interface Name        PMIPv6 Mobility
-------  -------------------------------------  --------  --------------------  ---------------
1        self-anchor / self-anchor              Disabled  management            none
2        Students / Students                    Enabled   vlan 81               none
"""


def test_a_wlan_takes_the_vlan_of_its_interface():
    """`show wlan summary` の Interface Name を `show interface summary` の口に引き、VLAN を持つ。

    出典: Cisco「Configure VLANs on Wireless LAN Controllers」
    （`config wlan interface 2 "vlan 81"` とその `show wlan summary`）、
    「Cisco Wireless Controller Command Reference, Release 8.5」と Mobility Express Command
    Reference 8.9 の show wlan summary の作例（WLAN ID / WLAN Profile Name / SSID / Status /
    Interface Name）。management は untagged なので VLAN を持たない。
    """
    wlans = {
        w.name: w for w in configparse.parse(OFFICIAL_INTERFACES + OFFICIAL_WLANS).wireless.wlans
    }
    assert {name: (w.ssid, w.enabled, w.vlan) for name, w in wlans.items()} == {
        "self-anchor": ("self-anchor", False, None),
        "Students": ("Students", True, 81),
    }


def test_the_official_ap_summary_row_is_read():
    """見張り。`show ap summary` の行を Cisco の作例の形で読む。アドレスの列が無い版でも落とさない。

    出典: Cisco「Configure AireOS Wireless LAN Controller and Lightweight Access Point」の Verify
    （`AP Name Slots AP Model Ethernet MAC Location Port Country Priority` の見出しと、
    `AP001b.d4e3.a81b 2 AIR-LAP1232AG-A-K9 00:1b:d4:e3:a8:1b default location 2 IN 1` の行）。
    """
    text = (
        "(Cisco Controller) >show ap summary\n\n"
        "Number of APs.................................... 1\n\n"
        "Global AP User Name.............................. Not Configured\n"
        "Global AP Dot1x User Name........................ Not Configured\n\n"
        "AP Name             Slots  AP Model             Ethernet MAC       Location          "
        "Port  Country  Priority\n"
        "------------------  -----  -------------------  -----------------  ----------------  "
        "----  -------  ------\n"
        "AP001b.d4e3.a81b    2      AIR-LAP1232AG-A-K9   00:1b:d4:e3:a8:1b  default location  "
        "2     IN       1\n"
    )
    aps = configparse.parse(text).wireless.aps
    assert [(ap.name, ap.model, ap.mac, ap.ip_address) for ap in aps] == [
        ("AP001b.d4e3.a81b", "AIR-LAP1232AG-A-K9", "00:1b:d4:e3:a8:1b", None)
    ]
