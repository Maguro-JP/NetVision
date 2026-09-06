"""Cisco AireOS の無線コントローラを、show 出力から読めること。

AireOS は設定ファイルではなく画面を貼ったものが来る。IOS-XE の C9800 とは
別物なので、IOS の読み手に渡すと何も入らない。型番が読めないと保守期限も
脆弱性も引けないので、そこがいちばん効く。
"""

from __future__ import annotations

from app.services import configparse
from app.services.configparse.base import Vendor

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
