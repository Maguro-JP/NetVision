"""生成の前に、決まるものを決めておく。

Config を丸ごとモデルに書かせると、決まりきったところまで毎回書き直される。
書き直されるたびに間違える余地があり、しかも間違え方が毎回違うので、
人が確かめる手間が減りません。

そこで順番を変えます。まず台帳と現場の流儀から、値と行をコードで確定します。
残った穴だけをモデルに頼み、返ってきた行はその穴にだけ差し込みます。
穴が無ければモデルを呼びません。

決めた値には必ず理由を付けます。決められなかったものは埋めずに穴として残します。
当てずっぽうで埋めた行は、確かめる側から見ると正しく書けた行と見分けが付きません。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Device
from app.services import addition, calc, config_plans, conventions
from app.services.configdiff.dialects import for_vendor

#: 依頼文から拾う値。書いてあるものは、それが正しいものとして使う
_VLAN = re.compile(r"vlan\s*(\d{1,4})", re.IGNORECASE)
_PORT = re.compile(
    r"\b((?:Gi|Te|Fa|Eth?|GigabitEthernet|TenGigabitEthernet|FastEthernet|Ethernet)"
    r"[a-zA-Z]*\s?\d+(?:/\d+)*)\b",
    re.IGNORECASE,
)
_SUBNET = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})\b")
_NAME = re.compile(r"(?:名前|name)\s*(?:は|:|：)?\s*([A-Za-z0-9_-]{2,32})")


@dataclass
class Decided:
    """コードで決めた値と、その理由。"""

    key: str
    value: str
    why: str


@dataclass
class Gap:
    """決められなかったところ。埋めずに残す。"""

    key: str
    question: str
    why: str


@dataclass
class Prefill:
    """下ごしらえの結果。"""

    #: ロジックだけで書けた行
    lines: list[str] = field(default_factory=list)
    decided: list[Decided] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: 何の依頼として読んだか
    intent: str = ""

    @property
    def complete(self) -> bool:
        """モデルを呼ばずに済むか。"""
        return bool(self.lines) and not self.gaps

    def decide(self, key: str, value: str, why: str) -> str:
        self.decided.append(Decided(key=key, value=value, why=why))
        return value

    def gap(self, key: str, question: str, why: str) -> None:
        self.gaps.append(Gap(key=key, question=question, why=why))


# ---------------------------------------------------------------------------
# 何を頼まれたかを読む（言葉の揺れは表で吸収する）

_INTENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("add_vlan", ("vlan を足", "vlan を作", "vlan追加", "vlan を追加", "セグメントを足")),
    ("access_port", ("アクセスポート", "アクセスに", "収容", "ぶら下げ", "つなぐポート")),
    ("trunk_port", ("トランク", "trunk", "全部の vlan を通", "vlan を通す")),
    ("shutdown", ("閉じ", "閉塞", "止め", "shutdown", "使わなくな")),
    ("no_shutdown", ("開け", "開放", "使えるように", "no shutdown", "有効に")),
    ("describe", ("説明を", "description", "名前を付け", "ラベル")),
    ("baseline", ("管理まわり", "いつもの設定", "初期設定", "ベースライン", "決まり文句")),
)


#: 当てる前に落とすもの。「VLAN 30 を足したい」の 30 が語の間に入るだけで
#: 読めなくなると、書き方の揺れに弱すぎる
_NOISE = re.compile(r"[\s\d/.:：、。]+")


def read_intent(request: str) -> str:
    """依頼文が、どの型の作業かを読む。当てはまらなければ空。"""
    text = _NOISE.sub("", request.lower())
    for name, words in _INTENTS:
        if any(_NOISE.sub("", word.lower()) in text for word in words):
            return name
    return ""


# ---------------------------------------------------------------------------
# 下ごしらえ


def build(db: Session, device: Device, request: str, *, vendor: str = "") -> Prefill:
    """依頼から、決まる値と書ける行を先に出す。

    書けるのは、いまのところ Cisco 系の書き方が通る機器だけです。それ以外は
    値を決めるところまでを渡し、行はモデルに書いてもらいます。ベンダーごとの
    書き方をここに増やせば、そのぶんモデルの仕事が減ります。
    """
    result = Prefill()
    result.intent = read_intent(request)
    if not result.intent:
        result.notes.append(
            "決まった型の依頼として読めませんでした。値の下ごしらえはせず、そのまま渡します"
        )
        return result

    dialect = for_vendor(vendor or config_plans.vendor_from_name(device.vendor))
    if dialect.name != "cisco":
        result.notes.append(
            f"{dialect.name} の書き方はまだ表に入れていないので、行はモデルに書いてもらいます。"
            "値の下ごしらえだけ先にしています"
        )

    style = conventions.learn(db)
    if result.intent == "add_vlan":
        _add_vlan(db, result, request, style)
    elif result.intent in {"access_port", "trunk_port"}:
        _port(db, result, request, device)
    elif result.intent in {"shutdown", "no_shutdown", "describe"}:
        _port_simple(result, request)
    elif result.intent == "baseline":
        _baseline(result, style)

    if dialect.name != "cisco":
        # 値は決めたが、行は書かない。書き方を知らないまま並べるより確かめやすい
        result.lines = []
    return result


def _add_vlan(db: Session, result: Prefill, request: str, style: conventions.Conventions) -> None:
    """VLAN を足す。番号もセグメントも、書いていなければ空きから決める。"""
    numbers = calc.vlan_numbers(db)
    written = _VLAN.search(request)
    if written:
        vlan_id = int(written.group(1))
        if vlan_id in numbers.used:
            result.gap(
                "vlan_id",
                f"VLAN {vlan_id} は既に使われています。別の番号にしますか",
                "使用中の番号に重ねると、別の用途のセグメントが混ざります",
            )
            return
        result.decide("vlan_id", str(vlan_id), "依頼文に書かれていた番号です")
    else:
        free = calc.free_vlan_ids(set(numbers.used), want=1, start=style.vlan_step or 2)
        if not free:
            result.gap("vlan_id", "使える VLAN 番号がありません", "空き番号が見つかりません")
            return
        vlan_id = free[0]
        step = f"（この現場は {style.vlan_step} 刻みです）" if style.vlan_step else ""
        result.decide("vlan_id", str(vlan_id), f"使われていない一番若い番号です{step}")

    name = _NAME.search(request)
    if name:
        result.decide("name", name.group(1), "依頼文に書かれていた名前です")
    else:
        result.gap(
            "name",
            f"VLAN {vlan_id} の名前は何にしますか",
            "用途は台帳からは決まりません。何のためのセグメントかは人しか知りません",
        )

    subnet = _SUBNET.search(request)
    if subnet:
        chosen = result.decide(
            "subnet", f"{subnet.group(1)}/{subnet.group(2)}", "依頼文に書かれていたセグメントです"
        )
    else:
        used = [s.subnet for s in calc.subnet_usage(db)]
        picked = addition.pick_subnet(used, vlan_id)
        if picked:
            chosen = result.decide(
                "subnet", picked, "いまの採番の並びに続けた、どことも重ならないセグメントです"
            )
        else:
            chosen = ""
            result.gap(
                "subnet",
                f"VLAN {vlan_id} に割り当てるセグメントは",
                "いまの採番から並びが読めませんでした。当てずっぽうで割ると他拠点とぶつかります",
            )

    result.lines = [f"vlan {vlan_id}"]
    if name:
        result.lines.append(f" name {name.group(1)}")
    if chosen:
        network = calc.usable_hosts(chosen)
        result.lines.append(f"interface Vlan{vlan_id}")
        gateway, _ = addition.addresses_in(chosen)
        result.lines.append(f" ip address {gateway} {addition.netmask_of(chosen)}")
        result.lines.append(" no shutdown")
        if network:
            result.notes.append(f"このセグメントには {network} 台まで入ります")


def _port(db: Session, result: Prefill, request: str, device: Device) -> None:
    """ポートの設定。ポート名と VLAN 番号が要る。"""
    port = _PORT.search(request)
    if port:
        result.decide("port", port.group(1), "依頼文に書かれていたポートです")
    else:
        free = [p for p in calc.port_usage(db) if p.device_id == device.id]
        remaining = free[0].free if free else 0
        result.gap(
            "port",
            "どのポートに入れますか",
            f"空きは {remaining} 本ありますが、どれに挿すかは実機を見た人しか決められません"
            if remaining
            else "空きポートが分かりません",
        )

    written = _VLAN.search(request)
    if written:
        result.decide("vlan_id", written.group(1), "依頼文に書かれていた番号です")
    elif result.intent == "access_port":
        result.gap(
            "vlan_id",
            "どの VLAN に入れますか",
            "どのセグメントに置くかは用途で決まるので、台帳からは決まりません",
        )

    if not port:
        # 対象が決まらないうちは1行も書かない。別のポートに入る事故が最悪
        return

    name = port.group(1)
    result.lines = [f"interface {name}"]
    if result.intent == "access_port":
        result.lines += [" switchport", " switchport mode access"]
        if written:
            result.lines.append(f" switchport access vlan {written.group(1)}")
        result.lines += [" spanning-tree portfast", " no shutdown"]
        result.notes.append(
            "端末をつなぐポートなので portfast を入れています。スイッチをつなぐ場合は外してください"
        )
    else:
        result.lines += [
            " switchport",
            " switchport mode trunk",
            " no shutdown",
        ]
        result.notes.append(
            "通す VLAN を絞る場合は switchport trunk allowed vlan を足してください。"
            "書かない場合は全部通ります"
        )


def _port_simple(result: Prefill, request: str) -> None:
    """閉じる・開ける・説明を入れる。値が1つ決まれば書ける。"""
    port = _PORT.search(request)
    if not port:
        result.gap("port", "どのポートですか", "対象が決まらないまま書くと、別のポートが止まります")
        return
    name = result.decide("port", port.group(1), "依頼文に書かれていたポートです")

    if result.intent == "shutdown":
        result.lines = [f"interface {name}", " shutdown"]
        result.notes.append("このポートの通信は止まります")
    elif result.intent == "no_shutdown":
        result.lines = [f"interface {name}", " no shutdown"]
    else:
        text = _NAME.search(request)
        result.lines = [f"interface {name}"]
        if text:
            result.lines.append(f" description {text.group(1)}")
        else:
            result.gap(
                "description",
                f"{name} に何と書きますか",
                "つないでいる先の名前は、台帳からは決まりません",
            )


def _baseline(result: Prefill, style: conventions.Conventions) -> None:
    """管理まわりの決まり文句。覚えた流儀をそのまま写す。"""
    if not style.baseline_lines:
        result.gap(
            "baseline",
            "この現場の決まり文句が分かりません",
            "Config をいくつか取り込むと、どの機器にも入っている行を覚えます",
        )
        return
    result.lines = list(style.baseline_lines)
    result.notes.append(
        f"{style.devices_seen} 台の Config を見て、どれにも入っていた行をそのまま写しています"
    )
