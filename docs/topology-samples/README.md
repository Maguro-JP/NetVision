# 構成図サンプルと、この製品での最適

集めた図と、そこから決めた「この製品ではこう描く」を置く場所です。
Cisco のキャンパス図をそのまま真似するのが最適ではありません。

## 結論（この製品での最適）

物理構成・拠点で絞った図が本命です。最適は **拠点ツリー＋対向の列** です。

```
        [センター]
            |
        [自拠点ルータ]
            |
        [自拠点スイッチ] ---- [営業所1]
                            [営業所2]
                            [営業所3]
```

- 上（または左）が上位。WAN / センターは真上
- 自拠点は縦に階層。同じ段は揃える
- 配下の拠点は **下ではなく右の列**。下に伸ばすと上りの線を全部横切る
- 線は縦横だけ。同じ辺の束は等間隔の通り道。交差は段内の並びとポート順で潰す
- 物理と L2 / L3 は別の図。1枚に混ぜない
- 人が置いた位置は、見るだけでは動かさない

小さくて Core / Dist / Access が分かれていない拠点は、Cisco の 3 段を空で作らない。
実際のホップ（結線の上下）で段を決める。種別だけで横一列にするのは不正解です。

## 最適ではないもの

| 形 | なぜこの製品では使わない |
|---|---|
| 放射状ツリー（radial） | 上下が読めない。ポート順も置けない |
| フルメッシュをそのまま描く | 線が髪の毛状になる。実ケーブルがあっても直交で通す |
| 力学モデル（force-directed） | 見るたびに位置が変わる。構成図ではない |
| 教科書のバス／リング／スターの一覧 | 物理トポロジの種類の説明用。拠点の運用図ではない |
| DC の fat-tree | データセンター向け。支社＋営業所の図とは別物 |

実務の Visio で多いのは Packet Pushers の **Tree / Dual Tree**（直角コネクタ、ハブ側で線を重ねてから扇状に開く）です。
冗長の 2 台は同じ段に横並び（Dual Tree）が正しい。上下に積むと片系に見える。

自動配置は、この形を先に出す。人が動かすのはそのあとです。
表示の好み（略記・シンプル・向き）は、この正解の上に乗ります。

避けられない交差だけ、弧のジャンプではなく **隙間（gap）** で示す、が Visio 側の定石です。

## 公式・解説（図は著作権があるのでリポジトリには入れない）

- Cisco CVD Campus LAN: 3 層（Access / Distribution / Core）と、小さい拠点の collapsed core
  https://www.cisco.com/c/en/us/td/docs/solutions/CVD/Campus/cisco-campus-lan-wlan-design-guide.html
- Packet Pushers: Tree / Dual Tree / Scalable Star（直角配線、ラベルはコネクタ端）
  https://packetpushers.net/blog/scalable-network-diagram-layouts-tips-ms-visio/
- NetworkAcademy: フルメッシュが規模で破綻すること、2 層と 3 層の使い分け
  https://www.networkacademy.io/ccna/network-fundamentals/three-tier-architecture

## third-party（Wikimedia、再配布時は各ファイルのライセンスを見る）

| ファイル | 何の例か | この製品での扱い |
|---|---|---|
| `flat-vs-hierarchy.png` | フラット vs 階層 | 階層側が正しい、という説明用 |
| `hierarchical-tree.jpeg` | 階層ツリー | 段を揃える参考。放射は使わない |
| `sample-network.svg` | 拠点っぽい接続 | 箱と線の密度の参考 |
| `ospf-cisco-style.svg` | Cisco 風の論理図 | L3 図は物理と別、の参考 |
| `fast-ethernet-logical.svg` | 論理トポロジ | 物理と論理を混ぜない、の参考 |
| `fat-tree.svg` | DC 向け fat-tree | 使わない |
| `tree-radial.png` | 放射状ツリー | **反例** |
| `network-topologies.svg` / `textbook-topologies.png` | バス・リング・スター一覧 | 教科書。運用図ではない |

## 公開図の評価

判定の詳細は `EVALUATION.md`。NetVision が出した図は `netvision-kansai.svg`。

`proposed-optimal.png`（同じ内容の SVG は `proposed-optimal.svg`）が、製品に載せる前の見本です。
関西支社を見る物理図です。手置きで描いてあります。製品の自動配置ではありません。
これが読めないなら、コードの話はまだ早いです。

- `optimal-branch.svg` … 上の結論どおりの支社図
- `avoid-radial.svg` … 同じ機器を放射に置いた反例
