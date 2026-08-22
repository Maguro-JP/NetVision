"""選んだところだけを直してもらう。

Config を全部作り直すのは危ない。1行直したいだけなのに全体が返ってくると、
どこが変わったのかを人が突き合わせることになり、結局読み切れない。

そこで、選んだ範囲だけを渡して、その範囲だけを返してもらう。返ってきたもの
は差分にして見せ、採るかどうかは人が決める。

### 決めごと

- 直すのは選んだ範囲だけ。範囲の外は触らせない（返ってきても捨てる）
- 出すのは差分。全体を上書きした結果だけを見せると、確認が飛ばされる
- モデルが無くても動く。規則で書ける直し（説明の追加、ポートの閉じ方など）は
  規則で書く。書けないものは、貼るための依頼文を返す
- 秘密は渡さない。選んだ範囲にパスワードが入っていたら伏せてから渡す
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from app.services import llm, secrets

_SYSTEM = (
    "あなたはネットワーク機器の設定を直す担当です。"
    "渡された設定の断片だけを直し、直した断片だけを返してください。"
    "説明や囲みは書かないでください。コマンドの行だけを返してください。"
    "渡された範囲に無い設定を足さないでください。"
)


@dataclass
class EditResult:
    """直した結果。採るかどうかは人が決める。"""

    before: str
    after: str
    diff: str
    source: str  # rule / model / prompt
    #: どう直したか、何を直さなかったか
    notes: list[str] = field(default_factory=list)
    prompt: str | None = None

    @property
    def changed(self) -> bool:
        return self.before.strip() != self.after.strip()


#: 規則で書ける直し。よく頼まれるものから順に見る。
#: モデルに頼むより速く、毎回同じ結果になり、間違えたときに直せる
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(閉じ|shut|止め|使わない|塞)", re.IGNORECASE), "shutdown"),
    (re.compile(r"(開け|開放|有効|使う|上げ|no shut)", re.IGNORECASE), "no_shutdown"),
    (re.compile(r"(説明|description|名前を付け)", re.IGNORECASE), "description"),
    (re.compile(r"(telnet.*(やめ|止め|禁止)|ssh.*(だけ|のみ))", re.IGNORECASE), "ssh_only"),
    (re.compile(r"(http.*(閉じ|止め|無効|切))", re.IGNORECASE), "no_http"),
)


def edit(selection: str, request: str, *, description: str = "") -> EditResult:
    """選んだ範囲を、頼まれたとおりに直す。"""
    before = selection.rstrip("\n")
    if not before.strip():
        return EditResult(
            before="",
            after="",
            diff="",
            source="rule",
            notes=["直す範囲が選ばれていません。"],
        )

    after, notes = _by_rule(before, request, description)
    if after is not None:
        return _result(before, after, "rule", notes)

    material = _material(before, request)
    answer = llm.ask(material, system=_SYSTEM)
    if answer is not None:
        cleaned = _clean(answer.text)
        return _result(
            before,
            cleaned,
            "model",
            [
                f"手元のモデル（{answer.model}）が直しました。",
                "選んだ範囲の外は触っていません。差分を見て、採るかどうか決めてください。",
            ],
        )

    return EditResult(
        before=before,
        after=before,
        diff="",
        source="prompt",
        notes=[
            "手元に直せるモデルがありません。下の依頼文を、"
            "会社で認められている AI に貼って、返ってきた行を貼り戻してください。"
        ],
        prompt=material,
    )


def _by_rule(before: str, request: str, description: str) -> tuple[str | None, list[str]]:
    """規則で書ける直しは、規則で書く。"""
    kind = None
    for pattern, name in _RULES:
        if pattern.search(request):
            kind = name
            break
    if kind is None:
        return None, []

    lines = before.splitlines()
    if kind == "shutdown":
        return _ensure(lines, " shutdown", remove=" no shutdown"), [
            "選んだポートを閉じました。使っていないポートは閉じておくのが基本です。"
        ]
    if kind == "no_shutdown":
        return _ensure(lines, " no shutdown", remove=" shutdown"), ["選んだポートを開けました。"]
    if kind == "description":
        text = description or _guess_description(request)
        if not text:
            return None, []
        return _ensure(lines, f" description {text}", replace_prefix=" description"), [
            f"説明を「{text}」にしました。"
            "障害のときに、抜いてよい線かどうかの判断がここで決まります。"
        ]
    if kind == "ssh_only":
        return _ensure(lines, " transport input ssh", replace_prefix=" transport input"), [
            "telnet で入れないようにしました。中身が丸見えになるためです。"
        ]
    if kind == "no_http":
        out = [line for line in lines if not re.match(r"^\s*ip http", line, re.IGNORECASE)]
        out.append("no ip http server")
        out.append("no ip http secure-server")
        return "\n".join(out), ["管理用の web を閉じました。"]
    return None, []


def _ensure(
    lines: list[str],
    wanted: str,
    *,
    remove: str | None = None,
    replace_prefix: str | None = None,
) -> str:
    """その行を入れる。反対の行があれば外す。同じ行は二重に足さない。"""
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if remove and stripped == remove.strip():
            continue
        if replace_prefix and stripped.startswith(replace_prefix.strip()):
            continue
        if stripped == wanted.strip():
            continue
        out.append(line)
    out.append(wanted)
    return "\n".join(out)


def _guess_description(request: str) -> str:
    """「説明を〇〇にして」から〇〇を拾う。ASCII に寄せる（化けるため）。"""
    match = re.search(r"[「『]([^」』]+)[」』]", request)
    text = match.group(1) if match else ""
    if not text:
        match = re.search(r"(?:description|説明)\s*(?:を|は|:)?\s*([A-Za-z0-9_\- ]+)", request)
        text = match.group(1).strip() if match else ""
    return "".join(ch for ch in text if ch.isascii() and (ch.isalnum() or ch in "-_ ")).strip()


def _material(before: str, request: str) -> str:
    """渡す材料。秘密は伏せてから渡す。"""
    hidden = secrets.mask(before)
    parts = [
        "次の設定の断片を直してください。",
        f"やりたいこと: {request}",
        "",
        "直す範囲（この範囲だけを返してください）:",
        hidden.text,
        "",
        "返すのはコマンドの行だけにしてください。説明は書かないでください。",
    ]
    if hidden.counts:
        parts.insert(3, f"（{secrets.describe(hidden.counts)}。伏せた行はそのまま返してください）")
    return "\n".join(parts)


def _clean(text: str) -> str:
    """返ってきたものから、囲みや説明を落とす。

    説明付きで返ってくることがある。そのまま貼ると、説明が機器に入る。
    """
    lines = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            continue
        lines.append(line)
    return "\n".join(lines).strip("\n")


def _result(before: str, after: str, source: str, notes: list[str]) -> EditResult:
    diff = "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="いまの設定",
            tofile="直したあと",
            lineterm="",
        )
    )
    return EditResult(before=before, after=after, diff=diff, source=source, notes=notes)
