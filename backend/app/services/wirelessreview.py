"""読み取った無線の設定を見て、粗を挙げる。

有線なら、線を抜けばそこで終わる。電波は建物の外まで届くので、SSID の
守り方がそのまま入口の強さになる。ここで見るのは、動かしてみなくても
言えることに限る。

- 誰でも入れる（守り無し・WEP・WPA1・TKIP）
- 合言葉が Config にそのまま書いてある
- 802.1X だと言いながら、問い合わせ先が書かれていない
- ゲスト用の SSID なのに、端末どうしが通じる／VLAN が業務側
- 電波の国が指定されていない

読み取れないものは指摘しない。「無い」のか「読めていない」のかを、この層
では区別できないため。
"""

from __future__ import annotations

from app.services.configparse import ParsedConfig
from app.services.configparse.base import ParsedWlan
from app.services.reviewnote import HIGH, LOW, MEDIUM, Finding

#: ゲスト用と読める名前。日本の現場で実際に使われている書き方を並べる
_GUEST_WORDS = ("guest", "ゲスト", "visitor", "来客", "free", "public")

#: そのまま残しておけない守り方
_BROKEN = {
    "wep": "WEP は数分で鍵を取り出せます。守っていないのと同じです",
    "wpa": "WPA（第1版）と TKIP は破られています。WPA2 以上にしてください",
}


def review(parsed: ParsedConfig) -> list[Finding]:
    wireless = parsed.wireless
    if wireless is None:
        return []

    findings: list[Finding] = []
    live = [wlan for wlan in wireless.wlans if wlan.enabled]
    for wlan in live:
        findings.extend(_one(wlan))
    findings.extend(_across(live))
    if wireless.wlans and wireless.country is None:
        findings.append(
            Finding(
                level=LOW,
                title="電波の国が指定されていません",
                why="国ごとに使ってよい周波数と出力が違います。指定が無いと、"
                "使えないはずの電波を出すか、使えるはずの帯が出ないままになります。",
                evidence=[f"読み取れた SSID {len(wireless.wlans)} 個、country の行なし"],
            )
        )
    return findings


def _label(wlan: ParsedWlan) -> str:
    return wlan.ssid or wlan.name


def _is_guest(wlan: ParsedWlan) -> bool:
    text = f"{wlan.name} {wlan.ssid or ''}".lower()
    return any(word in text for word in _GUEST_WORDS)


