/**
 * NetVision の印。
 *
 * 図を扱う道具なので、印も図にする。中心から三方へ伸びる線と、その先の点。
 * 文字だけの見出しだと、畳んだときの上の行や別ウィンドウのように小さく
 * 出る場所で埋もれる。形があれば、字を読まなくても見分けが付く。
 *
 * 外部の画像は使わない（閉域で動かす前提なので、読み込みに出ない）。
 */

import { useId } from "react";

export function Mark({ size = 22 }: { size?: number }) {
  // 同じ印を2か所（左のメニューと上の行）に出す。塗りの名前が同じだと、
  // 隠れている側の定義を参照して、もう片方が描かれないことがある
  const gradient = `${useId()}mark`;
  return (
    <svg
      className="brand-mark"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={gradient} x1="3" y1="3" x2="21" y2="21" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#79c0ff" />
          <stop offset="1" stopColor="#4c6ef5" />
        </linearGradient>
      </defs>
      <g stroke={`url(#${gradient})`} strokeWidth="1.5" strokeLinecap="round">
        <path d="M12 11.4 6.6 7.2M12 11.4 18.4 8.6M12 11.4 12.2 18.4" opacity="0.75" />
        <circle cx="6.6" cy="7.2" r="2.1" />
        <circle cx="18.4" cy="8.6" r="2.1" />
        <circle cx="12.2" cy="18.4" r="2.1" />
      </g>
      <circle cx="12" cy="11.4" r="3" fill={`url(#${gradient})`} />
    </svg>
  );
}

/** 印と名前をひとまとまりで出す。左のメニューと、畳んだときの上の行で使う。 */
export default function Logo({ size }: { size?: number }) {
  return (
    <span className="brand-lockup">
      <Mark size={size} />
      <span className="brand-name">
        Net<span>Vision</span>
      </span>
    </span>
  );
}
