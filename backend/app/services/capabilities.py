"""NetVision がネットワークの何にどこまで対応しているかの一覧。

「これは対応していますか」に、その都度口で答えるのは間違いのもとになる。
できることと同じ場所に、できないことも書いておく。

段階は3つに分ける。混ぜると「読めるから設定も起こせる」と読まれてしまう。

- 読む   … Config から拾って台帳と画面に出せる
- 検算   … 読んだ内容の粗を指摘できる
- 起こす … 設計から Config を組み立てられる

段階は機種によらない共通の話として書く。機種ごとの差（この型番だけ書式が
違う、など）は、対応した時点でここではなく個別の表に足す。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.configparse.features import FEATURE_LABELS

FULL = "full"  # ひととおりできる
PARTIAL = "partial"  # 一部だけ
NONE = "none"  # できない

LEVEL_LABELS = {FULL: "できる", PARTIAL: "一部", NONE: "できない"}


@dataclass(frozen=True)
class Capability:
    """機能1つぶんの対応状況。"""

    area: str
    name: str
    read: str
    review: str
    generate: str
    note: str = ""


#: 並び順は、現場で先に必要になる順。上から読めば、いま何が任せられるか分かる
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        area="土台",
        name="機器・型番・OS バージョン",
        read=FULL,
        review=FULL,
        generate=NONE,
        note="型番が読めない Config は機器を作りません。EoL も脆弱性も型番で引くためです。",
    ),
    Capability(
        area="土台",
        name="インターフェース・VLAN",
        read=FULL,
        review=FULL,
        generate=FULL,
        note="アクセス / ネイティブ / トランク許可を分けて持ちます。",
    ),
    Capability(
        area="土台",
        name="結線（LLDP / CDP・結線表）",
        read=FULL,
        review=PARTIAL,
        generate=FULL,
        note="図はここから作ります。台帳に無い機器を指す隣接は、未接続として預かります。",
    ),
    Capability(
        area="経路",
        name="静的経路",
        read=FULL,
        review=FULL,
        generate=FULL,
        note="届かないネクストホップ、同じ重みで並ぶ経路を指摘します。",
    ),
    Capability(
        area="経路",
        name="OSPF",
        read=FULL,
        review=PARTIAL,
        generate=PARTIAL,
        note="エリアと載せているネットワークまで。コスト設計や再配布の妥当性は見ていません。",
    ),
    Capability(
        area="経路",
        name="BGP",
        read=PARTIAL,
        review=NONE,
        generate=NONE,
        note="AS・ピア・広告するネットワークと、当てている route-map の名前まで。"
        "route-map の中身の評価はしていません。",
    ),
    Capability(
        area="経路",
        name="EIGRP / IS-IS / RIP",
        read=FULL,
        review=NONE,
        generate=NONE,
        note="流している範囲・passive・再配布まで読みます。"
        "設定の妥当性（メトリックの設計など）は見ていません。",
    ),
    Capability(
        area="経路",
        name="route-map / prefix-list",
        read=PARTIAL,
        review=PARTIAL,
        generate=NONE,
        note="名前と、どこで使われているかまで。中身の評価はしていません。"
        "どこからも使われていないものは指摘します。",
    ),
    Capability(
        area="経路",
        name="ポリシールーティング",
        read=PARTIAL,
        review=NONE,
        generate=NONE,
        note="どのポートに当てているかまで。中身は読んでいません。",
    ),
    Capability(
        area="経路",
        name="VRF",
        read=FULL,
        review=PARTIAL,
        generate=NONE,
        note="名前と RD まで。経路の相互流入（route-target）は見ていません。",
    ),
    Capability(
        area="冗長",
        name="HSRP / VRRP",
        read=FULL,
        review=FULL,
        generate=FULL,
        note="片側だけ・優先度が同じ、を指摘します。設計からも起こせます。",
    ),
    Capability(
        area="冗長",
        name="LAG（ポートの束ね）",
        read=PARTIAL,
        review=NONE,
        generate=NONE,
        note="束ねている番号まで。両端で束ね方が食い違っていないかは見ていません。",
    ),
    Capability(
        area="冗長",
        name="スタック / 機器間の同期",
        read=PARTIAL,
        review=NONE,
        generate=NONE,
        note="入っていることは分かります。中身は見ていません。",
    ),
    Capability(
        area="制限",
        name="ACL / フィルター",
        read=PARTIAL,
        review=NONE,
        generate=NONE,
        note="入っていること・本数・どのポートに当てているかまで。"
        "1行ずつの評価はしていません。順番と網羅の判断が要るためです。",
    ),
    Capability(
        area="制限",
        name="ファイアウォールのポリシー",
        read=PARTIAL,
        review=NONE,
        generate=NONE,
        note="ゾーンとポリシーが入っていることまで。通す通信の中身は起こしません。"
        "業務が決まらないと決められないところで、当てずっぽうで開けると意味が無くなります。",
    ),
    Capability(
        area="制限",
        name="NAT",
        read=PARTIAL,
        review=NONE,
        generate=NONE,
        note="入っていることまで。変換の対応表は読んでいません。",
    ),
    Capability(
        area="足まわり",
        name="STP",
        read=PARTIAL,
        review=NONE,
        generate=NONE,
        note="モードと優先度まで。ルートがどこかの妥当性は見ていません。",
    ),
    Capability(
        area="足まわり",
        name="DHCP（サーバー / リレー）",
        read=PARTIAL,
        review=NONE,
        generate=NONE,
        note="転送先まで。配布範囲は読んでいません。",
    ),
    Capability(
        area="足まわり",
        name="QoS",
        read=PARTIAL,
        review=NONE,
        generate=NONE,
        note="入っていることまで。",
    ),
    Capability(
        area="足まわり",
        name="マルチキャスト",
        read=PARTIAL,
        review=NONE,
        generate=NONE,
        note="PIM / IGMP スヌーピングが入っていることまで。",
    ),
    Capability(
        area="足まわり",
        name="802.1X / ポートセキュリティ",
        read=PARTIAL,
        review=NONE,
        generate=NONE,
        note="入っていることまで。",
    ),
    Capability(
        area="拠点間",
        name="IPsec / VPN",
        read=PARTIAL,
        review=NONE,
        generate=NONE,
        note="トンネルの種別・送信元・宛先まで。鍵や暗号方式は読んでいません。",
    ),
    Capability(
        area="拠点間",
        name="回線（キャリア設備）",
        read=NONE,
        review=NONE,
        generate=NONE,
        note="回線事業者から渡される値は、Config からは決まりません。",
    ),
    Capability(
        area="上位",
        name="負荷分散（ロードバランサ）",
        read=PARTIAL,
        review=NONE,
        generate=NONE,
        note="仮想サーバーの設定が入っていることまで。"
        "振り分け先の一覧や健全性監視は読んでいません。台帳には機器として載せられます。",
    ),
    Capability(
        area="上位",
        name="無線（コントローラ / AP）",
        read=PARTIAL,
        review=NONE,
        generate=NONE,
        note="機器としては台帳に載せられます。SSID や電波の設計は見ていません。",
    ),
    Capability(
        area="管理",
        name="SNMP / Syslog / NTP / AAA",
        read=FULL,
        review=NONE,
        generate=PARTIAL,
        note="送り先まで読みます。起こす Config には入れません。"
        "既存と違う値が入るほうが困るためです。",
    ),
    Capability(
        area="運用",
        name="ログの解析",
        read=NONE,
        review=NONE,
        generate=NONE,
        note="NetVision は Config と台帳を扱う道具で、流れているログは受けていません。"
        "やるなら Syslog を受ける口から作ることになります。",
    ),
    Capability(
        area="運用",
        name="実機への投入・監視",
        read=NONE,
        review=NONE,
        generate=NONE,
        note="実機へは触りません。閉域で使う前提で、外向きの通信をしない作りにしてあります。",
    ),
)

#: 読み取った機能と、この表の行を結ぶ。画面で「読めたもの」と
#: 「そもそも対応していないもの」を並べて見せるため
FEATURE_TO_CAPABILITY = {
    "access_control": "ACL / フィルター",
    "nat": "NAT",
    "dhcp": "DHCP（サーバー / リレー）",
    "spanning_tree": "STP",
    "aggregation": "LAG（ポートの束ね）",
    "qos": "QoS",
    "authentication": "802.1X / ポートセキュリティ",
    "management": "SNMP / Syslog / NTP / AAA",
    "vpn": "IPsec / VPN",
    "multicast": "マルチキャスト",
    "load_balance": "負荷分散（ロードバランサ）",
    "high_availability": "HSRP / VRRP",
}


def areas() -> list[str]:
    out: list[str] = []
    for capability in CAPABILITIES:
        if capability.area not in out:
            out.append(capability.area)
    return out


def feature_label(category: str) -> str:
    return FEATURE_LABELS.get(category, category)
