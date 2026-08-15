import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import QueryError from "../components/QueryError";

/**
 * 構成図の出力をまとめて選ぶ画面。
 *
 * 図の画面に出力のボタンを並べると、押す前に何が出るのか分からないまま
 * ファイルが落ちてくる。ここで中身を見てから選べるようにして、図の画面の
 * ボタンは「出力」1つに減らす。
 *
 * 絞り込み（拠点・検索・範囲）と見せ方（シンプル・ポート名・向き）は、
 * 図の画面から URL で受け取る。出すものと画面が食い違わないようにするため。
 */

type Kind = "svg" | "workbook" | "links" | "ports";

const KIND_LABEL: Record<Kind, string> = {
  svg: "図（SVG）",
  workbook: "Excel（図＋表）",
  links: "結線表",
  ports: "ポート一覧",
};

/** 保存したときのファイルの種類。押す前に何が落ちてくるか分かるようにする */
const KIND_FILE: Record<Kind, string> = {
  svg: "SVG ファイル",
  workbook: "Excel ファイル",
  links: "Excel ファイル",
  ports: "Excel ファイル",
};

const KIND_NOTE: Record<Kind, string> = {
  svg: "設計書や報告書に貼るベクタ形式。Word / PowerPoint にそのまま挿入でき、拡大しても潰れません。",
  workbook:
    "構成図・結線表・ポート一覧を1つにまとめた Excel。図はセルの罫線で描いてあるので、手元で直せます。配るのはこれ1つで済みます。",
  links:
    "結線を1行ずつ並べた表。作業指示・工事依頼・検収の突合など、図ではなく並べ替えと絞り込みが要る場面向けです。",
  ports:
    "機器から見たポートの割付。何ポートあって、上位のどこへ上がっていて、下に何がぶら下がっていて、どこが空いているか。増設の検討に使います。",
};

export default function TopologyExportPage() {
  const [params] = useSearchParams();
  const [kind, setKind] = useState<Kind>("svg");

  const layer = params.get("layer") ?? "physical";
  const siteId = params.get("site") ? Number(params.get("site")) : undefined;
  const scope = params.get("scope") ?? undefined;
  const search = params.get("q") ?? undefined;
  const simple = params.get("style") === "simple";
  const portStyle = params.get("port_style") ?? undefined;
  const alongLine = params.get("along_line") === "true";
  const orientation = params.get("orientation") ?? undefined;
  const vlan = params.get("vlan") ? Number(params.get("vlan")) : undefined;
  const vrf = params.get("vrf") ?? undefined;

  const svgUrl = api.topologyExportUrl(layer, {
    vlan,
    vrf: vrf || undefined,
    site_id: siteId,
    style: simple ? "simple" : undefined,
    port_style: portStyle,
    along_line: alongLine ? "true" : undefined,
    orientation,
  });
  const url: Record<Kind, string> = {
    svg: svgUrl,
    workbook: api.workbookUrl(layer, { site_id: siteId, scope }),
    links: api.linkTableUrl(layer, "xlsx", { site_id: siteId }),
    ports: api.portMapUrl({ site_id: siteId }),
  };

  // 図の中身。結線表の下見にも使う（出すものと同じ絞り込みで取る）
  const graph = useQuery({
    queryKey: ["topology-export", layer, siteId, search, scope, orientation],
    queryFn: () =>
      api.getTopology(layer, false, orientation ?? "vertical", {
        site_id: siteId,
        q: search,
        scope,
      }),
  });

  return (
    <div className="export-window">
      <header className="export-head">
        <div className="export-head-text">
          <strong>構成図を出す</strong>
          <span className="muted">
            {siteId ? "拠点で絞った" : "いま図に出ている"}範囲です。
            中身を見てから、必要なものだけ保存してください。
          </span>
        </div>
        {/* 保存は、押す前に何が落ちてくるか分かるように種類まで書く */}
        <a className="button export-save" href={url[kind]} download>
          <span className="export-save-icon" aria-hidden="true">
            ⤓
          </span>
          <span>
            {KIND_LABEL[kind]}を保存
            <span className="export-save-file">{KIND_FILE[kind]}</span>
          </span>
        </a>
      </header>

      <div className="export-kinds" role="tablist">
        {(Object.keys(KIND_LABEL) as Kind[]).map((value) => (
          <button
            key={value}
            role="tab"
            aria-selected={kind === value}
            className={kind === value ? "primary" : ""}
            onClick={() => setKind(value)}
          >
            {KIND_LABEL[value]}
          </button>
        ))}
      </div>

      <p className="muted export-note">{KIND_NOTE[kind]}</p>

      {graph.isError && <QueryError error={graph.error} />}

      {kind === "svg" && <SvgPreview url={svgUrl} />}
      {kind === "workbook" && (
        <WorkbookPreview
          url={svgUrl}
          devices={graph.data?.nodes.length}
          links={graph.data?.edges.length}
        />
      )}
      {kind === "links" && <LinkPreview graph={graph.data} />}
      {kind === "ports" && <PortsNote />}
    </div>
  );
}

