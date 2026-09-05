"""FortiGate（FortiOS）の読み取り。"""

from app.services import configparse


def test_the_version_is_read_even_when_the_header_is_cut_short():
    """版が読めないと、その機器は弱点の照合から漏れる。"""
    for header, model, version in (
        ("#config-version=FGT100F-7.2.8-FW-build1639-230929:opmode=0:vdom=0", "FGT100F", "7.2.8"),
        ("#config-version=FGT100F-7.2.8", "FGT100F", "7.2.8"),
        ("#config-version=FGT60F-7.4-FW-build2573-240201:opmode=0", "FGT60F", "7.4"),
        ("#config-version=FWF60E-6.4.14-FW-build2093-230314:opmode=0", "FWF60E", "6.4.14"),
    ):
        parsed = configparse.parse(f"{header}\nconfig system global\n set hostname fw1\nend\n")
        assert (parsed.model, parsed.os_version) == (model, version), header


def test_a_pasted_section_is_still_read_as_fortigate() -> None:
    """設定の一部だけを貼られることは多い。先頭の版数行が無くても読む。"""
    text = "\n".join(
        [
            "config system interface",
            '    edit "port1"',
            "        set ip 203.0.113.2 255.255.255.248",
            "    next",
            "end",
            "config router static",
            "    edit 1",
            "        set dst 10.20.0.0 255.255.0.0",
            "        set gateway 203.0.113.1",
            '        set device "port1"',
            "    next",
            "end",
        ]
    )
    parsed = configparse.parse(text)
    assert parsed.vendor is configparse.Vendor.FORTINET
    assert [i.ip_address for i in parsed.interfaces] == ["203.0.113.2/29"]
    assert [r.destination for r in parsed.routes] == ["10.20.0.0/16"]


def test_plain_prose_is_not_mistaken_for_fortigate() -> None:
    """end や next という語が出てくるだけの文章を、設定と取り違えない。"""
    text = "\n".join(["config change memo", "next", "end"])
    assert configparse.parse(text).vendor is not configparse.Vendor.FORTINET
