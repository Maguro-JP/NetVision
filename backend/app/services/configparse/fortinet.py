"""FortiOS の config を読む。"""

from __future__ import annotations

import re

from app.services.configparse import fortitext, policy, routing, tunnels
from app.services.configparse.base import (
    ParsedConfig,
    ParsedCrypto,
    ParsedCryptoMapEntry,
    ParsedDhcp,
    ParsedDhcpPool,
    ParsedIkePolicy,
    ParsedInterface,
    ParsedNat,
    ParsedNatPool,
    ParsedNatRule,
    ParsedQos,
    ParsedQosAction,
    ParsedQosClass,
    ParsedQosPolicy,
    ParsedTransformSet,
    ParsedVrf,
    Vendor,
)

_HOSTNAME = re.compile(r'^\s*set hostname\s+"?([^"\n]+)"?', re.MULTILINE)
#: 実物は `#config-version=FGT100F-7.2.8-FW-build1639-230929:opmode=0` の形。
#: 貼るときに後ろを切られていることがあるので、コロンまでを必須にしない
_VERSION = re.compile(r"#config-version=([\w.\-]+)", re.MULTILINE)
_MODEL_FROM_VERSION = re.compile(r"#config-version=([A-Za-z0-9]+)-")


def detect(text: str) -> Vendor | None:
    if "#config-version=" in text or re.search(r"^config system global", text, re.MULTILINE):
        return Vendor.FORTINET
    return None


def parse(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.FORTINET, os_name="FortiOS")

    match = _HOSTNAME.search(text)
    if match:
        parsed.hostname = match.group(1).strip()

    match = _VERSION.search(text)
    if match:
        # 例: FGT100F-7.2.8-FW-build1639-230926 → 7.2.8。
        # 版が 7.2 までしか書かれていないこともある
        version = re.search(r"-(\d+\.\d+(?:\.\d+)?)(?:-|$)", match.group(1))
        if version:
            parsed.os_version = version.group(1)

    match = _MODEL_FROM_VERSION.search(text)
    if match:
        parsed.model = match.group(1)

    _parse_interfaces(text, parsed)
    _parse_vdoms(text, parsed)
    tunnels.parse_fortios(text, parsed)
    routing.parse_fortios(text, parsed)
    policy.parse_fortios(text, parsed)
    _parse_address_objects(text, parsed)
    _parse_nat(text, parsed)
    _parse_dhcp(text, parsed)
    _parse_ipsec(text, parsed)
    _parse_qos(text, parsed)
    return parsed


def _parse_address_objects(text: str, parsed: ParsedConfig) -> None:
    """名前で書かれたアドレスの定義を控える。

    FortiGate は送信元も宛先も名前で書く。名前だけ控えておけば、
    「指している名前がこの Config に無い」を、Cisco の ACL と同じ目で言える。
    """
    for section in ("firewall address", "firewall addrgrp", "firewall vip"):
        for name, _ in fortitext.blocks(text, section):
            if name not in parsed.address_objects:
                parsed.address_objects.append(name)
    parsed.address_objects.append("all")
    parsed.address_objects.append("any")


def _parse_nat(text: str, parsed: ParsedConfig) -> None:
    """アドレス変換を読む。FortiGate では3か所に分かれて書いてある。

    - `firewall policy` の `set nat enable`（出る側。出口のアドレスに束ねる）
    - `firewall ippool`（出る側で、出口とは別のアドレスを使うとき）
    - `firewall vip`（入る側。外から中の1台へ通す）

    どれも「何が何に変わるか」は同じなので、同じ器へ入れる。そうしておけば、
    変換の指摘（束ねていない・変換先が無い）を作り直さずに使える。
    """
    nat = ParsedNat()

    for name, values in fortitext.blocks(text, "firewall ippool"):
        nat.pools.append(
            ParsedNatPool(
                name=name,
                start=values.get("startip"),
                end=values.get("endip"),
            )
        )

    for name, values in fortitext.blocks(text, "firewall policy"):
        if values.get("nat", "").lower() != "enable":
            continue
        label = values.get("name") or f"policy {name}"
        pool = fortitext.words(values.get("poolname"))
        out = fortitext.words(values.get("dstintf"))
        nat.rules.append(
            ParsedNatRule(
                kind="dynamic",
                direction="inside",
                matched=" ".join(fortitext.words(values.get("srcaddr"))) or "any",
                # 送信元は名前で書かれる。定義が無ければ、そこは言える
                matched_is_name=len(fortitext.words(values.get("srcaddr"))) <= 1,
                translated=pool[0] if pool else (out[0] if out else "出口のアドレス"),
                # FortiGate の NAT は、ふだんポートで束ねる（central-nat を除く）
                overload=True,
                raw=f"firewall policy {name} ({label}) set nat enable",
            )
        )
        for interface in fortitext.words(values.get("srcintf")):
            if interface not in nat.inside_interfaces:
                nat.inside_interfaces.append(interface)
        for interface in out:
            if interface not in nat.outside_interfaces:
                nat.outside_interfaces.append(interface)

    for name, values in fortitext.blocks(text, "firewall vip"):
        forward = values.get("portforward", "").lower() == "enable"
        nat.rules.append(
            ParsedNatRule(
                kind="static-port" if forward else "static",
                direction="outside",
                matched=values.get("extip"),
                translated=values.get("mappedip"),
                matched_is_name=False,
                protocol=values.get("protocol") if forward else None,
                raw=f"firewall vip {name}",
            )
        )
        for interface in fortitext.words(values.get("extintf")):
            if interface not in nat.outside_interfaces:
                nat.outside_interfaces.append(interface)

    if nat.rules or nat.pools:
        parsed.nat = nat


