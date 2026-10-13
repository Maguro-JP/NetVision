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


def test_five_and_hundred_gig_short_names_are_expanded():
    """Catalyst の 5G 口 `Fi` と 100G 口 `Hu` を正式名に寄せる。

    出典: Cisco「Interface and Hardware Components Configuration Guide, Cisco IOS XE Cupertino
    17.9.x (Catalyst 9300 Switches)」Configuring Interface Characteristics
    （5-Gigabit Ethernet (FiveGigabitEthernet or fi)、100-Gigabit Ethernet (HundredGigE or hu)）。
    """
    assert ifname.expand("Fi1/0/1") == "FiveGigabitEthernet1/0/1"
    assert ifname.expand("Hu1/1/1") == "HundredGigE1/1/1"
    assert ifname.normalize("Hu1/1/1") == ifname.normalize("HundredGigE1/1/1")


def test_comware_aggregate_short_names_are_expanded():
    """H3C Comware の集約口の略記 `BAGG`・`RAGG` を正式名に寄せる。

    `display interface brief` や `display link-aggregation` は BAGG1 と書き、Config は
    `interface Bridge-Aggregation1` と書くため、寄せないと同じ口が別の口に見える。
    出典: H3C「S9820-8M[S6880] Command References, R1116Pxx-6W100」04-Layer 2—LAN Switching
    Command Reference の display link-aggregation summary（Aggregate Interface Type の凡例
    「BAGG -- Bridge-Aggregation, ... RAGG -- Route-Aggregation」）。
    FGE・HGE・WGE の略記は公式の対応表に当たれていない（未確認）ので、ここでは寄せない。
    """
    assert ifname.expand("BAGG1") == "Bridge-Aggregation1"
    assert ifname.normalize("BAGG1") == ifname.normalize("Bridge-Aggregation1")
    assert ifname.expand("RAGG10") == "Route-Aggregation10"
    assert ifname.normalize("RAGG10") == ifname.normalize("Route-Aggregation10")
    assert ifname.expand("route-aggregation10") == "Route-Aggregation10"
