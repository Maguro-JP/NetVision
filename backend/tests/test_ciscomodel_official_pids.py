"""Cisco 公式の型番表を、そのまま試験にする。

私の記憶ではなく、Cisco が公開している EoL 通知と発注表の PID を使う。
一覧に無い型番でも規則で読めることを、実在の型番の広がりで確かめる。

出典:
- Catalyst 2960-X EoL 通知 (eos-eol-notice-c51-744432)
- Catalyst 2960-XR EoL 通知 (eos-eol-notice-c51-744422)
- Catalyst 9300 発注ガイド (nb-06-cat9300-swit-ser-cte-en)
- ISR 4331 製品ページの Primary PIDs、ISR 4000 発注ガイド
- Nexus 9300-FX / FX2 データシートの発注表、Nexus 93180YC-FX 製品ページの PIDs
"""

import pytest

from app.services import versions
from app.services.configparse import ciscomodel

#: (公式の型番, 系列)。系列は参照データが書く単位
OFFICIAL = [
    # Catalyst 2960-X（EoL 通知 Table 2）
    ("WS-C2960X-24PD-L", "C2960X"),
    ("WS-C2960X-24TD-L", "C2960X"),
    ("WS-C2960X-24TS-L", "C2960X"),
    ("WS-C2960X-24TS-LL", "C2960X"),
    ("WS-C2960X-24TS-LB", "C2960X"),
    ("WS-C2960X-24TS-IN", "C2960X"),
    ("WS-C2960X-48FPD-L", "C2960X"),
    ("WS-C2960X-48FPS-L", "C2960X"),
    ("WS-C2960X-48LPD-L", "C2960X"),
    ("WS-C2960X-48TD-L", "C2960X"),
    ("WS-C2960X-48TS-L", "C2960X"),
    ("WS-C2960X-48TS-LL", "C2960X"),
    ("WS-C2960X-48TS-BR", "C2960X"),
    ("C1-C2960X-24PD-L", "C2960X"),
    ("C1-C2960X-24TS-L", "C2960X"),
    ("C1-C2960X-48FPD-L", "C2960X"),
    ("C1-C2960X-24TS-L++", "C2960X"),
    ("C1-C2960X-48FPDL++", "C2960X"),
    ("WS-C2960RX-48FPD-L", "C2960RX"),
    ("WS-C2960RX-24TS-L", "C2960RX"),
    # Catalyst 2960-XR（EoL 通知 Table 2）
    ("WS-C2960XR-24PD-I", "C2960XR"),
    ("WS-C2960XR-24TD-I", "C2960XR"),
    ("WS-C2960XR-24TS-I", "C2960XR"),
    ("WS-C2960XR-48FPD-I", "C2960XR"),
    ("WS-C2960XR-48TS-I", "C2960XR"),
    ("WS-C2960XR-24PD-HC", "C2960XR"),
    ("WS-C2960XR-24TSI++", "C2960XR"),
    ("2D-C2960XR-24PD-I", "C2960XR"),
    ("NAL-2960XR-24PD-I", "C2960XR"),
    # Catalyst 9300（発注ガイド Table 3）
    ("C9300X-48HX-E", "C9300X"),
    ("C9300X-48HX-A", "C9300X"),
    ("C9300X-48HX-M", "C9300X"),
    ("C9300X-48TX-A", "C9300X"),
    ("C9300X-48HXN-A", "C9300X"),
    ("C9300X-24HX-E", "C9300X"),
    ("C9300X-12Y-A", "C9300X"),
    ("C9300X-24Y-M", "C9300X"),
    ("C9300-24T-E", "C9300"),
    ("C9300-24T-A", "C9300"),
    ("C9300-24T-M", "C9300"),
    ("C9300-24P-A", "C9300"),
    ("C9300-24U-E", "C9300"),
    ("C9300-24UX-A", "C9300"),
    ("C9300-48T-A", "C9300"),
    ("C9300-48P-E", "C9300"),
    ("C9300-48U-E", "C9300"),
    ("C9300-24S-A", "C9300"),
    ("C9300-48S-E", "C9300"),
    ("C9300L-24T-4G-E", "C9300L"),
    ("C9300L-24P-4G-A", "C9300L"),
    ("C9300L-48T-4X-M", "C9300L"),
    ("C9300L-48PF-4X-A", "C9300L"),
    ("C9300L-24UXG-4X-E", "C9300L"),
    ("C9300L-48UXG-2Q-A", "C9300L"),
    ("C9300LM-48UX-4Y-A", "C9300LM"),
    ("C9300LM-24U-4Y-E", "C9300LM"),
    # ISR 4000（製品ページの Primary PIDs、発注ガイド）
    ("ISR4331/K9", "ISR4331"),
    ("ISR4331/K9++", "ISR4331"),
    ("ISR4331/K9-RF", "ISR4331"),
    ("ISR4331/K9-WS", "ISR4331"),
    ("ISR4331-AX/K9", "ISR4331"),
    ("ISR4331-AXV/K9", "ISR4331"),
    ("ISR4331-V/K9", "ISR4331"),
    ("ISR4331-SEC/K9", "ISR4331"),
    ("ISR4331-VSEC/K9", "ISR4331"),
    ("ISR4331-DC/K9", "ISR4331"),
    ("ISR4331-B/K9", "ISR4331"),
    ("ISR4331R/K9", "ISR4331"),
    ("ISR4331R-V/K9-PK", "ISR4331"),
    ("C1-CISCO4331/K9", "4331"),
    ("SI-ISR4331-IWAN/K9", "ISR4331"),
    ("ISR4321/K9", "ISR4321"),
    ("ISR4351/K9", "ISR4351"),
    ("ISR4431/K9", "ISR4431"),
    ("ISR4451-X/K9", "ISR4451"),
    ("ISR4451-X-SEC/K9", "ISR4451"),
    ("ISR4461/K9", "ISR4461"),
    # Nexus 9300（データシート発注表、製品ページの PIDs）
    ("N9K-C93180YC-FX", "C93180YC"),
    ("N9K-C93180YC-FX-A", "C93180YC"),
    ("N9K-C93180YC-FX-PE", "C93180YC"),
    ("N9K-C93180YC-FX-PI", "C93180YC"),
    ("N9K-C93180YC-FX-RF", "C93180YC"),
    ("N9K-C93180YC-FX3H", "C93180YC"),
    ("N9K-C93180YC-EX", "C93180YC"),
    ("N9K-C93108TC-FX", "C93108TC"),
    ("N9K-C9348GC-FXP", "C9348GC"),
    ("N9K-C9336C-FX2", "C9336C"),
    ("N9K-C9336C-FX2-E", "C9336C"),
    ("N9K-C93240YC-FX2", "C93240YC"),
    ("N9K-C93360YC-FX2", "C93360YC"),
    ("N9K-C93216TC-FX2", "C93216TC"),
]


