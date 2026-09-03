/**
 * スマホから使う「手元 Config」の中核。
 *
 * ページは1枚のファイルで持ち歩ける形にしてあるので、中身の計算だけを
 * 取り出して確かめる。IP の取り違えは目で見て気づけないため、ここを厚くする。
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

type Cidr = {
  ok: boolean;
  error?: string;
  cidr: string;
  address: string;
  netmask: string;
  wildcard: string;
  broadcast: string;
  length: number;
};

type Result = { ok: boolean; problems: string[]; notes?: string[]; config: string };

function load() {
  const html = readFileSync(join(__dirname, "..", "public", "phone.html"), "utf8");
  const core = html.split('<script id="core">')[1].split("</script>")[0];
  const sandbox: Record<string, unknown> = {};
  new Function("globalThis", `${core}`).call(sandbox, sandbox);
  return sandbox.NV as {
    parseCidr: (text: string) => Cidr;
    generate: (input: unknown) => Result;
    usableHost: (net: Cidr) => boolean;
  };
}

const NV = load();

const BASE = {
  vendor: "cisco",
  hostname: "acc-sw-01",
  layer3: true,
  vlans: [{ id: "10", name: "office", address: "10.0.10.2/24" }],
};

describe("アドレスの計算", () => {
  it("マスクとワイルドカードを両方出す", () => {
    const net = NV.parseCidr("10.0.10.2/24");
    expect(net.ok).toBe(true);
    expect(net.cidr).toBe("10.0.10.0/24");
    expect(net.netmask).toBe("255.255.255.0");
    expect(net.wildcard).toBe("0.0.0.255");
    expect(net.broadcast).toBe("10.0.10.255");
  });

  it("端数の長さも取り違えない", () => {
    expect(NV.parseCidr("192.168.1.130/25").cidr).toBe("192.168.1.128/25");
    expect(NV.parseCidr("10.0.0.5/30").netmask).toBe("255.255.255.252");
    expect(NV.parseCidr("172.16.0.1/12").cidr).toBe("172.16.0.0/12");
  });

  it("長さを書いていなければ、書いてくださいと言う", () => {
    expect(NV.parseCidr("10.0.0.1").ok).toBe(false);
    expect(NV.parseCidr("10.0.0.1").error).toContain("長さ");
  });

  it("アドレスとして読めないものは、埋めずに断る", () => {
    expect(NV.parseCidr("10.0.0.300/24").ok).toBe(false);
    expect(NV.parseCidr("なにか/24").ok).toBe(false);
    expect(NV.parseCidr("10.0.0.1/40").ok).toBe(false);
  });

  it("2台で使い切る帯（/31）は、両方とも機器に付けられる", () => {
    expect(NV.usableHost(NV.parseCidr("10.0.0.0/31"))).toBe(true);
    expect(NV.usableHost(NV.parseCidr("10.0.0.1/31"))).toBe(true);
  });
});

describe("入れ間違いを、作る前に止める", () => {
  it("ネットワークアドレスは機器に付けられない", () => {
    const result = NV.generate({ ...BASE, vlans: [{ id: "10", address: "10.0.10.0/24" }] });
    expect(result.ok).toBe(false);
    expect(result.problems[0]).toContain("ネットワークアドレス");
  });

  it("ブロードキャストアドレスも付けられない", () => {
    const result = NV.generate({ ...BASE, vlans: [{ id: "10", address: "10.0.10.255/24" }] });
    expect(result.problems[0]).toContain("ブロードキャスト");
  });

  it("重なっているセグメントを見つける", () => {
    const result = NV.generate({
      ...BASE,
      vlans: [
        { id: "10", address: "10.0.0.1/23" },
        { id: "20", address: "10.0.1.1/24" },
      ],
    });
    expect(result.problems[0]).toContain("重なっています");
  });

  it("仮想アドレスがそのセグメントの外なら止める", () => {
    const result = NV.generate({
      ...BASE,
      vlans: [{ id: "10", address: "10.0.10.2/24", virtualIp: "10.0.99.1" }],
    });
    expect(result.problems[0]).toContain("中にありません");
  });

  it("仮想アドレスが自分と同じなら止める", () => {
    const result = NV.generate({
      ...BASE,
      vlans: [{ id: "10", address: "10.0.10.2/24", virtualIp: "10.0.10.2" }],
    });
    expect(result.problems[0]).toContain("同じです");
  });

  it("出口がどのセグメントにも無ければ言う", () => {
    const result = NV.generate({ ...BASE, defaultGateway: "192.168.99.1" });
    expect(result.problems[0]).toContain("どのセグメントにもありません");
  });

  it("VLAN 番号の範囲を外れたら止める", () => {
    expect(NV.generate({ ...BASE, vlans: [{ id: "5000" }] }).problems[0]).toContain("4094");
  });
});

describe("ベンダーごとの書き方", () => {
  const full = {
    ...BASE,
    vlans: [{ id: "10", name: "office", address: "10.0.10.2/24", virtualIp: "10.0.10.1" }],
    uplink: "Gi1/0/49",
    accessPorts: [{ range: "Gi1/0/1-24", vlan: "10" }],
    defaultGateway: "10.0.10.254",
  };

  it("Cisco IOS はマスクを別に書き、HSRP を standby で書く", () => {
    const config = NV.generate({ ...full, vendor: "cisco" }).config;
    expect(config).toContain("ip address 10.0.10.2 255.255.255.0");
    expect(config).toContain("standby 10 ip 10.0.10.1");
    expect(config).toContain("ip route 0.0.0.0 0.0.0.0 10.0.10.254");
    expect(config).toContain("switchport trunk allowed vlan 10");
  });

  it("NX-OS は HSRP を入れ子で書く", () => {
    const config = NV.generate({ ...full, vendor: "nxos" }).config;
    expect(config).toContain(" hsrp 10");
    expect(config).toContain("  ip 10.0.10.1");
  });

  it("Arista は長さで書き、仮想アドレスは virtual-router", () => {
    const config = NV.generate({ ...full, vendor: "arista" }).config;
    expect(config).toContain("ip address 10.0.10.2/24");
    expect(config).toContain("ip virtual-router address 10.0.10.1");
    expect(config).toContain("ip route 0.0.0.0/0 10.0.10.254");
  });

  it("Aruba CX は VRRP を address-family の下に書く", () => {
    const config = NV.generate({ ...full, vendor: "arubacx" }).config;
    expect(config).toContain("interface vlan 10");
    expect(config).toContain("vrrp 10 address-family ipv4");
    expect(config).toContain("address 10.0.10.1 primary");
  });

  it("Comware は Vlan-interface とマスクで書く", () => {
    const config = NV.generate({ ...full, vendor: "comware" }).config;
    expect(config).toContain("interface Vlan-interface10");
    expect(config).toContain("ip address 10.0.10.2 255.255.255.0");
    expect(config).toContain("vrrp vrid 10 virtual-ip 10.0.10.1");
  });

  it("AlliedWare Plus は vlan database と router vrrp で書く", () => {
    const config = NV.generate({ ...full, vendor: "allied" }).config;
    expect(config).toContain("vlan database");
    expect(config).toContain("router vrrp 10 vlan10");
  });
});

describe("出さないもの", () => {
  it("合言葉や鍵は出さず、埋める場所として残す", () => {
    const config = NV.generate(BASE).config;
    expect(config).toContain("投入前に記入が必要");
    expect(config).not.toMatch(/password\s+\S+|community\s+\S+/);
  });

  it("ゲートウェイを持たない機器には、アドレスを出さない", () => {
    const result = NV.generate({ ...BASE, layer3: false });
    expect(result.config).not.toContain("ip address");
    expect(result.config).toContain("vlan 10");
    expect(result.notes?.join("")).toContain("アドレスは出していません");
  });
});
