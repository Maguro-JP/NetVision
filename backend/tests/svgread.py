"""生成した SVG を読み直すための道具。

検証は生成側とは別の経路で行いたいので、出力された属性をそのまま読む。
置き方を変えてもテストが素通りしないようにするため。
"""

from __future__ import annotations

import re

from app.services.svglayout import Rect, text_rect

_TEXT_TAG = re.compile(r"<text\b([^>]*)>(.*?)</text>", re.DOTALL)
_ATTR = re.compile(r'([\w-]+)="([^"]*)"')
_ROTATE = re.compile(r"rotate\(([-\d.]+)")
# 省略したラベルには元の名前が <title> で入る。中身ごと落として、
# 実際に描かれている文字だけを取り出す
_TITLE = re.compile(r"<title>.*?</title>", re.DOTALL)
_TAGS = re.compile(r"<[^>]*>")


def _visible(inner: str) -> str:
    return _TAGS.sub("", _TITLE.sub("", inner)).strip()


def elements(svg: str, css_class: str | None = None) -> list[tuple[str, dict[str, str]]]:
    """`<text>` を (見えている文字, 属性) の形で返す。"""
    out: list[tuple[str, dict[str, str]]] = []
    for match in _TEXT_TAG.finditer(svg):
        attrs = dict(_ATTR.findall(match.group(1)))
        if css_class is not None and attrs.get("class") != css_class:
            continue
        out.append((_visible(match.group(2)), attrs))
    return out


def positions(svg: str, css_class: str | None = None) -> dict[str, tuple[float, float]]:
    """文字 → 描かれている座標。"""
    return {
        text: (float(attrs["x"]), float(attrs["y"]))
        for text, attrs in elements(svg, css_class)
        if text
    }


def rects(svg: str, css_class: str) -> list[Rect]:
    """文字が占める矩形。回転していれば外接矩形にする。"""
    out: list[Rect] = []
    for text, attrs in elements(svg, css_class):
        if not text:
            continue
        rotate = _ROTATE.search(attrs.get("transform", ""))
        out.append(
            text_rect(
                float(attrs["x"]),
                float(attrs["y"]),
                text,
                float(attrs.get("font-size", 10)),
                attrs.get("text-anchor", "start"),
                float(rotate.group(1)) if rotate else 0.0,
            )
        )
    return out