def _parse_dhcp(text: str, parsed: ParsedConfig) -> None:
    """配るアドレスを読む。範囲は `config ip-range` の中に入れ子で書かれる。"""
    dhcp = ParsedDhcp()
    ranges = _dhcp_ranges(text)
    for name, values in fortitext.blocks(text, "system dhcp server"):
        start, end = ranges.get(name, (None, None))
        network = values.get("netmask")
        gateway = values.get("default-gateway")
        dns = [
            values[key]
            for key in ("dns-server1", "dns-server2", "dns-server3")
            if values.get(key) and values[key] != "0.0.0.0"
        ]
        interface = values.get("interface")
        dhcp.pools.append(
            ParsedDhcpPool(
                name=f"dhcp {name}" + (f" ({interface})" if interface else ""),
                network=f"{gateway}/{network}" if gateway and network else network,
                range_start=start,
                range_end=end,
                default_routers=[gateway] if gateway else [],
                dns_servers=dns,
                lease=values.get("lease-time"),
            )
        )
    if dhcp.pools:
        parsed.dhcp = dhcp


def _dhcp_ranges(text: str) -> dict[str, tuple[str | None, str | None]]:
    """`config ip-range` は dhcp server の中の入れ子なので、別に読む。

    ここを読まないと「ゲートウェイのアドレスまで配っている」を言い間違える。
    FortiGate は配る範囲をそのまま書くので、範囲の外は配られない。
    """
    out: dict[str, tuple[str | None, str | None]] = {}
    server: str | None = None
    in_range = False
    start = end = None
    for raw in text.splitlines():
        line = raw.strip()
        lowered = line.lower()
        if lowered == "config system dhcp server":
            server, in_range = None, False
            continue
        edit = re.match(r'^edit\s+"?([^"]+)"?$', line)
        if edit and not in_range:
            server = edit.group(1)
            start = end = None
            continue
        if lowered == "config ip-range":
            in_range = True
            continue
        if in_range:
            if lowered == "end":
                in_range = False
                if server is not None:
                    out[server] = (start, end)
                continue
            found = re.match(r"^set\s+(start-ip|end-ip)\s+(\S+)", lowered)
            if found:
                if found.group(1) == "start-ip":
                    start = found.group(2)
                else:
                    end = found.group(2)
    return out


def _parse_qos(text: str, parsed: ParsedConfig) -> None:
    """優先制御を読む。FortiGate は「速さの型」と「どの通信に当てるか」に分かれる。

    - `firewall shaper traffic-shaper`（型。上限と優先度）
    - `firewall shaping-policy`（当て先。どの通信に、どの型を当てるか）

    Cisco の class-map / policy-map / service-policy と役割が同じなので、
    そこへ読み替える。当て先の無い型が「書いてあるだけ」として残る。
    """
    qos = ParsedQos()

    # どの型を、どの当て先が使っているか。Cisco の policy-map は中に class を
    # 並べるので、同じ形になるよう先に集める
    users: dict[str, list[str]] = {}
    for name, values in fortitext.blocks(text, "firewall shaping-policy"):
        label = values.get("name") or f"shaping-policy {name}"
        for key in ("traffic-shaper", "traffic-shaper-reverse", "per-ip-shaper"):
            if values.get(key):
                users.setdefault(values[key], []).append(label)

    for name, values in fortitext.blocks(text, "firewall shaper traffic-shaper"):
        actions = []
        if values.get("maximum-bandwidth"):
            actions.append(f"上限 {values['maximum-bandwidth']} kbps")
        if values.get("guaranteed-bandwidth"):
            actions.append(f"最低 {values['guaranteed-bandwidth']} kbps")
        if values.get("priority"):
            actions.append(f"優先度 {values['priority']}")
        qos.policies.append(
            ParsedQosPolicy(
                name=name,
                classes=[
                    ParsedQosAction(class_name=label, actions=actions)
                    for label in users.get(name, [])
                ],
            )
        )

    for name, values in fortitext.blocks(text, "firewall shaping-policy"):
        service = " ".join(fortitext.words(values.get("service"))) or "すべて"
        qos.classes.append(
            ParsedQosClass(
                name=values.get("name") or f"shaping-policy {name}",
                matches=[f"service {service}"],
            )
        )
        for key in ("traffic-shaper", "traffic-shaper-reverse", "per-ip-shaper"):
            shaper = values.get(key)
            if not shaper:
                continue
            direction = "input" if key == "traffic-shaper-reverse" else "output"
            for interface in fortitext.words(values.get("dstintf")) or ["shaping-policy"]:
                qos.applied.append((interface, direction, shaper))

    if qos.policies or qos.classes:
        parsed.qos = qos


