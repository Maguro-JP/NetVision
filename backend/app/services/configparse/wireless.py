"""無線（SSID と AP）の設定を読む。

有線は線を抜けば切れるが、電波は建物の外まで届く。だから「どの守り方で、
どの VLAN へ落としているか」が、そのまま入口の強さになる。ここはその
読み取りだけを行い、良し悪しは `wirelessreview.py` で言う。

読める書き方は、実際によく持ち込まれるものに絞ってある。

- Cisco IOS-XE（Catalyst 9800 / 組み込みコントローラ）の `wlan` ブロック
- Cisco AireOS の `config wlan ...` 形式
- Aruba Instant の `wlan ssid-profile`
- FortiGate の `config wireless-controller vap`

合言葉そのものは持たない。資料に載せるものではないので、置いてあるか、
伏せ字になっていないか、だけを見る。
"""

from __future__ import annotations

import re

from app.services.configparse.base import ParsedWireless, ParsedWirelessAp, ParsedWlan

# --- Cisco IOS-XE -----------------------------------------------------------
_IOSXE_WLAN = re.compile(r"^wlan\s+(\S+)\s+(\d+)\s+(.+?)\s*$", re.IGNORECASE)
_IOSXE_POLICY = re.compile(r"^wireless\s+profile\s+policy\s+(\S+)", re.IGNORECASE)
_IOSXE_TAG = re.compile(r"^wireless\s+tag\s+policy\s+(\S+)", re.IGNORECASE)
_IOSXE_TAG_MAP = re.compile(
    r"^\s*wlan\s+(\S+)\s+policy\s+(\S+)\s*$",
    re.IGNORECASE,
)
#: Catalyst 9800 は `wireless country JP`、AireOS は `country JP`、組み込みは `ap country JP`
_COUNTRY = re.compile(r"^(?:wireless\s+|ap\s+)?(?:dot11\s+)?country\s+(\S+)", re.IGNORECASE)

# --- AireOS -----------------------------------------------------------------
_AIRE_CREATE = re.compile(r"^config\s+wlan\s+create\s+(\d+)\s+(\S+)\s+(\S+)", re.IGNORECASE)
_AIRE_SET = re.compile(r"^config\s+wlan\s+(\S+)\s+(.+?)\s+(\d+)\s*(.*)$", re.IGNORECASE)

# --- Aruba Instant ----------------------------------------------------------
_ARUBA_SSID = re.compile(r"^wlan\s+ssid-profile\s+(\S+)", re.IGNORECASE)

# --- FortiGate --------------------------------------------------------------
_FORTI_VAP = re.compile(r"^\s*config\s+wireless-controller\s+vap\s*$", re.IGNORECASE)
_FORTI_EDIT = re.compile(r'^\s*edit\s+"?([^"\s]+)"?', re.IGNORECASE)
_FORTI_SET = re.compile(r"^\s*set\s+(\S+)\s+(.+?)\s*$", re.IGNORECASE)

#: 見つかったら「電波の設定がある」と見なす手掛かり
_HINTS = (
    "wlan ",
    "wireless profile policy",
    "wireless-controller vap",
    "ssid-profile",
    "config wlan create",
    "wireless-controller wtp",
    "ap database",
)


def parse(text: str) -> ParsedWireless | None:
    """無線の設定を読む。それらしい行が1つも無ければ None。

    None と「空の ParsedWireless」は意味が違う。前者は無線機ではない、
    後者は無線機だが SSID を1つも読めなかった、である。
    """
    lowered = text.lower()
    if not any(hint in lowered for hint in _HINTS):
        return None

    found = ParsedWireless()
    _iosxe(text, found)
    _aireos(text, found)
    _aruba(text, found)
    _fortinet(text, found)
    _common(text, found)
    if not found.wlans and not found.aps and found.country is None:
        return None
    return found


def _new(found: ParsedWireless, name: str) -> ParsedWlan:
    for wlan in found.wlans:
        if wlan.name == name:
            return wlan
    wlan = ParsedWlan(name=name)
    found.wlans.append(wlan)
    return wlan


