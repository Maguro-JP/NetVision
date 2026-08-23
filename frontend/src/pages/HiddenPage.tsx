import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../api/client";

/**
 * 表に出していない場所。
 *
 * 試している途中のもの、まだ人に渡していないものを置く。メニューに並べると
 * 「使えるもの」として扱われてしまい、途中のものを本番の判断に使われる。
 * かといって隠したまま忘れるのも困るので、入口だけ設定の一番下に置いてある。
 *
 * ここに置いたものは、まだ確かめきれていない、が前提。
 */

/** 入口を押した回数と、開くかどうか。うっかり触って開くのを避ける。 */
export const TAPS_TO_OPEN = 3;

export function opensAt(taps: number): boolean {
  return taps >= TAPS_TO_OPEN;
}

/** 途中のもの。仕上がったらメニューへ移し、この行を消す。 */
const WORKING_ON = [
  {
    title: "MIB と OS まわり",
    note: "SNMP で取れる値の扱いと、OS バージョンの持ち方・比べ方を整理中です。",
  },
  {
    title: "NetVision へのログイン",
    note: "誰が触ったかを残せるようにします。入るまでは待ち受けを 127.0.0.1 に絞っています。",
  },
  {
    title: "保存してあるものの暗号化",
    note: "サーバーに置くとき・移すときに備えます。docs/DATA_PROTECTION.md に選択肢があります。",
  },
  {
    title: "実機へのコマンド実行",
    note: "show run / show ver を代わりに打つ構想です。閉域の前提と手順が固まるまで作りません。",
  },
];

export default function HiddenPage() {
  const { data } = useQuery({ queryKey: ["meta"], queryFn: api.meta });

  return (
    <>
      <h1>まだ表に出していない場所</h1>
      <p className="subtitle">
        試している途中のものを置いています。ここにあるものは確かめきれていないので、
        本番の判断には使わないでください。仕上がったものはメニューへ移します。
      </p>

      <div className="card">
        <h2>手を付けているもの</h2>
        <ul>
          {WORKING_ON.map((item) => (
            <li key={item.title}>
              {item.title}
              <div className="muted">{item.note}</div>
            </li>
          ))}
        </ul>
      </div>

      <div className="card">
        <h2>いまの状態</h2>
        <ul className="muted">
          <li>版：{data?.version ?? "—"}</li>
          <li>表の版：{data?.schema_revision ?? "—"}</li>
          <li>
            デモ用の動き：{data?.demo_mode ? "入っています" : "入っていません"}
          </li>
        </ul>
      </div>

      <p>
        <Link to="/settings">設定に戻る</Link>
      </p>
    </>
  );
}
