"""経路の情報から、結線を推定する。

図は LLDP / CDP か結線表から作る。ところが現場から出てくるのは
running-config だけということが多い。LLDP を切っている現場もある。
その場合、機器は台帳に載るのに線が1本も引けず、図が点の集まりになる。

一方で、L3 の情報は running-config に必ず書いてある。同じセグメントに
いる2台は、間に何か挟まっていても「つながっている」ことは確かである。
そこまでは Config だけで言える。

### どこまで言い切れるか

- /30 や /31 に2台 … 2台しか入らないので、直結とみて間違いない
- それ以外のセグメントに2台だけ … つながってはいるが、間にスイッチが
  挟まっている可能性がある
- 3台以上いるセグメント … 誰と誰が直結なのかは決まらない。線は引かない

「たぶんこう」を勝手に台帳へ書かない。候補として出し、人が選んだものだけ
線にする。図は判断に使われるので、確かでない線が混ざると害のほうが大きい。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Device
from app.models.topology import Interface, Link, LinkOrigin
from app.services.configparse.routing import to_prefix

#: 2台しか入らない大きさ。ここに2台いれば直結と言い切れる
_POINT_TO_POINT = 30

HIGH = "high"
MEDIUM = "medium"


@dataclass
class LinkCandidate:
    a_device_id: int
    a_hostname: str
    a_interface: str
    b_device_id: int
    b_hostname: str
    b_interface: str
    segment: str
    confidence: str
    why: str
    evidence: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[int, int, str, str]:
        return (self.a_device_id, self.b_device_id, self.a_interface, self.b_interface)


@dataclass
class _Endpoint:
    device_id: int
    hostname: str
    interface: str
    address: str


def candidates(db: Session, *, limit: int = 500) -> list[LinkCandidate]:
    """同じセグメントにいる組を、線の候補として返す。

    すでに線がある組は出さない。同じ線を2本引いても図は良くならない。
    """
    groups = _by_segment(db)
    existing = _existing_pairs(db)

    out: list[LinkCandidate] = []
    for network, endpoints in groups.items():
        by_device: dict[int, _Endpoint] = {}
        for endpoint in endpoints:
            by_device.setdefault(endpoint.device_id, endpoint)
        if len(by_device) != 2:
            # 1台だけなら相手がいない。3台以上なら誰と誰が直結かは決まらない
            continue

        left, right = sorted(by_device.values(), key=lambda e: e.device_id)
        if (left.device_id, right.device_id) in existing:
            continue

        if network.prefixlen >= _POINT_TO_POINT:
            confidence = HIGH
            why = (
                f"{network} は2台しか入らない大きさです。"
                "この2台が同じ範囲にいるので、直結とみて間違いありません。"
            )
        else:
            confidence = MEDIUM
            why = (
                f"{network} にいるのは、台帳ではこの2台だけです。"
                "つながってはいますが、間にスイッチが挟まっている可能性があります。"
                "その場合、この線は「どこを通っているか」までは表していません。"
            )

        out.append(
            LinkCandidate(
                a_device_id=left.device_id,
                a_hostname=left.hostname,
                a_interface=left.interface,
                b_device_id=right.device_id,
                b_hostname=right.hostname,
                b_interface=right.interface,
                segment=str(network),
                confidence=confidence,
                why=why,
                evidence=[
                    f"{left.hostname} {left.interface}: {left.address}",
                    f"{right.hostname} {right.interface}: {right.address}",
                ],
            )
        )

    # 言い切れるものから先に見せる。下まで読まないと確かな線に気づかない、を避ける
    out.sort(key=lambda c: (c.confidence != HIGH, c.a_hostname, c.b_hostname))
    return out[:limit]


def _by_segment(db: Session) -> dict[ipaddress.IPv4Network, list[_Endpoint]]:
    """IP が付いているポートを、セグメントごとにまとめる。

    Config を読み直さず、取り込みのときに落とした観測値（interfaces）を使う。
    16万台でも一度の問い合わせで済む。
    """
    rows = db.execute(
        select(
            Interface.device_id,
            Device.hostname,
            Interface.name,
            Interface.ip_address,
        )
        .join(Device, Device.id == Interface.device_id)
        .where(Interface.ip_address.isnot(None))
    ).all()

    groups: dict[ipaddress.IPv4Network, list[_Endpoint]] = {}
    for device_id, hostname, name, address in rows:
        prefix = to_prefix(address)
        if prefix is None:
            continue
        try:
            network = ipaddress.ip_network(prefix, strict=False)
        except ValueError:
            continue
        if network.version != 4 or network.prefixlen >= 32:
            # /32 は自分1つぶんで、相手のいるセグメントではない
            continue
        groups.setdefault(network, []).append(
            _Endpoint(device_id=device_id, hostname=hostname, interface=name, address=address)
        )
    return groups


def _existing_pairs(db: Session) -> set[tuple[int, int]]:
    pairs = set()
    for a, b in db.execute(select(Link.a_device_id, Link.b_device_id)).all():
        pairs.add((min(a, b), max(a, b)))
    return pairs


def apply(db: Session, wanted: list[tuple[int, int]] | None = None) -> int:
    """候補を線にする。`wanted` で組を選べる。空なら言い切れるものだけ。

    迷ったものまで一括で入れない。図は判断に使われるので、確かでない線が
    混ざると害のほうが大きい。
    """
    found = candidates(db)
    if wanted is None:
        chosen = [c for c in found if c.confidence == HIGH]
    else:
        keys = {(min(a, b), max(a, b)) for a, b in wanted}
        chosen = [
            c
            for c in found
            if (min(c.a_device_id, c.b_device_id), max(c.a_device_id, c.b_device_id)) in keys
        ]

    created = 0
    for candidate in chosen:
        exists = db.scalar(
            select(Link).where(
                or_(
                    (Link.a_device_id == candidate.a_device_id)
                    & (Link.b_device_id == candidate.b_device_id),
                    (Link.a_device_id == candidate.b_device_id)
                    & (Link.b_device_id == candidate.a_device_id),
                )
            )
        )
        if exists is not None:
            continue
        db.add(
            Link(
                a_device_id=candidate.a_device_id,
                a_interface=candidate.a_interface,
                b_device_id=candidate.b_device_id,
                b_interface=candidate.b_interface,
                origin=LinkOrigin.INFERRED,
                label=candidate.segment,
            )
        )
        created += 1
    db.commit()
    return created
