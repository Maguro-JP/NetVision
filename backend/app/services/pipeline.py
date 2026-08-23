"""人 → AI → ロジック → AI → ロジック → AI → 人。

AI に全部やらせると、決まりきったところまで毎回書き直され、そのたびに
間違える余地が増えます。かといってロジックだけにすると、言葉の揺れを
受け止められず、出てきたものの意味も伝わりません。

得意なところだけを交互に使います。

  1. 聞き取り（AI）  … 言い回しを、こちらが扱える形の依頼に直す
  2. 下ごしらえ（ロジック）… 決まる値と書ける行を確定する
  3. 穴埋め（AI）    … 残った穴の行だけを書かせる
  4. 検め（ロジック）… 出てきた行を、資料・危なさ・流儀と突き合わせる
  5. 言い直し（AI）  … 何をしたのかを、専門用語なしで言う

AI の段は、どれも値を決めません。1 は言い換えるだけ、3 は穴の行だけ、
5 は説明だけです。決めるのは必ずロジックの段で、AI が出したものは
次のロジックの段が必ず受けます。AI が使えない環境では、その段を飛ばしても
道具として成立するようにしてあります（飛ばしたことは残します）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Device
from app.services import config_draft, configcheck, glossary, llm, prefill

#: 聞き取りの段で、モデルに許すこと。値を作らせない
_UNDERSTAND_SYSTEM = """あなたはネットワーク運用の受付です。
依頼の文を、短い1文に言い直してください。

- 書かれていない機器名・ポート名・番号・アドレスを足さない
- 分からないところは、分からないまま残す
- 言い直した1文だけを出す。説明は書かない
"""

_EXPLAIN_SYSTEM = """あなたはネットワークに詳しくない人に説明する担当です。
渡された事実だけを使って、何をしたのかを短く説明してください。

