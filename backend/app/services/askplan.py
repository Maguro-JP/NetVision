"""書いた文章から、増設の設計を起こす。

いまの入口は「機器を開いて、フォームに入れる」である。頼む側の頭の中には
「本社の A スイッチの下に B スイッチを足したい」という文があるのに、
それをフォームの項目に分解するのは頼む側の仕事になっていた。分解できるのは
台帳を知っている人だけなので、知らない人はそこで止まる。

そこで、文章をそのまま受けて、台帳と突き合わせて相手を特定する。

### 作りの決まり

- 当てるのは台帳にある名前だけ。文章から意味を推し量ったりはしない。
  「たぶんこの機器だろう」で別の機器の設計を出すのがいちばん困る
- 読み取った内容を必ず返す。人が見て違っていれば直せる形にする
- 決められないときは、決めずに候補を返す。1つに絞れないまま進めない
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, Site

#: 「増やす」と読み取る言い方。現場で実際に使われる言い方だけを載せる
_ADD_WORDS = ("増設", "追加", "足し", "足す", "新設", "つなぎ", "つなげ", "ぶら下げ")

#: 「その下に」と読み取る言い方
_UNDER_WORDS = ("の下", "配下", "の下部", "にぶら下げ", "につなぐ", "につな")

#: 機器の名前らしい並び。英数字と記号でできた語を拾う
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{2,}")


@dataclass
class Reading:
    """文章をどう読み取ったか。人が見て直せるように、必ず返す。"""

    site: str | None = None
    site_id: int | None = None
    upstream_hostname: str | None = None
    upstream_device_id: int | None = None
    new_hostname: str | None = None
    purpose: str = ""
    #: どこをどう読んだか。当てずっぽうでないことを示す
    notes: list[str] = field(default_factory=list)


@dataclass
class Understanding:
    reading: Reading
    #: 1つに絞れなかったときの候補。絞れていれば空
    upstream_choices: list[dict] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.reading.upstream_device_id is not None and not self.questions


def understand(db: Session, text: str) -> Understanding:
    """文章を読み取る。設計はまだ起こさない。"""
    reading = Reading()
    result = Understanding(reading=reading)
    body = (text or "").strip()
    if not body:
        result.questions.append("何をしたいか書いてください。")
        return result

    _read_site(db, body, reading)
    matched = _read_upstream(db, body, reading, result)
    _read_new_name(body, reading, matched)
    _read_purpose(body, reading)

    if reading.upstream_device_id is None and not result.upstream_choices:
        result.questions.append(
            "どの機器の下に足すのかが分かりませんでした。"
            "台帳にある機器の名前（ホスト名か管理ID）を入れてください。"
        )
    if reading.new_hostname is None:
        result.questions.append("足す機器の名前が読み取れませんでした。")
    return result


def _read_site(db: Session, text: str, reading: Reading) -> None:
    """拠点の名前が書いてあれば拾う。同じ名前の機器が複数あるときの絞りに使う。"""
    best: Site | None = None
    for site in db.scalars(select(Site)):
        if site.name and site.name in text:
            if best is None or len(site.name) > len(best.name):
                best = site  # 長く一致したほうが確からしい
    if best is None:
        return
    reading.site = best.name
    reading.site_id = best.id
    reading.notes.append(f"「{best.name}」を拠点として読みました。")


def _read_upstream(
    db: Session, text: str, reading: Reading, result: Understanding
) -> Device | None:
    """つなぐ先の機器を、台帳の名前と突き合わせて決める。

    文章から意味を推し量ったりはしない。台帳にある名前が文章に出てきた
    ときだけ当てる。出てこなければ「分からない」と返す。
    """
    lowered = text.lower()
    hits: list[tuple[int, Device]] = []
    for device in db.scalars(select(Device)):
        for name in (device.hostname, device.code, device.label):
            if not name:
                continue
            position = lowered.find(name.lower())
            if position >= 0:
                hits.append((position, device))
                break

    if not hits:
        return None

    if reading.site_id is not None:
        scoped = [(p, d) for p, d in hits if d.site_id == reading.site_id]
        if scoped:
            hits = scoped

    # 「の下」より前に出てくる名前を、つなぐ先とみなす。
    # 「A の下に B」と書いたときに、B を上位と読み違えないようにする
    under = _under_position(text)
    if under is not None:
        before = [(p, d) for p, d in hits if p < under]
        if before:
            hits = before

    hits.sort(key=lambda item: item[0])
    unique: list[Device] = []
    for _position, device in hits:
        if device not in unique:
            unique.append(device)

    if len(unique) > 1 and under is None:
        result.upstream_choices = [
            {
                "device_id": device.id,
                "hostname": device.hostname,
                "site": device.site.name if device.site else None,
            }
            for device in unique[:10]
        ]
        result.questions.append(
            "どの機器の下に足すのか、1つに絞れませんでした。候補から選んでください。"
        )
        return None

    chosen = unique[0]
    reading.upstream_device_id = chosen.id
    reading.upstream_hostname = chosen.hostname
    reading.notes.append(f"「{chosen.hostname}」につなぐものとして読みました。")
    return chosen


def _under_position(text: str) -> int | None:
    for word in _UNDER_WORDS:
        found = text.find(word)
        if found >= 0:
            return found
    return None


def _read_new_name(text: str, reading: Reading, upstream: Device | None) -> None:
    """足す機器の名前を拾う。

    「B を増設」のように、増やすという語の直前に出てくる名前を採る。
    見つからなければ、つなぐ先の名前から続き番号を作る。勝手に決めたことは
    読み取りの記録に残す。
    """
    names = [(m.start(), m.group(0)) for m in _NAME.finditer(text)]
    upstream_names = set()
    if upstream is not None:
        upstream_names = {
            (upstream.hostname or "").lower(),
            (upstream.code or "").lower(),
            (upstream.label or "").lower(),
        }

    add_at = _add_position(text)
    candidates = [
        (position, name)
        for position, name in names
        if name.lower() not in upstream_names and (add_at is None or position < add_at)
    ]
    if candidates:
        candidates.sort(key=lambda item: item[0])
        reading.new_hostname = candidates[-1][1]
        reading.notes.append(f"「{reading.new_hostname}」を足す機器の名前として読みました。")
        return

    if upstream is not None:
        reading.new_hostname = _next_name(upstream.hostname)
        reading.notes.append(
            f"足す機器の名前が書かれていないので、{upstream.hostname} から続けて"
            f"「{reading.new_hostname}」にしました。変えたいときは書き換えてください。"
        )


def _add_position(text: str) -> int | None:
    for word in _ADD_WORDS:
        found = text.find(word)
        if found >= 0:
            return found
    return None


def _next_name(hostname: str) -> str:
    """末尾の番号を1つ進める。無ければ -02 を付ける。"""
    match = re.search(r"^(.*?)(\d+)$", hostname or "")
    if match:
        head, number = match.group(1), match.group(2)
        return f"{head}{int(number) + 1:0{len(number)}d}"
    return f"{hostname}-02"


def _read_purpose(text: str, reading: Reading) -> None:
    """用途らしい語を拾う。VLAN の名前と description に使う。"""
    for word in ("無線", "事務所", "会議室", "サーバ", "カメラ", "電話", "工場", "倉庫"):
        if word in text:
            reading.purpose = word
            reading.notes.append(f"用途を「{word}」として読みました。")
            return
