"""show の表から、線が実際に上がっているかを読む。

Config には「止めていない」までしか書いていない。線がつながっているか、
相手が生きているかは show でしか分からない。図に線があるのに片側が
notconnect なら、それは図と実機のずれそのものなので、持ち帰って突き合わせる。

読めるのは、次の表。列の並びが決まっているものだけを見る。

- Cisco `show interfaces status`（Port / Name / Status / Vlan / Duplex / Speed / Type）
- Cisco / Arista / NEC IX `show ip interface brief`
  （Interface / IP-Address / ... / Status / Protocol。Cisco の出典は _read_brief）
- Junos `show interfaces terse`（Interface / Admin / Link / Proto ...。出典は _read_terse）
- Aruba CX `show interface brief`（Port / ... / Enabled / Status / ...）
- PAN-OS `show interface all`（name / id / speed/duplex/state / mac address）
- FortiGate `get system interface physical`（`==[port1]` の下に `status: up`）
- Cisco / NEC IX `show interfaces`（`GigabitEthernet1/0/1 is up, line protocol is up` の段落）
- Cisco NX-OS `show interface brief`
  （Ethernet / VLAN / Type / Mode / Status / Reason / Speed / Port。出典は _read_nxos）
- NEC QX `display interface brief`（Interface / Link / Speed / Duplex / Type / PVID）
- Huawei VRP `display interface brief`（Interface / PHY / Protocol ...。出典は _vrp_state）

出典は Huawei VRP の表だけ確かめた。ほかの表の形は未確認（既存の試験の形のまま）。

状態は書いてあった言葉をそのまま持つ（connected / notconnect / up / down /
disabled / err-disabled）。読めない行は黙って飛ばす。
"""

from __future__ import annotations

import re

from app.services.configparse.base import ParsedConfig, ParsedInterface
from app.services.configparse.ifname import normalize

#: Arista は末尾に Flags が付く。それ以外は同じ並び
_STATUS_HEADER = re.compile(
    r"^\s*Port\s+Name\s+Status\s+Vlan\s+Duplex\s+Speed\s+Type\b", re.IGNORECASE
)
#: Aruba CX `show interface brief`（Port / Native VLAN / Mode / Type / Enabled / Status / ...）
#: 見出しは2行に分かれる（1行目 Port Native Mode Type Enabled Status …、2行目 VLAN …）
_ARUBA_HEADER = re.compile(r"^\s*Port\s+Native\s+Mode\s+Type\s+Enabled\s+Status\b", re.IGNORECASE)
_ARUBA_ROW = re.compile(r"^\s*(\S+)\s+.*?\b(yes|no)\s+(up|down)\b", re.IGNORECASE)
#: PAN-OS `show interface all`。行の3列目が `1000/full/up` の形
_PANOS_HEADER = re.compile(r"^\s*name\s+id\s+speed/duplex/state\b", re.IGNORECASE)
_PANOS_STATE = re.compile(r"^[^/\s]+/[^/\s]+/(up|down)$", re.IGNORECASE)
#: FortiGate `get system interface physical`。`==[port1]` の見出しの下に `status: up`
_FORTI_PHYSICAL = re.compile(r"^\s*==\s*\[\s*(\S+?)\s*\]", re.IGNORECASE)
_FORTI_STATUS = re.compile(r"^\s*status:\s*(up|down)\b", re.IGNORECASE)
#: `show interfaces` の段落の1行目。`X is up, line protocol is up (connected)`
_PARAGRAPH = re.compile(
    r"^(\S+) is (up|down|administratively down),\s*line protocol is (up|down)\b", re.IGNORECASE
)
#: NEC QX / VRP の `display interface`。`GigabitEthernet1/0/1 current state: UP`
_STATE_PARAGRAPH = re.compile(
    r"^(\S+)\s+current state\s*:\s*(UP|DOWN|Administratively DOWN)\b", re.IGNORECASE
)
#: NX-OS `show interface brief`。Ethernet の行は 5 列目が up / down
_NXOS_HEADER = re.compile(r"^\s*Ethernet\s+VLAN\s+Type\s+Mode\s+Status\s+Reason\b", re.IGNORECASE)
#: NEC QX `display interface brief`（Interface / Link / Speed / Duplex / Type / PVID ...）
_UNDOFORM_HEADER = re.compile(
    r"^\s*Interface\s+Link\s+Speed\s+Duplex\s+Type\s+PVID\b", re.IGNORECASE
)
#: VRP の `display interface brief`。物理の状態は PHY の列に出る
_VRP_HEADER = re.compile(r"^\s*Interface\s+PHY\s+Protocol\b", re.IGNORECASE)
#: FastIron の `show interfaces brief`
_FASTIRON_HEADER = re.compile(r"^\s*Port\s+Link\s+State\s+Dupl\b", re.IGNORECASE)
#: アラクサラは1行に `Port 0/1  Link: Up` の形で並べる
_ALAXALA_PORT = re.compile(
    r"^\s*Port\s+(?P<name>[\d/]+)\s+Link\s*:\s*(?P<state>\S+)", re.IGNORECASE
)
_BRIEF_HEADER = re.compile(r"^\s*Interface\s+IP-Address\s+.*\bStatus\s+Protocol\s*$", re.I)
_TERSE_HEADER = re.compile(r"^\s*Interface\s+Admin\s+Link\s+Proto\b", re.I)

