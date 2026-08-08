"""FortiOS の config を読む。"""

from __future__ import annotations

import re

from app.services.configparse.base import ParsedConfig, ParsedInterface, Vendor

_HOSTNAME = re.compile(r'^\s*set hostname\s+"?([^"\n]+)"?', re.MULTILINE)
_VERSION = re.compile(r"#config-version=([\w.\-]+):", re.MULTILINE)
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
        # 例: FGT100F-7.2.8-FW-build1639-230926 → 7.2.8
        version = re.search(r"-(\d+\.\d+\.\d+)-", match.group(1) + "-")
        if version:
            parsed.os_version = version.group(1)

    match = _MODEL_FROM_VERSION.search(text)
    if match:
        parsed.model = match.group(1)

    _parse_interfaces(text, parsed)
    return parsed


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
        elif line == "set status down":
            current.enabled = False
        elif line == "next":
            current = None


def _prefix_length(netmask: str) -> int:
    try:
        return sum(bin(int(octet)).count("1") for octet in netmask.split("."))
    except ValueError:
        return 32