def _parse_ipsec(text: str, parsed: ParsedConfig) -> None:
    """拠点間の暗号化を読む。

    Cisco の crypto map とは書き方が違うが、決めていることは同じ
    （相手・鍵の作り方・何を暗号化するか）。同じ器へ入れて、方式の古さや
    相手の抜けを、ベンダーに関係なく同じ目で見られるようにする。
    """
    crypto = ParsedCrypto()

    for name, values in fortitext.blocks(text, "vpn ipsec phase1-interface"):
        proposal = fortitext.words(values.get("proposal"))
        for item in proposal or [""]:
            encryption, _, digest = item.partition("-")
            crypto.ike_policies.append(
                ParsedIkePolicy(
                    number=name,
                    version=2 if values.get("ike-version") == "2" else 1,
                    encryption=encryption or None,
                    hash=digest or None,
                    group=values.get("dhgrp"),
                    authentication="psk" if values.get("psksecret") else values.get("authmethod"),
                )
            )
        peer = values.get("remote-gw")
        crypto.maps.append(
            ParsedCryptoMapEntry(
                name=name,
                kind="ipsec-interface",
                peers=[peer] if peer else [],
                profile=values.get("interface"),
            )
        )
        if values.get("interface"):
            crypto.applied.append((values["interface"], name))
        if values.get("psksecret"):
            crypto.keyed_peers.append(peer or name)

    for name, values in fortitext.blocks(text, "vpn ipsec phase2-interface"):
        crypto.transform_sets.append(
            ParsedTransformSet(name=name, transforms=fortitext.words(values.get("proposal")))
        )
        parent = values.get("phase1name")
        for entry in crypto.maps:
            if entry.name == parent:
                entry.transform_sets.append(name)
                # 何を暗号化するか。両側で裏返しになっていないと通らない
                src = values.get("src-subnet")
                dst = values.get("dst-subnet")
                if src or dst:
                    entry.match_address = f"{src or 'any'} → {dst or 'any'}"
                    entry.match_is_acl = False

    if crypto.maps or crypto.ike_policies:
        parsed.crypto = crypto


def _parse_vdoms(text: str, parsed: ParsedConfig) -> None:
    """VDOM を仮想ルーターとして扱う。

    FortiGate で「1台の中にルーターを分ける」のは VDOM なので、他ベンダーの
    VRF と同じ列に並べておいた方が、機器をまたいで見るときに揃う。
    """
    in_vdom = False
    for raw in text.splitlines():
        line = raw.strip()
        if line in ("config vdom", "config global"):
            in_vdom = line == "config vdom"
            continue
        if in_vdom:
            match = re.match(r'^edit\s+"?([^"\n]+)"?$', line)
            if match:
                parsed.add_vrf(ParsedVrf(name=match.group(1), description="VDOM"))
            elif line == "end":
                in_vdom = False


def _parse_interfaces(text: str, parsed: ParsedConfig) -> None:
    """`config system interface` 配下の `edit "port1"` ブロックを読む。"""
    in_block = False
    current: ParsedInterface | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if line == "config system interface":
            in_block = True
            continue
        if not in_block:
            continue
        if line == "end":
            break

        match = re.match(r'^edit\s+"?([^"\n]+)"?$', line)
        if match:
            current = parsed.add_interface(ParsedInterface(name=match.group(1)))
            continue
        if current is None:
            continue

        if line.startswith("set ip "):
            parts = line.split()
            if len(parts) >= 4:
                current.ip_address = f"{parts[2]}/{_prefix_length(parts[3])}"
        elif line.startswith("set description "):
            current.description = line[len("set description ") :].strip().strip('"')
        elif line.startswith("set alias "):
            current.description = current.description or line[len("set alias ") :].strip().strip(
                '"'
            )
        elif line.startswith("set vlanid "):
            current.vlan = int(line.rsplit(maxsplit=1)[-1])
        elif line.startswith("set vdom "):
            current.vrf = line[len("set vdom ") :].strip().strip('"')
        elif line == "set status down":
            current.enabled = False
        elif line.startswith("set role "):
            # 外に面した口かどうか。ポリシーの「外から中へ」を見るときの手掛かり
            role = line[len("set role ") :].strip()
            note = f"role {role}"
            current.description = f"{current.description} / {note}" if current.description else note
        elif line == "next":
            current = None


def _prefix_length(netmask: str) -> int:
    try:
        return sum(bin(int(octet)).count("1") for octet in netmask.split("."))
    except ValueError:
        return 32
