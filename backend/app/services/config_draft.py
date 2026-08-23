"""資料を根拠に Config を下書きする。

書く手間そのものより、「その機種でその書き方が正しいか」を調べる時間の方が
長い。ここは、取り込んだベンダー資料の該当箇所を先に引き当て、それだけを
材料に下書きを起こすための処理。

作りは3段になっている。

  1. 引く   … 資料の中から、依頼に関係する章を探す（DB の中だけで完結）
  2. 起こす … モデルがあれば、引いた章を材料に下書きさせる
  3. 検める … 出てきた行を、資料に出てくる書き方と突き合わせ、危ない行に印を付ける

3 は必ず通す。モデルの出力をそのまま人へ渡すと、資料に無い書き方が
混ざっていても気付けない。逆にモデルが無い環境でも、1 と 3 は動くので、
資料から拾ったコマンドを手がかりとして返す。

生成するだけで、実機には投入しない。

## 手元にモデルを置けない場合

端末に何も入れられない職場がある。その場合でも、会社が承認している AI
（M365 Copilot など）を人が使うことはできる。そこで、材料を組み立てた
依頼文を出して人に渡し、返ってきた Config を貼り戻してもらう道を用意する。

生成をどこでやっても、検める側（資料との突き合わせと、差分・リスク判定）は
こちらに残る。ここが本体で、モデルは差し替えのきく部品として扱う。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Device
from app.services import config_plans, documents, llm, prefill
from app.services.configdiff.dialects import for_vendor

#: モデルへ渡す材料の上限。1台のサーバーで動かす前提なので、
#: 長く詰め込むより、当たりの良い章だけを渡した方が速くて外れが少ない
_MAX_SECTIONS = 6
_MAX_BODY = 1200
_MAX_CURRENT = 4000

#: 穴を埋めてもらうときの言い方。全部を書かせず、足りない行だけを書かせる
_FILL_SYSTEM = """あなたはネットワーク機器の設定を書く担当者です。
すでに決まっている行はそのまま使い、足りないところの行だけを書いてください。

- 出力は投入するコマンドの行だけ。説明文・記号での囲み・前置きは書かない
- すでに決まっている行は書き直さない
- 資料に書き方が無い部分は、その行を書かずに ! で始まる行に不足として残す
"""

_SYSTEM = """あなたはネットワーク機器の設定を書く担当者です。
渡された資料の抜粋に書かれている書き方だけを使って、依頼された設定を書いてください。