def _add(values: list[str], item: str) -> None:
    if item and item not in values:
        values.append(item)


# ---------------------------------------------------------------------------
# Cisco IOS-XE（Catalyst 9800）
# ---------------------------------------------------------------------------


def _iosxe(text: str, found: ParsedWireless) -> None:
    """`wlan <profile> <id> <ssid>` のブロックと、落とし先の VLAN を読む。

    IOS-XE では SSID と VLAN が別のところ（policy profile）に書いてあり、
    それをタグで結ぶ。3つを突き合わせないと「どの VLAN へ落ちるか」が
    分からないので、ここでまとめて解く。
    """
    policy_vlan: dict[str, int] = {}
    wlan_to_policy: dict[str, str] = {}

    current: ParsedWlan | None = None
    policy: str | None = None
    tag_lines = False

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        indented = line.startswith((" ", "\t"))
        body = line.strip()

        if not indented:
            head = _IOSXE_WLAN.match(body)
            current, policy, tag_lines = None, None, False
            if head:
                current = _new(found, head.group(1))
                current.ssid = head.group(3).strip()
                current.raw = body
                # 既定は「作った時点では止まっている」。no shutdown で上がる
                current.enabled = False
                continue
            in_policy = _IOSXE_POLICY.match(body)
            if in_policy:
                policy = in_policy.group(1)
                continue
            if _IOSXE_TAG.match(body):
                tag_lines = True
            continue

        if tag_lines:
            pair = _IOSXE_TAG_MAP.match(line)
            if pair:
                wlan_to_policy[pair.group(1)] = pair.group(2)
            continue

        if policy is not None:
            vlan = re.match(r"^vlan\s+(\d+)$", body, re.IGNORECASE)
            if vlan:
                policy_vlan[policy] = int(vlan.group(1))
            continue

        if current is not None:
            _iosxe_option(current, body)

    for wlan in found.wlans:
        if wlan.vlan is None:
            vlan = policy_vlan.get(wlan_to_policy.get(wlan.name, ""))
            if vlan is not None:
                wlan.vlan = vlan


def _iosxe_option(wlan: ParsedWlan, body: str) -> None:
    lowered = body.lower()
    negated = lowered.startswith("no ")
    rest = lowered[3:] if negated else lowered

    if rest in ("shutdown", "shut"):
        wlan.enabled = negated
        return
    if rest.startswith("broadcast-ssid"):
        wlan.broadcast = not negated
        return
    if rest.startswith("client vlan"):
        vlan = re.search(r"client vlan\s+(\d+)", rest)
        if vlan:
            wlan.vlan = int(vlan.group(1))
        return
    if rest.startswith("security wpa wpa2"):
        _flag(wlan, "wpa2", negated)
        return
    if rest.startswith("security wpa wpa3"):
        _flag(wlan, "wpa3", negated)
        return
    if rest.startswith("security wpa akm"):
        for token, name in (("psk", "psk"), ("dot1x", "dot1x"), ("sae", "sae"), ("owe", "owe")):
            if re.search(rf"\b{token}\b", rest):
                _flag(wlan, name, negated, where="akm")
        return
    if "ciphers aes" in rest or "ccmp" in rest:
        _flag(wlan, "aes", negated, where="cipher")
        return
    if "ciphers tkip" in rest or "tkip" in rest:
        _flag(wlan, "tkip", negated, where="cipher")
        return
    if rest.startswith("security wpa psk set-key"):
        wlan.has_psk = True
        # 0 は素のまま、それ以外（6 / 8）は伏せてある
        wlan.psk_in_clear = bool(re.search(r"set-key\s+(?:ascii|hex)\s+0\s", rest))
        return
    if rest.startswith("security static-wep-key") or rest.startswith("security wep"):
        _add(wlan.security, "wep")
        return
    if rest.startswith("security dot1x authentication-list"):
        wlan.auth_list = body.split()[-1]
        _add(wlan.akm, "dot1x")
        return
    if rest.startswith("security pmf"):
        if "mandatory" in rest or "required" in rest:
            wlan.pmf = "required"
        elif "optional" in rest:
            wlan.pmf = "optional"
        elif negated:
            wlan.pmf = "disabled"
        return
    if rest.startswith("no security wpa"):
        _add(wlan.security, "open")
        return
    if rest.startswith("radio policy dot11"):
        for band, name in (("24ghz", "2.4"), ("5ghz", "5"), ("6ghz", "6")):
            if band in rest:
                _add(wlan.bands, name)
        return
    if rest.startswith("peer-blocking") or "client-isolation" in rest:
        wlan.client_isolation = not negated
        return
    limit = re.match(r"^(?:max-associated-clients|client\s+association\s+limit)\s+(\d+)$", rest)
    if limit:
        wlan.client_limit = int(limit.group(1))