- 設定のコマンドは書かない
- 専門用語を使うときは、その場で言い換える
- 渡された事実に無いことは書かない
"""

#: 依頼文に無い値を、モデルが作っていないかを見るための形
_VALUES = re.compile(
    r"\b(?:\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?"
    r"|(?:Gi|Te|Fa|Eth?|GigabitEthernet|TenGigabitEthernet|FastEthernet|Ethernet)"
    r"[a-zA-Z]*\s?\d+(?:/\d+)*"
    r"|vlan\s*\d{1,4})\b",
    re.IGNORECASE,
)

#: 説明に混ざってはいけない形。コマンドが混ざると、貼れる設定に見えてしまう
_COMMANDISH = re.compile(
    r"^\s*(?:interface|switchport|ip |no |vlan \d|hostname|router |snmp-server)", re.IGNORECASE
)


@dataclass
class Stage:
    """通り道の1段。何が担当し、何をしたか。"""

    name: str
    #: "ai" か "logic"
    kind: str
    summary: str
    #: 飛ばした場合の理由。動いた段では空
    skipped: str = ""
    #: その段を担当したモデル
    model: str | None = None


@dataclass
class Run:
    """通り道と、その結果。"""

    request: str
    #: 聞き取りのあとの依頼文。飛ばしたときは元のまま
    understood: str = ""
    body: str = ""
    decided: list[prefill.Decided] = field(default_factory=list)
    gaps: list[prefill.Gap] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    ungrounded: list[str] = field(default_factory=list)
    explanation: str = ""
    stages: list[Stage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def stage(self, name: str, kind: str, summary: str, **extra: object) -> None:
        self.stages.append(Stage(name=name, kind=kind, summary=summary, **extra))  # type: ignore[arg-type]


def run(db: Session, device: Device, request: str, *, base: str | None = None) -> Run:
    """1本通す。どの段が何をしたかを残す。"""
    result = Run(request=request)

    result.understood = _understand(result, request)
    ready = _prepare(result, db, device, result.understood)
    draft = _fill(result, db, device, result.understood, ready, base=base)
    _verify(result, db, draft)
    result.explanation = _explain(result, device)

    result.stage("受け取り", "human", "ここから先は人が決めます")
    result.notes.append("ここで作るのは下書きだけです。実機へは投入しません")
    return result


# ---------------------------------------------------------------------------
# 1. 聞き取り（AI）


def _understand(result: Run, request: str) -> str:
    """言い回しを、扱える形に直す。値は増やさせない。"""
    answer = llm.ask(request, system=_UNDERSTAND_SYSTEM, role=llm.QUICK, temperature=0.0)
    if answer is None:
        result.stage(
            "聞き取り",
            "ai",
            "依頼文をそのまま使いました",
            skipped="モデルが使えないため",
        )
        return request

    rewritten = answer.text.strip().splitlines()[0].strip() if answer.text.strip() else ""
    if not rewritten:
        result.stage("聞き取り", "ai", "言い直しが空でした", skipped="使えない答えのため")
        return request

    added = _invented_values(request, rewritten)
    if added:
        # 書かれていない値を足してきたら使わない。ここで受けると、
        # 存在しないポートの設定が最後まで通ってしまう
        result.stage(
            "聞き取り",
            "ai",
            f"言い直しに、依頼に無い値（{', '.join(added)}）が混ざったので使いませんでした",
            skipped="値を作ったため",
            model=answer.model,
        )
        return request

    result.stage("聞き取り", "ai", f"「{rewritten}」として読みました", model=answer.model)
    return rewritten


def _invented_values(request: str, rewritten: str) -> list[str]:
    """言い直しに、元の文に無い値が入っていないか。"""
    before = {m.group(0).lower().replace(" ", "") for m in _VALUES.finditer(request)}
    after = {m.group(0).lower().replace(" ", "") for m in _VALUES.finditer(rewritten)}
    return sorted(after - before)


# ---------------------------------------------------------------------------
# 2. 下ごしらえ（ロジック）


def _prepare(result: Run, db: Session, device: Device, request: str) -> prefill.Prefill:
    ready = prefill.build(db, device, request)
    result.decided = ready.decided
    result.gaps = ready.gaps
    result.notes.extend(ready.notes)
    if ready.complete:
        summary = f"決まった値 {len(ready.decided)} 件。書けた行 {len(ready.lines)} 行"
    elif ready.decided:
        summary = f"決まった値 {len(ready.decided)} 件。残りは {len(ready.gaps)} 件"
    else:
        summary = "決められることがありませんでした"
    result.stage("下ごしらえ", "logic", summary)
    return ready


# ---------------------------------------------------------------------------
# 3. 穴埋め（AI）


def _fill(
    result: Run,
    db: Session,
    device: Device,
    request: str,
    ready: prefill.Prefill,
    *,
    base: str | None,
) -> config_draft.Draft:
    if ready.complete:
        result.stage(
            "穴埋め",
            "ai",
            "埋めるところがありませんでした",
            skipped="決まっていることだけで書けたため",
        )
    draft = config_draft.draft(db, device, request, base=base, ready=ready)
    result.body = draft.body
    if not ready.complete:
        if draft.model:
            result.stage(
                "穴埋め",
                "ai",
                f"足りない {len(ready.gaps)} 件ぶんの行を書かせました",
                model=draft.model,
            )
        else:
            result.stage(
                "穴埋め",
                "ai",
                "資料から拾ったコマンドを並べました",
                skipped="モデルが使えないため",
            )
    return draft


# ---------------------------------------------------------------------------
# 4. 検め（ロジック）


def _verify(result: Run, db: Session, draft: config_draft.Draft) -> None:
    """出てきた行を突き合わせる。AI が出したものは必ずここを通る。"""
    result.risks = list(draft.risks)
    result.ungrounded = draft.ungrounded

    if result.body.strip():
        seen = configcheck.check(db, result.body, filename="下書き.txt")
        result.findings = [f"{f.title}：{f.why}" for f in seen.findings]
    summary = (
        f"危ない行 {len(result.risks)} 件、資料に裏付けが無い行 {len(result.ungrounded)} 行、"
        f"見て気づいたこと {len(result.findings)} 件"
    )
    result.stage("検め", "logic", summary)


# ---------------------------------------------------------------------------
# 5. 言い直し（AI）


def _explain(result: Run, device: Device) -> str:
    """何をしたのかを、専門用語なしで言う。事実はロジックの段から渡す。"""
    facts = _facts(result, device)
    answer = llm.ask(facts, system=_EXPLAIN_SYSTEM, role=llm.QUICK, temperature=0.0)
    if answer is None:
        result.stage(
            "言い直し", "ai", "決まった書き方で説明を組み立てました", skipped="モデルが使えないため"
        )
        return _plain(result, device)

    text = "\n".join(
        line for line in answer.text.strip().splitlines() if not _COMMANDISH.match(line)
    ).strip()
    if not text:
        result.stage("言い直し", "ai", "説明が空でした", skipped="使えない答えのため")
        return _plain(result, device)

    result.stage("言い直し", "ai", "専門用語を避けて言い直しました", model=answer.model)
    return text


def _facts(result: Run, device: Device) -> str:
    parts = [f"機器: {device.hostname}", f"依頼: {result.understood}"]
    if result.decided:
        parts.append("決めたこと:")
        parts += [f"- {d.key} を {d.value} にした（{d.why}）" for d in result.decided]
    if result.gaps:
        parts.append("決められなかったこと:")
        parts += [f"- {g.question}（{g.why}）" for g in result.gaps]
    if result.risks:
        parts.append("気をつけること:")
        parts += [f"- {r}" for r in result.risks]
    return "\n".join(parts)


#: 決めた値のキーを、そのまま出しても分かる言い方に直す
_WORDS = {
    "vlan_id": "VLAN の番号",
    "subnet": "セグメント（住所の範囲）",
    "port": "ポート",
    "name": "名前",
    "description": "説明",
    "baseline": "管理まわりの決まり文句",
}


def _word(key: str) -> str:
    """キーを、知らない人にも通じる言い方にする。"""
    if key in _WORDS:
        return _WORDS[key]
    found = glossary.find(key)
    return found.term if found else key


def _plain(result: Run, device: Device) -> str:
    """モデルが無いときの説明。用語集の言い換えを使う。"""
    lines = [f"{device.hostname} に入れる設定の下書きを作りました。"]
    for item in result.decided:
        lines.append(f"・{_word(item.key)} は {item.value} にしました（{item.why}）")
    for gap in result.gaps:
        lines.append(f"・{gap.question}（{gap.why}）")
    if result.risks:
        lines.append("・通信に影響する行が含まれます：" + "／".join(result.risks))
    lines.append("入れる前に、上の行をそのまま人が読める形で確かめてください。")
    return "\n".join(lines)