def _one(wlan: ParsedWlan) -> list[Finding]:
    out: list[Finding] = []
    name = _label(wlan)
    security = set(wlan.security)
    akm = set(wlan.akm)

    open_air = not security or security == {"open"}
    if open_air and not akm:
        level = MEDIUM if _is_guest(wlan) else HIGH
        out.append(
            Finding(
                level=level,
                title=f"SSID {name} は誰でも入れます",
                why="守り方の指定がありません。電波の届く範囲にいれば、"
                + (
                    "誰でもこの網に入れます。来客向けであっても、"
                    "せめて端末どうしを通さない設定と、業務と別の VLAN が要ります。"
                    if _is_guest(wlan)
                    else "誰でもこの網に入れます。業務で使う SSID なら塞いでください。"
                ),
                evidence=[wlan.raw or f"wlan {wlan.name}"],
            )
        )

    for weak, why in _BROKEN.items():
        if weak in security:
            out.append(
                Finding(
                    level=HIGH,
                    title=f"SSID {name} が古い守り方のままです（{weak.upper()}）",
                    why=why,
                    evidence=[wlan.raw or f"wlan {wlan.name}"],
                )
            )
    if "tkip" in wlan.ciphers:
        out.append(
            Finding(
                level=HIGH,
                title=f"SSID {name} が TKIP を使っています",
                why="TKIP は破られています。AES（CCMP）だけにしてください。"
                "TKIP を残すと、速度も 802.11n 以降の速さまで落ちます。",
                evidence=[wlan.raw or f"wlan {wlan.name}"],
            )
        )

    if wlan.psk_in_clear:
        out.append(
            Finding(
                level=HIGH,
                title=f"SSID {name} の合言葉が、そのまま読める形で書かれています",
                why="Config を渡した相手全員が、この網に入れます。"
                "資料や版管理に載る前提で、伏せた形で持ってください。",
                evidence=[f"wlan {wlan.name} の合言葉の行"],
            )
        )

    if "dot1x" in akm and not wlan.auth_list:
        out.append(
            Finding(
                level=HIGH,
                title=f"SSID {name} は 802.1X ですが、問い合わせ先がありません",
                why="本人確認を RADIUS に頼む設定なのに、その相手が書かれていません。"
                "この SSID には誰もつなげません。",
                evidence=[wlan.raw or f"wlan {wlan.name}"],
            )
        )

    if "psk" in akm and not wlan.has_psk:
        out.append(
            Finding(
                level=MEDIUM,
                title=f"SSID {name} は合言葉で入る設定ですが、合言葉がありません",
                why="設定は合言葉を求める形になっていますが、その合言葉が"
                "この Config にありません。取り込んだのが抜粋なら問題ありませんが、"
                "これで全部なら、この SSID は使えません。",
                evidence=[wlan.raw or f"wlan {wlan.name}"],
            )
        )

    if _is_guest(wlan):
        if not wlan.client_isolation:
            out.append(
                Finding(
                    level=MEDIUM,
                    title=f"来客向けの SSID {name} で、端末どうしが通じます",
                    why="同じ SSID につないだ端末どうしが直接やり取りできます。"
                    "隣の席の人の端末が見え、そこから入られます。",
                    evidence=[wlan.raw or f"wlan {wlan.name}"],
                )
            )
        if wlan.vlan is None:
            out.append(
                Finding(
                    level=MEDIUM,
                    title=f"来客向けの SSID {name} の落とし先が分かりません",
                    why="VLAN の指定が読み取れません。業務の VLAN へ落ちていると、"
                    "来客が社内の網に入ります。",
                    evidence=[wlan.raw or f"wlan {wlan.name}"],
                )
            )

    if not wlan.broadcast:
        out.append(
            Finding(
                level=LOW,
                title=f"SSID {name} を伏せています",
                why="名前を伏せても守りにはなりません（つないだ端末が名前を出します）。"
                "そのうえ、端末は探し続けるので電池を使い、他所でも同じ名前を探します。",
                evidence=[wlan.raw or f"wlan {wlan.name}"],
            )
        )

    if "wpa3" in security and wlan.pmf in (None, "disabled"):
        out.append(
            Finding(
                level=MEDIUM,
                title=f"SSID {name} は WPA3 ですが、管理フレームを守っていません",
                why="WPA3 は管理フレームの守り（PMF）が要ります。"
                "無いままだと、切断を装った攻撃を止められません。",
                evidence=[wlan.raw or f"wlan {wlan.name}"],
            )
        )

    return out


def _across(live: list[ParsedWlan]) -> list[Finding]:
    """SSID どうしを見比べて言えること。"""
    out: list[Finding] = []
    seen: dict[str, list[str]] = {}
    for wlan in live:
        if wlan.ssid:
            seen.setdefault(wlan.ssid.lower(), []).append(wlan.name)
    for ssid, names in seen.items():
        if len(names) > 1:
            out.append(
                Finding(
                    level=MEDIUM,
                    title=f"同じ名前の SSID が {len(names)} 個あります（{ssid}）",
                    why="同じ名前で守り方や落とし先が違うと、端末はどちらに"
                    "つながったのか分かりません。つながる先が日によって変わります。",
                    evidence=names[:5],
                )
            )

    guests = [w for w in live if _is_guest(w)]
    work = [w for w in live if not _is_guest(w) and w.vlan is not None]
    work_vlans = {w.vlan for w in work}
    for guest in guests:
        if guest.vlan is not None and guest.vlan in work_vlans:
            same = [w for w in work if w.vlan == guest.vlan]
            out.append(
                Finding(
                    level=HIGH,
                    title=f"来客向けの SSID {_label(guest)} が、業務と同じ VLAN です",
                    why="来客の端末が、業務の網にそのまま入ります。"
                    "SSID を分けても、落とし先が同じなら分けたことになりません。",
                    evidence=[f"VLAN {guest.vlan}: {_label(guest)} と {_label(same[0])}"],
                )
            )
    return out