#: `show interfaces status` の Status 列に出る言葉。
#: 出典: Interface and Hardware Components Configuration Guide, Cisco IOS XE（Catalyst 9300 /
#: 9400、16.11.x・17.2.x）「Checking Port Status and Connectivity」の出力例（connected、
#: notconnect、err-disabled）。ほかの言葉は既存の試験の形（未確認）
_STATUS_WORDS = (
    "connected",
    "notconnect",
    "notconnec",
    "disabled",
    "err-disabled",
    "inactive",
    "monitoring",
)


def parse(text: str, parsed: ParsedConfig) -> None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _STATUS_HEADER.match(line):
            _read_status(lines[index + 1 :], parsed)
        elif _BRIEF_HEADER.match(line):
            _read_brief(lines[index + 1 :], parsed)
        elif _TERSE_HEADER.match(line):
            _read_terse(lines[index + 1 :], parsed)
        elif _ARUBA_HEADER.match(line):
            _read_aruba(lines[index + 1 :], parsed)
        elif _UNDOFORM_HEADER.match(line):
            _read_undoform(lines[index + 1 :], parsed)
        elif _PANOS_HEADER.match(line):
            _read_panos(lines[index + 1 :], parsed)
        elif _NXOS_HEADER.match(line):
            _read_nxos(lines[index + 1 :], parsed)
        elif _VRP_HEADER.match(line):
            _read_vrp(lines[index + 1 :], parsed)
        elif _FASTIRON_HEADER.match(line):
            _read_fastiron(lines[index + 1 :], parsed)
    _read_alaxala(lines, parsed)
    _read_forti_physical(lines, parsed)
    _read_paragraphs(lines, parsed)
    _read_etherchannel(lines, parsed)
    _read_spanning_tree(lines, parsed)


def _port(parsed: ParsedConfig, name: str) -> ParsedInterface | None:
    """Config 側に無いポートは作らない。show にしか出ないポートは線の材料にならない。"""
    key = normalize(name)
    for interface in parsed.interfaces:
        if interface.key == key:
            return interface
    return None


def _read_status(rows: list[str], parsed: ParsedConfig) -> None:
    for row in rows:
        columns = row.split()
        if len(columns) < 2:
            break
        index = next(
            (i for i, c in enumerate(columns) if i >= 1 and c.lower() in _STATUS_WORDS), None
        )
        if index is None:
            # 知らない言葉（機種によって出る sfpAbsent など）の行でも、Config にある口の行なら
            # 表はまだ続いている。上下は決めずに次の行へ進む。口でなければ表の終わり
            if _port(parsed, columns[0]) is not None:
                continue
            break
        status = columns[index].lower()
        port = _port(parsed, columns[0])
        if port is None:
            continue
        # Name 列にポートの説明が出る。config 側に description が無いときだけ使う。
        # 空白を含むこともあるので、Status 列の手前までをまとめて1つとして読む
        if not port.description and index > 1:
            port.description = " ".join(columns[1:index])
        # 言葉は機種ごとに違うので、up / notconnect / disabled / err-disabled にそろえる
        if status == "connected":
            port.link_state = "up"
        elif status.startswith("notconnec"):
            port.link_state = "notconnect"
        else:
            port.link_state = status


def _read_brief(rows: list[str], parsed: ParsedConfig) -> None:
    """Cisco `show ip interface brief`。

    出典: Cisco IOS Interface and Hardware Component Command Reference（IOS XE 16）の
    show ip interface の Table「show ip interface brief Field Descriptions」（Status は
    up / down / administratively down。Protocol は値の一覧が無い）。一覧に無い値の行でも、
    先頭が Config にある口なら表は続いているとみなし、上下は決めずに次へ進む。
    """
    for row in rows:
        columns = row.split()
        if len(columns) < 4:
            break
        # 末尾2つが Status と Protocol。`administratively down` は2語になる
        protocol = columns[-1].lower()
        status = (
            " ".join(columns[-3:-1]).lower()
            if columns[-2].lower() == "down" and columns[-3].lower() == "administratively"
            else columns[-2].lower()
        )
        if protocol not in ("up", "down") or status not in ("up", "down", "administratively down"):
            if _port(parsed, columns[0]) is not None:
                continue
            break
        port = _port(parsed, columns[0])
        if port is None:
            continue
        if status == "administratively down":
            port.link_state = "disabled"
        else:
            port.link_state = "up" if status == "up" and protocol == "up" else "down"


