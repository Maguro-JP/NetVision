"""無線（SSID と AP）の読み取りと、その中身への指摘。

有線と違い、電波は建物の外まで届く。SSID の守り方がそのまま入口の強さに
なるので、書き方がベンダーごとに違っても同じ器に入れて、同じ目で見る。
"""

from app.services import configparse, policyreview, wirelessreview

IOSXE = """
hostname wlc-01
ap country JP
wlan corp-wlan 1 CORP
 client vlan 10
 security wpa wpa2
 security wpa akm dot1x
 security dot1x authentication-list RADIUS-A
 no shutdown
wlan guest-wlan 2 GUEST-WIFI
 no security wpa
 no security wpa wpa2
 no shutdown
wlan shop-wlan 3 SHOP
 security wpa psk set-key ascii 0 password123
 security wpa akm psk
 no shutdown
wireless profile policy GUEST-POLICY
 vlan 200
wireless tag policy DEFAULT-TAG
 wlan guest-wlan policy GUEST-POLICY
"""

FORTIGATE = """
config wireless-controller vap
    edit "office"
        set ssid "OFFICE"
        set security wpa2-only-personal
        set passphrase ENC 1a2b3c
        set vlanid 30
        set status enable
    next
    edit "old"
        set ssid "OLD-AP"
        set security wpa-personal
        set passphrase ENC ffff
        set vlanid 31
        set status enable
    next
end
"""

ARUBA = """
wlan ssid-profile guest
 essid Guest-Free
 opmode opensystem
 vlan 200
 deny-inter-user-bridging
wlan ssid-profile staff
 essid STAFF
 opmode wpa2-psk-aes
 wpa-passphrase secret-pass
 vlan 20
"""


def wlans(text: str) -> dict:
    parsed = configparse.parse(text)
    assert parsed.wireless is not None
    return {wlan.ssid or wlan.name: wlan for wlan in parsed.wireless.wlans}


def titles(text: str) -> list[str]:
    return [f.title for f in wirelessreview.review(configparse.parse(text))]


def test_iosxe_reads_ssid_security_and_the_vlan_behind_the_tag():
    """IOS-XE は SSID と VLAN が別のところに書いてある。突き合わせて読む。"""
    found = wlans(IOSXE)
    corp = found["CORP"]
    assert corp.security == ["wpa2"]
    assert corp.akm == ["dot1x"]
    assert corp.auth_list == "RADIUS-A"
    assert corp.vlan == 10
    assert corp.enabled
    # SSID → policy tag → policy profile → vlan とたどる
    assert found["GUEST-WIFI"].vlan == 200


def test_a_wlan_without_no_shutdown_is_not_live():
    """作っただけの SSID は電波に出ていない。指摘の対象にもしない。"""
    text = IOSXE.replace(" no shutdown\nwlan guest-wlan", "\nwlan guest-wlan")
    found = wlans(text)
    assert found["CORP"].enabled is False
    assert "SSID CORP は誰でも入れます" not in titles(text)


def test_open_ssid_is_reported():
    assert "SSID GUEST-WIFI は誰でも入れます" in titles(IOSXE)


def test_psk_written_in_the_clear_is_reported():
    assert "SSID SHOP の合言葉が、そのまま読める形で書かれています" in titles(IOSXE)


def test_dot1x_without_a_radius_is_reported():
    text = IOSXE.replace(" security dot1x authentication-list RADIUS-A\n", "")
    assert "SSID CORP は 802.1X ですが、問い合わせ先がありません" in titles(text)


def test_guest_and_office_on_the_same_vlan_is_reported():
    """SSID を分けても、落とし先が同じなら分けたことにならない。"""
    text = IOSXE.replace(" vlan 200", " vlan 10")
    assert "来客向けの SSID GUEST-WIFI が、業務と同じ VLAN です" in titles(text)


def test_fortigate_vap_is_read_and_old_wpa_is_reported():
    found = wlans(FORTIGATE)
    assert found["OFFICE"].security == ["wpa2"]
    assert found["OFFICE"].vlan == 30
    # 伏せてある合言葉は指摘しない
    assert found["OFFICE"].psk_in_clear is False
    assert "SSID OLD-AP が古い守り方のままです（WPA）" in titles(FORTIGATE)
    assert "SSID OLD-AP が TKIP を使っています" in titles(FORTIGATE)


def test_aruba_instant_is_read():
    found = wlans(ARUBA)
    assert found["Guest-Free"].security == ["open"]
    assert found["Guest-Free"].client_isolation is True
    assert found["STAFF"].security == ["wpa2"]
    assert found["STAFF"].akm == ["psk"]
    assert found["STAFF"].vlan == 20
    # 端末どうしは通さないので、そこは言わない
    assert "来客向けの SSID Guest-Free で、端末どうしが通じます" not in titles(ARUBA)


def test_country_missing_is_reported():
    text = IOSXE.replace("ap country JP\n", "")
    assert "電波の国が指定されていません" in titles(text)
    assert "電波の国が指定されていません" not in titles(IOSXE)


def test_a_wired_config_says_nothing_about_radio():
    """無線の設定が無い機器では、何も言わない。"""
    wired = "hostname sw-01\ninterface GigabitEthernet1/0/1\n switchport access vlan 10\n"
    parsed = configparse.parse(wired)
    assert parsed.wireless is None
    assert wirelessreview.review(parsed) == []


