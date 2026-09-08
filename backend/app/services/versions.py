"""ネットワーク機器の OS バージョン比較。

`15.2(7)E6`（IOS）、`17.09.04a`（IOS-XE）、`21.4R3-S5.4`（Junos）、`7.2.8`（FortiOS）と
形式がばらばらで、semver ではない。数字の並びは数値として、それ以外は文字列として
比較する「自然順比較」で扱う。これで `17.09.04` と `17.9.4` を同じものとして扱える。
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"(\d+)|([A-Za-z]+)")


def tokenize(version: str) -> list[tuple[int, int | str]]:
    """比較用のトークン列にする。数字は数値、英字は小文字の文字列。

    先頭の要素は種別（0=数値, 1=文字列）で、数値が文字列より先に来るようにしている。
    区切り記号（. ( ) - _）は意味を持たないので落とす。
    """
    tokens: list[tuple[int, int | str]] = []
    for number, word in _TOKEN.findall(version or ""):
        if number:
            tokens.append((0, int(number)))
        else:
            tokens.append((1, word.lower()))
    return tokens


def compare(left: str | None, right: str | None) -> int:
    """left < right なら -1、等しければ 0、left > right なら 1。"""
    if left is None or right is None:
        return 0

    a, b = tokenize(left), tokenize(right)
    for x, y in zip(a, b, strict=False):
        if x == y:
            continue
        return -1 if x < y else 1

    # ここまで同じなら、要素が多い方を新しいとみなす（21.4R3 < 21.4R3-S5）
    if len(a) == len(b):
        return 0
    return -1 if len(a) < len(b) else 1


def equals(left: str | None, right: str | None) -> bool:
    return left is not None and right is not None and compare(left, right) == 0


def in_range(
    version: str | None,
    *,
    start_including: str | None = None,
    start_excluding: str | None = None,
    end_including: str | None = None,
    end_excluding: str | None = None,
) -> bool:
    """バージョンが指定範囲に入るか。範囲指定が1つも無い場合は False（判定不能）。"""
    if version is None:
        return False
    if not any((start_including, start_excluding, end_including, end_excluding)):
        return False

    if start_including is not None and compare(version, start_including) < 0:
        return False
    if start_excluding is not None and compare(version, start_excluding) <= 0:
        return False
    if end_including is not None and compare(version, end_including) > 0:
        return False
    if end_excluding is not None and compare(version, end_excluding) >= 0:
        return False
    return True


def normalize_name(value: str | None) -> str:
    """`IOS-XE` `IOS XE` `iosxe` を同じものとして扱うためのキー。"""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def matches_model(pattern: str | None, model: str | None) -> bool:
    """影響範囲の型番指定と機器の型番を突き合わせる。

    パターンが空なら「ベンダー全体」の意味。末尾 `*` で前方一致を書ける
    （`C9500*` で C9500-40X と C9500-48Y4C の両方に当てる）。
    """
    if not pattern:
        return True
    if model is None:
        return False

    left, right = normalize_name(pattern.rstrip("*")), normalize_name(model)
    if right.startswith(left) if pattern.endswith("*") else left == right:
        return True
    # Cisco は同じハードを `WS-` や `/K9`、ライセンスの束（-A / -E）で飾って印字する。
    # 参照データは飾り無しで書かれることが多いので、飾りを外しても比べる
    from app.services.configparse import ciscomodel

    return ciscomodel.matches(pattern, model)
