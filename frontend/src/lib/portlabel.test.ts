import { describe, expect, it } from "vitest";

import { formatPort, mediaOf, splitPort } from "./portlabel";

describe("splitPort", () => {
  it.each([
    ["TenGigabitEthernet1/1/1", "Te", "1/1/1", "ten"],
    ["GigabitEthernet1/0/24", "Gi", "1/0/24", "giga"],
    ["FastEthernet0/1", "Fa", "0/1", "fast"],
    ["Port-channel10", "Po", "10", "lag"],
    ["GigaEthernet0.1", "GE", "0.1", "giga"],
    ["Bridge-Aggregation1", "BAGG", "1", "lag"],
    ["ge-0/0/1", "ge", "0/0/1", "giga"],
    ["xe-0/0/0", "xe", "0/0/0", "ten"],
    ["ae0", "ae", "0", "lag"],
    ["Gi1/0/24", "Gi", "1/0/24", "giga"],
  ])("%s を分解する", (name, short, number, media) => {
    expect(splitPort(name)).toEqual({ short, number, media });
  });
});

describe("formatPort", () => {
  it("略記と番号だけを出し分ける", () => {
    const name = "TenGigabitEthernet1/1/1";
    expect(formatPort(name, "full")).toBe(name);
    expect(formatPort(name, "short")).toBe("Te1/1/1");
    expect(formatPort(name, "number")).toBe("1/1/1");
  });

  it("種別が分からない名前は削らない", () => {
    // 色にも情報が乗らないまま元の名前が消えると、何のポートか分からなくなる
    expect(formatPort("port1", "number")).toBe("port1");
    expect(mediaOf("port1")).toBe("other");
  });

  it("NEC IX の GigaEthernet を Cisco の GigabitEthernet と混ぜない", () => {
    expect(formatPort("GigaEthernet0.1", "short")).toBe("GE0.1");
    expect(formatPort("GigabitEthernet0/1", "short")).toBe("Gi0/1");
  });

  it("空文字はそのまま", () => {
    expect(formatPort("", "short")).toBe("");
  });
});

describe("後から足したベンダーのポート名", () => {
  it("ファーウェイは速度が頭に来る", () => {
    expect(splitPort("10GE1/0/1")).toEqual({
      short: "10GE",
      number: "1/0/1",
      media: "ten",
    });
    expect(splitPort("100GE1/0/1").media).toBe("hundred");
  });

  it("仮想の口と束ねを、その色で出す", () => {
    expect(splitPort("Vlanif10").media).toBe("virtual");
    expect(splitPort("Ve10").media).toBe("virtual");
    expect(splitPort("Eth-Trunk1").media).toBe("lag");
  });
});
