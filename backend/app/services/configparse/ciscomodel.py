"""Cisco の型番（PID）を、命名規則で読む。

型番は EoL・脆弱性・保守契約を引く鍵になる。ここが弱いと、現場で一番多い
機種が何にも引っかからない。実際に起きていたのは次の3つ。

- 参照データの `C2960X*` が、実機の `WS-C2960X-48FPD-L` に当たらない
  （素朴な前方一致では `wsc2960x…` は `c2960x` で始まらない）
- `ISR4331` と `ISR4331/K9`、`C9300-48P` と `C9300-48P-A` が別物になる
  （`/K9` は暗号の有無、末尾の `-A` / `-E` はライセンスの束で、ハードは同じ）
- `show inventory` の最初の `PID:` を採るので、貼り方によってはモジュールや
  SFP の型番がシャーシの型番になる

型番の一覧を持って当てるのではなく、命名規則をほどく。一覧に無い型番でも
読めるようにするため。ただし規則から外れる書き方は、そのまま持つ（推測で
直さない）。

### 用語

- pid … 機器が印字したままの型番（`WS-C2960X-48FPD-L`）
- canonical … 突き合わせ用に、ハードを変えない飾りを外したもの（`C2960X-48FPD-L`）
- family … 系列（`C2960X`）。参照データが系列単位で書かれることが多い
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: ハードを変えない頭の飾り。旧 Catalyst の `WS-`、Nexus の `N9K-` など、
#: 無線 AP の `AIR-`、Cisco ONE 束の `C1-`、2D バーコード品の `2D-`、
#: 国別在庫の `NAL-` / `SI-`。参照データではこれ無しで書かれることが多い
#: （出典: Cisco の EoL 通知と発注表に載る PID の並び）
_HEAD_DECORATION = re.compile(
    r"^(?:C1-|2D-|NAL-|SI-)?(?:WS-|N[3579]K-|N2K-|AIR-|CISCO)?", re.IGNORECASE
)
#: ハードを変えない尻の飾り。`/K9`・`-K9` は暗号の有無、`-A` / `-E` / `-S` / `-L` は
#: ライセンスの束（Catalyst 9000 は -A / -E、2960 は -L / -S）。`=` はスペア品。
#: 末尾の `-RF` は再生品
_TAIL_DECORATION = re.compile(
    # 暗号の有無・スペア・再生品・保守在庫・特価
    r"(?:/K9|-K9|=|\+\+|-RF|-WS|-PK)$"
    # ライセンスの束（Catalyst 9000 は -A / -E / -M、2960 は -L / -S / -I / -LL / -LB、
    # 国別の -IN / -BR、まとめ買いの -HC）
    r"|(?<=[A-Z0-9])(?:-A|-E|-M|-L|-S|-I|-LL|-LB|-IN|-BR|-HC)$"
    # ISR の束（アプリ・音声・セキュリティ・DNA）と電源の違い
    r"|(?<=\d)(?:-AXV|-AX|-VSEC|-SEC|-V|-B|-DNA|-CDNA|-DC|-PM\d*|-SPM(?:-8G)?)$"
    # Nexus の気流の向き（-PE 排気 / -PI 吸気）と束の印
    r"|(?<=[A-Z0-9])(?:-PE|-PI|-B\d|-H|-Z)$"
    # ASA 5500-X は機器が `ASA5516` と印字し、参照データは `ASA5516-X` と書く
    r"|(?<=^ASA55\d\d)-X$"
    # Firepower の発注 PID は載せる像（ASA / NGFW）が付く。ハードは同じ
    # （出典: Cisco Network Security Ordering Guide の Table 18A）
    r"|(?<=^FPR\d{4})(?:-NGFW|-ASA)$|(?<=^FPR\d{4}E)(?:-NGFW|-ASA)$",
    re.IGNORECASE,
)
#: 系列の切れ目。`IE-3300-8T2S` は `IE-3300` まで、`ASR1001-X` は `ASR1001` まで
_FAMILY = re.compile(
    # 並び順に意味がある。`C9800-L` は `C9800` より先に、`SG550X` は末尾の文字まで
    r"^(?P<family>(?:C9800(?:-L)?|IE-\d{4}|IR\d{3,4}|ISR\d{4}|ASR\d{4}|C\d{4,5}[A-Z]*|"
    r"SG\d{3}[A-Z]*|SF\d{3}[A-Z]*|SX\d{3}[A-Z]*|CBS\d{3}[A-Z]*|CISCO\d{4}|ASA\d{4}|"
    r"N\d{4}|FPR-?\d{4}[A-Z]?|NCS-?\d{3,4}|ASR-?9\d{3}|ASR9K|AP\d{4}[A-Z]*|CW\d{4}[A-Z]*|MS\d{3}|WLC\d{4})|[A-Z]+\d+[A-Z]*)",
    re.IGNORECASE,
)
#: Cisco の型番らしい並び。ここに当たらないものは、型番として扱わない
#: （`show inventory` の DESCR や、SFP の型番 `GLC-SX-MMD` を弾くため）
_LOOKS_LIKE_PID = re.compile(
    r"^(?:C1-|2D-|NAL-|SI-)?(?:WS-|N[23579]K-|AIR-)?"
    r"(?:C|IE-|IR|ISR|ASR|SG|SF|SX|CBS|CISCO|ASA|FPR-?|AP|CW|MS|WLC|N)\d{3,5}"
    # IOS XR の機種。`show version` は `cisco NCS-5500 () processor` と印字する
    # （出典: NCS 5500 の System Setup ガイドの show version 例）
    r"|^(?:NCS-?\d{3,4}|ASR-?9\d{3}|ASR9K)\b"
    # 国別在庫の PID は `WS-C` を落として数字から始まる（`NAL-2960XR-24PD-I`）
    r"|^(?:NAL|SI|2D)-\d{4}[A-Z]*-",
    re.IGNORECASE,
)
#: 頭の飾りを外したあと、数字から始まる Catalyst。`C` を戻して読む
_BARE_CATALYST = re.compile(r"^\d{4}[A-Z]*-", re.IGNORECASE)
#: モジュール・光・電源・ファンの型番。シャーシとして採らない
_NOT_CHASSIS = re.compile(
    r"^(?:GLC-|SFP-|QSFP-|CAB-|PWR-|C\d{4}-NM-|C\d{4}-PWR|C\d{4}-FAN|C9K-PWR|C9K-FAN|"
    r"NIM-|SM-|EHWIC|HWIC|VWIC|PVDM|STACK-|C3850-NM|C9300-NM|C9500-NM|N9K-PAC|N9K-C9300-FAN|"
    r"UCS|MEM-|FLASH|USB|ACS-)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CiscoModel:
    pid: str
    canonical: str
    family: str

    def keys(self) -> set[str]:
        """突き合わせに使う鍵。印字のまま・飾りを外したもの・系列。"""
        return {_key(self.pid), _key(self.canonical), _key(self.family)}


def looks_like_pid(value: str | None) -> bool:
    """Cisco の型番らしいか。DESCR の文や SFP の型番を型番として採らないため。"""
    return bool(value) and bool(_LOOKS_LIKE_PID.match(value.strip()))


def is_chassis_pid(value: str | None) -> bool:
    """シャーシの型番か。モジュール・光・電源・ファンは違う。"""
    return looks_like_pid(value) and not _NOT_CHASSIS.match((value or "").strip())


def read(pid: str | None) -> CiscoModel | None:
    """型番をほどく。規則に当たらなければ None（推測で直さない）。"""
    if not pid:
        return None
    raw = pid.strip()
    if not looks_like_pid(raw):
        return None
    canonical = _HEAD_DECORATION.sub("", raw)
    if _BARE_CATALYST.match(canonical):
        canonical = "C" + canonical
    # Firepower は機器が `FPR-1120`、発注 PID が `FPR1120-…` と書く。突き合わせは後者に寄せる
    canonical = re.sub(r"^FPR-(?=\d)", "FPR", canonical, flags=re.IGNORECASE)
    # 尻の飾りは1つずつ外す（`-AX/K9-RF` のように重なることがある）
    for _ in range(4):
        stripped = _TAIL_DECORATION.sub("", canonical)
        if stripped == canonical:
            break
        canonical = stripped
    found = _FAMILY.match(canonical)
    family = found.group("family") if found else canonical
    return CiscoModel(pid=raw, canonical=canonical.upper(), family=family.upper())


def matches(pattern: str | None, model: str | None) -> bool:
    """参照データの型番指定（末尾 `*` で前方一致）と、機器の型番を突き合わせる。

    どちらも飾りを外してから比べる。`C2960X*` が `WS-C2960X-48FPD-L` に、
    `ISR4331` が `ISR4331/K9` に、`C9300-48P` が `C9300-48P-A` に当たる。
    飾りの無い名前どうしでは、今までどおり印字のままでも当たる。
    """
    if not pattern:
        return True
    if not model:
        return False
    prefix = pattern.endswith("*")
    wanted = pattern.rstrip("*")
    mine = read(model)
    theirs = read(wanted)
    if prefix and mine and theirs:
        # 系列だけの指定（`C9200*`）は系列どうしで比べる。`C9200L` は別の系列
        if theirs.family == theirs.canonical:
            return mine.family == theirs.family
        # それより細かい指定は、文字の並びの境で切る（`C2960X*` は `C2960XR-…` に当たらない）
        return _starts_at_a_boundary(theirs.canonical, mine.canonical)
    left_keys = {_key(wanted)} | ({_key(theirs.canonical)} if theirs else set())
    right_keys = {_key(model)} | ({_key(mine.canonical)} if mine else set())
    if prefix:
        return any(right.startswith(left) for left in left_keys for right in right_keys if left)
    return any(left == right for left in left_keys for right in right_keys if left)


def _starts_at_a_boundary(head: str, whole: str) -> bool:
    """`head*` が `whole` に当たるか。文字の並びの途中では切らない。

    `C2960X` は `C2960X-48FPD-L` に当たり、`C2960XR-24PD-I` には当たらない
    （X の後に R が続くのは別の系列）。`C9300-48` は `C9300-48P-A` に当たる
    （数字の後に文字が続くのはポートの種類で、同じ系列）。
    """
    head, whole = head.upper(), whole.upper()
    if not whole.startswith(head):
        return False
    rest = whole[len(head) :]
    if not rest or not rest[0].isalnum():
        return True
    return head[-1].isdigit() and rest[0].isalpha()


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())
