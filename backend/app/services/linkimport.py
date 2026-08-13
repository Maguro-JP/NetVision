"""結線表の取り込み。

導入のとき、手元にあるのが結線表だけということがある。Config が揃うのを
待たずに図を立ち上げたい、という要求は現場では普通で、そのために

  1. 記入用のひな形を出す
  2. 埋めて取り込む
  3. 機器が揃っていない行は預かっておき、揃った時点で結線にする

という形にしてある。3 が要るのは、機器登録と結線表のどちらが先になるかが
人によって違うため。順番の都合を人に押し付けないための仕組みで、
Config を先に取り込んだ場合の扱い（configs.adopt_orphan_snapshots）と同じ考え方。

書かれたホスト名から機器を勝手に作ることはしない。表の打ち間違いから
架空の機器が生まれる方が、後から気づきにくく厄介なため。繋がっていない行は
一覧で見えるようにしてある。
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, PendingLink
from app.models.topology import LinkOrigin
from app.services.configparse import normalize, normalize_hostname
from app.services.tablefile import read_table

#: ひな形の列。表の見出しはこの順で出す
#: 「回線名」は線の呼び名（上位回線・予備回線など）。ポート名とは別物
COLUMNS = ("機器A", "ポートA", "機器B", "ポートB", "回線名")

#: 見出しの表記ゆれ。現場の表をそのまま持ってきても読めるように
ALIASES: dict[str, tuple[str, ...]] = {
    "a_hostname": ("機器a", "機器名a", "ホスト名a", "自機器", "from", "device_a", "local"),
    "a_interface": ("ポートa", "インターフェースa", "自ポート", "port_a", "local_port"),
    "b_hostname": ("機器b", "機器名b", "ホスト名b", "対向機器", "to", "device_b", "remote"),
    "b_interface": ("ポートb", "インターフェースb", "対向ポート", "port_b", "remote_port"),
    "label": ("回線名", "備考", "回線", "メモ", "note", "label"),
}


@dataclass
class RowIssue:
    row_number: int
    message: str


@dataclass
class LinkImportResult:
    total_rows: int = 0
    #: 両端の機器が揃っていて、結線として作れた行
    linked: int = 0
    #: 既に同じ結線があった行
    already: int = 0
    #: 機器がまだ無いので預かった行
    pending: int = 0
    issues: list[RowIssue] = field(default_factory=list)
    unmapped_columns: list[str] = field(default_factory=list)
    committed: bool = False


def template_xlsx() -> bytes:
    """記入用のひな形。1行だけ書き方の見本を入れておく。

    空の表だけ渡されても、何をどう書けばいいかは分からない。見本は
    見ればそれと分かる名前にして、消し忘れても実機と紛れないようにする。
    """
    book = Workbook()
    sheet = book.active
    sheet.title = "結線表"
    sheet.append(list(COLUMNS))
    example = ["（例）core-sw-01", "GigabitEthernet1/0/1"]
    example += ["（例）edge-rt-01", "ge-0/0/0", "本社 上位回線"]
    sheet.append(example)

    for index, width in enumerate((24, 24, 24, 24, 30), start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width

    note = book.create_sheet("書き方")
    for line in (
        ["結線表の書き方"],
        [],
        ["・1行に1本の結線を書きます。両端の機器名とポート名を入れてください"],
        ["・機器名は台帳のホスト名に合わせます。大文字小文字とドメインの有無は問いません"],
        ["・ポート名は Config の表記のまま書いてください（Gi1/0/1 のような略記も読めます）"],
        ["・回線名は任意です。上位回線・予備回線など、その線の呼び名を書く欄です"],
        [
            "・回線名は図では線の中央に出ます。"
            "ポート名は機器の脇に出たままです（どちらか一方ではありません）"
        ],
        [
            "・書き出した表にある「注意」は VLAN の食い違いなど"
            "システムが出す警告で、回線名とは別です"
        ],
        ["・（例）の行は消してから取り込んでください"],
        [],
        ["台帳にまだ無い機器を書いた行は、取り込んだあとも「未接続」として預かります。"],
        ["その機器を登録した時点で、自動で結線に変わります。取り込み直す必要はありません。"],
        ["書かれた名前から機器を作ることはしません（打ち間違いから架空の機器が増えるため）。"],
    ):
        note.append(line)
    note.column_dimensions["A"].width = 100

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _map_columns(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    """表の見出しを項目に割り当てる。"""

    def key(text: str) -> str:
        return "".join(text.split()).lower().replace("（", "").replace("）", "")

    mapping: dict[str, str] = {}
    for header in headers:
        normalized = key(header)
        for field_name, aliases in ALIASES.items():
            if field_name in mapping.values():
                continue
            if normalized in aliases or normalized == key(field_name):
                mapping[header] = field_name
                break
    unmapped = [h for h in headers if h not in mapping]
    return mapping, unmapped


def run_import(
    db: Session, filename: str, content: bytes, *, commit: bool = False
) -> LinkImportResult:
    """結線表を取り込む。commit=False なら中身を見るだけで DB は変えない。"""
    table = read_table(filename, content)
    mapping, unmapped = _map_columns(table.headers)
    result = LinkImportResult(total_rows=len(table.rows), unmapped_columns=unmapped)

    missing = [
        name
        for name in ("a_hostname", "a_interface", "b_hostname", "b_interface")
        if name not in mapping.values()
    ]
    if missing:
        labels = {
            "a_hostname": "機器A",
            "a_interface": "ポートA",
            "b_hostname": "機器B",
            "b_interface": "ポートB",
        }
        result.issues.append(
            RowIssue(0, f"列が足りません: {' / '.join(labels[m] for m in missing)}")
        )
        return result

    devices = {normalize_hostname(d.hostname): d for d in db.scalars(select(Device))}

    for index, raw in enumerate(table.rows, start=2):
        values = {mapping[h]: str(raw.get(h) or "").strip() for h in mapping}
        a_host, b_host = values.get("a_hostname", ""), values.get("b_hostname", "")
        a_port, b_port = values.get("a_interface", ""), values.get("b_interface", "")

        if not any((a_host, b_host, a_port, b_port)):
            continue  # 空行は黙って飛ばす
        if a_host.startswith("（例）") or b_host.startswith("（例）"):
            continue  # ひな形の見本行

        if not (a_host and b_host and a_port and b_port):
            result.issues.append(RowIssue(index, "機器名とポート名を4つとも書いてください"))
            continue
        if normalize_hostname(a_host) == normalize_hostname(b_host):
            result.issues.append(RowIssue(index, "同じ機器どうしが書かれています"))
            continue

        a_device = devices.get(normalize_hostname(a_host))
        b_device = devices.get(normalize_hostname(b_host))

        if a_device is not None and b_device is not None:
            from app.services.configs import upsert_link

            link = upsert_link(
                db,
                a_device.id,
                a_port,
                b_device.id,
                b_port,
                origin=LinkOrigin.MANUAL,
            )
            if link is None:
                result.already += 1
            else:
                link.label = values.get("label") or None
                result.linked += 1
        else:
            _remember(db, a_host, a_port, b_host, b_port, values.get("label"), filename)
            result.pending += 1

    if commit:
        db.commit()
        result.committed = True
    else:
        db.rollback()

    return result


def _remember(
    db: Session,
    a_host: str,
    a_port: str,
    b_host: str,
    b_port: str,
    label: str | None,
    source: str,
) -> None:
    """まだ結べない行を預かる。同じ行を二重に持たない。"""
    for existing in db.scalars(select(PendingLink).where(PendingLink.a_hostname == a_host)):
        if (
            normalize_hostname(existing.b_hostname) == normalize_hostname(b_host)
            and normalize(existing.a_interface) == normalize(a_port)
            and normalize(existing.b_interface) == normalize(b_port)
        ):
            return
    db.add(
        PendingLink(
            a_hostname=a_host,
            a_interface=a_port,
            b_hostname=b_host,
            b_interface=b_port,
            label=label or None,
            source=source,
        )
    )


def resolve_pending(db: Session) -> int:
    """預かってある行のうち、両端が揃ったものを結線に変える。

    機器を登録したあとに呼ぶ。取り込み直しを求めないための仕掛けなので、
    登録の側から自動で呼ばれる。戻り値は結線に変わった数。
    """
    from app.services.configs import upsert_link

    pendings = list(db.scalars(select(PendingLink)))
    if not pendings:
        return 0

    devices = {normalize_hostname(d.hostname): d for d in db.scalars(select(Device))}
    resolved = 0

    for pending in pendings:
        a_device = devices.get(normalize_hostname(pending.a_hostname))
        b_device = devices.get(normalize_hostname(pending.b_hostname))
        if a_device is None or b_device is None:
            continue

        link = upsert_link(
            db,
            a_device.id,
            pending.a_interface,
            b_device.id,
            pending.b_interface,
            origin=LinkOrigin.MANUAL,
        )
        if link is not None:
            link.label = pending.label
        db.delete(pending)
        resolved += 1

    if resolved:
        db.flush()
    return resolved
