"""H3C（Comware）の config を読む。

NEC QX は H3C の Comware を載せた機種なので、書き方はそのまま重なる。
読む中身は同じでよいが、メーカーが違う。

分けている理由は、型番と保守期限・脆弱性が別物だから。H3C の機器を NEC と
して記録すると、別のメーカーの期限と脆弱性で判定することになり、しかも
黙って通る（ファーウェイ VRP を分けたのと同じ理由）。

読み取りは Comware の読み手に任せて、名乗りと型番だけ上から直す。
"""

from __future__ import annotations

import re

from app.services.configparse import nec
from app.services.configparse.base import ParsedConfig, Vendor

#: H3C の名乗り。NEC QX と見分けが付くのはここだけ
_BANNER = re.compile(r"\bH3C\b|H3C\s+Comware\s+Software|New\s+H3C\s+Technologies", re.IGNORECASE)
#: NEC の名乗り。両方出ているなら NEC 側を優先する（OEM 元の名前が
#: 資料に残っていることがあるため）
_NEC = re.compile(r"\bNEC\b|UNIVERGE|\bQX-S\d", re.IGNORECASE)

_VERSION = re.compile(r"^\s*version\s+([\w.]+),?\s*Release", re.IGNORECASE | re.MULTILINE)
#: `H3C S5560X-30C-EI` / `H3C MSR3620` のような型番
_MODEL = re.compile(r"\bH3C\s+((?:S|MSR|SR|WX|LS)[\w-]*\d[\w-]*)", re.IGNORECASE)


def detect(text: str) -> Vendor | None:
    """H3C かどうか。名乗りが出ていて、NEC の名乗りが無いときだけ言う。

    書き方が同じなので、名乗りが無いものを H3C だと決めない。決め打つと、
    NEC QX の config を H3C として読むことになる。
    """
    if not _BANNER.search(text):
        return None
    return None if _NEC.search(text) else Vendor.H3C


def parse(text: str) -> ParsedConfig:
    parsed = nec.parse(text, Vendor.NEC_QX)
    parsed.vendor = Vendor.H3C
    parsed.os_name = "Comware"

    found = _VERSION.search(text)
    if found:
        parsed.os_version = found.group(1)
    found = _MODEL.search(text)
    if found:
        parsed.model = found.group(1)
    return parsed
