"""口の名前を正式名へ寄せる（ifname.expand / normalize）。

同じ口が正式名と略記で書かれるので、突き合わせる前に寄せる。
"""

from app.services.configparse import ifname


def test_catalyst_mgig_names_meet_their_abbreviations() -> None:
    """Catalyst 9000 の 2.5G 口は `TwoGigabitEthernet`、表示では `Tw1/0/1` と略す。

    出典: Command Reference, Cisco IOS XE 17.10.x（Catalyst 9300）の interface コマンド
    （TwoGigabitEthernet / FiveGigabitEthernet / TwentyFiveGigE）と、Cisco「Migration Guide
    from Cisco Catalyst」Table 5（mGig downlink は Tw1/0/1、25-GE uplink は TwentyFiveGigE1/1/1）。
    """
    assert ifname.normalize("Tw1/0/1") == ifname.normalize("TwoGigabitEthernet1/0/1")
    assert ifname.expand("Tw1/0/1") == "TwoGigabitEthernet1/0/1"
    assert ifname.expand("FiveGigabitEthernet1/0/37") == "FiveGigabitEthernet1/0/37"
    # 25G は `Twe`。2.5G の `Tw` と取り違えない
    assert ifname.expand("Twe1/1/1") == "TwentyFiveGigE1/1/1"
    assert ifname.normalize("Tw1/0/1") != ifname.normalize("Twe1/0/1")


def test_existing_abbreviations_stay_as_they_were() -> None:
    """既にある略記の寄せ方は変えない。"""
    assert ifname.expand("Gi 0/1") == "GigabitEthernet0/1"
    assert ifname.expand("Te1/1/1") == "TenGigabitEthernet1/1/1"
    assert ifname.expand("ge-0/0/1") == "ge-0/0/1"
    assert ifname.expand("GE1/0/1") == "GigabitEthernet1/0/1"