- 出力は投入するコマンドの行だけ。説明文・記号での囲み・前置きは書かない
- 資料に書き方が無い部分は、その行を書かずに ! で始まる行に不足として残す
- 値を仮で置いた場合は、その行の末尾に ! 要確認 を付ける
"""

_FENCE = re.compile(r"^```[a-zA-Z0-9]*$")


@dataclass
class Source:
    """下書きの根拠にした資料の1章。"""

    document_id: int
    title: str
    heading: str | None
    commands: list[str] = field(default_factory=list)


@dataclass
class Line:
    """下書きの1行と、その行に対する検めの結果。"""

    text: str
    #: 資料に同じ書き方が出てくるか
    grounded: bool = False
    risks: list[str] = field(default_factory=list)


@dataclass
class Draft:
    body: str
    lines: list[Line] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: 下書きを起こしたモデル。規則ベースで出したときは None
    model: str | None = None
    vendor: str = ""
    #: コードで先に決めたこと。モデルに渡す前に確定させたもの
    decided: list[prefill.Decided] = field(default_factory=list)
    #: コードでは決められず、モデルか人に回したところ
    gaps: list[prefill.Gap] = field(default_factory=list)

    @property
    def ungrounded(self) -> list[str]:
        """資料に裏付けが無かった行。人が最初に見るところ。"""
        return [line.text for line in self.lines if not line.grounded]


def _strip_fences(text: str) -> str:
    """囲みを外す。書くなと伝えても付けてくるモデルがあるため。"""
    return "\n".join(line for line in text.splitlines() if not _FENCE.match(line.strip()))


def _command_key(line: str) -> str:
    """コマンドの骨格。値の違いは無視して、書き方が同じかだけを見る。"""
    words = []
    for word in line.strip().split():
        if any(char.isdigit() for char in word):
            words.append("*")
        else:
            words.append(word.lower())
    return " ".join(words)


def _find_sections(db: Session, device: Device, request: str) -> list[Source]:
    """依頼に関係する章を引く。機種で絞って空なら、ベンダーだけで引き直す。"""
    hits = documents.search_sections(
        db, request, vendor=device.vendor or None, model=device.model or None, limit=_MAX_SECTIONS
    )
    if not hits:
        hits = documents.search_sections(
            db, request, vendor=device.vendor or None, limit=_MAX_SECTIONS
        )
    if not hits:
        hits = documents.search_sections(db, request, limit=_MAX_SECTIONS)
    return [
        Source(
            document_id=document.id,
            title=document.title,
            heading=section.heading,
            commands=[line for line in section.commands.splitlines() if line.strip()],
        )
        for document, section in hits
    ]


def _material(sources: list[Source], current: str, request: str) -> str:
    parts = ["# 資料の抜粋"]
    for source in sources:
        head = f"## {source.title}"
        if source.heading:
            head += f" / {source.heading}"
        parts.append(head)
        if source.commands:
            parts.append("\n".join(source.commands[:40])[:_MAX_BODY])
    if current:
        parts.append("# いま入っている設定（抜粋）")
        parts.append(current[:_MAX_CURRENT])
    parts.append("# 依頼")
    parts.append(request)
    return "\n\n".join(parts)


def _merge(decided: list[str], answer: str) -> str:
    """決まっていた行に、モデルが足した行だけを足す。

    同じ行は増やさない。決まっていた行の順番は変えない。書いた場所ごとに
    順番が変わると、差分が読めなくなる。
    """
    out = list(decided)
    seen = {line.strip() for line in decided}
    for line in answer.splitlines():
        if line.strip() and line.strip() not in seen:
            out.append(line)
            seen.add(line.strip())
    return "\n".join(out).strip()


def _fill_material(
    sources: list[Source], current: str, request: str, ready: prefill.Prefill
) -> str:
    """穴だけを埋めてもらうための材料。

    決まっている行を先に見せて「ここは書き直さなくていい」と伝える。全部を
    書かせると、決まりきったところまで毎回書き直され、そのたびに間違える
    余地が増える。
    """
    parts = [_material(sources, current, request)]
    if ready.decided:
        parts.append("# すでに決まっていること（変えないでください）")
        parts.append("\n".join(f"{d.key}: {d.value}" for d in ready.decided))
    if ready.lines:
        parts.append("# すでに書けている行（書き直さないでください）")
        parts.append("\n".join(ready.lines))
    if ready.gaps:
        parts.append("# 足りないところ（ここだけ書いてください）")
        parts.append("\n".join(f"- {gap.question}" for gap in ready.gaps))
    return "\n\n".join(parts)


def _check(body: str, sources: list[Source], vendor: str) -> tuple[list[Line], list[str]]:
    """行ごとに、資料の裏付けと危なさを見る。"""
    known = {_command_key(command) for source in sources for command in source.commands}
    dialect = for_vendor(vendor)
    lines: list[Line] = []
    risks: list[str] = []
    for text in body.splitlines():
        stripped = text.strip()
        if not stripped or stripped.startswith("!"):
            continue
        found = dialect.risks_for(stripped)
        for message in found:
            if message not in risks:
                risks.append(message)
        lines.append(
            Line(text=text, grounded=_command_key(stripped) in known, risks=found),
        )
    return lines, risks


def draft(db: Session, device: Device, request: str, *, base: str | None = None) -> Draft:
    """依頼の文と資料から、投入コマンドの下書きを作る。

    `base` を渡すとその config を前提に書く。省くと最新の取り込み済み config。
    """
    snapshot = config_plans.latest_snapshot(db, device.id)
    current = base if base is not None else (snapshot.content if snapshot else "")
    vendor = (snapshot.vendor if snapshot and snapshot.vendor else "") or (
        config_plans.vendor_from_name(device.vendor)
    )

    sources = _find_sections(db, device, request)
    result = Draft(body="", sources=sources, vendor=vendor)

    if not sources:
        result.notes.append(
            "依頼に関係する資料が見つかりませんでした。"
            "「ベンダー資料」にコマンドリファレンスを登録すると、それを根拠に書けます"
        )

    # 先に、台帳と流儀から決まるものを決める。ここで決まったぶんはモデルに渡さない
    ready = prefill.build(db, device, request, vendor=vendor)
    result.decided = ready.decided
    result.gaps = ready.gaps
    result.notes.extend(ready.notes)

    if ready.complete:
        # 穴が無いなら、モデルを呼ぶ理由がない。毎回同じ結果になり、速く、
        # 間違えたときにどこで間違えたかが分かる
        result.body = "\n".join(ready.lines)
        result.lines, result.risks = _check(result.body, sources, vendor)
        result.notes.append(
            f"決まっていることだけで書けたので、AI は使っていません"
            f"（決めた値 {len(ready.decided)} 件。理由は一緒に出しています）"
        )
        result.notes.append("ここで作るのは下書きだけです。実機へは投入しません")
        return result

    if ready.lines or ready.gaps:
        answer = llm.ask(
            _fill_material(sources, current, request, ready),
            system=_FILL_SYSTEM,
            role=llm.DEEP,
            temperature=0.0,
        )
    else:
        answer = llm.ask(
            _material(sources, current, request), system=_SYSTEM, role=llm.DEEP, temperature=0.0
        )
    if answer is None:
        # モデルが無くても、資料から拾ったコマンドは手がかりとして返す
        picked = list(ready.lines)
        for source in sources:
            for command in source.commands:
                if command not in picked:
                    picked.append(command)
        result.body = "\n".join(picked)
        result.notes.append(
            "モデルが使えないため、決まったぶんの行と、資料から拾ったコマンドを並べています。"
            "順番と値は人が直してください"
        )
    else:
        # 決まっていた行は残し、モデルが書いたのは足りないところだけを足す。
        # 返ってきた本文で置き換えると、確定していた行まで書き換わる
        result.body = _merge(ready.lines, _strip_fences(answer.text))
        result.model = answer.model

    result.lines, result.risks = _check(result.body, sources, vendor)

    if result.ungrounded:
        result.notes.append(
            f"資料に同じ書き方が見つからない行が {len(result.ungrounded)} 行あります。"
            "投入前にその行を確かめてください"
        )
    for gap in result.gaps:
        result.notes.append(f"{gap.question}：{gap.why}")
    result.notes.append("ここで作るのは下書きだけです。実機へは投入しません")
    return result


def prompt_for(db: Session, device: Device, request: str, *, base: str | None = None) -> Draft:
    """人が AI へ貼るための依頼文を組み立てる。

    モデルを置けない端末では、承認済みの AI を人が使うほうが現実的なため。
    `body` に依頼文を入れて返す（下書きそのものではない）。
    """
    snapshot = config_plans.latest_snapshot(db, device.id)
    current = base if base is not None else (snapshot.content if snapshot else "")
    vendor = (snapshot.vendor if snapshot and snapshot.vendor else "") or (
        config_plans.vendor_from_name(device.vendor)
    )
    sources = _find_sections(db, device, request)

    machine = f"{device.vendor} {device.model}".strip() or "ネットワーク機器"
    header = (
        f"{machine} の設定を書いてください。\n"
        "下に貼る資料の抜粋に書かれている書き方だけを使ってください。\n"
        "出力は投入するコマンドの行だけにしてください（説明・囲みは不要）。\n"
        "資料に書き方が無い部分は、その行を書かずに ! で始まる行に不足として残してください。\n"
    )
    result = Draft(
        body=header + "\n" + _material(sources, current, request),
        sources=sources,
        vendor=vendor,
    )
    if not sources:
        result.notes.append(
            "依頼に関係する資料が見つかりませんでした。資料を登録すると、"
            "その中身を根拠として依頼文に載せます"
        )
    result.notes.append(
        "この文を AI に貼り、返ってきた設定をこの画面に貼り戻してください。"
        "資料との突き合わせと、通信に影響する行の印は、こちらで付けます"
    )
    result.notes.append("実機の情報を外へ出すことになります。職場の決まりを確かめてください")
    return result


def review(db: Session, device: Device, request: str, body: str) -> Draft:
    """人が持ち帰った設定を検める。

    どこで書かれたものでも、通す道は同じにしておく。書いた場所によって
    確かめ方が変わると、確かめ漏れがそこに溜まる。
    """
    snapshot = config_plans.latest_snapshot(db, device.id)
    vendor = (snapshot.vendor if snapshot and snapshot.vendor else "") or (
        config_plans.vendor_from_name(device.vendor)
    )
    sources = _find_sections(db, device, request) if request.strip() else []
    result = Draft(body=_strip_fences(body).strip(), sources=sources, vendor=vendor)
    result.lines, result.risks = _check(result.body, sources, vendor)

    if not sources:
        result.notes.append("突き合わせる資料がありません。行の裏付けは確かめられていません")
    elif result.ungrounded:
        result.notes.append(
            f"資料に同じ書き方が見つからない行が {len(result.ungrounded)} 行あります。"
            "投入前にその行を確かめてください"
        )
    result.notes.append("ここで作るのは下書きだけです。実機へは投入しません")
    return result