#: terse の続きの行の先頭。アドレス（`10.0.0.16`、`fe80::1/64`）か family の名前
_TERSE_CONTINUATION = re.compile(
    r"^(?:\d{1,3}(?:\.\d{1,3}){3}(?:/\d+)?"  # IPv4。`ae0.0` のような口の名前は数字で始まらない
    r"|[0-9a-f]*:[0-9a-f:]*(?:/\d+)?"  # IPv6。`xe-0/0/0:1` のような口は - や / を含むので当たらない
    r"|inet6?|mpls|iso|eth-switch|bridge|vpls|ccc|tnp|multiservice)$",
    re.IGNORECASE,
)


def _read_terse(rows: list[str], parsed: ParsedConfig) -> None:
    """Junos `show interfaces terse`。

    出典: Junos OS CLI Reference「show interfaces terse」（Admin は up / down、Link は
    up / down）と、Junos Software CLI Reference の show interfaces のサンプル出力。
    サンプルには Link が `ready` の行（`st0.1  up  ready inet`）と、同じ論理口の2つ目の
    アドレスだけを字下げして並べた行（`10.0.0.16  --> 0/0`）がある。どちらも表の途中なので、
    読みやめない。
    """
    for row in rows:
        columns = row.split()
        if columns and _TERSE_CONTINUATION.match(columns[0]):
            # 同じ論理口の続きの行（アドレスか family だけ）。口の上下は前の行で読んである。
            # 表そのものが字下げされていることがあるので、字下げでは見分けない
            continue
        if len(columns) < 3:
            break
        admin, link = columns[1].lower(), columns[2].lower()
        if admin not in ("up", "down"):
            break
        if link not in ("up", "down"):
            # `ready` などの言葉。口の上下は決めず、表は続ける
            continue
        port = _port(parsed, columns[0].split(".")[0]) or _port(parsed, columns[0])
        if port is None:
            continue
        if admin == "down":
            port.link_state = "disabled"
        elif port.link_state is None or link == "up":
            port.link_state = "up" if link == "up" else "down"


def _read_aruba(rows: list[str], parsed: ParsedConfig) -> None:
    started = False
    for row in rows:
        if not row.strip():
            break
        if row.strip().startswith("-"):
            continue
        found = _ARUBA_ROW.match(row)
        if not found:
            # 見出しの2行目（VLAN / (Mb/s)）は読めない行として飛ばす。
            # 表が始まったあとに読めない行が来たら、そこで表は終わり
            if started:
                break
            continue
        started = True
        port = _port(parsed, found.group(1))
        if port is None:
            continue
        if found.group(2).lower() == "no":
            port.link_state = "disabled"
        else:
            port.link_state = found.group(3).lower()


def _read_undoform(rows: list[str], parsed: ParsedConfig) -> None:
    for row in rows:
        columns = row.split()
        if len(columns) < 2:
            break
        link = columns[1].upper()
        if link not in ("UP", "DOWN", "ADM", "STBY"):
            break
        port = _port(parsed, columns[0])
        if port is None:
            continue
        port.link_state = {"UP": "up", "DOWN": "down", "ADM": "disabled", "STBY": "down"}[link]


#: 表の中の言い回しを、こちらの3つに寄せる
_LINK_WORDS = {
    "up": "up",
    "down": "down",
    "*down": "disabled",
    "administratively": "disabled",
    "adm": "disabled",
    "disabled": "disabled",
    "forward": "up",
    "none": "down",
    "blocked": "up",
}


#: VRP の PHY 列の頭に付く印。`*` は人が止めた、`^` は控え、`~` と `#` はループ検出で止めた
_VRP_MARKS = {"*": "disabled", "^": "down", "~": "err-disabled", "#": "err-disabled"}
#: 末尾の `(s)` `(e)` `(dl)` などの印。線の上下そのものは印の前の語で決まる
_VRP_SUFFIX = re.compile(r"\([a-z]+\)$", re.IGNORECASE)


