"""Cisco 公式の型番表を、そのまま試験にする。

私の記憶ではなく、Cisco が公開している EoL 通知と発注表の PID を使う。
一覧に無い型番でも規則で読めることを、実在の型番の広がりで確かめる。

出典:
- Catalyst 2960-X EoL 通知 (eos-eol-notice-c51-744432)
- Catalyst 2960-XR EoL 通知 (eos-eol-notice-c51-744422)
- Catalyst 9300 発注ガイド (nb-06-cat9300-swit-ser-cte-en)
- ISR 4331 製品ページの Primary PIDs、ISR 4000 発注ガイド
- Nexus 9300-FX / FX2 データシートの発注表、Nexus 93180YC-FX 製品ページの PIDs
- Catalyst 9200 データシート (nb-06-cat9200-ser-data-sheet-cte-en)
- Catalyst 9500 データシート (nb-06-cat9500-ser-data-sheet-cte-en)
- ISR 1000 データシート (datasheet-c78-739512)
- ASR 1000 データシート (datasheet-c78-731632) の Product portfolio
- Catalyst IE3300 データシート (datasheet-c78-741759)
- Catalyst 8300 データシート (datasheet-c78-744088)
- Firepower 1000 データシート (datasheet-c78-742469) の Model 列と、
  Cisco Network Security Ordering Guide (guide-c07-737902) の Table 18A
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
    # Catalyst 9200 / 9200L / 9200CX（データシート。ライセンスの -A / -E は後ろに付く）
    ("C9200-24P", "C9200"),
    ("C9200-24PB-A", "C9200"),
    ("C9200-24PXG", "C9200"),
    ("C9200-48PL", "C9200"),
    ("C9200-48PXG", "C9200"),
    ("C9200-48T-E", "C9200"),
    ("C9200L-24P-4G", "C9200L"),
    ("C9200L-24T-4G-E", "C9200L"),
    ("C9200L-48P-4X3", "C9200L"),
    ("C9200L-48PXG-2Y", "C9200L"),
    ("C9200L-48PL-4X-A", "C9200L"),
    ("C9200CX-12P-2X2G", "C9200CX"),
    ("C9200CX-8PT-2G", "C9200CX"),
    ("C9200CX-8UXG-2XH", "C9200CX"),
    # Catalyst 9500 / 9500X（データシート）
    ("C9500-12Q", "C9500"),
    ("C9500-16X", "C9500"),
    ("C9500-24Y4C", "C9500"),
    ("C9500-32C", "C9500"),
    ("C9500-32QC", "C9500"),
    ("C9500-40X", "C9500"),
    ("C9500-48Y4C-A", "C9500"),
    ("C9500X-28C8D", "C9500X"),
    ("C9500X-60L4D-E", "C9500X"),
    # ISR 1000（データシートの発注情報）
    ("C1101-4P", "C1101"),
    ("C1101-4PLTEP", "C1101"),
    ("C1109-2PLTEVZ", "C1109"),
    ("C1111-8P", "C1111"),
    ("C1111-8PLTEEAW", "C1111"),
    ("C1111X-8P", "C1111X"),
    ("C1112-8PWE", "C1112"),
    ("C1116-4PLTEEAWE", "C1116"),
    ("C1117-4PLTELAW", "C1117"),
    ("C1121X-8PLTEPW", "C1121X"),
    ("C1127X-8PLTEP", "C1127X"),
    ("C1131X-8", "C1131X"),
    ("C1161X-8PLTEP", "C1161X"),
    # ASR 1000（データシートの Product portfolio）
    ("ASR1001-X", "ASR1001"),
    ("ASR1001-HX", "ASR1001"),
    ("ASR1002-X", "ASR1002"),
    ("ASR1002-HX", "ASR1002"),
    ("ASR1004", "ASR1004"),
    ("ASR1006", "ASR1006"),
    ("ASR1006-X", "ASR1006"),
    ("ASR1009-X", "ASR1009"),
    ("ASR1013", "ASR1013"),
    # Catalyst IE3300（データシート）
    ("IE-3300-8P2S", "IE-3300"),
    ("IE-3300-8T2S-E", "IE-3300"),
    ("IE-3300-8T2X-E", "IE-3300"),
    ("IE-3300-8U2X-E", "IE-3300"),
    # Catalyst 8300（データシート）
    ("C8300-1N1S-4T2X", "C8300"),
    ("C8300-1N1S-6T", "C8300"),
    ("C8300-2N2S-4T2X", "C8300"),
    ("C8300-2N2S-6T", "C8300"),
    # Firepower 1000（データシートは `FPR-1120`、発注表は `FPR1120-NGFW-K9`）
    ("FPR-1010", "FPR1010"),
    ("FPR-1120", "FPR1120"),
    ("FPR-1140", "FPR1140"),
    ("FPR-1150", "FPR1150"),
    ("FPR1010-NGFW-K9", "FPR1010"),
    ("FPR1010E-NGFW-K9", "FPR1010E"),
    ("FPR1120-NGFW-K9", "FPR1120"),
    ("FPR1120-ASA-K9", "FPR1120"),
    ("FPR1140-ASA-K9", "FPR1140"),
    ("FPR1150-NGFW-K9", "FPR1150"),
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
    # 機器の `show version` は `FPR-1120`、参照データは発注 PID で書かれる
    assert versions.matches_model("FPR1120-NGFW-K9", "FPR-1120")
    assert versions.matches_model("FPR1120*", "FPR-1120")


def test_a_different_hardware_still_does_not_hit() -> None:
    assert not versions.matches_model("C93180YC-FX", "N9K-C93180YC-EX")
    assert not versions.matches_model("C9300-48P", "C9300-48U-A")
    assert not versions.matches_model("ISR4331", "ISR4351/K9")
    assert not versions.matches_model("C2960X*", "WS-C2960XR-24PD-I")
    assert not versions.matches_model("C9200*", "C9200L-24T-4G-E")
    assert not versions.matches_model("C9500*", "C9500X-28C8D")
    assert not versions.matches_model("C1111*", "C1111X-8P")
    assert not versions.matches_model("ASR1001-X", "ASR1001-HX")
    assert not versions.matches_model("FPR1010*", "FPR1010E-NGFW-K9")
    assert not versions.matches_model("FPR1120", "FPR1150-ASA-K9")
