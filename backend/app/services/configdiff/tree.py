"""Config を「行の木」として持つ。

機器の config は見出し行とその配下のぶら下がり行でできている。
テキストのまま行単位で差分を取ると、

- 同じ行（`shutdown` など）が複数のブロックに現れて取り違える
- ブロックの境界を跨いで削除してしまう
- `no ...` を単なる別の行として扱ってしまう
- 値を書き換えただけの行（`description` など）を「1行削除＋1行追加」と誤解する

といった形で簡単に壊れる。投入コマンドを作る以上、壊れ方が事故に直結するので、
必ず木にしてから比較する。

木の子は「キー」で引く。既定のキーは行そのものだが、値を1つしか持てない
コマンド（`description`, `ip address` など）はコマンド名までをキーにする。
こうしておくと値の変更が「置き換え」として1件に畳まれ、ACL のように同じ
コマンド名で複数行が並ぶものは行ごとに別物として残る。

`no X` は「X が無い状態」として正規化する。running-config に `no ip domain-lookup`
のように否定形がそのまま出てくることがあり、これを独立した行として扱うと
「X」と「no X」が同じブロックに共存できてしまうため。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 型のためだけの参照（循環 import を避ける）
    from app.services.configdiff.dialects import Dialect

# 行ではなく飾りなので、木に入れる前に落とすもの
_IGNORED = re.compile(
    r"^\s*(?:!|#|end\s*$|exit\s*$|next\s*$|Building configuration|Current configuration"
    r"|Using \d+ out of)",
    re.IGNORECASE,
)


def _indent_of(raw: str) -> int:
    return len(raw) - len(raw.lstrip(" \t"))


@dataclass
class ConfigNode:
    """config の1行と、その配下。

    挿入順は dict がそのまま保つので、順序が意味を持つブロックでも使える。
    """

    line: str = ""
    children: dict[str, ConfigNode] = field(default_factory=dict)

    # --- 構築 ------------------------------------------------------------

    def child(self, key: str, line: str) -> ConfigNode:
        """子を取得する。無ければ作る。既にあって行が変わっていれば差し替える。"""
        node = self.children.get(key)
        if node is None:
            node = ConfigNode(line=line)
            self.children[key] = node
        elif node.line != line:
            # 値だけ変わった単一値コマンド。順序は保ったまま中身を更新する
            node.line = line
        return node

    def remove(self, key: str) -> bool:
        return self.children.pop(key, None) is not None

    # --- 参照 ------------------------------------------------------------

    def find(self, path: tuple[str, ...]) -> ConfigNode | None:
        node = self
        for key in path:
            child = node.children.get(key)
            if child is None:
                return None
            node = child
        return node

    def walk(self, prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], ConfigNode]]:
        """(キーのパス, ノード) を深さ優先で並べる。"""
        out: list[tuple[tuple[str, ...], ConfigNode]] = []
        for key, child in self.children.items():
            path = (*prefix, key)
            out.append((path, child))
            out.extend(child.walk(path))
        return out

    def lines(self, depth: int = 0) -> list[str]:
        """木をテキストに戻す（インデント形式）。"""
        out: list[str] = []
        for child in self.children.values():
            out.append(" " * depth + child.line)
            out.extend(child.lines(depth + 1))
        return out

    def to_text(self) -> str:
        body = "\n".join(self.lines())
        return body + "\n" if body else ""

    def copy(self) -> ConfigNode:
        clone = ConfigNode(line=self.line)
        for key, child in self.children.items():
            clone.children[key] = child.copy()
        return clone

    def insert_first(self, key: str, line: str) -> ConfigNode:
        """先頭に子を差し込む。前提コマンドを前に置くために使う。"""
        if key in self.children:
            return self.children[key]
        node = ConfigNode(line=line)
        self.children = {key: node, **self.children}
        return node


def parse(text: str, dialect: Dialect) -> ConfigNode:
    """config を木にする。

    `no X` は「X が無い状態」に畳む。同じブロックに `X` が既にあれば取り除く。
    """
    root = ConfigNode()
    # (インデント幅, そのノード) のスタック
    stack: list[tuple[int, ConfigNode]] = [(-1, root)]

    for raw in text.splitlines():
        if not raw.strip() or _IGNORED.match(raw):
            continue

        indent = _indent_of(raw)
        payload, removes = dialect.parse_change(raw.strip())
        if not payload:
            continue

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        key = dialect.key_of(payload)

        if removes:
            # 「無い状態」なので木には積まない。既に積まれていれば取り消す。
            parent.remove(key)
            continue

        node = parent.child(key, payload)
        stack.append((indent, node))

    return root