@pytest.mark.parametrize(("pid", "family"), OFFICIAL)
def test_every_official_pid_lands_in_its_family(pid: str, family: str) -> None:
    model = ciscomodel.read(pid)
    assert model is not None, pid
    assert model.family == family, (pid, model.canonical)


def test_the_family_pattern_hits_every_member() -> None:
    """参照データが `C2960X*` と書けば、公式の 2960-X の型番すべてに当たること。"""
    for pid, family in OFFICIAL:
        pattern = f"{family}*"
        assert versions.matches_model(pattern, pid), (pattern, pid)


def test_the_same_hardware_with_a_different_bundle_is_the_same_model() -> None:
    """束や気流の向きが違うだけの型番は、同じハードとして当たる。"""
    assert versions.matches_model("ISR4331", "ISR4331-AX/K9")
    assert versions.matches_model("ISR4331", "ISR4331-DC/K9")
    assert versions.matches_model("C9300-48P", "C9300-48P-M")
    assert versions.matches_model("C2960X-24TS", "C1-C2960X-24TS-L++")
    assert versions.matches_model("C93180YC-FX", "N9K-C93180YC-FX-PE")


def test_a_different_hardware_still_does_not_hit() -> None:
    assert not versions.matches_model("C93180YC-FX", "N9K-C93180YC-EX")
    assert not versions.matches_model("C9300-48P", "C9300-48U-A")
    assert not versions.matches_model("ISR4331", "ISR4351/K9")
    assert not versions.matches_model("C2960X*", "WS-C2960XR-24PD-I")
