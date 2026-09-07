"""ログから、何が起きているのかを言えること。

数えて並べるだけでは現場は動けない。ここで見るのは、障害・設定の食い違い・
攻撃の疑いを、根拠と次の一手つきで出せているか。断定していないかも見る。
"""

from app.services import logreview, syslog
from tests.conftest import API


def _findings(text: str):
    events = [event for line in text.splitlines() if (event := syslog.parse_line(line))]
    return logreview.review(events)


def _titles(findings) -> str:
    return " / ".join(f.title for f in findings)


def _one(findings, word: str):
    return next(f for f in findings if word in f.title)


FLAPPING = "\n".join(
    f"Aug 22 03:16:{second:02d} core-sw-01 %LINK-3-UPDOWN: "
    f"Interface GigabitEthernet1/0/13, changed state to {'down' if second % 2 else 'up'}"
    for second in range(10, 20)
)


def test_a_port_going_up_and_down_is_called_out(db_session):
    """1度の抜き差しと、線が悪いのを分ける。回数でしか分けられない。"""
    findings = _findings(FLAPPING)
    item = _one(findings, "上下")
    assert item.level == logreview.HIGH
    assert item.kind == logreview.TROUBLE
    assert "GigabitEthernet1/0/13" in item.why
    assert item.what_now
    assert item.count >= 6


def test_one_or_two_ups_and_downs_are_not_called_out():
    """人が抜き差ししただけで指摘が出ると、次から読まれなくなる。"""
    quiet = "\n".join(FLAPPING.splitlines()[:3])
    assert "上下" not in _titles(_findings(quiet))


def test_a_duplex_mismatch_is_reported_as_a_setting_problem():
    text = (
        "Aug 22 03:17:02 acc-sw-04 %CDP-4-DUPLEX_MISMATCH: duplex mismatch discovered "
        "on GigabitEthernet0/5 (not half duplex), with acc-sw-05 GigabitEthernet0/1 (half duplex)."
    )
    item = _one(_findings(text), "二重")
    assert item.kind == logreview.CONFIG
    assert item.level == logreview.HIGH


def test_a_native_vlan_mismatch_is_reported_as_a_setting_problem():
    text = (
        "Aug 22 03:17:30 acc-sw-04 %CDP-4-NATIVE_VLAN_MISMATCH: Native VLAN mismatch "
        "discovered on GigabitEthernet0/6 (10), with core-sw-01 GigabitEthernet1/0/6 (1)."
    )
    item = _one(_findings(text), "タグ無し VLAN")
    assert item.kind == logreview.CONFIG


def test_a_setting_problem_is_not_filed_under_routing():
    """食い違いという言葉だけで拾うと、二重の話が経路の指摘として出る。"""
    text = (
        "Aug 22 03:17:02 acc-sw-04 %CDP-4-DUPLEX_MISMATCH: duplex mismatch discovered "
        "on GigabitEthernet0/5 (not half duplex), with acc-sw-05 Gi0/1 (half duplex)."
    )
    assert "経路の相手と、決めごと" not in _titles(_findings(text))


def test_repeated_login_failures_look_like_someone_trying():
    text = "\n".join(
        f"Aug 22 03:19:{second:02d} rt-01 %SEC_LOGIN-4-LOGIN_FAILED: Login failed "
        f"[user: admin] [Source: 198.51.100.7] [localport: 22] "
        f"[Reason: Login Authentication Failed]"
        for second in range(10, 30)
    )
    item = _one(_findings(text), "ログインの失敗")
    assert item.kind == logreview.ATTACK
    assert "198.51.100.7" in item.why
    assert "可能性" in item.why  # 断定しない