def test_the_findings_ride_along_with_the_other_checks():
    """無線の指摘も、ほかの点検と同じ出口から出る。"""
    titles_all = [f.title for f in policyreview.review(configparse.parse(IOSXE))]
    assert "SSID GUEST-WIFI は誰でも入れます" in titles_all


def test_an_ap_profile_with_telnet_open_is_reported():
    """AP は天井にあって手が届かない。開けたままの入口は気づかれないまま残る。"""
    text = IOSXE + "ap profile default-ap-profile\n telnet\n ssh\n"
    parsed = configparse.parse(text)
    aps = {ap.name: ap for ap in parsed.wireless.aps}
    assert aps["profile default-ap-profile"].insecure_access == ["telnet"]
    assert [f.title for f in wirelessreview.review(parsed) if "telnet" in f.title]


def test_an_ap_profile_with_only_ssh_is_quiet():
    text = IOSXE + "ap profile default-ap-profile\n ssh\n"
    parsed = configparse.parse(text)
    assert not [f.title for f in wirelessreview.review(parsed) if "telnet" in f.title]


def test_aireos_telnet_on_an_ap_is_reported():
    text = (
        "config wlan create 1 OFFICE OFFICE\n"
        "config ap telnet enable AP-3F-01\n"
        "config ap ssh enable AP-3F-02\n"
    )
    parsed = configparse.parse(text)
    aps = {ap.name: ap for ap in parsed.wireless.aps}
    assert aps["AP-3F-01"].insecure_access == ["telnet"]
    assert "AP-3F-02" not in aps or aps["AP-3F-02"].insecure_access == []


def test_the_tags_of_an_ap_are_read():
    text = IOSXE + "ap name AP-3F-01 policy-tag OFFICE-TAG\n"
    parsed = configparse.parse(text)
    aps = {ap.name: ap for ap in parsed.wireless.aps}
    assert aps["AP-3F-01"].profile == "OFFICE-TAG"


AP_SUMMARY = """
show ap summary
Number of APs: 2

AP Name   Slots AP Model    Ethernet MAC    Radio MAC       Location Country IP Address  State
----------------------------------------------------------------------------------------------
AP-3F-01  2     C9120AXI-Q  0011.2233.4455  0011.2233.4460  3F east  JP      10.0.30.11  Registered
AP-3F-02  2     C9120AXI-Q  0011.2233.4466  0011.2233.4470  3F west  JP      10.0.30.12  Registered
"""

FORTI_WTP = """
config system global
    set hostname "fw-01"
end
config wireless-controller wtp
    edit "FP231FTF21000001"
        set name "AP-1F-01"
        set location "1F entrance"
        set admin enable
        set wtp-profile "FAP231F-default"
    next
    edit "FP231FTF21000002"
        set admin enable
        set wtp-profile "FAP231F-default"
    next
end
"""


def test_the_ap_list_of_a_cisco_controller_is_read():
    """AP は台数が多い。コントローラの一覧をそのまま台帳の下敷きにする。"""
    parsed = configparse.parse(IOSXE + AP_SUMMARY)
    aps = {ap.name: ap for ap in parsed.wireless.aps}
    assert aps["AP-3F-01"].model == "C9120AXI-Q"
    assert aps["AP-3F-01"].mac == "0011.2233.4455"
    assert aps["AP-3F-01"].ip_address == "10.0.30.11"
    assert aps["AP-3F-01"].location == "3F east"
    assert aps["AP-3F-02"].ip_address == "10.0.30.12"


def test_the_ap_list_of_a_fortigate_is_read_by_serial():
    parsed = configparse.parse(FORTI_WTP)
    aps = {ap.name: ap for ap in parsed.wireless.aps}
    assert aps["AP-1F-01"].serial == "FP231FTF21000001"
    assert aps["AP-1F-01"].location == "1F entrance"
    assert aps["AP-1F-01"].profile == "FAP231F-default"
    # 名前を付けていない AP はシリアルで呼ぶ
    assert aps["FP231FTF21000002"].serial == "FP231FTF21000002"


def test_aps_from_the_controller_land_in_the_ledger(client):
    """コントローラの Config を取り込むと、一覧の AP が台帳に載る。"""
    from tests.conftest import API

    client.post(
        f"{API}/devices",
        json={
            "hostname": "AP-3F-02",
            "vendor": "Cisco",
            "model": "手で入れた",
            "device_type": "wireless_ap",
        },
    )
    res = client.post(
        f"{API}/configs/upload",
        files=[("files", ("wlc.txt", (IOSXE + AP_SUMMARY).encode(), "text/plain"))],
    )
    assert res.status_code == 200, res.text
    result = res.json()[0]
    assert result["aps_registered"] == 1  # 既に載っている AP-3F-02 は触らない

    devices = {d["hostname"]: d for d in client.get(f"{API}/devices").json()}
    assert devices["AP-3F-01"]["device_type"] == "wireless_ap"
    assert devices["AP-3F-01"]["model"] == "C9120AXI-Q"
    assert devices["AP-3F-01"]["mgmt_ip"] == "10.0.30.11"
    assert devices["AP-3F-02"]["model"] == "手で入れた"
