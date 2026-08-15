"""良い図の見た目を、手置きの座標で SVG にする。

関西6営業所のレシピの再掲ではない。紙の資料として読める線の引き方の見本。
NetVision は使わない。製品の自動配置でもない。
"""

from pathlib import Path

OUT = Path(__file__).with_name("proposed-optimal.svg")
FONT = "'WenQuanYi Micro Hei', 'Droid Sans Fallback', 'Yu Gothic', sans-serif"


def main() -> None:
    svg = f"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1120 640" width="1120" height="640">
  <style>
    text {{ font-family: {FONT}; fill: #2c2a26; }}
    .title {{ font-size: 18px; font-weight: 700; }}
    .sub {{ font-size: 11px; fill: #6a655c; }}
    .box {{ fill: #f6f1e8; stroke: #2c2a26; stroke-width: 1.7; }}
    .core {{ fill: #e7eef6; }}
    .fw {{ fill: #f3e6e2; }}
    .wan {{ fill: #eeeae3; }}
    .office {{ fill: #eef3ea; }}
    .name {{ font-size: 13px; font-weight: 600; }}
    .host {{ font-size: 10px; fill: #5c574e; }}
    .port {{ font-size: 9px; fill: #3d6b8c; }}
    .wire {{ fill: none; stroke: #3a5a40; stroke-width: 1.7; }}
    .uplink {{ stroke: #2f4d6a; }}
    .layer {{ font-size: 10px; fill: #8a8478; letter-spacing: 0.08em; }}
    .legend {{ font-size: 11px; fill: #5c574e; }}
  </style>

  <rect x="0" y="0" width="1120" height="640" fill="#fbfaf7"/>
  <text class="title" x="40" y="36">良い図（紙の資料として読める構成図）</text>
  <text class="sub" x="40" y="56">物理ケーブルのみ。論理は別紙。手置き。製品の出力ではない。</text>

  <text class="layer" x="40" y="96">WAN / 境界</text>
  <rect class="box wan" x="468" y="72" width="184" height="40" rx="4"/>
  <text class="name" x="560" y="90" text-anchor="middle">インターネット</text>
  <text class="host" x="560" y="104" text-anchor="middle">回線</text>

  <rect class="box fw" x="476" y="132" width="168" height="50" rx="4"/>
  <text class="name" x="560" y="152" text-anchor="middle">本社 FW</text>
  <text class="host" x="560" y="168" text-anchor="middle">hq-fw-01</text>

  <text class="layer" x="40" y="214">コア（冗長は同じ段）</text>
  <rect class="box core" x="360" y="208" width="156" height="54" rx="4"/>
  <text class="name" x="438" y="230" text-anchor="middle">本社コア A</text>
  <text class="host" x="438" y="246" text-anchor="middle">hq-core-rt-01</text>
  <rect class="box core" x="604" y="208" width="156" height="54" rx="4"/>
  <text class="name" x="682" y="230" text-anchor="middle">本社コア B</text>
  <text class="host" x="682" y="246" text-anchor="middle">hq-core-rt-02</text>

  <text class="layer" x="40" y="348">自拠点（見る対象）</text>
  <rect class="box" x="476" y="328" width="168" height="54" rx="4"/>
  <text class="name" x="560" y="350" text-anchor="middle">関西支社 RT</text>
  <text class="host" x="560" y="366" text-anchor="middle">ksi-br-rt-01</text>
  <rect class="box" x="476" y="434" width="168" height="54" rx="4"/>
  <text class="name" x="560" y="456" text-anchor="middle">関西支社 SW</text>
  <text class="host" x="560" y="472" text-anchor="middle">ksi-br-sw-01</text>

  <text class="layer" x="860" y="268">配下拠点（右の列）</text>
  <rect class="box office" x="860" y="280" width="176" height="48" rx="4"/>
  <text class="name" x="948" y="300" text-anchor="middle">京都営業所 RT</text>
  <text class="host" x="948" y="316" text-anchor="middle">kyo-rt-01</text>
  <rect class="box office" x="860" y="368" width="176" height="48" rx="4"/>
  <text class="name" x="948" y="388" text-anchor="middle">奈良営業所 RT</text>
  <text class="host" x="948" y="404" text-anchor="middle">nara-rt-01</text>
  <rect class="box office" x="860" y="456" width="176" height="48" rx="4"/>
  <text class="name" x="948" y="476" text-anchor="middle">神戸営業所 RT</text>
  <text class="host" x="948" y="492" text-anchor="middle">kobe-rt-01</text>

  <path class="wire uplink" d="M560,112 L560,132"/>
  <path class="wire uplink" d="M560,182 L560,196 L438,196 L438,208"/>
  <path class="wire uplink" d="M560,182 L560,196 L682,196 L682,208"/>
  <path class="wire uplink" d="M516,235 L604,235"/>
  <path class="wire uplink" d="M438,262 L438,300 L560,300 L560,328"/>
  <path class="wire uplink" d="M682,262 L682,300 L560,300 L560,328"/>
  <path class="wire" d="M560,382 L560,434"/>
  <path class="wire" d="M644,348 L790,348 L790,304 L860,304"/>
  <path class="wire" d="M644,355 L776,355 L776,392 L860,392"/>
  <path class="wire" d="M644,362 L762,362 L762,480 L860,480"/>

  <text class="port" x="548" y="126" text-anchor="end">Gi0/0</text>
  <text class="port" x="548" y="194" text-anchor="end">Gi0/1</text>
  <text class="port" x="450" y="202">Te1/0/1</text>
  <text class="port" x="690" y="202">Te1/0/1</text>
  <text class="port" x="520" y="228">Gi0/0</text>
  <text class="port" x="578" y="228">Gi0/0</text>
  <text class="port" x="450" y="276">Gi0/1</text>
  <text class="port" x="690" y="276">Gi0/1</text>
  <text class="port" x="572" y="322">Gi0/0</text>
  <text class="port" x="572" y="396">Gi0/2</text>
  <text class="port" x="572" y="430">Gi1/0/1</text>
  <text class="port" x="650" y="342">Gi0/1/1</text>
  <text class="port" x="650" y="368">Gi0/1/2</text>
  <text class="port" x="650" y="394">Gi0/1/3</text>
  <text class="port" x="854" y="300" text-anchor="end">Gi0/0</text>
  <text class="port" x="854" y="388" text-anchor="end">Gi0/0</text>
  <text class="port" x="854" y="476" text-anchor="end">Gi0/0</text>

  <rect x="40" y="530" width="520" height="86" rx="6" fill="#fff" stroke="#d9d3c8"/>
  <text class="legend" x="56" y="554">この図が守っていること（並べ方の百科ではない）</text>
  <text class="legend" x="56" y="574">1. 段で上下が読める。冗長は横並び（縦に積むと片系に見える）</text>
  <text class="legend" x="56" y="592">2. 配下は右の列。下に置くと上り回線を横切る</text>
  <text class="legend" x="56" y="610">3. 線は直角。束は等間隔。ポート名は、その箱の折れ曲がり</text>
</svg>
"""
    OUT.write_text(svg, encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