def _vrp_state(word: str) -> str | None:
    """PHY 列の値を up / down / disabled / err-disabled にそろえる。

    出典: Huawei「Troubleshooting S Series Switch Port and Interface Problems」の
    display interface brief の凡例（*down: administratively down、^down: standby、
    ~down: LDT down、#down: LBDT down、(l) loopback、(s) spoofing、(E) E-Trunk down、
    (b) BFD down、(e) ETHOAM down、(dl) DLDP down、(d) Dampening Suppressed など）。
    """
    base = _VRP_SUFFIX.sub("", word).lower()
    if base[:1] in _VRP_MARKS and base[1:] == "down":
        return _VRP_MARKS[base[:1]]
    return base if base in ("up", "down") else None


def _read_vrp(rows: list[str], parsed: ParsedConfig) -> None:
    """VRP は PHY の列。`*down` は人が止めたという意味で、線の話ではない。

    印の付いた行（`^down`、`up(s)` など）で表の読み取りを止めない。
    """
    for row in rows:
        columns = row.split()
        if len(columns) < 2:
            break
        state = _vrp_state(columns[1])
        if state is None:
            break
        port = _port(parsed, columns[0])
        if port is not None:
            port.link_state = state


def _read_fastiron(rows: list[str], parsed: ParsedConfig) -> None:
    """FastIron は Link の列。State の列は STP の状態なので混ぜない。"""
    for row in rows:
        columns = row.split()
        if len(columns) < 2:
            break
        state = _LINK_WORDS.get(columns[1].lower())
        if state is None:
            break
        # 表は `1/1/1`、config 側は `ethernet 1/1/1`。同じ口なのでどちらでも引く
        port = _port(parsed, columns[0]) or _port(parsed, f"Ethernet{columns[0]}")
        if port is not None:
            port.link_state = state


def _read_alaxala(rows: list[str], parsed: ParsedConfig) -> None:
    """アラクサラは表ではなく `Port 0/1  Link: Up` の形で並べる。"""
    for row in rows:
        found = _ALAXALA_PORT.match(row)
        if not found:
            continue
        state = _LINK_WORDS.get(found.group("state").lower())
        if state is None:
            continue
        port = _port(parsed, f"GigabitEthernet{found.group('name')}")
        if port is not None:
            port.link_state = state


def _read_panos(rows: list[str], parsed: ParsedConfig) -> None:
    for row in rows:
        if row.strip().startswith("-"):
            continue
        columns = row.split()
        if len(columns) < 3:
            break
        found = _PANOS_STATE.match(columns[2])
        if not found:
            break
        port = _port(parsed, columns[0])
        if port is not None:
            port.link_state = found.group(1).lower()


def _read_forti_physical(lines: list[str], parsed: ParsedConfig) -> None:
    """`==[port1]` の見出しごとに、その下の `status:` を読む。表ではなく段落の形。"""
    current: ParsedInterface | None = None
    for raw in lines:
        head = _FORTI_PHYSICAL.match(raw)
        if head:
            current = _port(parsed, head.group(1))
            continue
        if current is None:
            continue
        found = _FORTI_STATUS.match(raw)
        if found:
            current.link_state = found.group(1).lower()
            current = None


#: `31337 input errors, 31337 CRC, ...` の行。数え始めからの累計
# 壊れて届いた数（CRC）。書き方は3通りあるが、どれも「数字 + CRC」か
# 「CRC: 数字」の形で、他の意味には使われない。行の頭に固定すると、
# NEC QX のように途中で改行する出力を取りこぼす
_CRC_LINE = re.compile(r"(?:(?P<crc>\d+)\s+CRC\b|CRC\s*:\s*(?P<colon>\d+))", re.IGNORECASE)


def _read_paragraphs(lines: list[str], parsed: ParsedConfig) -> None:
    """`show interfaces` の段落。状態と、受けたときに壊れていた数を読む。"""
    current: ParsedInterface | None = None
    for raw in lines:
        found = _PARAGRAPH.match(raw.strip())
        if found:
            current = _port(parsed, found.group(1))
            if current is None:
                continue
            admin, protocol = found.group(2).lower(), found.group(3).lower()
            if admin == "administratively down":
                current.link_state = "disabled"
            else:
                current.link_state = "up" if admin == "up" and protocol == "up" else "down"
            continue
        state = _STATE_PARAGRAPH.match(raw.strip())
        if state:
            current = _port(parsed, state.group(1))
            if current is None:
                continue
            word = state.group(2).lower()
            current.link_state = "disabled" if word.startswith("admin") else word
            continue
        if current is None:
            continue
        counters = _CRC_LINE.search(raw.strip())
        if counters:
            found = counters.group("crc") or counters.group("colon")
            current.input_crc_errors = int(found)


