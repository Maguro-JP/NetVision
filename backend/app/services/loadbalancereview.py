"""負荷分散を見て、サービスに繋がらない・落ちた機に流し続ける形を指摘する。

どれも「機器は動いているのにサービスが応答しない」の原因になる。
振り分けの方針そのもの（どの機を何台置くか）は要件が要るので言わない。
"""

from __future__ import annotations

from app.services.configparse import ParsedConfig
from app.services.configparse.loadbalance import ParsedLoadBalance
from app.services.reviewnote import HIGH, LOW, MEDIUM, Finding


def _note(level: str, title: str, why: str, evidence: list[str]) -> Finding:
    return Finding(level=level, title=title, why=why, evidence=evidence)


def review(parsed: ParsedConfig) -> list[Finding]:
    balance: ParsedLoadBalance | None = getattr(parsed, "load_balance", None)
    if balance is None:
        return []

    findings: list[Finding] = []

    for virtual in balance.virtual_servers:
        if not virtual.enabled:
            continue
        pool = balance.pool(virtual.pool)
        where = f"{virtual.address or virtual.name}"
        if virtual.port:
            where += f":{virtual.port}"

        if virtual.pool is None:
            findings.append(
                _note(
                    HIGH,
                    f"受け口 {virtual.name} に振り分け先が指定されていません",
                    "外から見える受け口はありますが、どこへ流すかが書かれていません。"
                    "接続はここで止まります。",
                    [where],
                )
            )
            continue
        if pool is None:
            findings.append(
                _note(
                    HIGH,
                    f"受け口 {virtual.name} が指している {virtual.pool} がありません",
                    "指定された振り分け先のまとまりが、この Config の中に見当たりません。"
                    "名前の書き間違いか、消してしまったかのどちらかです。接続は流れません。",
                    [where, f"pool {virtual.pool}"],
                )
            )
            continue
        if not pool.members:
            findings.append(
                _note(
                    HIGH,
                    f"{pool.name} に振り分け先が1台も入っていません",
                    "受け口とまとまりはありますが、中身が空です。"
                    "作りかけか、入れ替えのときに消したまま戻していない状態です。",
                    [where, f"pool {pool.name}"],
                )
            )
            continue
        if not pool.alive:
            findings.append(
                _note(
                    HIGH,
                    f"{pool.name} の振り分け先が全部止めてあります",
                    "入っている機はすべて、手で止めた状態になっています。"
                    "作業のあと戻し忘れていないか確かめてください。",
                    [f"{m.address}:{m.port}" if m.port else m.address for m in pool.members[:8]],
                )
            )

    for pool in balance.pools:
        if not pool.members:
            continue
        if not pool.monitors:
            findings.append(
                _note(
                    MEDIUM,
                    f"{pool.name} に健全性監視が付いていません",
                    "監視が無いと、振り分け先が落ちていても流し続けます。"
                    "利用者から見ると「たまに繋がらない」という形になり、"
                    "機器の側では異常として見えません。",
                    [f"{m.address}:{m.port}" if m.port else m.address for m in pool.members[:8]],
                )
            )
        elif len(pool.alive) == 1:
            findings.append(
                _note(
                    LOW,
                    f"{pool.name} で生きている振り分け先が1台だけです",
                    "負荷分散の形にはなっていますが、実際に流れる先は1台です。"
                    "その1台が落ちるとサービスも止まります。"
                    "意図した構成か、増設の途中かを確かめてください。",
                    [f"{m.address}:{m.port}" if m.port else m.address for m in pool.alive],
                )
            )

    used = {virtual.pool for virtual in balance.virtual_servers}
    for pool in balance.pools:
        if pool.name not in used:
            findings.append(
                _note(
                    LOW,
                    f"{pool.name} は、どの受け口からも使われていません",
                    "定義はありますが、当てている受け口が見当たりません。"
                    "消し忘れなら害はありませんが、受け口側の書き忘れなら"
                    "そのサービスは公開されていないことになります。",
                    [f"pool {pool.name}"],
                )
            )

    return findings