def _flag(wlan: ParsedWlan, name: str, negated: bool, where: str = "security") -> None:
    target = {
        "security": wlan.security,
        "akm": wlan.akm,
        "cipher": wlan.ciphers,
    }[where]
    if negated:
        if name in target:
            target.remove(name)
        return
    _add(target, name)


# ---------------------------------------------------------------------------
# Cisco AireOS
# ---------------------------------------------------------------------------


def _aireos(text: str, found: ParsedWireless) -> None:
    """`config wlan ...` の並び。番号で SSID を指す書き方を解く。"""
    by_id: dict[str, ParsedWlan] = {}
    for raw in text.splitlines():
        body = raw.strip()
        create = _AIRE_CREATE.match(body)
        if create:
            wlan = _new(found, create.group(2))
            wlan.ssid = create.group(3)
            wlan.enabled = False
            wlan.raw = body
            by_id[create.group(1)] = wlan
            continue
        item = _AIRE_SET.match(body)
        if not item:
            continue
        what, wlan_id, value = item.group(2).lower(), item.group(3), item.group(4).strip()
        wlan = by_id.get(wlan_id)
        if wlan is None:
            continue
        _aireos_option(wlan, item.group(1).lower(), what, value)


def _aireos_option(wlan: ParsedWlan, verb: str, what: str, value: str) -> None:
    lowered = f"{verb} {what} {value}".strip().lower()
    if verb == "enable":
        wlan.enabled = True
    elif verb == "disable":
        wlan.enabled = False
    elif verb == "broadcast-ssid":
        wlan.broadcast = what != "disable" and "disable" not in lowered
    elif verb == "security":
        if "wpa2 enable" in lowered or what.startswith("wpa2"):
            _add(wlan.security, "wpa2")
        if "wpa3" in lowered:
            _add(wlan.security, "wpa3")
        if "wep" in lowered:
            _add(wlan.security, "wep")
        if "psk enable" in lowered or "akm psk" in lowered:
            _add(wlan.akm, "psk")
        if "802.1x enable" in lowered or "dot1x" in lowered:
            _add(wlan.akm, "dot1x")
        if "set-key" in lowered or "psk set" in lowered:
            wlan.has_psk = True
            wlan.psk_in_clear = True  # AireOS の設定行は素のまま出る
        if "tkip" in lowered:
            _add(wlan.ciphers, "tkip")
        if "aes" in lowered or "ccmp" in lowered:
            _add(wlan.ciphers, "aes")
        if "none" in lowered or "disable" in lowered and "wpa" in lowered:
            _add(wlan.security, "open")
    elif verb == "interface":
        vlan = re.search(r"vlan[-_ ]?(\d+)", lowered)
        if vlan:
            wlan.vlan = int(vlan.group(1))


# ---------------------------------------------------------------------------
# Aruba Instant
# ---------------------------------------------------------------------------

_ARUBA_OPMODE = {
    "opensystem": ("open", None, None),
    "wpa2-psk-aes": ("wpa2", "psk", "aes"),
    "wpa2-aes": ("wpa2", "dot1x", "aes"),
    "wpa3-sae-aes": ("wpa3", "sae", "aes"),
    "wpa3-aes-ccm-128": ("wpa3", "dot1x", "aes"),
    "wpa-psk-tkip": ("wpa", "psk", "tkip"),
    "wpa-tkip": ("wpa", "dot1x", "tkip"),
    "static-wep": ("wep", None, None),
}


