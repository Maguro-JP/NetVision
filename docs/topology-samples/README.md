# 構成図サンプルと、この製品での最適

集めた図と、そこから決めた「この製品ではこう描く」を置く場所です。
Cisco のキャンパス図をそのまま真似するのが最適ではありません。

判定の本文は `EVALUATION.md`。ここは置き場所の案内です。

## 良い図

印刷してラックの前で指差せる資料です。並べ方の ASCII ではありません。

- 誰が上流か、どこが冗長か、どのポートかが一目で残る
- 同じ役割は同じ高さ。冗長の2台は横並び（縦に積むと片系に見える）
- 自拠点は縦。配下拠点は右の列（下に置くと上り回線を横切る）
- 線は箱の辺から直角。ハブ側で重ねてから等間隔で扇。箱を貫かない
- ポート名は、その箱の折れ曲がり。線の中央に置かない
- 物理と L2 / L3 は別紙
- 人が置いた位置は、見るだけでは動かない
- 交差は隙間。自動で線を引き直さない。A3 で読める

`proposed-optimal.png`（同じ内容の SVG は `proposed-optimal.svg`）が、その見た目の手置き見本です。
製品の自動配置ではありません。これが読めないなら、コードの話はまだ早いです。

画面の好み（略記・シンプル）は、この正解の上に乗せるものです。

## 最適ではないもの

| 形 | なぜこの製品では使わない |
|---|---|
| 放射状 | 上下が読めない。ポート順も置けない |
| フルメッシュをそのまま描く | 線が髪の毛状になる。実ケーブルがあっても直交で通す |
| 力学モデル | 見るたびに位置が変わる。構成図ではない |
| 教科書のバス／リング／スターの一覧 | 物理トポロジの種類の説明用。拠点の運用図ではない |
| DC の fat-tree | データセンター向け。支社＋営業所の図とは別物 |

小さくて Core / Dist / Access が分かれていない拠点は、Cisco の 3 段を空で作らない。
実際のホップ（結線の上下）で段を決める。種別だけで横一列にするのは不正解です。

## 公式・解説（図は著作権があるのでリポジトリには入れない）

- Cisco CVD Campus LAN: 3 層（Access / Distribution / Core）と、小さい拠点の collapsed core
  https://www.cisco.com/c/en/us/td/docs/solutions/CVD/Campus/cisco-campus-lan-wlan-design-guide.html
- Packet Pushers: 直角コネクタ、ハブ側で線を重ねてから扇、ラベルはコネクタ端、交差は隙間、自動で引き直さない、A3 で読める
  https://packetpushers.net/blog/scalable-network-diagram-layouts-tips-ms-visio/
- NetworkAcademy: フルメッシュが規模で破綻すること、2 層と 3 層の使い分け
  https://www.networkacademy.io/ccna/network-fundamentals/three-tier-architecture

## third-party（Wikimedia。作者とライセンスは `third-party/SOURCES.txt`）

| ファイル | 何の例か | この製品での扱い |
|---|---|---|
| `flat-vs-hierarchy.png` | フラット vs 階層 | 階層側が正しい、という説明用 |
| `hierarchical-tree.jpeg` | 階層 | 段を揃える参考。放射は使わない |
| `sample-network.svg` | 拠点っぽい接続 | 箱と線の密度の参考 |
| `ospf-cisco-style.svg` | Cisco 風の論理図 | L3 図は物理と別、の参考 |
| `fast-ethernet-logical.svg` | 論理トポロジ | 物理と論理を混ぜない、の参考 |
| `fat-tree.svg` | DC 向け fat-tree | 使わない |
| `tree-radial.png` | 放射状 | **反例** |
| `network-topologies.svg` / `textbook-topologies.png` | バス・リング・スター一覧 | 教科書。運用図ではない |

## 製品が出している図

`netvision-kansai.svg` は、関西支社で絞った自動配置＋SVG 書き出しです。
対向の冗長は折りたたまず、紙の地色で出します。交差の隙間や、見本と同じ資料にすることは、まだです。

パターンごとの書き出しは `patterns/` にあります。

- `optimal-branch.svg` … 支社の並べ方の小さな図
- `avoid-radial.svg` … 同じ機器を放射に置いた反例
