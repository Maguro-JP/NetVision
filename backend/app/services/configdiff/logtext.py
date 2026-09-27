"""取り込んだログから、設定そのもの（running-config の部分）だけを取り出す。

現場で取るログは `show version` → `show running-config` → `show cdp neighbors` の
ように、いくつかの show の出力が1つのファイルに続いている。そのまま差分に掛けると、
版数や稼働時間、隣接機器の表示まで「設定の行」として扱われ、投入手順に
`no Cisco IOS XE Software, Version ...` のような行が混ざる。投入後の突き合わせも、
稼働時間が変わるだけで一致しなくなる。

プロンプト付きのコマンド行（`host#show ...`、`host> show ...`、`<host>display ...`）が
あれば、設定を出すコマンドの直後から次のプロンプト行の手前までを設定とみなす。
プロンプト行が無い（設定だけを貼った）文字列や、設定を出すコマンドが見つからない
ものは、手を付けずにそのまま返す。

プロンプトとコマンドの形は、手元の見本（tests/fixtures の Cisco IOS・Junos）と
既存の `template_from_config._SHOW_OUTPUT` に合わせた。各社のマニュアルでの確認は未確認。
"""

from __future__ import annotations

import re

#: プロンプト付きのコマンド行。`host#`、`host>`、`user@host>`、`host(config)#`、`<host>`、`[host]`
_PROMPT_COMMAND = re.compile(
    r"^(?:[<\[][\w.\-@:/]+[>\]]|[\w.\-@:/]+(?:\([\w\-]*\))?\s?[#>])\s*"
    r"(?P<command>(?:show|display|get|more)\b.*)$",
    re.IGNORECASE,
)

#: 設定そのものを出すコマンド
_CONFIG_DUMP = re.compile(
    r"^(?:show\s+(?:run\w*(?:-config)?|startup-config|conf\w*|full-configuration)"
    r"|display\s+(?:current|saved)-configuration"
    r"|more\s+system:running-config)\b",
    re.IGNORECASE,
)

#: 設定の前に出る見出し（Cisco IOS）。バイト数は設定を変えるたびに変わる
_DUMP_HEADER = re.compile(r"^\s*(?:Building configuration\.*|Current configuration\s*:.*)\s*$")


def config_part(text: str) -> str:
    """ログの中の設定の部分。見つからなければ元の文字列をそのまま返す。"""
    lines = text.splitlines()
    prompts = [i for i, line in enumerate(lines) if _PROMPT_COMMAND.match(line.strip())]
    if not prompts:
        return text

    for index in prompts:
        command = _PROMPT_COMMAND.match(lines[index].strip())
        if command is None or not _CONFIG_DUMP.match(command.group("command").strip()):
            continue
        end = next((p for p in prompts if p > index), len(lines))
        body = [line for line in lines[index + 1 : end] if not _DUMP_HEADER.match(line)]
        return "\n".join(body).strip("\n") + "\n"
    return text