def _aruba(text: str, found: ParsedWireless) -> None:
    current: ParsedWlan | None = None
    for raw in text.splitlines():
        body = raw.strip()
        head = _ARUBA_SSID.match(body)
        if head:
            current = _new(found, head.group(1))
            current.raw = body
            continue
        if current is None:
            continue
        if not raw.startswith((" ", "\t")):
            current = None
            continue
        lowered = body.lower()
        if lowered.startswith("essid "):
            current.ssid = body.split(None, 1)[1]
        elif lowered.startswith("opmode "):
            mode = lowered.split(None, 1)[1].strip()
            security, akm, cipher = _ARUBA_OPMODE.get(mode, (None, None, None))
            if security:
                _add(current.security, security)
            if akm:
                _add(current.akm, akm)
            if cipher:
                _add(current.ciphers, cipher)
        elif lowered.startswith("wpa-passphrase"):
            current.has_psk = True
            current.psk_in_clear = True
        elif lowered.startswith("vlan "):
            vlan = re.match(r"vlan\s+(\d+)", lowered)
            if vlan:
                current.vlan = int(vlan.group(1))
        elif lowered.startswith("hide-ssid"):
            current.broadcast = False
        elif lowered.startswith("deny-inter-user-bridging"):
            current.client_isolation = True
        elif lowered.startswith("max-clients-threshold"):
            limit = re.match(r"max-clients-threshold\s+(\d+)", lowered)
            if limit:
                current.client_limit = int(limit.group(1))


# ---------------------------------------------------------------------------
# FortiGate（FortiAP をぶら下げる形）
# ---------------------------------------------------------------------------

_FORTI_SECURITY = {
    "open": ("open", None, None),
    "wpa2-only-personal": ("wpa2", "psk", "aes"),
    "wpa2-only-enterprise": ("wpa2", "dot1x", "aes"),
    "wpa3-sae": ("wpa3", "sae", "aes"),
    "wpa3-only-enterprise": ("wpa3", "dot1x", "aes"),
    "wpa-personal": ("wpa", "psk", "tkip"),
    "wep64": ("wep", None, None),
    "wep128": ("wep", None, None),
}


def _fortinet(text: str, found: ParsedWireless) -> None:
    in_vap = False
    depth = 0
    current: ParsedWlan | None = None
    for raw in text.splitlines():
        body = raw.strip()
        if _FORTI_VAP.match(raw):
            in_vap, depth, current = True, 0, None
            continue
        if not in_vap:
            continue
        if body.startswith("config "):
            depth += 1
            continue
        if body == "end":
            if depth:
                depth -= 1
                continue
            in_vap, current = False, None
            continue
        if depth:
            continue
        edit = _FORTI_EDIT.match(body)
        if edit:
            current = _new(found, edit.group(1))
            current.raw = body
            continue
        if body == "next":
            current = None
            continue
        item = _FORTI_SET.match(body)
        if current is None or not item:
            continue
        key, value = item.group(1).lower(), item.group(2).strip().strip('"')
        if key == "ssid":
            current.ssid = value
        elif key == "security":
            security, akm, cipher = _FORTI_SECURITY.get(value.lower(), (None, None, None))
            if security:
                _add(current.security, security)
            if akm:
                _add(current.akm, akm)
            if cipher:
                _add(current.ciphers, cipher)
        elif key == "passphrase":
            current.has_psk = True
            current.psk_in_clear = not value.startswith("ENC")
        elif key == "vlanid":
            if value.isdigit():
                current.vlan = int(value)
        elif key == "broadcast-ssid":
            current.broadcast = value.lower() != "disable"
        elif key == "status":
            current.enabled = value.lower() == "enable"
        elif key == "max-clients":
            if value.isdigit():
                current.client_limit = int(value)
        elif key == "pmf":
            current.pmf = {"enable": "required", "optional": "optional"}.get(
                value.lower(), "disabled"
            )
        elif key == "intra-vap-privacy":
            current.client_isolation = value.lower() == "enable"


