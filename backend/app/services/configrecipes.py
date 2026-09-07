"""「よくやること」を選んで、値を入れるだけで Config を作る。

いまある生成は、図から起こすもの（design.py）と、条件から構成ごと起こすもの
（blueprint.py）と、資料を根拠に文章から起こすもの（config_draft.py）である。
どれも入口が大きい。「この口をこの VLAN のアクセスにしたいだけ」という
いちばん多い用事に対して、図も台帳も資料も要らない入口が無かった。

ここは、その一番小さい入口にする。台帳を読まない。保存もしない。
選んだ献立と入れた値だけから組み立てる。

### 決めごと

- 機種ごとの書き方は、既にこのリポジトリで出している書き方に合わせる。
  推測で新しい言い回しを作らない
- 書けない機種は、それらしい行を出さずに「この機種では出せません」と言う。
  無いのと読めていないのを混ぜない、と同じ考え方
- 値は必ず確かめる。VLAN 番号の範囲、住所の形。おかしな値のまま
  それらしい Config を出すと、投入して初めて気づくことになる
- 判断が要るところ（タグ無し VLAN の番号など）は、埋めずに残す
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.services.configparse.base import Vendor

#: 人が埋めるところの印。design.py と同じ印にして、読む人が迷わないようにする
TODO = "（要記入）"

#: 番号を持てる VLAN の範囲
_VLAN_MIN, _VLAN_MAX = 1, 4094


class RecipeError(ValueError):
    """値がおかしい、またはその機種では出せない。"""


@dataclass(frozen=True)
class Field:
    """入力欄1つ。画面はこれを並べるだけでよい。"""

    name: str
    label: str
    kind: str  # text / number / vlan_list / ip / choice
    required: bool = True
    help: str = ""
    placeholder: str = ""


@dataclass(frozen=True)
class Recipe:
    """献立1つ。"""

    name: str
    label: str
    why: str
    fields: tuple[Field, ...]
    render: Callable[[Vendor, dict[str, str]], list[str]]


@dataclass
class Rendered:
    """組み立てた結果。"""

    vendor: Vendor
    body: str
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 機種の分類。design.py で実際に出している書き方に合わせる
# ---------------------------------------------------------------------------

_IOS_LIKE = (Vendor.CISCO_IOS, Vendor.CISCO_NXOS, Vendor.ARISTA)
_COMMENT = {
    Vendor.NEC_QX: "#",
    Vendor.HUAWEI: "#",
    Vendor.JUNIPER: "#",
}


def comment_of(vendor: Vendor) -> str:
    """その機種で、注記を書くときの記号。"""
    return _COMMENT.get(vendor, "!")


#: AX は種別と番号を空けて書く。design.py と同じ直し方をする
_AX_PORT = re.compile(r"^([A-Za-z-]+?)\s*([\d/.:]+)$")
#: FastIron は番号だけで書く
_ICX_PORT = re.compile(r"^[A-Za-z-]*\s*([\d/]+)$")


def _ax_port(name: str) -> str:
    found = _AX_PORT.match(name.strip())
    return f"{found.group(1).lower()} {found.group(2)}" if found else name


def _icx_port(name: str) -> str:
    found = _ICX_PORT.match(name.strip())
    return found.group(1) if found else name


def _unsupported(vendor: Vendor, what: str) -> RecipeError:
    return RecipeError(f"{what}は、この機種（{vendor}）では出せません")


# ---------------------------------------------------------------------------
# 値の確かめ
# ---------------------------------------------------------------------------


def _text(values: dict[str, str], key: str, label: str) -> str:
    got = (values.get(key) or "").strip()
    if not got:
        raise RecipeError(f"{label}を入れてください")
    return got


def _vlan(values: dict[str, str], key: str = "vlan_id", label: str = "VLAN 番号") -> int:
    raw = _text(values, key, label)
    try:
        number = int(raw)
    except ValueError as error:
        raise RecipeError(f"{label}は数字で入れてください（{raw}）") from error
    if not _VLAN_MIN <= number <= _VLAN_MAX:
        raise RecipeError(f"{label}は {_VLAN_MIN}〜{_VLAN_MAX} の範囲です（{number}）")
    return number


def _vlan_list(values: dict[str, str], key: str, label: str) -> list[int]:
    raw = _text(values, key, label)
    numbers: list[int] = []
    for part in re.split(r"[,\s]+", raw):
        if not part:
            continue
        numbers.extend(_expand(part, label))
    if not numbers:
        raise RecipeError(f"{label}を入れてください")
    # 重複を落として並べ直す。同じ番号が2回書いてあっても意味は同じ
    return sorted(set(numbers))


def _expand(part: str, label: str) -> list[int]:
    """`10-12` のような書き方をほどく。現場ではこの書き方で渡ってくる。"""
    found = re.fullmatch(r"(\d+)-(\d+)", part)
    if found:
        start, end = int(found.group(1)), int(found.group(2))
        if start > end:
            raise RecipeError(f"{label}の範囲が逆です（{part}）")
        if not (_VLAN_MIN <= start and end <= _VLAN_MAX):
            raise RecipeError(f"{label}は {_VLAN_MIN}〜{_VLAN_MAX} の範囲です（{part}）")
        return list(range(start, end + 1))
    return [_vlan({label: part}, label, label)]


def _address(values: dict[str, str], key: str, label: str) -> ipaddress.IPv4Address:
    raw = _text(values, key, label)
    try:
        return ipaddress.IPv4Address(raw)
    except ValueError as error:
        raise RecipeError(f"{label}が住所の形になっていません（{raw}）") from error


def _network(values: dict[str, str], key: str, label: str) -> ipaddress.IPv4Network:
    raw = _text(values, key, label)
    try:
        return ipaddress.ip_network(raw, strict=False)
    except ValueError as error:
        raise RecipeError(f"{label}が区画の形になっていません（{raw}）") from error


def _prefixlen(values: dict[str, str], key: str = "prefixlen") -> int:
    raw = _text(values, key, "マスク長")
    try:
        length = int(raw)
    except ValueError as error:
        raise RecipeError(f"マスク長は数字で入れてください（{raw}）") from error
    if not 1 <= length <= 32:
        raise RecipeError(f"マスク長は 1〜32 の範囲です（{length}）")
    return length


def _netmask(prefixlen: int) -> str:
    return str(ipaddress.IPv4Network(f"0.0.0.0/{prefixlen}").netmask)


def _name(values: dict[str, str], key: str, label: str) -> str:
    """VLAN 名のように、機器がそのまま持つ名前。空白と記号を弾く。"""
    got = _text(values, key, label)
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", got):
        raise RecipeError(f"{label}は英数字と _ - . で入れてください（{got}）")
    return got


# ---------------------------------------------------------------------------
# 献立
# ---------------------------------------------------------------------------


def _make_vlan(vendor: Vendor, values: dict[str, str]) -> list[str]:
    vlan_id = _vlan(values)
    name = _name(values, "name", "VLAN 名")

    if vendor in _IOS_LIKE:
        return [f"vlan {vlan_id}", f" name {name}"]
    if vendor is Vendor.ALAXALA:
        return [f"vlan {vlan_id}", f'  name "{name}"']
    if vendor is Vendor.ALLIED:
        return ["vlan database", f" vlan {vlan_id} name {name}"]
    if vendor is Vendor.ARUBA_CX:
        return [f"vlan {vlan_id}", f"    name {name}"]
    if vendor is Vendor.NEC_QX:
        return [f"vlan {vlan_id}", f" name {name}"]
    if vendor is Vendor.HUAWEI:
        # VRP は VLAN の名前を description で持つ
        return [f"vlan {vlan_id}", f" description {name}"]
    if vendor is Vendor.JUNIPER:
        return [f"set vlans {name} vlan-id {vlan_id}"]
    if vendor is Vendor.FASTIRON:
        return [
            f"vlan {vlan_id} name {name} by port",
            f"! この VLAN に載せる口: {TODO}（untagged ethe / tagged ethe）",
        ]
    raise _unsupported(vendor, "VLAN を作る設定")


def _access_port(vendor: Vendor, values: dict[str, str]) -> list[str]:
    port = _text(values, "interface", "ポート名")
    vlan_id = _vlan(values)
    description = (values.get("description") or "").strip()

    if vendor in _IOS_LIKE:
        lines = [f"interface {port}"]
        if description:
            lines.append(f" description {description}")
        lines += [
            " switchport mode access",
            f" switchport access vlan {vlan_id}",
            # 端末を挿す口なので、上がるのを待たせない・向こうの木を信じない
            " spanning-tree portfast",
            " spanning-tree bpduguard enable",
        ]
        return lines
    if vendor is Vendor.ALLIED:
        lines = [f"interface {port}"]
        if description:
            lines.append(f" description {description}")
        return lines + [
            " switchport",
            " switchport mode access",
            f" switchport access vlan {vlan_id}",
            " spanning-tree portfast",
            " spanning-tree portfast bpdu-guard enable",
        ]
    if vendor is Vendor.ALAXALA:
        lines = [f"interface {_ax_port(port)}"]
        if description:
            lines.append(f'  description "{description}"')
        return lines + [
            "  switchport mode access",
            f"  switchport access vlan {vlan_id}",
            "  spanning-tree portfast",
        ]
    if vendor is Vendor.ARUBA_CX:
        lines = [f"interface {port}", "    no shutdown"]
        if description:
            lines.append(f"    description {description}")
        return lines + [
            "    no routing",
            f"    vlan access {vlan_id}",
            "    spanning-tree port-type admin-edge",
            "    spanning-tree bpdu-guard",
        ]
    if vendor is Vendor.NEC_QX:
        lines = [f"interface {port}"]
        if description:
            lines.append(f" description {description}")
        return lines + [
            " port link-type access",
            f" port access vlan {vlan_id}",
            " stp edged-port",
        ]
    if vendor is Vendor.HUAWEI:
        lines = [f"interface {port}"]
        if description:
            lines.append(f" description {description}")
        return lines + [
            " port link-type access",
            f" port default vlan {vlan_id}",
            " stp edged-port enable",
        ]
    if vendor is Vendor.JUNIPER:
        lines = []
        if description:
            lines.append(f'set interfaces {port} description "{description}"')
        return lines + [
            f"set interfaces {port} unit 0 family ethernet-switching interface-mode access",
            f"set interfaces {port} unit 0 family ethernet-switching vlan members {vlan_id}",
        ]
    if vendor is Vendor.FASTIRON:
        # 口の下に VLAN を書く形ではなく、VLAN の下に口を並べる
        lines = [f"vlan {vlan_id}", f" untagged ethe {_icx_port(port)}"]
        if description:
            lines += ["!", f"interface ethernet {_icx_port(port)}", f" port-name {description}"]
        return lines
    raise _unsupported(vendor, "アクセスポートの設定")


def _trunk_port(vendor: Vendor, values: dict[str, str]) -> list[str]:
    port = _text(values, "interface", "ポート名")
    allowed = _vlan_list(values, "allowed", "通す VLAN")
    native = values.get("native_vlan", "").strip()
    native_id = _vlan(values, "native_vlan", "タグ無し VLAN") if native else None
    if native_id is not None and native_id in allowed:
        # タグ付きとタグ無しに同じ番号を書くと、機種によって解釈が割れる
        raise RecipeError(
            f"タグ無し VLAN（{native_id}）を、通す VLAN にも書いています。どちらかにしてください"
        )
    comma = ",".join(str(v) for v in allowed)
    spaced = " ".join(str(v) for v in allowed)

    if vendor in _IOS_LIKE:
        lines = [f"interface {port}"]
        if vendor is Vendor.CISCO_IOS:
            # 機種によっては方式の指定が要る。NX-OS では受け付けない
            lines.append(" switchport trunk encapsulation dot1q")
        lines += [" switchport mode trunk", f" switchport trunk allowed vlan {comma}"]
        lines.append(
            f" switchport trunk native vlan {native_id}"
            if native_id is not None
            else f"! タグ無し VLAN（native）: {TODO}（両端で同じ番号にする）"
        )
        return lines
    if vendor is Vendor.ALLIED:
        lines = [
            f"interface {port}",
            " switchport",
            " switchport mode trunk",
            f" switchport trunk allowed vlan add {comma}",
        ]
        lines.append(
            f" switchport trunk native vlan {native_id}"
            if native_id is not None
            else f"! タグ無し VLAN（native）: {TODO}（両端で同じ番号にする）"
        )
        return lines
    if vendor is Vendor.ALAXALA:
        lines = [
            f"interface {_ax_port(port)}",
            "  switchport mode trunk",
            f"  switchport trunk allowed vlan {comma}",
        ]
        lines.append(
            f"  switchport trunk native vlan {native_id}"
            if native_id is not None
            else f"! タグ無し VLAN（native）: {TODO}（両端で同じ番号にする）"
        )
        return lines
    if vendor is Vendor.ARUBA_CX:
        lines = [f"interface {port}", "    no shutdown", "    no routing"]
        lines.append(f"    vlan trunk allowed {comma}")
        lines.append(
            f"    vlan trunk native {native_id}"
            if native_id is not None
            else f"! タグ無し VLAN（native）: {TODO}（両端で同じ番号にする）"
        )
        return lines
    if vendor is Vendor.NEC_QX:
        lines = [f"interface {port}", " port link-type trunk", f" port trunk permit vlan {spaced}"]
        lines.append(
            f" port trunk pvid vlan {native_id}"
            if native_id is not None
            else f"# タグ無し VLAN（PVID）: {TODO}（両端で同じ番号にする）"
        )
        return lines
    if vendor is Vendor.HUAWEI:
        lines = [
            f"interface {port}",
            " port link-type trunk",
            f" port trunk allow-pass vlan {spaced}",
        ]
        lines.append(
            f" port trunk pvid vlan {native_id}"
            if native_id is not None
            else f"# タグ無し VLAN（PVID）: {TODO}（両端で同じ番号にする）"
        )
        return lines
    if vendor is Vendor.JUNIPER:
        members = " ".join(str(v) for v in allowed)
        lines = [
            f"set interfaces {port} unit 0 family ethernet-switching interface-mode trunk",
            f"set interfaces {port} unit 0 family ethernet-switching vlan members [ {members} ]",
        ]
        lines.append(
            f"set interfaces {port} native-vlan-id {native_id}"
            if native_id is not None
            else f"# タグ無し VLAN（native-vlan-id）: {TODO}（両端で同じ番号にする）"
        )
        return lines
    if vendor is Vendor.FASTIRON:
        lines: list[str] = []
        for vlan_id in allowed:
            lines += [f"vlan {vlan_id}", f" tagged ethe {_icx_port(port)}", "!"]
        if native_id is not None:
            lines += [f"vlan {native_id}", f" untagged ethe {_icx_port(port)}"]
        else:
            lines.append(f"! タグ無し VLAN: {TODO}（その VLAN に untagged ethe で書く）")
        return lines
    raise _unsupported(vendor, "トランクの設定")


def _svi(vendor: Vendor, values: dict[str, str]) -> list[str]:
    vlan_id = _vlan(values)
    address = _address(values, "ip", "IP アドレス")
    length = _prefixlen(values)
    mask = _netmask(length)

    if vendor in _IOS_LIKE or vendor is Vendor.ALLIED:
        return [
            f"interface Vlan{vlan_id}",
            f" ip address {address} {mask}",
            " no shutdown",
        ]
    if vendor is Vendor.ALAXALA:
        return [f"interface vlan {vlan_id}", f"  ip address {address} {mask}"]
    if vendor is Vendor.ARUBA_CX:
        return [f"interface vlan {vlan_id}", f"    ip address {address}/{length}"]
    if vendor is Vendor.NEC_QX:
        return [f"interface Vlan-interface{vlan_id}", f" ip address {address} {length}"]
    if vendor is Vendor.HUAWEI:
        return [f"interface Vlanif{vlan_id}", f" ip address {address} {mask}"]
    if vendor is Vendor.JUNIPER:
        return [f"set interfaces irb unit {vlan_id} family inet address {address}/{length}"]
    if vendor is Vendor.FASTIRON:
        return [f"interface ve {vlan_id}", f" ip address {address} {mask}"]
    raise _unsupported(vendor, "VLAN に住所を付ける設定")


def _static_route(vendor: Vendor, values: dict[str, str]) -> list[str]:
    destination = _network(values, "destination", "宛先の区画")
    next_hop = _address(values, "next_hop", "次に渡す先")

    if vendor in _IOS_LIKE or vendor in (Vendor.ALLIED, Vendor.ALAXALA):
        return [f"ip route {destination.network_address} {destination.netmask} {next_hop}"]
    if vendor is Vendor.ARUBA_CX:
        return [f"ip route {destination} {next_hop}"]
    if vendor in (Vendor.NEC_QX, Vendor.HUAWEI):
        return [f"ip route-static {destination.network_address} {destination.netmask} {next_hop}"]
    if vendor is Vendor.JUNIPER:
        return [f"set routing-options static route {destination} next-hop {next_hop}"]
    if vendor is Vendor.FASTIRON:
        return [f"ip route {destination.network_address} {destination.netmask} {next_hop}"]
    if vendor is Vendor.NEC_IX:
        return [f"ip route {destination} {next_hop}"]
    if vendor is Vendor.YAMAHA:
        target = "default" if destination.prefixlen == 0 else str(destination)
        return [f"ip route {target} gateway {next_hop}"]
    raise _unsupported(vendor, "経路の設定")


def _logging(vendor: Vendor, values: dict[str, str]) -> list[str]:
    syslog = _address(values, "syslog_host", "ログの送り先")
    ntp = _address(values, "ntp_host", "時刻の参照先")

    if vendor in _IOS_LIKE or vendor is Vendor.ALLIED:
        return [
            f"logging host {syslog}",
            "logging trap informational",
            "service timestamps log datetime msec localtime show-timezone",
            f"ntp server {ntp}",
        ]
    if vendor is Vendor.ALAXALA:
        return [f"logging host {syslog}", f"ntp server {ntp}"]
    if vendor is Vendor.ARUBA_CX:
        return [f"logging {syslog}", f"ntp server {ntp}"]
    if vendor is Vendor.NEC_QX:
        return [
            "info-center enable",
            f"info-center loghost {syslog}",
            f"ntp-service unicast-server {ntp}",
        ]
    if vendor is Vendor.HUAWEI:
        return [
            "info-center enable",
            f"info-center loghost {syslog}",
            f"ntp-service unicast-server {ntp}",
        ]
    if vendor is Vendor.JUNIPER:
        return [
            f"set system syslog host {syslog} any notice",
            f"set system ntp server {ntp}",
        ]
    if vendor is Vendor.FASTIRON:
        return [f"logging host {syslog}", f"ntp server {ntp}"]
    if vendor is Vendor.YAMAHA:
        return [f"syslog host {syslog}", f"ntpdate {ntp}"]
    raise _unsupported(vendor, "ログと時刻の設定")


def _hardening(vendor: Vendor, values: dict[str, str]) -> list[str]:
    """入口を ssh だけにする。値を持たないので、選ぶだけで出せる。"""
    if vendor in _IOS_LIKE:
        return [
            "service password-encryption",
            "no ip http server",
            "no ip http secure-server",
            "ip ssh version 2",
            "line vty 0 15",
            " transport input ssh",
            " exec-timeout 10 0",
        ]
    if vendor is Vendor.ALLIED:
        return ["service password-encryption", "no service telnet", "service ssh"]
    if vendor is Vendor.ALAXALA:
        return ["line vty 0 15", "  transport input ssh"]
    if vendor is Vendor.ARUBA_CX:
        return ["ssh server vrf default", "no telnet server vrf default"]
    if vendor is Vendor.NEC_QX:
        return ["line vty 0 63", " protocol inbound ssh"]
    if vendor is Vendor.HUAWEI:
        return ["undo telnet server enable", "user-interface vty 0 4", " protocol inbound ssh"]
    if vendor is Vendor.JUNIPER:
        return ["set system services ssh protocol-version v2", "delete system services telnet"]
    if vendor is Vendor.FASTIRON:
        return ["no telnet server", "crypto key generate rsa modulus 2048"]
    if vendor is Vendor.YAMAHA:
        return ["telnetd service off", "sshd service on"]
    raise _unsupported(vendor, "管理の入口を絞る設定")


RECIPES: tuple[Recipe, ...] = (
    Recipe(
        name="vlan",
        label="VLAN を作る",
        why="番号と名前だけで作れる。名前を付けておかないと、後から用途が分からなくなる。",
        fields=(
            Field("vlan_id", "VLAN 番号", "number", placeholder="10"),
            Field("name", "VLAN 名", "text", placeholder="USERS", help="英数字と _ - . のみ"),
        ),
        render=_make_vlan,
    ),
    Recipe(
        name="access_port",
        label="端末をつなぐ口にする",
        why="いちばん多い用事。端末側の口なので、上がるのを待たせない設定も一緒に入れる。",
        fields=(
            Field("interface", "ポート名", "text", placeholder="GigabitEthernet0/5"),
            Field("vlan_id", "載せる VLAN", "number", placeholder="10"),
            Field("description", "説明", "text", required=False, placeholder="執務室"),
        ),
        render=_access_port,
    ),
    Recipe(
        name="trunk_port",
        label="機器どうしをつなぐ口にする",
        why="通す VLAN を書き漏らすと、その VLAN だけ通らない。タグ無しは両端で同じにする。",
        fields=(
            Field("interface", "ポート名", "text", placeholder="GigabitEthernet0/1"),
            Field("allowed", "通す VLAN", "vlan_list", placeholder="10,30,40-42"),
            Field(
                "native_vlan",
                "タグ無し VLAN",
                "number",
                required=False,
                placeholder="999",
                help="空なら、決めるところとして注記に残す",
            ),
        ),
        render=_trunk_port,
    ),
    Recipe(
        name="svi",
        label="VLAN に住所を付ける",
        why="その VLAN の出口を作る。ここを作らないと、VLAN をまたいだ通信ができない。",
        fields=(
            Field("vlan_id", "VLAN 番号", "number", placeholder="30"),
            Field("ip", "IP アドレス", "ip", placeholder="10.0.0.21"),
            Field("prefixlen", "マスク長", "number", placeholder="24"),
        ),
        render=_svi,
    ),
    Recipe(
        name="static_route",
        label="経路を1本足す",
        why="宛先と、次に渡す先だけで書ける。既定の経路は 0.0.0.0/0 と書く。",
        fields=(
            Field("destination", "宛先の区画", "text", placeholder="0.0.0.0/0"),
            Field("next_hop", "次に渡す先", "ip", placeholder="10.0.0.1"),
        ),
        render=_static_route,
    ),
    Recipe(
        name="logging",
        label="ログと時刻の送り先を入れる",
        why="ここが機器ごとにばらつくと、後から何が起きたかを追えなくなる。",
        fields=(
            Field("syslog_host", "ログの送り先", "ip", placeholder="10.10.20.20"),
            Field("ntp_host", "時刻の参照先", "ip", placeholder="10.10.20.10"),
        ),
        render=_logging,
    ),
    Recipe(
        name="hardening",
        label="管理の入口を絞る",
        why="telnet が開いたままの機器が現場に残りやすい。値を入れずに出せる。",
        fields=(),
        render=_hardening,
    ),
)

_BY_NAME = {recipe.name: recipe for recipe in RECIPES}


def get(name: str) -> Recipe:
    recipe = _BY_NAME.get(name)
    if recipe is None:
        raise RecipeError(f"知らない献立です: {name}")
    return recipe


def vendors_for(recipe: Recipe) -> list[Vendor]:
    """その献立を出せる機種。画面で選べる機種を絞るために使う。"""
    found: list[Vendor] = []
    for vendor in Vendor:
        if vendor is Vendor.UNKNOWN:
            continue
        try:
            recipe.render(vendor, _SAMPLE)
        except RecipeError as error:
            if "この機種" in str(error):
                continue
            found.append(vendor)  # 値の話なので、機種としては出せる
        else:
            found.append(vendor)
    return found


#: 出せる機種を数えるためだけの値。ここで出した Config は使わない
_SAMPLE = {
    "vlan_id": "10",
    "name": "SAMPLE",
    "interface": "GigabitEthernet0/1",
    "allowed": "10",
    "ip": "10.0.0.1",
    "prefixlen": "24",
    "destination": "0.0.0.0/0",
    "next_hop": "10.0.0.254",
    "syslog_host": "10.0.0.9",
    "ntp_host": "10.0.0.8",
}


def render(name: str, vendor: Vendor, values: dict[str, str]) -> Rendered:
    """献立と値から Config を組み立てる。台帳は読まないし、保存もしない。"""
    recipe = get(name)
    lines = recipe.render(vendor, values)
    mark = comment_of(vendor)
    header = [
        f"{mark} 「{recipe.label}」から起こした設定です。投入前に内容を確認してください。",
        mark,
    ]
    notes = [note for note in (f"{recipe.why}",) if note]
    if any(TODO in line for line in lines):
        notes.append("（要記入）と書いた行は、決めてから入れてください。")
    return Rendered(vendor=vendor, body="\n".join(header + lines) + "\n", notes=notes)