def test_a_success_right_after_the_failures_is_heavier():
    text = "\n".join(
        f"Aug 22 03:19:{second:02d} rt-01 %SEC_LOGIN-4-LOGIN_FAILED: Login failed "
        f"[user: admin] [Source: 198.51.100.7]"
        for second in range(10, 30)
    ) + (
        "\nAug 22 03:19:35 rt-01 %SEC_LOGIN-5-LOGIN_SUCCESS: Login Success "
        "[user: admin] [Source: 198.51.100.7]"
    )
    item = _one(_findings(text), "総当たり")
    assert item.level == logreview.HIGH
    assert "破られた" in item.why


def test_a_few_failed_logins_are_left_alone():
    """打ち間違いを毎回攻撃と言うと、本物のときに読まれない。"""
    text = "\n".join(
        f"Aug 22 03:19:{second:02d} rt-01 %SEC_LOGIN-4-LOGIN_FAILED: Login failed [user: ops]"
        for second in range(10, 13)
    )
    assert "ログイン" not in _titles(_findings(text))


def test_a_lot_of_denies_from_one_source_look_like_scanning():
    text = "\n".join(
        f"Aug 22 03:18:{second:02d} fw-01 %ASA-4-106023: Deny tcp "
        f"src outside:203.0.113.9/5{second} "
        f'dst inside:10.10.10.5({1000 + second}) by access-group "outside_in"'
        for second in range(10, 40)
    )
    item = _one(_findings(text), "止めた通信")
    assert item.kind == logreview.ATTACK
    assert "203.0.113.9" in item.why
    assert item.count >= 20
    assert item.first_at is not None  # 根拠の行が、数えた行と同じであること


def test_a_change_followed_by_trouble_is_linked():
    """原因の当たりとして最初に見るところ。"""
    text = (
        "Aug 22 03:10:01 core-sw-01 %SYS-5-CONFIG_I: Configured from console by admin\n" + FLAPPING
    )
    item = _one(_findings(text), "設定を変えた直後")
    assert item.level == logreview.HIGH
    assert item.kind == logreview.CONFIG


def test_a_change_long_before_the_trouble_is_not_linked():
    text = (
        "Aug 22 01:00:00 core-sw-01 %SYS-5-CONFIG_I: Configured from console by admin\n" + FLAPPING
    )
    assert "設定を変えた直後" not in _titles(_findings(text))


def test_addresses_running_out_is_a_design_problem():
    text = (
        "Aug 22 03:20:10 dhcp-rt-01 %DHCPD-4-POOL_EXHAUSTED: "
        "DHCP pool OFFICE is exhausted, no free address"
    )
    item = _one(_findings(text), "配る住所")
    assert item.kind == logreview.CONFIG
    assert "設計" in item.what_now


def test_a_tunnel_that_will_not_come_up_is_reported():
    text = (
        "Aug 22 03:21:00 vpn-rt-01 %CRYPTO-4-IKMP_NO_SA: IKE message from 198.51.100.30 "
        "has no SA and is not an initialization offer\n"
        "Aug 22 03:21:30 vpn-rt-01 %CRYPTO-6-IKMP_MODE_FAILURE: Processing of Main mode "
        "failed with peer at 198.51.100.30"
    )
    item = _one(_findings(text), "IPsec")
    assert item.count == 2
    assert item.kind == logreview.CONFIG


def test_a_rogue_dhcp_server_is_called_out():
    text = (
        "Aug 22 03:23:00 acc-sw-09 %DHCP_SNOOPING-5-DHCP_SNOOPING_DENY: DHCP_SNOOPING drop "
        "message on untrusted port, message type: DHCPOFFER, interface Gi0/9"
    )
    item = _one(_findings(text), "住所を配ろう")
    assert item.kind == logreview.ATTACK


def test_every_finding_says_why_and_what_to_do_next():
    """原因の候補だけ出されても、現場では動けない。"""
    text = (
        FLAPPING
        + "\nAug 22 03:25:00 core-rt-02 %SYS-5-RESTART: System restarted --"
        + "\nAug 22 03:26:00 core-rt-02 %PLATFORM_THERMAL-1-OVERTEMP: overtemperature condition."
    )
    findings = _findings(text)
    assert findings
    for item in findings:
        assert item.why and item.what_now
        assert item.evidence
        assert item.level in (logreview.HIGH, logreview.MEDIUM, logreview.LOW)
        assert item.kind in (logreview.TROUBLE, logreview.CONFIG, logreview.ATTACK)