# ---------------------------------------------------------------------------
# ベンダーに依らないもの
# ---------------------------------------------------------------------------


_IOSXE_AP_PROFILE = re.compile(r"^ap\s+profile\s+(\S+)", re.IGNORECASE)
_IOSXE_AP_TAG = re.compile(
    r"^ap\s+name\s+(\S+)\s+(policy-tag|site-tag|rf-tag)\s+(\S+)", re.IGNORECASE
)
_AIRE_AP_ACCESS = re.compile(
    r"^config\s+ap\s+(telnet|ssh)\s+(enable|disable)\s+(\S+)", re.IGNORECASE
)


def _ap_side(text: str, found: ParsedWireless) -> None:
    """AP 側の入口。IOS-XE は `ap profile` の中、AireOS は `config ap telnet`。

    telnet は合言葉がそのまま流れる。AP は天井に付いていて手が届かないので、
    開けたままだと気づかれないまま残る。
    """
    profile_access: dict[str, list[str]] = {}
    current: str | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        body = line.strip()
        indented = line.startswith((" ", "\t"))

        if not indented:
            current = None
            head = _IOSXE_AP_PROFILE.match(body)
            if head:
                current = head.group(1)
                profile_access.setdefault(current, [])
                continue
            tag = _IOSXE_AP_TAG.match(body)
            if tag:
                ap = _ap(found, tag.group(1))
                ap.profile = ap.profile or tag.group(3)
                continue
            aire = _AIRE_AP_ACCESS.match(body)
            if aire and aire.group(2).lower() == "enable" and aire.group(1).lower() == "telnet":
                ap = _ap(found, aire.group(3))
                if "telnet" not in ap.insecure_access:
                    ap.insecure_access.append("telnet")
            continue

        if current is not None:
            lowered = body.lower()
            if lowered == "telnet":
                profile_access[current].append("telnet")
            elif lowered in ("http", "no https"):
                profile_access[current].append("http")

    # プロファイルに開いている入口は、それを当てている AP に効く。
    # どの AP にも当てていなくても、プロファイルそのものを残しておく
    for name, access in profile_access.items():
        if not access:
            continue
        ap = _ap(found, f"profile {name}")
        for item in access:
            if item not in ap.insecure_access:
                ap.insecure_access.append(item)


#: Cisco `show ap summary`。IOS-XE / AireOS とも、AP Name / Slots / AP Model /
#: Ethernet MAC … の並びで始まる
_AP_SUMMARY_HEADER = re.compile(r"^\s*AP Name\s+Slots\s+AP Model\s+Ethernet MAC\b", re.IGNORECASE)
_MAC = re.compile(
    r"^(?:[0-9a-f]{4}\.){2}[0-9a-f]{4}$|^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$", re.IGNORECASE
)
_IPV4 = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
_FORTI_WTP = re.compile(r"^\s*config\s+wireless-controller\s+wtp\s*$", re.IGNORECASE)
#: Aruba コントローラ `show ap database`（Name / Group / AP Type / IP Address / Status …）
_ARUBA_DB_HEADER = re.compile(r"^\s*Name\s+Group\s+AP Type\s+IP Address\s+Status\b", re.IGNORECASE)


