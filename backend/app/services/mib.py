"""SNMP で取った値を読む。

この道具は外へ通信しないので、機器へ取りに行くことはしません。人が
`snmpwalk` や `snmpget` で取ってきた出力を持ち込み、それを読みます。
取り方が変わっても（将来こちらから取りに行くようになっても）、読む側は
ここに残ります。

読めると何が分かるか。Config は「どう設定してあるか」で、SNMP は「いま
どうなっているか」です。設定では有効なのに落ちているポート、台帳に書いて
あるのと違う OS の版、入れ替えたのに台帳が古いままの号機。どれも設定
ファイルだけを見ていても出てきません。

意味を持っている OID だけを日本語にします。知らない OID は「知らない」と
数えて出します。分かったふりをすると、取れていない値を「異常なし」と
読まれます。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device
from app.models.topology import Interface
from app.services import configparse, versions

#: `SNMPv2-MIB::sysDescr.0 = STRING: Cisco IOS Software ...`
#: `.1.3.6.1.2.1.1.1.0 = STRING: ...`
#: 型名は net-snmp の snmplib/mib.c が付けるもの（`STRING: `、`Hex-STRING: `、
#: `Network Address: `、`Wrong Type (should be INTEGER): ` など）。`-`・空白・括弧を含む
_LINE = re.compile(
    r"^(?P<oid>[.\w:-]+?)\s*=\s*(?:(?P<type>[A-Za-z][A-Za-z0-9 ()\-]*?):)?\s*(?P<value>.*)$",
)

#: `Hex-STRING: 73 77 31` の中身
_HEX_BYTES = re.compile(r"^(?:[0-9A-Fa-f]{2}\s*)+$")

#: 末尾の添字（`.0` や `.10101`）を外して、どの値かだけを見る
_INDEX = re.compile(r"\.(\d+)$")

#: 引用符や単位の飾りを外す
_QUOTED = re.compile(r'^"(.*)"$')


@dataclass(frozen=True)
class Meaning:
    """その値が何なのか。`use` は、この道具のどこで使えるか。"""

    label: str
    plain: str
    use: str = ""


#: 名前でも数字でも引けるようにする。現場から来る出力は、どちらの形もある
MEANINGS: dict[str, Meaning] = {
    "sysdescr": Meaning(
        "機器の自己紹介",
        "メーカー・機種・OS の版が、1行にまとめて入っています",
        "台帳の型番と OS 版の裏取りに使えます",
    ),
    "sysobjectid": Meaning(
        "機種を表す番号",
        "メーカーが機種ごとに決めている番号です",
        "型番が書かれていない出力でも、機種の当たりが付きます",
    ),
    "sysuptime": Meaning(
        "電源が入ってからの時間",
        "最後に再起動してからどれだけ経ったかです",
        "短ければ、最近落ちたか入れ替えたということです",
    ),
    "sysname": Meaning(
        "機器の名前",
        "機器に付けられているホスト名です",
        "台帳の名前と食い違えば、どちらかが古いということです",
    ),
    "syscontact": Meaning("連絡先", "その機器の担当として書かれている連絡先です"),
    "syslocation": Meaning(
        "設置場所",
        "その機器に書かれている置き場所です",
        "台帳の拠点・ラックと突き合わせられます",
    ),
    "ifnumber": Meaning("ポートの本数", "その機器が持っているポートの数です"),
    "ifdescr": Meaning("ポートの名前", "ポートの呼び名です（GigabitEthernet1/0/1 など）"),
    "ifalias": Meaning(
        "ポートの説明",
        "そのポートに書かれている説明です",
        "Config の description と同じものです",
    ),
    "ifadminstatus": Meaning(
        "ポートの設定上の状態",
        "人が「使う」と決めているかどうかです（1 が使う、2 が閉じている）",
        "閉じているのに線が来ている、を見つけられます",
    ),
    "ifoperstatus": Meaning(
        "ポートの実際の状態",
        "いま通信できているかどうかです（1 が上がっている、2 が落ちている）",
        "設定は使うことになっているのに落ちている、が分かります",
    ),
    "ifspeed": Meaning(
        "ポートの速さ",
        "そのポートがいま出ている速さです（bps）",
        "1Gbps のはずが 100Mbps で上がっている、が見つかります",
    ),
    "ifhighspeed": Meaning("ポートの速さ（Mbps）", "1Gbps を超える速さを Mbps で表したものです"),
    "entphysicalserialnum": Meaning(
        "製造番号",
        "その機器（や部品）の製造番号です",
        "台帳の製造番号と突き合わせると、入れ替えに気づけます",
    ),
    "entphysicalmodelname": Meaning("型番", "その機器（や部品）の型番です"),
    "entphysicalsoftwarerev": Meaning("ソフトの版", "その機器に入っているソフトの版です"),
    "hrprocessorload": Meaning(
        "CPU の使用率",
        "その機器の頭がどれだけ働いているかです（％）",
        "高いままなら、その機器に無理をさせています",
    ),
    "cpmcputotal5minrev": Meaning("CPU の使用率（5分平均）", "直近5分の平均です（％）"),
}

#: 数字の OID から、上の名前へ。現場の出力は数字だけのことがある
NUMERIC: dict[str, str] = {
    "1.3.6.1.2.1.1.1": "sysdescr",
    "1.3.6.1.2.1.1.2": "sysobjectid",
    "1.3.6.1.2.1.1.3": "sysuptime",
    "1.3.6.1.2.1.1.4": "syscontact",
    "1.3.6.1.2.1.1.5": "sysname",
    "1.3.6.1.2.1.1.6": "syslocation",
    "1.3.6.1.2.1.2.1": "ifnumber",
    "1.3.6.1.2.1.2.2.1.2": "ifdescr",
    "1.3.6.1.2.1.2.2.1.5": "ifspeed",
    "1.3.6.1.2.1.2.2.1.7": "ifadminstatus",
    "1.3.6.1.2.1.2.2.1.8": "ifoperstatus",
    "1.3.6.1.2.1.31.1.1.1.15": "ifhighspeed",
    "1.3.6.1.2.1.31.1.1.1.18": "ifalias",
    "1.3.6.1.2.1.47.1.1.1.1.11": "entphysicalserialnum",
    "1.3.6.1.2.1.47.1.1.1.1.13": "entphysicalmodelname",
    "1.3.6.1.2.1.47.1.1.1.1.10": "entphysicalsoftwarerev",
    "1.3.6.1.2.1.25.3.3.1.2": "hrprocessorload",
    "1.3.6.1.4.1.9.9.109.1.1.1.1.8": "cpmcputotal5minrev",
}

#: 状態の数字を、そのまま出しても分かる言い方に
_STATUS = {"1": "上がっています", "2": "落ちています", "3": "試験中です"}
_ADMIN = {"1": "使う設定です", "2": "閉じています", "3": "試験中です"}


@dataclass
class Value:
    """読めた値1つ。"""

    key: str
    oid: str
    index: str
    raw: str
    label: str = ""
    plain: str = ""


@dataclass
class PortState:
    """ポート1本の、いまの状態。"""

    index: str
    name: str = ""
    alias: str = ""
    admin: str = ""
    oper: str = ""
    speed_mbps: int | None = None

    @property
    def down_but_enabled(self) -> bool:
        """使う設定なのに落ちている。いちばん見たいのはここ。"""
        return self.admin == "1" and self.oper == "2"


@dataclass
class MibReport:
    lines: int = 0
    read: int = 0
    unknown: int = 0
    hostname: str | None = None
    descr: str | None = None
    vendor: str | None = None
    model: str | None = None
    os_version: str | None = None
    serial: str | None = None
    location: str | None = None
    uptime: str | None = None
    ports: list[PortState] = field(default_factory=list)
    values: list[Value] = field(default_factory=list)
    #: 台帳と食い違ったところ
    differences: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def down_ports(self) -> list[PortState]:
        return [p for p in self.ports if p.down_but_enabled]


def key_of(oid: str) -> tuple[str, str]:
    """OID から「どの値か」と「何番目か」を出す。

    `SNMPv2-MIB::sysDescr.0` も `.1.3.6.1.2.1.1.1.0` も `sysdescr` に寄せます。
    書き方が違うだけで同じものなので、比べられる形にそろえます。
    """
    text = oid.strip().lstrip(".")
    index = ""
    if "::" in text:
        text = text.split("::", 1)[1]
        matched = _INDEX.search(text)
        if matched:
            index = matched.group(1)
            text = text[: matched.start()]
        return text.lower(), index

    # 数字だけの形。長いものから当てて、残りを添字にする
    for numeric, name in sorted(NUMERIC.items(), key=lambda item: -len(item[0])):
        if text == numeric:
            return name, ""
        if text.startswith(numeric + "."):
            return name, text[len(numeric) + 1 :]
    return text, ""


def _hex_text(value: str) -> str:
    """`73 77 31` を `sw1` に戻す。

    機器名に日本語や制御文字が入っていると、net-snmp は文字列を Hex-STRING で出す。
    文字として読めるときだけ戻し、読めなければ16進のまま返す（MAC アドレスなど）。
    """
    if not _HEX_BYTES.match(value.strip()):
        return value
    data = bytes.fromhex(value.replace(" ", "").replace("\n", ""))
    try:
        text = data.decode("utf-8").rstrip("\x00")
    except UnicodeDecodeError:
        return value
    return text if text and text.isprintable() else value


def parse(text: str) -> list[Value]:
    """snmpwalk / snmpget の出力を読む。読めない行は落とす。"""
    out: list[Value] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        matched = _LINE.match(stripped)
        if not matched:
            continue
        key, index = key_of(matched.group("oid"))
        value = matched.group("value").strip()
        quoted = _QUOTED.match(value)
        if quoted:
            value = quoted.group(1)
        if (matched.group("type") or "").strip() == "Hex-STRING":
            value = _hex_text(value)
        meaning = MEANINGS.get(key)
        out.append(
            Value(
                key=key,
                oid=matched.group("oid").strip(),
                index=index,
                raw=value,
                label=meaning.label if meaning else "",
                plain=meaning.plain if meaning else "",
            )
        )
    return out


def review(db: Session, text: str) -> MibReport:
    """読んで、台帳と突き合わせる。台帳には何も書かない。"""
    report = MibReport(lines=len([line for line in text.splitlines() if line.strip()]))
    values = parse(text)
    report.values = values
    report.read = len(values)
    report.unknown = len([v for v in values if v.key not in MEANINGS])

    ports: dict[str, PortState] = {}
    for value in values:
        if value.key == "sysname":
            report.hostname = value.raw or None
        elif value.key == "sysdescr":
            report.descr = value.raw
        elif value.key == "syslocation":
            report.location = value.raw
        elif value.key == "sysuptime":
            report.uptime = value.raw
        elif value.key == "entphysicalserialnum" and value.raw and report.serial is None:
            report.serial = value.raw
        elif value.key == "entphysicalmodelname" and value.raw and report.model is None:
            report.model = value.raw
        elif value.key in {"ifdescr", "ifalias", "ifadminstatus", "ifoperstatus", "ifhighspeed"}:
            port = ports.setdefault(value.index, PortState(index=value.index))
            if value.key == "ifdescr":
                port.name = value.raw
            elif value.key == "ifalias":
                port.alias = value.raw
            elif value.key == "ifadminstatus":
                port.admin = _first_number(value.raw)
            elif value.key == "ifoperstatus":
                port.oper = _first_number(value.raw)
            elif value.key == "ifhighspeed":
                port.speed_mbps = _as_int(value.raw)

    report.ports = sorted(ports.values(), key=lambda p: _as_int(p.index) or 0)

    if report.descr:
        parsed = configparse.parse(report.descr)
        report.vendor = str(parsed.vendor) if parsed.vendor else None
        report.os_version = parsed.os_version
        if report.model is None:
            report.model = parsed.model

    _compare_with_ledger(db, report)
    _explain(report)
    return report


def _first_number(value: str) -> str:
    """`up(1)` も `1` も `1` にする。"""
    matched = re.search(r"\d+", value or "")
    return matched.group(0) if matched else ""


def _as_int(value: str | None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _compare_with_ledger(db: Session, report: MibReport) -> None:
    """台帳と突き合わせる。食い違いは、どちらかが古いということ。"""
    if not report.hostname:
        report.notes.append("機器の名前（sysName）が読めなかったので、台帳とは突き合わせていません")
        return

    device = db.scalars(select(Device).where(Device.hostname == report.hostname)).first()
    if device is None:
        report.notes.append(
            f"{report.hostname} は台帳にありません。取り込むか、名前を確かめてください"
        )
        return

    if report.os_version and device.os_version:
        order = versions.compare(device.os_version, report.os_version)
        if order != 0:
            direction = "台帳のほうが古い" if order < 0 else "台帳のほうが新しい"
            report.differences.append(
                f"OS の版が違います。台帳 {device.os_version} / 実機 {report.os_version}"
                f"（{direction}）"
            )
    elif report.os_version and not device.os_version:
        report.differences.append(
            f"台帳に OS の版が入っていません。実機は {report.os_version} です"
        )

    if report.model and device.model and report.model.lower() not in device.model.lower():
        report.differences.append(f"型番が違います。台帳 {device.model} / 実機 {report.model}")

    if report.serial and device.serial_number and report.serial != device.serial_number:
        report.differences.append(
            f"製造番号が違います。台帳 {device.serial_number} / 実機 {report.serial}。"
            "機器が入れ替わっている可能性があります"
        )

    known = {
        configparse.normalize(row.name): row
        for row in db.scalars(select(Interface).where(Interface.device_id == device.id))
    }
    for port in report.ports:
        if not port.name:
            continue
        row = known.get(configparse.normalize(port.name))
        if row is None:
            continue
        if port.alias and row.description and port.alias != row.description:
            report.differences.append(
                f"{port.name} の説明が違います。台帳 {row.description} / 実機 {port.alias}"
            )


def _explain(report: MibReport) -> None:
    down = report.down_ports
    if down:
        report.notes.append(
            f"使う設定なのに落ちているポートが {len(down)} 本あります："
            + "、".join(p.name or p.index for p in down[:5])
        )
    if report.unknown:
        report.notes.append(
            f"意味を持っていない値が {report.unknown} 件ありました。そこは読めていません"
        )
    if not report.ports:
        report.notes.append(
            "ポートの状態が入っていません。ifDescr / ifOperStatus まで取ると、"
            "落ちているポートが分かります"
        )
    report.notes.append("ここでは読むだけです。台帳には何も書きません")


def status_label(port: PortState) -> str:
    """ポートの状態を1行で。数字のままでは判断できない。"""
    admin = _ADMIN.get(port.admin, "設定が読めません")
    oper = _STATUS.get(port.oper, "状態が読めません")
    return f"{admin} / {oper}"
