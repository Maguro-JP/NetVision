"""関西支社の物理構成図を、手置きの座標で SVG にする。

NetVision は使わない。箱の位置と通り道は、現場の紙面と同じ決め方。
"""

from pathlib import Path

OUT = Path(__file__).with_name("proposed-optimal.svg")

W, H = 188, 50
FONT = "'WenQuanYi Micro Hei', 'Droid Sans Fallback', 'Yu Gothic', sans-serif"


def box(x: float, y: float, title: str, sub: str) -> str:
    mid = x + W / 2
    sub_line = (
        f'<text x="{mid:.0f}" y="{y + 38:.0f}" text-anchor="middle" font-size="10" '
        f'fill="#5a5a5a">{sub}</text>'
        if sub
        else ""
    )
    title_y = y + (22 if sub else 32)
    return (
        f'<g>\n'
        f'  <rect x="{x:.0f}" y="{y:.0f}" width="{W:.0f}" height="{H:.0f}" '
        f'rx="2" fill="#ffffff" stroke="#2a2a2a" stroke-width="1.5"/>\n'
        f'  <text x="{mid:.0f}" y="{title_y:.0f}" text-anchor="middle" font-size="13" '
        f'font-weight="700" fill="#1a1a1a">{title}</text>\n'
        f"  {sub_line}\n"
        f"</g>"
    )


def frame(x: float, y: float, w: float, h: float, name: str) -> str:
    return (
        f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="2" '
        f'fill="none" stroke="#7a8699" stroke-width="1.2" stroke-dasharray="5 3"/>\n'
        f'<text x="{x + 8:.0f}" y="{y + 16:.0f}" font-size="11" fill="#5a6570">{name}</text>'
    )


def wire(points: list[tuple[float, float]]) -> str:
    pts = " ".join(f"{x:.0f},{y:.0f}" for x, y in points)
    return f'<polyline points="{pts}" fill="none" stroke="#2a2a2a" stroke-width="1.5"/>'


def port(x: float, y: float, text: str, anchor: str = "start") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="10" fill="#1e4f8a" text-anchor="{anchor}" '
        f'paint-order="stroke" stroke="#f7f4ee" stroke-width="3">{text}</text>'
    )


# --- 座標。上段が上位。冗長は同じ高さ。配下は右の列。 ---
core_a = (48.0, 88.0)
core_b = (276.0, 88.0)
br_rt = (162.0, 252.0)
br_sw = (162.0, 348.0)

offices = [
    ("shiga-rt-01", "shiga-sw-01", "滋賀県営業所"),
    ("kyoto-rt-01", "kyoto-sw-01", "京都府営業所"),
    ("osaka-rt-01", "osaka-sw-01", "大阪府営業所"),
    ("hyogo-rt-01", "hyogo-sw-01", "兵庫県営業所"),
    ("nara-rt-01", "nara-sw-01", "奈良県営業所"),
    ("wakayama-rt-01", "wakayama-sw-01", "和歌山県営業所"),
]
office_x = 560.0
office_sw_x = office_x + W + 18
office_ys = [252.0 + i * 88.0 for i in range(6)]

parts: list[str] = []
width, height = 1000, 860
parts.append(
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
    f'viewBox="0 0 {width} {height}" font-family="{FONT}">'
)
parts.append('<rect width="100%" height="100%" fill="#f7f4ee"/>')
parts.append(
    '<text x="40" y="36" font-size="18" font-weight="700" fill="#1a1a1a">'
    "関西支社 物理構成図</text>"
)
parts.append(
    '<text x="960" y="34" font-size="11" fill="#6b6b6b" text-anchor="end">'
    "物理ケーブルのみ · 論理は別紙</text>"
)

# 枠
parts.append(frame(24, 56, 464, 98, "西日本センター"))
parts.append(frame(138, 224, 236, 190, "関西支社"))
for i, (_, _, site) in enumerate(offices):
    y = office_ys[i]
    parts.append(frame(546, y - 24, 430, 86, site))

