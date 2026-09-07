"""書いたつもりの正規表現が、そのとおりに書けていること。

`\\b` のつもりで制御文字（0x08）が入っていた、という取りこぼしが実際にあった。
当たらない正規表現は例外を出さず、ただ黙る。書き損じたその日には気づけず、
「差分は出ているのに中身がおかしい」という形で後から出てくる。

読める文字だけで書けているかを、ここで一度に見る。
"""

from __future__ import annotations

import pathlib
import re

#: 出てきてよい制御文字。改行とタブだけ
_ALLOWED = {"\n", "\t", "\r"}
_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SUFFIXES = (".py", ".ts", ".tsx", ".md", ".json", ".yml", ".yaml", ".sh")
#: 見に行かない場所。取り込んだものや作られたものは、こちらの書き損じではない
_SKIP = ("/.git/", "/node_modules/", "/.venv/", "/dist/", "/__pycache__/", "/.mypy_cache/")


def _files() -> list[pathlib.Path]:
    return [
        path
        for path in _ROOT.rglob("*")
        if path.suffix in _SUFFIXES
        and path.is_file()
        and not any(part in f"/{path.relative_to(_ROOT)}/".replace("//", "/") for part in _SKIP)
    ]


def test_no_source_file_holds_a_stray_control_character() -> None:
    offenders: list[str] = []
    for path in _files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            found = [c for c in line if ord(c) < 32 and c not in _ALLOWED]
            if found:
                codes = [hex(ord(c)) for c in found]
                offenders.append(f"{path.relative_to(_ROOT)}:{index} {codes}")
    assert not offenders, offenders


def test_the_check_itself_would_notice_one() -> None:
    # 見つける側が壊れていないこと。0x08 は `\b` の書き損じで入りやすい
    assert [c for c in "^description\x08" if ord(c) < 32 and c not in _ALLOWED]
    assert not [c for c in re.compile(r"^description\b").pattern if ord(c) < 32]
