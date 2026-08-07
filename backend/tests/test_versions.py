import pytest

from app.services import versions


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        # 同じバージョンの表記ゆれ（ゼロ埋めの有無）
        ("17.09.04", "17.9.4", 0),
        ("15.2(7)E6", "15.2(7)E6", 0),
        # 通常の大小
        ("17.9.4", "17.9.5", -1),
        ("17.9.5", "17.9.4", 1),
        ("17.9.4", "17.10.1", -1),  # 10 を 1 と誤読しない
        ("9.9.9", "10.0.0", -1),
        # 文字を含む Cisco IOS 形式
        ("15.2(7)E6", "15.2(7)E7", -1),
        ("17.09.04a", "17.09.04b", -1),
        ("17.09.04", "17.09.04a", -1),  # サフィックス付きの方が新しい
        # Junos
        ("21.4R3", "21.4R3-S5", -1),
        ("21.4R3-S4", "21.4R3-S5", -1),
        ("21.4R3-S5", "21.4R3-S5.4", -1),
        ("20.4R3", "21.4R3", -1),
        # FortiOS
        ("7.2.8", "7.4.1", -1),
    ],
)
def test_version_ordering(left, right, expected):
    assert versions.compare(left, right) == expected


def test_none_is_treated_as_indeterminate():
    assert versions.compare(None, "1.0") == 0
    assert versions.equals(None, "1.0") is False


@pytest.mark.parametrize(
    ("version", "kwargs", "expected"),
    [
        ("17.9.4", {"version_end_excluding": "17.9.5"}, True),
        ("17.9.5", {"version_end_excluding": "17.9.5"}, False),
        ("17.9.5", {"version_end_including": "17.9.5"}, True),
        ("17.9.4", {"version_start_including": "17.9.0", "version_end_excluding": "17.9.5"}, True),
        ("17.8.9", {"version_start_including": "17.9.0", "version_end_excluding": "17.9.5"}, False),
        ("17.9.0", {"version_start_excluding": "17.9.0"}, False),
        # 範囲指定が無い場合は判定できないので False
        ("17.9.4", {}, False),
    ],
)
def test_version_ranges(version, kwargs, expected):
    normalized = {k.replace("version_", ""): v for k, v in kwargs.items()}
    assert versions.in_range(version, **normalized) is expected


def test_missing_version_never_matches_a_range():
    assert versions.in_range(None, end_excluding="17.9.5") is False


@pytest.mark.parametrize(
    ("pattern", "model", "expected"),
    [
        (None, "C9500-40X", True),  # 指定なしはベンダー全体
        ("C9500-40X", "C9500-40X", True),
        ("c9500-40x", "C9500-40X", True),  # 大文字小文字は無視
        ("C9500-40X", "C9500-48Y4C", False),
        ("C9500*", "C9500-40X", True),  # 前方一致
        ("C9500*", "C9300-48P", False),
        ("C9500-40X", None, False),
    ],
)
def test_model_matching(pattern, model, expected):
    assert versions.matches_model(pattern, model) is expected


def test_os_name_normalization():
    assert versions.normalize_name("IOS-XE") == versions.normalize_name("IOS XE")
    assert versions.normalize_name("IOS-XE") == versions.normalize_name("ios_xe")
    assert versions.normalize_name("IOS") != versions.normalize_name("IOS-XE")