# 線（箱より先に描く）
ca_x, ca_y = core_a
cb_x, cb_y = core_b
rt_x, rt_y = br_rt
sw_x, sw_y = br_sw
# センター間
parts.append(wire([(ca_x + W, ca_y + 16), (cb_x, ca_y + 16)]))
parts.append(wire([(ca_x + W, ca_y + 34), (cb_x, ca_y + 34)]))
parts.append(port(ca_x + W + 4, ca_y + 12, "Te0/0/0"))
parts.append(port(cb_x - 4, ca_y + 12, "Te0/0/0", "end"))
parts.append(port(ca_x + W + 4, ca_y + 46, "Te0/0/1"))
parts.append(port(cb_x - 4, ca_y + 46, "Te0/0/1", "end"))

# 支社→センター（2本。同じ高さの対へ、縦横、等間隔）
rt_top_l = rt_x + 48
rt_top_r = rt_x + 140
ca_bot = (ca_x + W / 2, ca_y + H)
cb_bot = (cb_x + W / 2, cb_y + H)
parts.append(wire([(ca_bot[0], ca_bot[1]), (ca_bot[0], 176), (rt_top_l, 176), (rt_top_l, rt_y)]))
parts.append(wire([(cb_bot[0], cb_bot[1]), (cb_bot[0], 208), (rt_top_r, 208), (rt_top_r, rt_y)]))
parts.append(port(ca_bot[0] + 6, ca_bot[1] + 12, "ge-0/0/5"))
parts.append(port(cb_bot[0] + 6, cb_bot[1] + 12, "ge-0/0/5"))
parts.append(port(rt_top_l + 6, rt_y - 6, "Gi0/0/0"))
parts.append(port(rt_top_r + 6, rt_y - 6, "Gi0/0/1"))

# 支社 RT→SW
parts.append(wire([(rt_x + W / 2, rt_y + H), (sw_x + W / 2, sw_y)]))
parts.append(port(rt_x + W / 2 + 6, rt_y + H + 12, "Gi0/0/1"))
parts.append(port(sw_x + W / 2 + 6, sw_y - 4, "Gi1/0/1"))

# 営業所。右へ出して縦の通り道、高さで抜く。斜めは出さない
bus0 = 418.0
for i, (rt_name, sw_name, _site) in enumerate(offices):
    oy = office_ys[i]
    bus_x = bus0 + i * 14
    leave_y = rt_y + 10 + i * 6
    enter_y = oy + H / 2
    parts.append(
        wire(
            [
                (rt_x + W, leave_y),
                (bus_x, leave_y),
                (bus_x, enter_y),
                (office_x, enter_y),
            ]
        )
    )
    parts.append(port(rt_x + W + 4, leave_y - 3, f"Gi0/1/{i + 1}"))
    parts.append(port(office_x - 4, enter_y - 3, "GE0/0", "end"))
    # 営業所内 RT→SW
    parts.append(wire([(office_x + W, oy + H / 2), (office_sw_x, oy + H / 2)]))
    parts.append(port(office_x + W + 4, oy + H / 2 - 3, "GE0/1"))
    parts.append(port(office_sw_x - 4, oy + H / 2 - 3, "Gi1/0/25", "end"))

# 機器
parts.append(box(*core_a, "west-core-rt-01", "MX304"))
parts.append(box(*core_b, "west-core-rt-02", "MX304"))
parts.append(box(*br_rt, "ksi-br-rt-01", "ISR4451-X"))
parts.append(box(*br_sw, "ksi-br-sw-01", "C9300-48P"))
for i, (rt_name, sw_name, _site) in enumerate(offices):
    y = office_ys[i]
    parts.append(box(office_x, y, rt_name, "IX2215"))
    parts.append(box(office_sw_x, y, sw_name, "QX-S5528P"))

parts.append(
    '<text x="40" y="828" font-size="11" fill="#4a4a4a">'
    "上段が上位。冗長は同じ高さに横並び。"
    "配下拠点は右の列（下に置くと上り回線を横切る）。"
    "線は箱の辺から縦横。ポート名はその機器の外。</text>"
)
parts.append(
    '<text x="40" y="846" font-size="11" fill="#4a4a4a">'
    "手置き。製品の自動配置ではない。</text>"
)
parts.append("</svg>")
OUT.write_text("\n".join(parts) + "\n", encoding="utf-8")
print(OUT)