/** 出す図そのものを出して見せる。書き出したものと同じ絵を見てから保存できる。 */
function SvgPreview({ url }: { url: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let objectUrl: string | null = null;
    setSrc(null);
    setFailed(null);
    fetch(url)
      .then(async (response) => {
        if (!response.ok) throw new Error(await response.text());
        return response.blob();
      })
      .then((blob) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch((error: unknown) => {
        if (alive) setFailed(error instanceof Error ? error.message : String(error));
      });
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);

  if (failed) return <div className="error">図を作れませんでした: {failed}</div>;
  if (!src) return <div className="muted">図を作っています…</div>;
  return (
    <div className="export-preview">
      <img src={src} alt="構成図" />
    </div>
  );
}

/** Excel の中身。図は同じ内容だが、描き直しになるので見た目は少し変わる。 */
function WorkbookPreview({
  url,
  devices,
  links,
}: {
  url: string;
  devices?: number;
  links?: number;
}) {
  return (
    <>
      <table className="export-sheets">
        <thead>
          <tr>
            <th>シート</th>
            <th>中身</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>構成図</td>
            <td>セルの罫線で描いた図。機器 {devices ?? "—"} 台</td>
          </tr>
          <tr>
            <td>結線</td>
            <td>結線 {links ?? "—"} 本を1行ずつ</td>
          </tr>
          <tr>
            <td>ポート</td>
            <td>機器ごとのポート割付</td>
          </tr>
        </tbody>
      </table>
      <p className="muted">
        図のシートは、下の絵と同じ構成をセルに描き直したものです（線の形は Excel
        の都合で変わります）。
      </p>
      <SvgPreview url={url} />
    </>
  );
}

/** 結線表の下見。出す表と同じ並びで、頭の分だけ見せる。 */
function LinkPreview({
  graph,
}: {
  graph?: { nodes: { device_id: number; hostname: string }[]; edges: unknown[] };
}) {
  if (!graph) return <div className="muted">読み込んでいます…</div>;
  const name = new Map(graph.nodes.map((node) => [node.device_id, node.hostname]));
  const rows = graph.edges as {
    id: number;
    source: number;
    target: number;
    source_interface: string | null;
    target_interface: string | null;
    confirmed_both_ways: boolean;
  }[];
  const head = rows.slice(0, PREVIEW_ROWS);

  return (
    <>
      <div className="export-rows">
        <table>
          <thead>
            <tr>
              <th>機器</th>
              <th>ポート</th>
              <th>対向</th>
              <th>対向ポート</th>
              <th>確認</th>
            </tr>
          </thead>
          <tbody>
            {head.map((row) => (
              <tr key={row.id}>
                <td>{name.get(row.source) ?? row.source}</td>
                <td className="mono">{row.source_interface ?? "—"}</td>
                <td>{name.get(row.target) ?? row.target}</td>
                <td className="mono">{row.target_interface ?? "—"}</td>
                <td className="muted">{row.confirmed_both_ways ? "両側" : "片側"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted">
        全 {rows.length} 本のうち {head.length} 本を出しています。保存すると全部入ります。
      </p>
    </>
  );
}

function PortsNote() {
  return (
    <p className="muted">
      1枚目は機器ごとの一覧（総ポート数・使用・空き・上位/配下）、2枚目は結線図です。
      画面で下見はできないので、保存して開いてください。
    </p>
  );
}

/** 下見に出す行数。全部出すと保存する前にここで読み終えてしまう */
const PREVIEW_ROWS = 30;
