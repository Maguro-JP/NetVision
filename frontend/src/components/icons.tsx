/**
 * 画面で使う小さな印。
 *
 * 文字の記号（⚙ や ☰）は、環境によって色も大きさも揃わない。絵文字として
 * 描かれて浮くこともある。線で描いておけば、どこでも同じ太さ・同じ色で出る。
 *
 * 大きさは字の大きさに合わせず、押せる場所（.icon-button）の側で決める。
 */

type Props = { size?: number };

function frame(size: number | undefined, children: React.ReactNode) {
  return (
    <svg
      width={size ?? 18}
      height={size ?? 18}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

export function GearIcon({ size }: Props) {
  return frame(
    size,
    <>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M19.4 14.5a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.11-1.56 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.56-1.11 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34H9a1.7 1.7 0 0 0 1-1.56V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87V9a1.7 1.7 0 0 0 1.56 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1.03Z" />
    </>,
  );
}

export function MenuIcon({ size }: Props) {
  return frame(
    size,
    <>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </>,
  );
}

/** メニューを出したままにする（画鋲）。 */
export function PinIcon({ size }: Props) {
  return frame(
    size,
    <>
      <path d="M9 3h6M12 3v7M7.5 10h9l1.5 4H6l1.5-4ZM12 14v7" />
    </>,
  );
}

/** メニューを畳む（左へ寄せる）。 */
export function CollapseIcon({ size }: Props) {
  return frame(
    size,
    <>
      <path d="M13.5 8 9.5 12l4 4M19 4v16" />
    </>,
  );
}
