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
