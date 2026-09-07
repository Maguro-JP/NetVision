import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../api/client";
import type { ConfigRecipe } from "../api/types";

/**
 * 選んで、値を入れるだけの Config 作成。
 *
 * 図も台帳も資料も要らない入口。「この口をこの VLAN のアクセスにしたい」
 * という一番多い用事に、一番短い道を用意する。
 *
 * 生成するだけで、実機には投入しない。
 */

/** 入力欄1つ分の、画面での扱い。数字の欄に文字を打たせない程度の区別。 */
export function inputTypeOf(kind: string): string {
  return kind === "number" ? "number" : "text";
}

/** 入れ終わったかどうか。必須の欄が埋まるまでボタンを押させない。 */
export function isReady(
  recipe: ConfigRecipe | undefined,
  values: Record<string, string>,
): boolean {
  if (!recipe) return false;
  return recipe.fields
    .filter((field) => field.required)
    .every((field) => (values[field.name] ?? "").trim() !== "");
}

export default function ConfigRecipesPage() {
  const recipes = useQuery({
    queryKey: ["configRecipes"],
    queryFn: api.configRecipes,
  });

  const [selected, setSelected] = useState<string>("");
  const [vendor, setVendor] = useState<string>("");
  const [values, setValues] = useState<Record<string, string>>({});

  const recipe = useMemo(
    () => recipes.data?.find((item) => item.name === selected),
    [recipes.data, selected],
  );

  const render = useMutation({
    mutationFn: () => api.renderConfigRecipe(selected, vendor, values),
  });

  const choose = (name: string) => {
    setSelected(name);
    setValues({});
    render.reset();
    const next = recipes.data?.find((item) => item.name === name);
    // 前に選んだ機種がその献立で出せないことがある。出せる機種に寄せる
    const keep = next?.vendors.some((v) => v.name === vendor);
    setVendor(keep ? vendor : (next?.vendors[0]?.name ?? ""));
  };

  return (
    <>
      <h1>Config を作る</h1>
      <p className="subtitle">
        やりたいことを選んで、値を入れるだけで作れます。台帳も資料も要りません。
        <br />
        <strong>作るだけで、実機には投入しません。</strong>
        内容を確認して手で流し込んでください。
      </p>

      {recipes.isLoading && <p className="muted">読み込み中…</p>}
      {recipes.error && (
        <div className="error">{(recipes.error as Error).message}</div>
      )}

      <h2>やりたいこと</h2>
      <div className="toolbar">
        {recipes.data?.map((item) => (
          <button
            key={item.name}
            className={item.name === selected ? "primary" : ""}
            onClick={() => choose(item.name)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {recipe && (
        <>
          <p className="muted">{recipe.why}</p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              render.mutate();
            }}
          >
            <div className="form-grid">
              <div className="field">
                <label htmlFor="vendor">機種 *</label>
                <select
                  id="vendor"
                  value={vendor}
                  onChange={(e) => setVendor(e.target.value)}
                >
                  {recipe.vendors.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.label}
                    </option>
                  ))}
                </select>
                <p className="muted">
                  この一覧に無い機種は、この用事の書き方をこちらで持っていません。
                </p>
              </div>

              {recipe.fields.map((field) => (
                <div className="field" key={field.name}>
                  <label htmlFor={`f-${field.name}`}>
                    {field.label}
                    {field.required ? " *" : ""}
                  </label>
                  <input
                    id={`f-${field.name}`}
                    type={inputTypeOf(field.kind)}
                    placeholder={field.placeholder}
                    value={values[field.name] ?? ""}
                    onChange={(e) =>
                      setValues((v) => ({ ...v, [field.name]: e.target.value }))
                    }
                  />
                  {field.help && <p className="muted">{field.help}</p>}
                </div>
              ))}
            </div>

            <div className="toolbar">
              <button
                className="primary"
                type="submit"
                disabled={!isReady(recipe, values) || render.isPending}
              >
                {render.isPending ? "作成中…" : "作る"}
              </button>
            </div>
          </form>
        </>
      )}

      {render.error && (
        <div className="error">{(render.error as Error).message}</div>
      )}

      {render.data && (
        <>
          <h2>できた Config（{render.data.vendor_label}）</h2>
          {render.data.notes.map((note) => (
            <p className="muted" key={note}>
              {note}
            </p>
          ))}
          <pre className="config-preview">{render.data.body}</pre>
          <div className="toolbar">
            <button
              onClick={() => navigator.clipboard?.writeText(render.data.body)}
            >
              コピー
            </button>
          </div>
        </>
      )}
    </>
  );
}
