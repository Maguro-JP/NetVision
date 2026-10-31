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


def test_comware_twenty_five_gig_short_name_is_expanded():
    """H3C Comware の 25G 口 `WGE1/0/1` を、Config の `Twenty-FiveGigE1/0/1` と同じ口に寄せる。

    出典: H3C「S6850 & S9850 Switch Series Command References, Release 655x-6W100」03-Ethernet
    interface commands（`display interface twenty-fivegige 1/0/3 brief description` の出力が
    `WGE1/0/3`、display counters などの Interface 列の説明が「Abbreviated interface name」）。
    Cisco の `TwentyFiveGigE`・`Twe` と同じ正式名にそろえる。
    """
    assert ifname.expand("WGE1/0/3") == "TwentyFiveGigE1/0/3"
    assert ifname.expand("Twenty-FiveGigE1/0/3") == "TwentyFiveGigE1/0/3"
    assert ifname.normalize("WGE1/0/3") == ifname.normalize("Twenty-FiveGigE1/0/3")
    assert ifname.normalize("WGE1/0/3") == ifname.normalize("Twe1/0/3")


def test_comware_forty_and_hundred_gig_short_names_are_expanded():
    """H3C Comware の 40G 口 `FGE` と 100G 口 `HGE` を、Config の正式名と同じ口に寄せる。

    出典: H3C「S7500E-XS Command References, R2418P05-6W100」01-Ethernet interface commands
    （`display interface fortygige 1/1/3 brief description` の出力が `FGE1/1/3`）と、
    H3C「S12500R Switch Router Series Command References (R36xx)-6W101」03-Ethernet interface
    commands（`display interface hundredgige 1/0/3 brief description` の出力が `HGE1/0/3`）。
    40G は Cisco の `Fo`・`FortyGigabitEthernet` と、100G は `Hu`・`HundredGigE` と
    同じ正式名にそろえる。
    """
    assert ifname.normalize("FGE1/1/3") == ifname.normalize("FortyGigE1/1/3")
    assert ifname.normalize("FGE1/1/3") == ifname.normalize("Fo1/1/3")
    assert ifname.expand("FortyGigE1/1/3") == "FortyGigabitEthernet1/1/3"
    assert ifname.normalize("HGE1/0/3") == ifname.normalize("HundredGigE1/0/3")
    assert ifname.expand("HGE1/0/3") == "HundredGigE1/0/3"


def test_h3c_ten_gig_and_gig_abbreviations_follow_the_command_reference():
    """H3C Comware の 10G 口 `XGE` と 1G 口 `GE` を、Config の正式名と同じ口として扱う。

    出典: H3C「03-Interface Command Reference」02-Ethernet interface commands の
    display interface（`display interface ten-gigabitethernet 1/0/1 brief description` の
    出力が `XGE1/0/1`、`display interface brief` の出力が `GE1/0/2`。Config 側の名前は
    `Ten-GigabitEthernet1/0/1`）。10G は Cisco の `Te`・`TenGigabitEthernet` と
    同じ正式名にそろえる。
    """
    assert ifname.normalize("XGE1/0/1") == ifname.normalize("Ten-GigabitEthernet1/0/1")
    assert ifname.normalize("XGE1/0/1") == ifname.normalize("Te1/0/1")
    assert ifname.expand("XGE1/0/1") == "TenGigabitEthernet1/0/1"
    assert ifname.normalize("GE1/0/2") == ifname.normalize("GigabitEthernet1/0/2")
    assert ifname.expand("GE1/0/2") == "GigabitEthernet1/0/2"


def test_cisco_gi_and_fa_follow_the_configuration_guide() -> None:
    """Cisco の `show interfaces status` に出る `Gi` と `Fa` を正式名にそろえる。

    出典: Cisco「Interface and Hardware Components Configuration Guide」（Catalyst 9400、
    Cupertino 17.9.x〜17.17.x）の Checking Port Status and Connectivity の出力例
    （`Gi1/0/1 connected ...`、`show interfaces status err-disabled` の
    `Fa9/4 err-disabled link-flap`）。
    """
    assert ifname.expand("Gi1/0/1") == "GigabitEthernet1/0/1"
    assert ifname.expand("Fa9/4") == "FastEthernet9/4"
