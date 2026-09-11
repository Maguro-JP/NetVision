"""パーサが返す構造。ベンダーごとの差はここに吸収して、上位は同じ形で扱う。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from app.services.configparse.features import FEATURE_LABELS, ParsedFeature  # noqa: F401
from app.services.configparse.ifname import expand, normalize, normalize_hostname


class Vendor(enum.StrEnum):
    CISCO_IOS = "cisco_ios"
    CISCO_NXOS = "cisco_nxos"
    CISCO_ASA = "cisco_asa"
    CISCO_XR = "cisco_xr"
    JUNIPER = "juniper"
    FORTINET = "fortinet"
    NEC_IX = "nec_ix"
    NEC_QX = "nec_qx"
    YAMAHA = "yamaha"
    PALO_ALTO = "palo_alto"
    ARISTA = "arista"
    ARUBA_CX = "aruba_cx"
    ARUBA_OS = "aruba_os"
    ALLIED = "allied"
    ALAXALA = "alaxala"
    HUAWEI = "huawei"
    CISCO_WLC = "cisco_wlc"
    FASTIRON = "fastiron"
    EXOS = "exos"
    DELL_OS10 = "dell_os10"
    VYOS = "vyos"
    ROUTEROS = "routeros"
    H3C = "h3c"
    CISCO_SB = "cisco_sb"
    UNKNOWN = "unknown"


class NeighborProtocol(enum.StrEnum):
    CDP = "cdp"
    LLDP = "lldp"


@dataclass
class ParsedInterface:
    """ポート1つ分。

    VLAN は3つの顔を分けて持つ。混ぜると現場の判断に使えないため。

    - `vlan` … アクセス VLAN（そのポートに繋いだ端末が乗るセグメント）
    - `native_vlan` … トランクのタグ無し VLAN。片側だけ変えると通信が漏れる
    - `trunk_vlans` … トランクで許可している VLAN。ここが両端で食い違うと不通になる
    """

    name: str
    description: str | None = None
    ip_address: str | None = None
    #: アドレスを固定で書いていないときの、その理由。DHCP で貰う・他の口から
    #: 借りる（unnumbered）・相手と決める（negotiated）は「無い」ではない。
    #: 空欄にしてしまうと、付け忘れと見分けが付かなくなる
    address_source: str | None = None
    vlan: int | None = None
    mode: str | None = None  # access / trunk / hybrid / routed
    native_vlan: int | None = None
    #: IP 電話用に、同じ口へタグ付きで載せる VLAN（Cisco の switchport voice vlan）。
    #: これを読まないと、その VLAN がどこにも使われていないように見える
    voice_vlan: int | None = None
    trunk_vlans: list[int] = field(default_factory=list)
    # 許可 VLAN を書いていないトランクは「全部通す」。番号を並べて持つと
    # 4094個になるので、意味の方を持っておき、実際の集合は機器の定義 VLAN で解決する
    trunk_all: bool = False
    trunk_excluded: set[int] = field(default_factory=set)  # `except` 指定の除外分
    vrf: str | None = None
    #: 同じポートに付いている2つ目以降のアドレス（secondary、IPv6）。
    #: 1つ目を上書きすると、その機器の本来のアドレスが台帳から消える
    extra_addresses: list[str] = field(default_factory=list)
    #: 束ねているポートの番号（LAG / ポートチャネル）。同じ番号どうしが1本の線になる
    lag_group: int | None = None
    #: 束ね方（active / passive / on）。両端で食い違うと束にならない
    lag_mode: str | None = None
    #: 実際に束に入っているか（show etherchannel summary の印）。
    #: 設定してあっても入っていないことがあり、そのときは帯域が出ない
    lag_bundled: bool | None = None
    #: STP が塞いでいる VLAN（show spanning-tree の BLK）。
    #: 冗長のために正常なこともあるが、「通らない」の原因はここが多い
    stp_blocked_vlans: list[int] = field(default_factory=list)
    #: 受けたときに壊れていた数（show interfaces の CRC）。
    #: 線・光・二重の食い違いでしか増えない。Config からは絶対に分からない
    input_crc_errors: int | None = None
    #: 1回に送れる大きさ。両端で違うと、大きい荷物だけが通らない。
    #: 「だいたい動くのに、たまに固まる」といういちばん厄介な形になる
    mtu: int | None = None
    #: 速度と二重。片方だけ固定すると、遅くはなるが通ってしまう
    speed: str | None = None
    duplex: str | None = None
    #: OSPF の決めごと。両端で1つでも食い違うと、隣どうしとして認識しない。
    #: 上がらないので気づきはするが、原因がここだと分かるまでが長い
    ospf_area: str | None = None
    ospf_hello: int | None = None
    ospf_dead: int | None = None
    #: 線の見立て（point-to-point / broadcast）。ここが違うと隣にならない
    ospf_network: str | None = None
    #: 合言葉の掛け方（none / text / md5）。値は持たない
    ospf_auth: str | None = None
    enabled: bool = True
    #: 実機の線の状態（up / down / notconnect など、show の出力から）。
    #: Config だけでは分からない。書いていなければ None のまま（推測しない）
    link_state: str | None = None

    @property
    def key(self) -> str:
        return normalize(self.name)

    def carried_vlans(self, defined: set[int] | None = None) -> set[int]:
        """このポートが運ぶ VLAN すべて。図でセグメントを追うのに使う。

        `defined` はその機器に定義されている VLAN。「全部通す」トランクは
        ここと突き合わせて実際の集合にする。
        """
        out = set(self.trunk_vlans)
        if self.trunk_all and defined:
            out |= defined - self.trunk_excluded
        # IP 電話の VLAN も、その口が運ぶ VLAN。数え落とすと「どこにも
        # 使われていない」ことになり、逆に定義が無いことにも気づけない
        for value in (self.vlan, self.native_vlan, self.voice_vlan):
            if value is not None:
                out.add(value)
        return out


@dataclass
class ParsedVrf:
    """機器の中に切った仮想ルーター（VRF / VPN インスタンス / VDOM）。

    経路表が分かれるので、同じ図に見えていても VRF が違えば通信できない。
    「繋がっているのに通らない」の原因になりやすいところなので、明示的に持つ。
    """

    name: str
    rd: str | None = None  # route-distinguisher
    description: str | None = None


@dataclass
class ParsedTunnel:
    """トンネル1本分（GRE / IPsec など）。

    論理構成図の材料。物理では「拠点→支社→センター」と辿っていても、
    論理ではセンターと直結して見えることがある。そのズレが障害切り分けの
    肝になるので、物理と別に持つ。

    宛先は IP でしか書かれていないことが多い。相手機器の特定は、
    台帳の管理IP・インターフェースIP と突き合わせて後から行う。
    """

    name: str
    kind: str | None = None  # gre / ipsec / vxlan など、設定に書かれていた種別
    source: str | None = None  # 送信元 IP、またはインターフェース名
    destination: str | None = None
    vrf: str | None = None
    description: str | None = None


@dataclass
class ParsedNeighbor:
    """隣接情報。ここから物理結線を推定する。"""

    local_interface: str
    remote_hostname: str
    remote_interface: str | None = None
    remote_platform: str | None = None
    protocol: NeighborProtocol = NeighborProtocol.CDP

    @property
    def remote_key(self) -> str:
        return normalize_hostname(self.remote_hostname)


@dataclass
class ParsedConfig:
    vendor: Vendor = Vendor.UNKNOWN
    hostname: str | None = None
    #: OS の名前。機器が印字した名前か、そのメーカーが自分の OS をそう呼んでいる名前だけを
    #: 入れる。こちらの記憶で付けた名前は入れない（別のメーカーの名前を当てて黙って通る
    #: ことがあった）。読めていなければ None のまま。無いのと読めていないのを混ぜない
    os_name: str | None = None
    os_version: str | None = None
    model: str | None = None
    serial_number: str | None = None
    interfaces: list[ParsedInterface] = field(default_factory=list)
    neighbors: list[ParsedNeighbor] = field(default_factory=list)
    vlans: dict[int, str] = field(default_factory=dict)
    vrfs: list[ParsedVrf] = field(default_factory=list)
    tunnels: list[ParsedTunnel] = field(default_factory=list)
    #: L3。読めなければ空のまま。無いのか読めていないのかは上位で区別しない
    routes: list[ParsedRoute] = field(default_factory=list)
    ospf: ParsedOspf | None = None
    bgp: ParsedBgp | None = None
    #: OSPF / BGP 以外の経路プロトコル（EIGRP / RIP / IS-IS）
    protocols: list[ParsedRoutingProtocol] = field(default_factory=list)
    #: 経路の選び方・配り方を決める道具
    policies: list[ParsedPolicy] = field(default_factory=list)
    redundancy: list[ParsedRedundancy] = field(default_factory=list)
    #: その機器で使っている機能（ACL / NAT / DHCP / STP など）
    features: list[ParsedFeature] = field(default_factory=list)
    #: 通信の制限（中身と、当てている場所まで）
    acls: list[ParsedAcl] = field(default_factory=list)
    #: 名前で書かれたアドレスの定義（FortiGate の firewall address など）。
    #: 「指している名前が無い」を言うために、名前だけ控える
    address_objects: list[str] = field(default_factory=list)
    #: アドレス変換。読めなければ None（無いのか読めていないのかは区別しない）
    nat: ParsedNat | None = None
    dhcp: ParsedDhcp | None = None
    qos: ParsedQos | None = None
    crypto: ParsedCrypto | None = None
    #: 無線（SSID と AP）。読めなければ None
    wireless: ParsedWireless | None = None
    #: マルチキャストの配り方（PIM / RP / IGMP スヌーピング）。読めなければ None
    multicast: object | None = None
    #: 負荷分散（受け口・振り分け先・健全性監視）。読めなければ None
    load_balance: object | None = None
    #: 2台で1台に見せる仕組み（HA / failover / VSX / MLAG）。読めなければ None
    ha_pair: object | None = None

    def add_tunnel(self, tunnel: ParsedTunnel) -> ParsedTunnel:
        """同じトンネルが複数箇所に出てきたらまとめる。"""
        for existing in self.tunnels:
            if normalize(existing.name) == normalize(tunnel.name):
                for name in ("kind", "source", "destination", "vrf", "description"):
                    if getattr(existing, name) is None:
                        setattr(existing, name, getattr(tunnel, name))
                return existing
        self.tunnels.append(tunnel)
        return tunnel

    def add_interface(self, interface: ParsedInterface) -> ParsedInterface:
        """同じポートが複数箇所に出てきたらまとめる（config と show 出力の両方に出るため）。"""
        for existing in self.interfaces:
            if existing.key == interface.key:
                for name in (
                    "description",
                    "ip_address",
                    "vlan",
                    "mode",
                    "native_vlan",
                    "voice_vlan",
                    "vrf",
                    "lag_group",
                ):
                    if getattr(existing, name) is None:
                        setattr(existing, name, getattr(interface, name))
                if interface.trunk_vlans and not existing.trunk_vlans:
                    existing.trunk_vlans = interface.trunk_vlans
                existing.trunk_all = existing.trunk_all or interface.trunk_all
                return existing
        interface.name = expand(interface.name)
        self.interfaces.append(interface)
        return interface

    def add_neighbor(self, neighbor: ParsedNeighbor) -> None:
        neighbor.local_interface = expand(neighbor.local_interface)
        if neighbor.remote_interface:
            neighbor.remote_interface = expand(neighbor.remote_interface)

        key = (normalize(neighbor.local_interface), neighbor.remote_key)
        for existing in self.neighbors:
            if (normalize(existing.local_interface), existing.remote_key) == key:
                # CDP と LLDP の両方に出た場合、情報が多い方を残す
                if not existing.remote_interface and neighbor.remote_interface:
                    existing.remote_interface = neighbor.remote_interface
                if not existing.remote_platform and neighbor.remote_platform:
                    existing.remote_platform = neighbor.remote_platform
                return
        self.neighbors.append(neighbor)

    def add_vrf(self, vrf: ParsedVrf) -> ParsedVrf:
        """同じ VRF が定義箇所と参照箇所の両方に出てくるのでまとめる。"""
        for existing in self.vrfs:
            if existing.name == vrf.name:
                existing.rd = existing.rd or vrf.rd
                existing.description = existing.description or vrf.description
                return existing
        self.vrfs.append(vrf)
        return vrf


@dataclass
class ParsedRoute:
    """静的経路1本。

    「繋がらない」の原因はここに出やすい。片側だけ書いてある戻り経路、
    宛先の機器が台帳にいないネクストホップ、といった粗を見つける材料にする。
    """

    destination: str  # "0.0.0.0/0" の形にそろえる
    next_hop: str | None = None
    interface: str | None = None
    distance: int | None = None  # アドミニストレイティブ・ディスタンス
    vrf: str | None = None

    @property
    def is_default(self) -> bool:
        return self.destination in {"0.0.0.0/0", "::/0"}


@dataclass
class ParsedOspfArea:
    area: str
    networks: list[str] = field(default_factory=list)


@dataclass
class ParsedOspf:
    """OSPF の設定。どのエリアに何を入れているかまで持つ。

    機器を足すときに「この拠点は OSPF で、エリア0 に入れている」と分かれば、
    足す側の書き方もそれに合わせられる。
    """

    process_id: str | None = None
    router_id: str | None = None
    areas: list[ParsedOspfArea] = field(default_factory=list)
    passive_interfaces: list[str] = field(default_factory=list)
    passive_default: bool = False
    redistributes: list[str] = field(default_factory=list)

    def area_of(self, network: str) -> str | None:
        for area in self.areas:
            if network in area.networks:
                return area.area
        return None


@dataclass
class ParsedBgpNeighbor:
    """BGP の相手1台分。

    相手ごとに、出入りの経路へ何を掛けているかまで持つ。BGP は相手単位で
    設定が違うのが普通で、「1台だけ route-map が抜けている」が事故になる。
    合言葉（password）は入っているかどうかだけを持ち、値は持たない。
    """

    address: str
    #: 相手そのものではなく、まとめて掛けるための雛形の行（IOS の peer-group、
    #: Junos の group）。数に入れると相手が1台多く見える
    is_peer_group: bool = False
    remote_as: str | None = None
    description: str | None = None
    #: 受ける側・出す側に掛けている道具。in と out は別物なので分けて持つ
    route_map_in: str | None = None
    route_map_out: str | None = None
    prefix_list_in: str | None = None
    prefix_list_out: str | None = None
    filter_list_in: str | None = None
    filter_list_out: str | None = None
    #: どのアドレスから喋るか。ループバックを指すのが定石
    update_source: str | None = None
    next_hop_self: bool = False
    #: 直結していない相手と張るときの許容ホップ数
    ebgp_multihop: int | None = None
    #: 受け取る経路数の上限。無いと、相手の事故がこちらのメモリを食い潰す
    maximum_prefix: int | None = None
    #: 合言葉を掛けているか（値は持たない）
    password: bool = False
    shutdown: bool = False
    peer_group: str | None = None
    #: 相手を有効にしたアドレスファミリ（ipv4 unicast / vpnv4 など）
    address_families: list[str] = field(default_factory=list)
    #: activate を書いていないのに address-family の中にだけ出てくる相手もいる
    activated: bool = False

    def is_ebgp(self, local_as: str | None) -> bool:
        """外の AS が相手かどうか。掛けるべき安全策が変わる。"""
        if not self.remote_as or not local_as:
            return False
        return self.remote_as.strip() != local_as.strip()


@dataclass
class ParsedBgp:
    local_as: str | None = None
    router_id: str | None = None
    neighbors: list[ParsedBgpNeighbor] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    #: 他のプロトコルから取り込んでいるもの
    redistributes: list[str] = field(default_factory=list)
    #: 経路をまとめて出す指定（aggregate-address）
    aggregates: list[str] = field(default_factory=list)
    #: 落ちた相手を待つ時間（keepalive, holdtime）
    timers: str | None = None
    #: 同じ宛先へ複数の経路を使う設定
    maximum_paths: int | None = None
    log_neighbor_changes: bool = False
    #: 書いていないと、同じ経路長でも AS の並びを見ずに先着順で決まる
    always_compare_med: bool = False


@dataclass
class ParsedRoutingProtocol:
    """OSPF / BGP 以外の経路プロトコル1つ。

    現場では EIGRP や RIP がまだ動いている。読めないと、その機器の経路が
    どこから来ているのか説明できず、更改のときに再現できない。
    """

    name: str  # eigrp / rip / isis
    identifier: str | None = None  # AS 番号・プロセス番号など
    router_id: str | None = None
    networks: list[str] = field(default_factory=list)
    passive_interfaces: list[str] = field(default_factory=list)
    redistributes: list[str] = field(default_factory=list)
    #: RIP の版。v1 と v2 が混ざると経路が消えるので、書いてあるかを持つ
    version: str | None = None
    #: redistribute の行そのもの。絞り込み（route-map）が付いているかは
    #: 元の行を見ないと分からない
    redistribute_lines: list[str] = field(default_factory=list)


@dataclass
class ParsedPolicyEntry:
    """route-map / prefix-list の1行分。

    順番（seq）と permit / deny が要る。route-map も prefix-list も上から
    順に見て、最初に当たったところで止まる。並びが違えば結果が変わるので、
    中身だけ持っても意味が無い。
    """

    seq: str | None = None
    action: str = "permit"  # permit / deny
    #: 何に当てるか（match ip address prefix-list FOO など）
    matches: list[str] = field(default_factory=list)
    #: 当たったら何をするか（set local-preference 200 など）
    sets: list[str] = field(default_factory=list)
    raw: str = ""


@dataclass
class ParsedPolicy:
    """経路の選び方・配り方を決める道具（route-map / prefix-list / ACL 参照）。

    「広告しているはずなのに相手に届かない」の原因はたいていここにある。
    名前と、どこで使われているかに加えて、中身の並びも持つ。
    並びを持たないと、「掛かってはいるが、その手前で落ちている」が見えない。
    """

    kind: str  # route-map / prefix-list / policy-based-routing
    name: str
    detail: str = ""
    used_by: list[str] = field(default_factory=list)
    entries: list[ParsedPolicyEntry] = field(default_factory=list)

    @property
    def has_permit(self) -> bool:
        """通す行があるか。deny だけの道具を掛けると、全部落ちる。"""
        return any(entry.action == "permit" for entry in self.entries)


@dataclass
class ParsedAclEntry:
    """ACL の1行。

    ACL は上から順に見て、最初に当たった行で決まる。だから「何行あるか」より
    「どの順で何を許しているか」のほうが大事になる。
    """

    seq: str | None = None
    action: str = "permit"  # permit / deny
    protocol: str | None = None
    source: str | None = None  # "any" / "10.0.0.0/24" / "host 10.0.0.1"
    source_port: str | None = None
    destination: str | None = None
    destination_port: str | None = None
    log: bool = False
    remark: str | None = None
    #: 名前で書かれたアドレスの中身。分かるときだけ入れる。
    #: 表には名前を出したいが、覆っているかどうかは中身で見ないと言えない
    source_value: str | None = None
    destination_value: str | None = None
    raw: str = ""

    @property
    def is_any_any(self) -> bool:
        """送信元も宛先も限定していない行。ここより下は読まれない。"""
        return (self.source or "any") == "any" and (self.destination or "any") == "any"


@dataclass
class ParsedAcl:
    """通信の制限1つ分。

    書いてあるだけでは何も起きない。どこに、どちら向きで当てているかまで
    見て、はじめて「効いている」と言える。
    """

    name: str
    kind: str = "extended"  # standard / extended / mac / named
    entries: list[ParsedAclEntry] = field(default_factory=list)
    #: 当てている場所（"interface GigabitEthernet0/0 in" の形）
    applied_to: list[str] = field(default_factory=list)
    #: 経路の道具や NAT から参照されている場合の参照元
    referenced_by: list[str] = field(default_factory=list)
    #: 戻りの通信を動的に通す仕組みと組み合わせて使っている並びか
    #: （ヤマハの dynamic フィルタなど）。「実質すべて通している」の判断が変わる
    stateful: bool = False


@dataclass
class ParsedNatPool:
    name: str
    start: str | None = None
    end: str | None = None
    netmask: str | None = None


@dataclass
class ParsedNatRule:
    """アドレス変換1行分。

    NAPT（overload）かどうかで、外から入れるかどうかが変わる。ここを
    取り違えると「出られるのに入れない」の説明ができない。
    """

    kind: str  # static / dynamic / static-port
    direction: str = "inside"  # inside / outside
    #: 変換する側（ACL 名、route-map 名、または元のアドレス）
    matched: str | None = None
    #: 変換した先（pool 名、interface、または変換後のアドレス）
    translated: str | None = None
    #: 1つのアドレスにポートで束ねる（NAPT）
    overload: bool = False
    #: `matched` が名前（ACL / アドレス object）か、アドレスそのものか。
    #: 名前なら「その名前が Config に無い」と言えるが、アドレスには言えない
    matched_is_name: bool = True
    protocol: str | None = None  # static-port のとき tcp / udp
    raw: str = ""


@dataclass
class ParsedNat:
    pools: list[ParsedNatPool] = field(default_factory=list)
    rules: list[ParsedNatRule] = field(default_factory=list)
    #: どのポートが内側・外側か。片方しか無いと変換は動かない
    inside_interfaces: list[str] = field(default_factory=list)
    outside_interfaces: list[str] = field(default_factory=list)


@dataclass
class ParsedDhcpPool:
    name: str
    network: str | None = None
    #: 配る範囲をそのまま書く機種（FortiGate / Junos）はここに入る。
    #: Cisco のように「network から excluded を引く」形とは別に持つ
    range_start: str | None = None
    range_end: str | None = None
    default_routers: list[str] = field(default_factory=list)
    dns_servers: list[str] = field(default_factory=list)
    domain: str | None = None
    lease: str | None = None


@dataclass
class ParsedDhcp:
    pools: list[ParsedDhcpPool] = field(default_factory=list)
    #: 配らないアドレス（開始, 終了）
    excluded: list[tuple[str, str]] = field(default_factory=list)
    #: 他所のサーバーへ回す指定（ポート名, 転送先）
    relays: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class ParsedQosClass:
    name: str
    match_type: str = "match-all"  # match-all / match-any
    matches: list[str] = field(default_factory=list)


@dataclass
class ParsedQosAction:
    class_name: str
    actions: list[str] = field(default_factory=list)


@dataclass
class ParsedQosPolicy:
    name: str
    classes: list[ParsedQosAction] = field(default_factory=list)


@dataclass
class ParsedQos:
    classes: list[ParsedQosClass] = field(default_factory=list)
    policies: list[ParsedQosPolicy] = field(default_factory=list)
    #: 当てている場所（"interface GigabitEthernet0/0 output" の形）
    applied: list[tuple[str, str, str]] = field(default_factory=list)
    #: 当てるだけでは効かず、ポート側で別に有効にする必要がある書き方か
    #: （NEC IX の `service-policy enable`）。この形の機種でだけ True
    needs_enable: bool = False
    #: 有効にしてあるポート。`needs_enable` が True のときだけ意味を持つ
    enabled_on: list[str] = field(default_factory=list)


@dataclass
class ParsedIkePolicy:
    """鍵を交換するときの決めごと（ISAKMP / IKEv2 の proposal）。

    両側で揃っていないと、そもそもトンネルが上がらない。古い方式のまま
    残っていることも多いので、強さが分かる形で持つ。
    """

    number: str | None = None
    version: int = 1
    encryption: str | None = None
    hash: str | None = None
    group: str | None = None
    authentication: str | None = None
    lifetime: int | None = None


@dataclass
class ParsedTransformSet:
    name: str
    transforms: list[str] = field(default_factory=list)
    mode: str | None = None  # tunnel / transport


@dataclass
class ParsedCryptoMapEntry:
    name: str
    seq: str | None = None
    kind: str = "ipsec-isakmp"
    peers: list[str] = field(default_factory=list)
    transform_sets: list[str] = field(default_factory=list)
    #: 何を暗号化するか（ACL 名、または範囲そのもの）。
    #: ここが両側で裏返しになっていないと通らない
    match_address: str | None = None
    #: `match_address` が ACL の名前か。範囲を直に書く機種では False
    match_is_acl: bool = True
    pfs: str | None = None
    profile: str | None = None


@dataclass
class ParsedCrypto:
    ike_policies: list[ParsedIkePolicy] = field(default_factory=list)
    transform_sets: list[ParsedTransformSet] = field(default_factory=list)
    maps: list[ParsedCryptoMapEntry] = field(default_factory=list)
    #: crypto map を当てているポート
    applied: list[tuple[str, str]] = field(default_factory=list)
    #: 合言葉を入れてある相手（値は持たない）
    keyed_peers: list[str] = field(default_factory=list)


@dataclass
class ParsedWlan:
    """電波で流している網1つ分（SSID）。

    有線と違い、届く範囲を線で仕切れない。だから「どの守り方で、どの
    VLAN へ落としているか」がそのまま入口の強さになる。合言葉そのものは
    持たない（資料に載せるものではない）。置いてあるかどうかだけを持つ。
    """

    #: 設定上の名前（プロファイル名）。SSID と別に付けられる機種がある
    name: str
    #: 電波に乗って見える名前
    ssid: str | None = None
    #: 有効か。作ってあるが止めてある SSID は珍しくない
    enabled: bool = True
    #: 名前を伏せているか（隠しても守りにはならないが、意図として持つ）
    broadcast: bool = True
    #: 守り方。open / wep / wpa / wpa2 / wpa3 を並べる（併用があるため）
    security: list[str] = field(default_factory=list)
    #: 本人確認のしかた。psk / dot1x / sae / owe / mac
    akm: list[str] = field(default_factory=list)
    #: 暗号。tkip / aes(ccmp) / gcmp
    ciphers: list[str] = field(default_factory=list)
    #: 合言葉が Config に書いてあるか。値は持たない
    has_psk: bool = False
    #: 合言葉が伏せ字になっていない（そのまま読める）
    psk_in_clear: bool = False
    #: 落とす先の VLAN
    vlan: int | None = None
    #: 802.1X の問い合わせ先（authentication list / RADIUS の名前）
    auth_list: str | None = None
    #: 同じ SSID につなげる台数の上限
    client_limit: int | None = None
    #: 管理フレームの守り（PMF）。disabled / optional / required
    pmf: str | None = None
    #: 使う帯（2.4 / 5 / 6）
    bands: list[str] = field(default_factory=list)
    #: つないだ端末どうしを通さない（ゲスト用途で要る）
    client_isolation: bool = False
    #: 守り方を読めたか。一覧の表（AireOS の `show wlan summary` など）には
    #: 守り方が出てこない。読めていないものを「守っていない」と読むと、
    #: 掛けてあるのに「誰でも入れます」と言うことになる
    security_read: bool = True
    raw: str = ""


@dataclass
class ParsedWirelessAp:
    """アクセスポイント側の決めごと。"""

    name: str
    #: 電波の国。ここが違うと使えない周波数を使うことになる
    country: str | None = None
    #: 当てているプロファイル・タグ
    profile: str | None = None
    #: 管理の入口（telnet / http）が開いているか
    insecure_access: list[str] = field(default_factory=list)
    #: 一覧（show ap summary / wtp）から分かるもの。台帳に載せる材料
    model: str | None = None
    mac: str | None = None
    ip_address: str | None = None
    serial: str | None = None
    location: str | None = None


@dataclass
class ParsedWireless:
    """無線の設定。SSID と AP、それに国の指定。"""

    wlans: list[ParsedWlan] = field(default_factory=list)
    aps: list[ParsedWirelessAp] = field(default_factory=list)
    country: str | None = None
    #: 不正な AP を見張る仕組みが入っているか
    rogue_detection: bool = False


@dataclass
class ParsedRedundancy:
    """既定ゲートウェイの冗長（HSRP / VRRP）。

    片方の機器にしか入っていない、優先度が両方同じ、といった粗が
    そのまま「切り替わらない」障害になるので、機器ごとに持って突き合わせる。
    """

    interface: str
    group: str | None = None
    virtual_ip: str | None = None
    priority: int | None = None
    protocol: str = "hsrp"  # hsrp / vrrp
    #: 優先度が高い側が、復旧したときに役を取り返すか（HSRP は既定で取り返さない）
    preempt: bool = False
