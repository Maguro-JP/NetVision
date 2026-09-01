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
        note="エリアと載せているネットワークに加え、ポートごとの決めごと"
        "（エリア・hello / dead・線の見立て・合言葉の掛け方）まで読みます。"
        "結線が入っていれば、両端で食い違っているところを指摘します。"
        "コスト設計や再配布の妥当性は見ていません。",
    ),
    Capability(
        area="経路",
        name="BGP",
        read=PARTIAL,
        review=PARTIAL,
        generate=NONE,
        note="AS・ピア・広告するネットワークに加え、相手ごとの出入りのフィルタ・"
        "受ける経路の上限・合言葉・update-source まで読みます。"
        "外の AS へ出す経路を絞っていない、上限が無い、掛けた route-map が"
        "この Config に無い、といったところを指摘します。"
        "経路設計そのもの（どこへ何を出すべきか）は要件が要るので言いません。",
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
        review=PARTIAL,
        generate=NONE,
        note="束ねている番号と方式まで読み、結線が入っていれば両端で突き合わせます。"
        "片側だけ束ねている、方式が違う（LACP と決め打ち）を指摘します。"
        "束ねた線の負荷の分かれ方は、通る量が分からないので見ていません。",
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
        review=PARTIAL,
        generate=NONE,
        note="1行ずつ、当て先まで読みます。上の行に覆われて読まれない行、"
        "当てていない ACL、実質すべて通している ACL、落としているのに記録が"
        "残らない ACL、存在しない ACL を指しているところを指摘します。"
        "読めるのは Cisco IOS・Comware（NEC QX）・NEC IX・Junos・FortiOS の"
        "書き方です。通すべき通信が通るかどうかは、要件が要るので言いません。",
    ),
    Capability(
        area="制限",
        name="ファイアウォールのポリシー",
        read=PARTIAL,
        review=PARTIAL,
        generate=NONE,
        note="FortiOS の firewall policy を、上から順の1行ずつとして読みます。"
        "ACL と同じ器に入れてあるので、上の行に覆われて読まれない行や、"
        "実質すべて通している状態も同じように指摘します。止めてある行"
        "（set status disable）は並びの検算から外します。通す通信の中身は"
        "起こしません。業務が決まらないと決められないところで、"
        "当てずっぽうで開けると意味が無くなります。",
    ),
    Capability(
        area="制限",
        name="NAT",
        read=PARTIAL,
        review=PARTIAL,
        generate=NONE,
        note="何が何に変わるか、ポートで束ねているか、内側と外側がどのポートかまで"
        "読みます。束ねていない動的変換、内外の片方しか無い、指している名前が無い、"
        "を指摘します。読めるのは Cisco IOS・FortiGate・Junos・NEC（IX / QX）の"
        "書き方です。",
    ),
    Capability(
        area="足まわり",
        name="STP",
        read=PARTIAL,
        review=PARTIAL,
        generate=NONE,
        note="モードと優先度まで読み、端末用の口に BPDU guard が無い、"
        "スイッチをつなぐ口に portfast が入っている、モードを決めていない、"
        "を指摘します。ルートをどこに置くのが妥当かは、"
        "相手側の優先度まで見ないと言えないので見ていません。",
    ),
    Capability(
        area="足まわり",
        name="DHCP（サーバー / リレー）",
        read=PARTIAL,
        review=PARTIAL,
        generate=NONE,
        note="配る範囲・既定ゲートウェイ・名前解決の先・転送先まで読みます。"
        "ゲートウェイが範囲の外、ゲートウェイまで配ってしまう、配っていない、"
        "を指摘します。読めるのは Cisco IOS・FortiGate・Junos・NEC（IX / QX）の"
        "書き方です。",
    ),
    Capability(
        area="足まわり",
        name="QoS",
        read=PARTIAL,
        review=PARTIAL,
        generate=NONE,
        note="区分と動作、当てているポートまで読み、どこにも当たっていないものを"
        "指摘します。読めるのは Cisco IOS の policy-map、FortiGate の shaper、"
        "Junos の scheduler-map、NEC QX の qos policy です（NEC IX は"
        "確かめられる設定が無いので読んでいません）。帯域の配分が妥当かどうかは、"
        "実際に通る量が分からないので言いません。",
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
        review=PARTIAL,
        generate=NONE,
        note="当てている口まで読み、問い合わせ先（RADIUS）が無い、"
        "仕組みは入れたのにどの口にも当てていない、破ったときの動きを"
        "決めていない、黙って落とす設定になっている、を指摘します。"
        "誰を通すべきかという方針そのものは、要件が要るので言いません。",
    ),
    Capability(
        area="拠点間",
        name="IPsec / VPN",
        read=PARTIAL,
        review=PARTIAL,
        generate=NONE,
        note="相手・暗号方式・鍵の作り方・暗号化する範囲まで読みます。"
        "破られている方式（DES / MD5 / DH group 1・2・5）、相手や範囲の抜け、"
        "どのポートにも当たっていない設定を指摘します。読めるのは Cisco IOS・"
        "FortiGate・Junos・NEC QX の書き方です（NEC IX は確かめられる設定が"
        "無いので読んでいません）。合言葉そのものは持ちません。",
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
        review=PARTIAL,
        generate=NONE,
        note="SSID の守り方・落とし先の VLAN・合言葉の置き方まで読み、"
        "誰でも入れる SSID、WEP / WPA1 / TKIP、素のまま書かれた合言葉、"
        "問い合わせ先の無い 802.1X、来客が業務と同じ VLAN を指摘します。"
        "読めるのは Cisco IOS-XE / AireOS・Aruba Instant・FortiGate の書き方です。"
        "電波の設計（置き場所・出力・チャネル）は見ていません。",
    ),
    Capability(
        area="管理",
        name="SNMP / Syslog / NTP / AAA",
        read=FULL,
        review=PARTIAL,
        generate=PARTIAL,
        note="送り先まで読み、送り先が1つも無い（記録が再起動で消える）、"
        "SNMP の合言葉が既定のまま、SNMP から書き換えられる、"
        "といったところを指摘します。起こす Config には入れません。"
        "既存と違う値が入るほうが困るためです。",
    ),
    Capability(
        area="運用",
        name="ログの解析",
        read=PARTIAL,
        review=PARTIAL,
        generate=NONE,
        note="人が貼ったログを読み、機器・重さ・並びで数えたうえで、"
        "障害・設定の食い違い・攻撃の兆しを根拠つきで挙げます。"
        "機器から自動で集めることはしません（外へ通信しない道具のため）。",
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
