"""現行 config と将来 config の差分から、投入コマンドを組み立てる。

ここが一番事故に近い場所なので、作り方で担保する。

  1. 現行と将来を両方とも木にする
  2. 木の差分から投入コマンドを生成する
  3. 生成したコマンドをもう一度読み直して現行に当て直し、将来と一致するか見る
  4. 一致しなければコマンドを返さない。どこが収束しなかったかを名指しで返す

3 が肝。「差分を作る処理」と「差分を検証する処理」を別経路にしてあるので、
生成側の書き間違い（`no` の付け方を間違えた、消し忘れた、順序を壊した）が
そのまま検証で落ちる。台帳の「書き出し→取り込み」を往復で担保したのと同じ考え方。

原理的な限界も明示しておく。機器の既定値を知らないので、
「将来 config に書かれていない ＝ 既定のまま」なのか「消したい」なのかは
config テキストだけからは決まらない。ここは削除として扱い、警告に出す。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.configdiff import tree
from app.services.configdiff.dialects import Dialect, for_vendor
from app.services.configdiff.tree import ConfigNode


def normalize(node: ConfigNode, dialect: Dialect) -> ConfigNode:
    """機器側で暗黙に必要になる行を、木の上に明示する。

    たとえば IOS のポートに `switchport mode access` を入れるには、先に
    `switchport` が入っている必要がある。config には書かれないが機器の状態
    としては存在するので、比較する前に両方の木へ同じ規則で補っておく。

    こうしておくと差分側で特別扱いする必要が無くなり、「差分を当てた結果が
    将来と一致するか」の検証もそのまま通せる。
    """
    for child in node.children.values():
        normalize(child, dialect)

    required: list[str] = []
    for child in node.children.values():
        for line in dialect.prerequisites_for(node.line, child.line):
            # 「無い状態」は木に置けない。前提として書けるのは肯定形だけ
            if dialect.parse_change(line)[1] or line in required:
                continue
            required.append(line)

    for line in reversed(required):
        node.insert_first(dialect.key_of(line), line)

    return node


def equivalent(
    left: ConfigNode, right: ConfigNode, dialect: Dialect, path: tuple[str, ...]
) -> bool:
    """2つの木が同じ状態か。

    順序を見るのは、順序が意味を持つブロック（ACL など）の中だけ。
    それ以外で順序まで一致を求めると、正しい差分を「不一致」と誤判定してしまう。
    """
    if left.line != right.line or left.children.keys() != right.children.keys():
        return False

    if path and dialect.is_ordered(path):
        if list(left.children) != list(right.children):
            return False

    for key, child in left.children.items():
        if not equivalent(child, right.children[key], dialect, (*path, key)):
            return False
    return True


@dataclass
class Command:
    """投入する1行。"""

    text: str
    depth: int
    kind: str  # add / remove / block / context
    path: tuple[str, ...]

    @property
    def rendered(self) -> str:
        return " " * self.depth + self.text


@dataclass
class DiffResult:
    commands: list[Command] = field(default_factory=list)
    rollback: list[Command] = field(default_factory=list)
    verified: bool = False
    # 検証で収束しなかった箇所（あれば commands は空にして返す）
    mismatches: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    changed: int = 0

    @property
    def script(self) -> str:
        body = "\n".join(c.rendered for c in self.commands)
        return body + "\n" if body else ""

    @property
    def rollback_script(self) -> str:
        body = "\n".join(c.rendered for c in self.rollback)
        return body + "\n" if body else ""

    @property
    def is_empty(self) -> bool:
        return not self.commands


# ---------------------------------------------------------------------------
# 差分の組み立て
# ---------------------------------------------------------------------------


def _emit_subtree(node: ConfigNode, path: tuple[str, ...], depth: int, out: list[Command]) -> None:
    """ノード配下をまるごと追加する。"""
    for key, child in node.children.items():
        child_path = (*path, key)
        out.append(Command(child.line, depth, "add", child_path))
        _emit_subtree(child, child_path, depth + 1, out)


def _diff_block(
    current: ConfigNode,
    target: ConfigNode,
    path: tuple[str, ...],
    depth: int,
    dialect: Dialect,
    result: DiffResult,
) -> list[Command]:
    """1ブロック分の差分。返すのは「このブロックに入れるコマンド」。"""
    out: list[Command] = []

    # 順序が意味を持つブロックは行単位で足し引きしない。
    # 追記だけで済むなら追記、そうでなければブロックごと入れ替える。
    if path and dialect.is_ordered(path):
        return _diff_ordered_block(current, target, path, depth, dialect, result, out)

    # --- 消える行（将来に無い） ---
    for key, child in current.children.items():
        if key in target.children:
            continue
        out.append(Command(dialect.negate(child.line), depth, "remove", (*path, key)))
        result.removed += 1

    # --- 入る行・変わる行 ---
    for key, child in target.children.items():
        existing = current.children.get(key)
        child_path = (*path, key)

        if existing is None:
            out.append(Command(child.line, depth, "add", child_path))
            result.added += 1
            _emit_subtree(child, child_path, depth + 1, out)
            continue

        inner = _diff_block(existing, child, child_path, depth + 1, dialect, result)

        if existing.line != child.line:
            # 単一値コマンドの値が変わった。同じキーなので上書きで足りる
            out.append(Command(child.line, depth, "add", child_path))
            result.changed += 1
            out.extend(inner)
        elif inner:
            # 中身だけ変わったブロック。見出しを再掲して配下に入る。
            # ただしブロックごと入れ替える場合は既に見出しを自前で出しているので、
            # ここで足すと「ブロックに入ってから、そのブロックを消す」形になってしまう
            rebuilt = any(command.path == child_path for command in inner)
            if not rebuilt:
                out.append(Command(child.line, depth, "context", child_path))
            out.extend(inner)

    return out


def _diff_ordered_block(
    current: ConfigNode,
    target: ConfigNode,
    path: tuple[str, ...],
    depth: int,
    dialect: Dialect,
    result: DiffResult,
    out: list[Command],
) -> list[Command]:
    """ACL のように順序が評価順そのものになるブロック。"""
    current_keys = list(current.children)
    target_keys = list(target.children)

    if current_keys == target_keys[: len(current_keys)]:
        # 既存の並びはそのままで、後ろに足すだけ。無停止で入れられる
        for key in target_keys[len(current_keys) :]:
            child = target.children[key]
            out.append(Command(child.line, depth, "add", (*path, key)))
            result.added += 1
        return out

    if current_keys == target_keys:
        return out

    # 途中に挟む / 消す / 並べ替える場合、行単位で入れると評価順が壊れる。
    # 一度消してから入れ直すしかない
    result.warnings.append(
        f"「{path[-1]}」は順序が意味を持つため、消してから入れ直します。"
        "入れ直すまでの間そのブロックは無効になります"
    )
    out.append(Command(dialect.negate(path[-1]), depth - 1, "remove", path))
    out.append(Command(path[-1], depth - 1, "add", path))
    for key in target_keys:
        child = target.children[key]
        out.append(Command(child.line, depth, "add", (*path, key)))
    result.changed += 1
    return out


# ---------------------------------------------------------------------------
# 検証：作ったコマンドを読み直して当て直す
# ---------------------------------------------------------------------------


def apply_script(base: ConfigNode, script: str, dialect: Dialect) -> ConfigNode:
    """投入コマンドを config に当てた結果の木を返す。

    差分を作った処理の結果ではなく、出力されたコマンドの文字列を読み直して
    当てる。生成側の書き間違いをここで捕まえるため、経路を分けている。
    """
    result = base.copy()
    stack: list[tuple[int, ConfigNode]] = [(-1, result)]

    for raw in script.splitlines():
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        payload, removes = dialect.parse_change(raw.strip())
        if not payload or payload.startswith("#"):
            continue

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        key = dialect.key_of(payload)

        if removes:
            parent.remove(key)
            continue

        node = parent.child(key, payload)
        stack.append((indent, node))

    return result


def _describe_mismatch(current: ConfigNode, target: ConfigNode, path: tuple[str, ...]) -> list[str]:
    """どこが一致しなかったかを人が読める形で並べる。"""
    where = " > ".join(path) if path else "（最上位）"
    out: list[str] = []

    for key, child in target.children.items():
        got = current.children.get(key)
        if got is None:
            out.append(f"{where}: 「{child.line}」が入りませんでした")
        elif got.line != child.line:
            out.append(f"{where}: 「{got.line}」のままで「{child.line}」になりませんでした")
        else:
            out.extend(_describe_mismatch(got, child, (*path, key)))

    for key, child in current.children.items():
        if key not in target.children:
            out.append(f"{where}: 「{child.line}」が残ってしまいました")

    if not out and list(current.children) != list(target.children):
        out.append(f"{where}: 行の順序が将来の config と違います")

    return out


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build(current_text: str, target_text: str, vendor: str | None = None) -> DiffResult:
    """現行 config と将来 config から、投入コマンドと切り戻しコマンドを作る。"""
    dialect = for_vendor(vendor)
    current = normalize(tree.parse(current_text, dialect), dialect)
    target = normalize(tree.parse(target_text, dialect), dialect)

    result = DiffResult()
    result.commands = _diff_block(current, target, (), 0, dialect, result)

    # 逆向き（将来 → 現行）が切り戻し
    rollback = DiffResult()
    result.rollback = _diff_block(target, current, (), 0, dialect, rollback)

    if not result.commands:
        result.verified = True
        return result

    # --- 検証 ---
    applied = apply_script(current, result.script, dialect)
    if equivalent(applied, target, dialect, ()):
        result.verified = True
    else:
        result.mismatches = _describe_mismatch(applied, target, ())
        result.commands = []
        result.rollback = []
        return result

    # 切り戻しも同じように検証する（戻れない手順を渡さない）
    if result.rollback:
        restored = apply_script(target, result.rollback_script, dialect)
        if not equivalent(restored, current, dialect, ()):
            result.rollback = []
            result.warnings.append(
                "切り戻しコマンドが現行の状態に戻りきらないため、出力していません。"
                "投入前に必ず現行 config を保存してください"
            )

    seen: set[str] = set()
    for command in result.commands:
        for message in dialect.risks_for(command.rendered):
            if message not in seen:
                seen.add(message)
                result.risks.append(message)

    if result.removed:
        result.warnings.append(
            "将来 config に書かれていない設定は削除として扱っています。"
            "既定値のままにしたいだけの設定が混ざっていないか確認してください"
        )

    return result