def _read_nxos(rows: list[str], parsed: ParsedConfig) -> None:
    """NX-OS `show interface brief` の Ethernet の表。

    出典: Cisco Nexus 5000 / 5600 / 6000 Series Interfaces Command Reference の
    show interface brief の出力例（見出し2行と罫線、Status は up / down、Reason は
    none・Link not connected・SFP not inserted・Configuration Incomplete、
    `Eth1/5.2` の枝の口）。出力例に無い Status の言葉は意味を決めず、表は続ける
    （show interfaces status と同じ扱い）。
    """
    for row in rows:
        stripped = row.strip()
        if stripped.startswith("-") or stripped.startswith("Interface"):
            continue  # 見出しの2行目と罫線
        columns = stripped.split()
        if len(columns) < 5:
            break
        if not columns[0].lower().startswith("eth"):
            break
        status = columns[4].lower()
        if status not in ("up", "down"):
            continue
        port = _port(parsed, columns[0])
        if port is None:
            continue
        # Reason 列に Administratively down と出るものは止めてある口
        reason = " ".join(columns[5:]).lower()
        port.link_state = "disabled" if "administratively" in reason else status


#: `show etherchannel summary` の表。設定してあっても束に入っていないことがある
_ETHERCHANNEL_HEADER = re.compile(r"^\s*Group\s+Port-channel\s+Protocol\s+Ports\s*$", re.IGNORECASE)
_ETHERCHANNEL_ROW = re.compile(
    r"^\s*(?P<group>\d+)\s+(?P<channel>\S+)\s+(?P<protocol>\S+)\s+(?P<ports>.*)$"
)
_MEMBER = re.compile(r"^(?P<name>\S+?)\((?P<flag>[A-Za-z]+)\)$")


def _read_etherchannel(lines: list[str], parsed: ParsedConfig) -> None:
    """束ねた口が、実際に束に入っているかを読む。

    設定だけ見ると束ねてあるように見えても、速度や相手の設定が合わないと
    入らない。入っていない口は帯域に数えられていないので、「倍あるはず」が
    出ていない形になる。印の意味は P / bundled が入っている、それ以外は
    入っていない。
    """
    inside = False
    group: int | None = None
    for line in lines:
        if _ETHERCHANNEL_HEADER.match(line):
            inside, group = True, None
            continue
        if not inside:
            continue
        if not line.strip():
            inside = False
            continue
        if set(line.strip()) <= {"-", "+", " "}:
            continue

        row = _ETHERCHANNEL_ROW.match(line)
        members = ""
        if row:
            group = int(row.group("group"))
            members = row.group("ports")
        elif group is not None and line.startswith(" "):
            members = line  # 口が多いと次の行へ続く
        else:
            continue

        for word in members.split():
            found = _MEMBER.match(word)
            if not found:
                continue
            port = _port(parsed, found.group("name"))
            if port is None:
                continue
            if port.lag_group is None and group is not None:
                port.lag_group = group
            port.lag_bundled = found.group("flag").lower() in {"p", "bndl"}


#: `show spanning-tree` の口ごとの表。塞いでいる口はここでしか分からない
_STP_VLAN = re.compile(r"^VLAN0*(\d{1,4})\s*$")
_STP_TABLE = re.compile(r"^\s*Interface\s+Role\s+Sts\s+Cost\b", re.IGNORECASE)
_STP_ROW = re.compile(r"^\s*(?P<name>\S+)\s+(?P<role>\S+)\s+(?P<state>[A-Z]{3})\b")


def _read_spanning_tree(lines: list[str], parsed: ParsedConfig) -> None:
    """STP が塞いでいる口を読む。

    冗長のために塞いでいるなら正常だが、「線はつながっているのに通らない」の
    原因はここであることが多い。設定からは分からないので、show の出力からしか
    言えない。
    """
    vlan: int | None = None
    inside = False
    for line in lines:
        head = _STP_VLAN.match(line.strip())
        if head:
            vlan, inside = int(head.group(1)), False
            continue
        if _STP_TABLE.match(line):
            inside = True
            continue
        if not inside:
            continue
        if not line.strip() or set(line.strip()) <= {"-", " "}:
            if not line.strip():
                inside = False
            continue
        row = _STP_ROW.match(line)
        if row is None:
            inside = False
            continue
        if row.group("state").upper() != "BLK":
            continue
        port = _port(parsed, row.group("name"))
        if port is None or vlan is None:
            continue
        if vlan not in port.stp_blocked_vlans:
            port.stp_blocked_vlans.append(vlan)