def test_the_heaviest_comes_first():
    text = (
        "Aug 22 03:22:00 acc-sw-09 %SW_MATM-4-MACFLAP_NOTIF: Host 0011.2233.4455 in vlan 10 "
        "is flapping between port Gi0/3 and port Gi0/9\n"
        "Aug 22 03:21:00 rt-01 %NTP-4-UNSYNC: NTP is unsynchronized\n"
    )
    findings = _findings(text)
    assert findings[0].level == logreview.HIGH


def test_a_quiet_log_says_so(db_session):
    """何も出ないときに黙ると、動いていないのか静かなのか分からない。"""
    text = "Aug 22 03:00:00 core-sw-01 %SYS-5-CONFIG_I: Configured from console by admin"
    report = syslog.review(db_session, text)
    assert report.findings == []
    assert any("見つかりませんでした" in note for note in report.notes)


def test_the_api_returns_the_findings(client):
    text = "\n".join([FLAPPING, "Aug 22 03:25:00 core-rt-02 %SYS-5-RESTART: System restarted --"])
    body = client.post(f"{API}/syslog", json={"text": text}).json()
    assert body["findings"]
    first = body["findings"][0]
    assert first["title"] and first["why"] and first["what_now"]
    assert first["level"] in ("high", "medium", "low")
    assert first["kind"] in ("障害", "設定", "攻撃")


TOPOLOGY_CHANGES = "\n".join(
    f"Aug 22 04:0{minute}:00 acc-sw-02 %SPANTREE-5-TOPOTRAP: Topology Change Trap for vlan 10"
    for minute in range(0, 9)
)


def test_a_few_topology_changes_are_not_called_a_loop() -> None:
    """木の作り直しは、端末が1台つながるだけでも出る。輪の証拠ではない。"""
    assert "輪" not in _titles(_findings(TOPOLOGY_CHANGES))


def test_many_topology_changes_are_called_out_without_calling_it_a_loop() -> None:
    """数が多ければ落ち着いていない。それでも輪だとは言わない。"""
    text = TOPOLOGY_CHANGES + "\n" + TOPOLOGY_CHANGES.replace("04:0", "05:0")
    item = _one(_findings(text), "作り直され")
    assert item.level == logreview.LOW
    assert "輪" not in _titles(_findings(text))


def test_a_blocked_port_is_called_a_loop() -> None:
    """塞いだ・食い違いの行があって初めて、輪の話をする。"""
    text = (
        TOPOLOGY_CHANGES + "\nAug 22 04:10:00 acc-sw-02 %SPANTREE-2-BLOCK_BPDUGUARD: "
        "Received BPDU on port GigabitEthernet0/7 with BPDU Guard enabled. Disabling port."
    )
    item = _one(_findings(text), "輪")
    assert item.level == logreview.HIGH
    assert item.count == 1


def test_one_mac_move_is_not_called_an_attack() -> None:
    """端末を挿し替えても無線で歩いても出る行なので、1件で疑わない。"""
    text = "Aug 22 06:00:00 acc-sw-03 %%10MAC/4/MAC_MOVE: MAC moved to GigabitEthernet0/3."
    assert not _findings(text)


def test_a_mac_moving_over_and_over_is_called_out() -> None:
    """数が多ければ、輪かなりすましの入口として見せる。"""
    text = "\n".join(
        f"Aug 22 06:0{minute}:00 acc-sw-03 %%10MAC/4/MAC_MOVE: MAC moved to GigabitEthernet0/3."
        for minute in range(0, 7)
    )
    item = _one(_findings(text), "居場所")
    assert item.kind == logreview.TROUBLE
