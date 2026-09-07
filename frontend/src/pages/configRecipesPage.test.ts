import { describe, expect, it } from "vitest";

import { inputTypeOf, isReady } from "./ConfigRecipesPage";
import type { ConfigRecipe } from "../api/types";

const RECIPE: ConfigRecipe = {
  name: "access_port",
  label: "端末をつなぐ口にする",
  why: "",
  fields: [
    {
      name: "interface",
      label: "ポート名",
      kind: "text",
      required: true,
      help: "",
      placeholder: "",
    },
    {
      name: "vlan_id",
      label: "載せる VLAN",
      kind: "number",
      required: true,
      help: "",
      placeholder: "",
    },
    {
      name: "description",
      label: "説明",
      kind: "text",
      required: false,
      help: "",
      placeholder: "",
    },
  ],
  vendors: [{ name: "cisco_ios", label: "Cisco IOS" }],
};

describe("Config を作る", () => {
  it("必須が埋まるまでは作らせない", () => {
    expect(isReady(RECIPE, {})).toBe(false);
    expect(isReady(RECIPE, { interface: "Gi0/5" })).toBe(false);
    expect(isReady(RECIPE, { interface: "Gi0/5", vlan_id: "10" })).toBe(true);
  });

  it("空白だけの入力は入っていないものとして扱う", () => {
    expect(isReady(RECIPE, { interface: "  ", vlan_id: "10" })).toBe(false);
  });

  it("任意の欄は空でも作れる", () => {
    expect(
      isReady(RECIPE, { interface: "Gi0/5", vlan_id: "10", description: "" }),
    ).toBe(true);
  });

  it("数字の欄だけ数字として扱う", () => {
    expect(inputTypeOf("number")).toBe("number");
    expect(inputTypeOf("vlan_list")).toBe("text");
  });
});