def _ap_inventory(text: str, found: ParsedWireless) -> None:
    """AP の一覧。コントローラの show / config から、台帳に載せる材料を拾う。

    AP は台数が多く、1台ずつ登録すると抜ける。コントローラが持っている一覧を
    そのまま台帳の下敷きにする。読めるのは並びが決まっているものだけ。
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _AP_SUMMARY_HEADER.match(line):
            _read_ap_summary(lines[index + 1 :], found)
        elif _FORTI_WTP.match(line):
            _read_forti_wtp(lines[index + 1 :], found)
        elif _ARUBA_DB_HEADER.match(line):
            _read_aruba_ap_database(lines[index + 1 :], found)


def _read_ap_summary(rows: list[str], found: ParsedWireless) -> None:
    """列の幅は機種ごとに違うので、位置ではなく「MAC の直前が型番、直後が場所」で読む。"""
    for row in rows:
        if row.strip().startswith("-"):
            continue  # 見出しの下の罫線
        columns = row.split()
        if len(columns) < 4:
            break
        macs = [i for i, token in enumerate(columns) if _MAC.match(token)]
        if not macs:
            break
        first = macs[0]
        if first < 3 or not columns[1].isdigit():
            break
        ap = _ap(found, columns[0])
        ap.model = ap.model or " ".join(columns[2:first])
        ap.mac = ap.mac or columns[first].lower()
        # Ethernet MAC の後ろに Radio MAC が続く機種がある。その次からが場所
        rest = columns[first + 1 :]
        if rest and _MAC.match(rest[0]):
            rest = rest[1:]
        addresses = [token for token in rest if _IPV4.match(token)]
        if addresses:
            ap.ip_address = ap.ip_address or addresses[0]
            # 場所と国の間に区切りが無い。国はアドレスの直前の1語なので、それを外す
            where = rest[: rest.index(addresses[0])]
            words = where[:-1] if len(where) >= 2 else []
            if words and ap.location is None:
                ap.location = " ".join(words)


def _read_aruba_ap_database(rows: list[str], found: ParsedWireless) -> None:
    """Aruba コントローラの AP 一覧。名前・グループ・機種・アドレスの順で始まる。"""
    for row in rows:
        if row.strip().startswith("-"):
            continue
        columns = row.split()
        if len(columns) < 4:
            break
        if not _IPV4.match(columns[3]):
            break
        ap = _ap(found, columns[0])
        ap.profile = ap.profile or columns[1]
        ap.model = ap.model or columns[2]
        ap.ip_address = ap.ip_address or columns[3]


def _read_forti_wtp(rows: list[str], found: ParsedWireless) -> None:
    """FortiGate は `config wireless-controller wtp` の下に AP がシリアルで並ぶ。"""
    current: ParsedWirelessAp | None = None
    serial: str | None = None
    for row in rows:
        body = row.strip()
        if body.lower() == "end":
            break
        edit = re.match(r'^edit\s+"?([^"\s]+)"?', body, re.IGNORECASE)
        if edit:
            serial = edit.group(1)
            current = None
            continue
        if body.lower() == "next":
            if current is None and serial:
                current = _ap(found, serial)
                current.serial = serial
            current, serial = None, None
            continue
        setting = re.match(r'^set\s+(\S+)\s+"?([^"\n]+?)"?\s*$', body, re.IGNORECASE)
        if not setting or serial is None:
            continue
        key, value = setting.group(1).lower(), setting.group(2)
        if key == "name":
            current = _ap(found, value)
            current.serial = serial
        elif key == "wtp-profile":
            if current is None:
                current = _ap(found, serial)
                current.serial = serial
            current.profile = current.profile or value
        elif key == "location":
            if current is None:
                current = _ap(found, serial)
                current.serial = serial
            current.location = value


def _common(text: str, found: ParsedWireless) -> None:
    _ap_side(text, found)
    _ap_inventory(text, found)
    for raw in text.splitlines():
        body = raw.strip()
        country = _COUNTRY.match(body)
        if country and found.country is None:
            found.country = country.group(1).upper()
        lowered = body.lower()
        if "rogue" in lowered and ("detection" in lowered or "policy" in lowered):
            found.rogue_detection = True
        # config の `ap name X …` だけ。show の見出し（AP Name  Slots …）は大文字なので当てない
        name = re.match(r"^ap name (\S+) ", body)
        if name:
            _ap(found, name.group(1))


def _ap(found: ParsedWireless, name: str) -> ParsedWirelessAp:
    for ap in found.aps:
        if ap.name == name:
            return ap
    ap = ParsedWirelessAp(name=name)
    found.aps.append(ap)
    return ap
