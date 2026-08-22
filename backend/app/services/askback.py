"""出した結果について、聞き返せるようにする。

指摘を出しても、「なぜそれが駄目なのか」「どう直すのか」が分からなければ
手が止まる。用語の説明は用意したが、その人のいまの状況に沿った答えには
ならない。

そこで、聞き返しを受ける口を用意する。作りは3段で、上から順に試す。

1. 用語や指摘の中に答えがあるなら、それをそのまま返す（モデルは要らない）
2. 手元にモデルがあるなら、読み取った内容を材料にして答えてもらう
3. どちらも無いなら、承認済みの AI へ貼るための依頼文を組み立てて返す

いちばん大事なのは、材料を必ず添えることである。何を見て答えたのかが
無い答えは、確かめようがない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.services import glossary, llm
from app.services.l3review import Finding

_SYSTEM = (
    "あなたはネットワークの担当者を助ける相手です。"
    "渡された材料に書いてあることだけを使って答えてください。"
    "材料に無いことは「材料からは分かりません」と書いてください。"
    "専門用語を使うときは、そのつど短く言い換えを添えてください。"
    "答えは日本語で、5行以内にしてください。"
)


@dataclass
class AskBackResult:
    """聞き返しへの答え。

    `source` は、どうやって答えたか。人が確かめるときの手がかりになる。
    """

    answer: str
    source: str  # glossary / model / prompt
    used: list[str] = field(default_factory=list)
    #: モデルが無いときに、承認済みの AI へ貼るための依頼文
    prompt: str | None = None
    note: str = ""


def ask(
    db: Session,
    question: str,
    *,
    findings: list[Finding] | None = None,
    context: str = "",
) -> AskBackResult:
    """聞かれたことに答える。答えられないときは、そう返す。"""
    question = (question or "").strip()
    if not question:
        return AskBackResult(answer="聞きたいことを書いてください。", source="glossary")

    hit = _from_glossary(question)
    if hit is not None:
        return hit

    material = _material(question, findings or [], context)

    answer = llm.ask(material, system=_SYSTEM)
    if answer is not None:
        return AskBackResult(
            answer=answer.text,
            source="model",
            used=[f"手元のモデル（{answer.model}）"],
            note="材料に書いてあることだけを使って答えるよう頼んでいますが、"
            "モデルの答えは確かめてから使ってください。",
        )

    return AskBackResult(
        answer="手元に答えられるモデルがありません。下の依頼文を、"
        "会社で認められている AI に貼って聞いてください。",
        source="prompt",
        prompt=material,
        note="貼る前に、社外に出してよい内容か確かめてください。管理IP や機器名が含まれています。",
    )


def _from_glossary(question: str) -> AskBackResult | None:
    """用語の説明で答えられるなら、それで答える。

    モデルを通すより速く、毎回同じ答えになり、人が直せる。
    """
    # まず、質問の中にその言葉が出てきているかを見る。人は「トランクって
    # 何ですか」と書くので、質問文そのもので引くと当たらない
    lowered = question.lower()
    found = [
        term
        for term in glossary.TERMS
        if term.term.lower() in lowered or any(alias.lower() in lowered for alias in term.also)
    ]
    if not found:
        found = glossary.search(question)
    if not found or len(found) > 3:
        return None

    lines = []
    for term in found:
        lines.append(f"【{term.term}】{term.plain}")
        lines.append(f"狂うと何が起きるか: {term.matters}")
    return AskBackResult(
        answer="\n".join(lines),
        source="glossary",
        used=[term.term for term in found],
        note="言葉の説明から答えました。状況に沿った答えが要るときは、"
        "もう少し詳しく書いて聞き直してください。",
    )


def _material(question: str, findings: list[Finding], context: str) -> str:
    """答えるための材料。何を見て答えたのかを、必ず添えられる形にする。"""
    parts = [f"質問: {question}", ""]

    if findings:
        parts.append("いま出ている指摘:")
        for finding in findings[:8]:
            parts.append(f"- {finding.title}")
            parts.append(f"  なぜ: {finding.why}")
            for line in finding.evidence[:3]:
                parts.append(f"  見たところ: {line}")
        parts.append("")

    if context:
        parts.append("対象の Config（抜粋）:")
        parts.append(context[:4000])
        parts.append("")

    words = [term for term in glossary.TERMS if term.term in question]
    if words:
        parts.append("言葉の意味:")
        for term in words[:3]:
            parts.append(f"- {term.term}: {term.plain}")
        parts.append("")

    parts.append("この材料に書いてあることだけを使って答えてください。")
    return "\n".join(parts)
