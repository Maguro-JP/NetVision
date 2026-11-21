#!/usr/bin/env python3
"""バグの帳簿（docs/reports/bugs.md）を、読む・足す・状態を変える・数える。

ユーザーの決まり: 何よりもバグ修正を優先させる。特に致命的なバグ、クラッシュや操作不能、破損や漏洩等は即修正版を出す。
バグを会話の中だけで知っていても、セッションが閉じれば消え、次のサイクルは新しい機能から始めてしまう。
1 件 1 節で帳簿に書けば、開発のサイクルの最初に「直していないバグがあるか」を機械で答えられる。

使い方:
  bugs.py --list [--open] [--json]                 一覧（--open は直していない物だけ）
  bugs.py --add "<症状を 1 行で>" [--severity 致命的|重い|軽い] [--repro "<再現の手順>"] [--source "<知った所>"] [--detail "<詳しい症状>"]
                                                   1 件足す。重さを書かなければ語から決め、迷う物は重い側にする
  bugs.py --set B-003 [--state 未修正|修正中|確認待ち|修正済み|対象外] [--severity …] [--commit <コミット>] [--release <修正版>] [--repro …]
                                                   その節を書き換える。修正済みにすると、直した日が入る
  bugs.py --count [--strict]                       直していない致命的の数。--strict は 1 件でもあれば終了コード 1
  bugs.py --stats [--since YYYY-MM-DD]             日報・週報に写す 1 行（見つけた、直した、残り、致命的を直すまでの時間）
  どれも --root DIR でリポジトリの根を指せる（既定は git の根）
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))  # 日本時間に夏時間は無いので、固定の差で足りる
FILE = Path("docs") / "reports" / "bugs.md"
TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "bugs.md"
SEVERITIES = ["致命的", "重い", "軽い"]
STATES = ["未修正", "修正中", "確認待ち", "修正済み", "対象外"]
# 節に書く欄と、その順番
FIELDS = ["重さ", "状態", "見つけた日", "知った所", "症状", "再現の手順", "直したコミット", "修正版", "直した日"]

# 致命的: クラッシュ、起動しない、操作不能、データの破損や消失、情報の漏洩、悪用できる脆弱性
FATAL = re.compile(
    r"クラッシュ|異常終了|強制終了|落ち(?:る|た|て|ます|ました)|起動(?:しな|できな|しませ|でき(?:ませ|ず))|立ち上がらな|開(?:か|け)な"
    r"|操作不能|操作(?:が|を)?(?:でき|出来)な|固ま|フリーズ|ハング|応答(?:しな|な)|無限ループ|真っ白|真っ黒"
    r"|破損|壊れ|消失|消え(?:る|た|て|ます|ました)|失われ|上書きされ|保存(?:され|でき)な"
    r"|漏洩|漏えい|流出|漏れ(?:る|た|て)|脆弱性|任意(?:の)?コード|認証(?:を)?(?:回避|バイパス)|丸見え"
    r"|Traceback|panic|Segmentation fault|core dumped|SIGSEGV|Unhandled|Uncaught|FATAL|out of memory|data loss", re.I)
# テストや Actions が「落ちた」は、アプリが落ちたのとは違う。何が落ちたかを見てから重さを決めるので、語だけでは致命的にしない
NOT_CRASH = re.compile(r"(?:テスト|CI|Actions|チェック|検査|ジョブ|workflow|ビルド|lint)[^。、\n]{0,8}?落ち[^\s。、]*", re.I)
# 軽い: 見た目、文言、まれな場面
LIGHT = re.compile(r"見た目|文言|誤字|脱字|typo|表記|色(?:が|の)|余白|ずれ(?:る|て)|位置が|アイコン|フォント|改行|まれに|稀に|ごくたまに", re.I)


def now() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def root_of(arg) -> Path:
    if arg:
        return Path(arg).resolve()
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return Path.cwd()


def judge(text: str) -> str:
    """症状の文から重さを決める。致命的の語があれば致命的、見た目や文言だけなら軽い、どちらでもなければ重い（迷ったら重い側）。"""
    if FATAL.search(NOT_CRASH.sub("", text)):
        return "致命的"
    if LIGHT.search(text):
        return "軽い"
    return "重い"


def _parts(text: str) -> list:
    """帳簿の文を、節の頭（## ）で切る。HTML のコメントの中（ひな形の見本）にある見出しでは切らない。つなげると元の文に戻る。"""
    hidden = [m.span() for m in re.finditer(r"<!--.*?-->", text, flags=re.S)]
    cuts = [m.start() for m in re.finditer(r"^## ", text, flags=re.M) if not any(a <= m.start() < z for a, z in hidden)]
    edges = [0] + cuts + [len(text)]
    return [text[a:z] for a, z in zip(edges, edges[1:]) if z > a]


def parse(text: str) -> list:
    """帳簿の文を、節ごとの dict の列にする。"""
    out = []
    for part in _parts(text):
        m = re.match(r"## +(B-\d+)\s*(.*)", part)
        if not m:
            continue
        b = {"id": m.group(1), "title": m.group(2).strip()}
        for f in FIELDS:
            v = re.search(rf"^- {re.escape(f)}[:：][ \t]*(.*)$", part, re.M)
            b[f] = v.group(1).strip() if v else ""
        out.append(b)
    return out


def load(root: Path) -> list:
    try:
        return parse((root / FILE).read_text(encoding="utf-8"))
    except OSError:
        return []


def is_open(b: dict) -> bool:
    """直していないか。状態の欄が空や書き損じの節は、直していない側に数える（黙って抜けるより、数え過ぎる方が安全）。"""
    return not b["状態"].startswith(("修正済み", "対象外"))


def open_fatal(bugs: list) -> list:
    """直していない致命的。修正版が出るまでは、確認待ちもここに数える。"""
    return [b for b in bugs if is_open(b) and b["重さ"].startswith("致命的")]


def blocking_fatal(bugs: list) -> list:
    """ほかの開発より先に直す致命的。確認待ち（修正は済み、❔ の返事を待っている）は、待つ間にほかへ進めるので入れない。"""
    return [b for b in open_fatal(bugs) if not b["状態"].startswith("確認待ち")]


def _clean(s: str) -> str:
    """欄の値は 1 行にする。改行が入ると、次の欄として読まれる。"""
    return re.sub(r"\s+", " ", str(s or "")).strip()


def add(root: Path, title: str, severity: str = "", repro: str = "", source: str = "", detail: str = "") -> dict:
    p = root / FILE
    if p.exists():
        text = p.read_text(encoding="utf-8")
    else:
        text = TEMPLATE.read_text(encoding="utf-8") if TEMPLATE.is_file() else "# バグの帳簿\n"
    title = _clean(title)
    nums = [int(b["id"][2:]) for b in parse(text)]
    b = {"id": f"B-{(max(nums) + 1) if nums else 1:03d}", "title": title,
         "重さ": severity or judge(f"{title} {detail}"), "状態": "未修正", "見つけた日": now(), "知った所": _clean(source),
         "症状": _clean(detail) or title, "再現の手順": _clean(repro), "直したコミット": "", "修正版": "", "直した日": ""}
    lines = [f"## {b['id']} {title}"] + [f"- {f}: {b[f]}".rstrip() for f in FIELDS]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.rstrip("\n") + "\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return b


def set_fields(root: Path, bug_id: str, values: dict):
    """その節の欄を書き換える。無い欄は節の末尾に足す。節が無ければ None。"""
    p = root / FILE
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    parts = _parts(text)
    for i, part in enumerate(parts):
        if not re.match(rf"## +{re.escape(bug_id)}\b", part):
            continue
        if values.get("状態") == "修正済み" and not values.get("直した日"):
            values = {**values, "直した日": now()}
        for f, v in values.items():
            line = f"- {f}: {_clean(v)}".rstrip()
            if re.search(rf"^- {re.escape(f)}[:：].*$", part, re.M):
                part = re.sub(rf"^- {re.escape(f)}[:：].*$", lambda _m, s=line: s, part, count=1, flags=re.M)
            else:
                part = part.rstrip("\n") + "\n" + line + "\n" + ("\n" if i < len(parts) - 1 else "")
        parts[i] = part
        p.write_text("".join(parts), encoding="utf-8")
        return next(b for b in parse("".join(parts)) if b["id"] == bug_id)
    return None


def _when(s: str):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip()[:16].strip(), fmt)
        except ValueError:
            continue
    return None


def _span(minutes: int) -> str:
    d, rest = divmod(minutes, 1440)
    h, m = divmod(rest, 60)
    return (f"{d} 日 " if d else "") + (f"{h} 時間 " if h else "") + f"{m} 分"


def stats(bugs: list, since: str = "") -> str:
    """報告に写す 1 行。since（YYYY-MM-DD）より前に見つけた・直した物は、見つけた・直したに数えない。残りは今の数。"""
    start = _when(since) if since else None
    found = [b for b in bugs if not start or ((_when(b["見つけた日"]) or datetime.min) >= start)]
    fixed = [b for b in bugs if b["状態"].startswith("修正済み") and (not start or ((_when(b["直した日"]) or datetime.min) >= start))]
    rest = [b for b in bugs if is_open(b)]
    took = []
    for b in fixed:
        a, z = _when(b["見つけた日"]), _when(b["直した日"])
        if b["重さ"].startswith("致命的") and a and z and z >= a:
            took.append(int((z - a).total_seconds() // 60))
    took.sort()
    time = f"中央 {_span(took[len(took) // 2])}・最長 {_span(took[-1])}" if took else "該当なし"
    return f"バグ: 見つけた {len(found)}、直した {len(fixed)}、残り {len(rest)}（致命的 {len(open_fatal(bugs))}）、致命的を直すまで {time}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--add", metavar="症状")
    ap.add_argument("--set", metavar="B-NNN")
    ap.add_argument("--count", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--since", default="")
    ap.add_argument("--severity", choices=SEVERITIES)
    ap.add_argument("--state", choices=STATES)
    ap.add_argument("--repro")
    ap.add_argument("--source")
    ap.add_argument("--detail")
    ap.add_argument("--commit")
    ap.add_argument("--release")
    ap.add_argument("--root")
    a = ap.parse_args()
    root = root_of(a.root)

    if a.add:
        b = add(root, a.add, a.severity or "", a.repro or "", a.source or "", a.detail or "")
        guess = "" if a.severity else "（重さは語から決めた。違えば --set で直す）"
        print(f"{b['id']} を足しました（{b['重さ']}）{guess}: {FILE}")
        if b["重さ"] == "致命的":
            print("致命的です。今の作業を止めて区切り、修正版を出します（bug-first の 5）")
        return 0
    if a.set:
        values = {k: v for k, v in (("状態", a.state), ("重さ", a.severity), ("再現の手順", a.repro), ("知った所", a.source),
                                    ("症状", a.detail), ("直したコミット", a.commit), ("修正版", a.release)) if v}
        if not values:
            print("変える欄がありません（--state、--severity、--commit、--release、--repro、--source、--detail のどれかを付ける）", file=sys.stderr)
            return 2
        b = set_fields(root, a.set, values)
        if b is None:
            print(f"{a.set} の節が {FILE} にありません", file=sys.stderr)
            return 1
        print(f"{b['id']} を書き換えました: 重さ {b['重さ']}、状態 {b['状態']}")
        if b["重さ"].startswith("致命的") and b["状態"] == "修正済み" and not b["修正版"]:
            print("注意: 致命的なのに修正版の欄が空です。出した版を --release で書きます（版を持たないリポジトリは「版なし」）")
        return 0

    bugs = load(root)
    if a.count:
        fatal = open_fatal(bugs)
        names = "（" + "、".join(f"{b['id']} {b['title']}"[:40] for b in fatal[:3]) + "）" if fatal else ""
        print(f"直していない致命的: {len(fatal)} 件{names}")
        return 1 if (fatal and a.strict) else 0
    if a.stats:
        print(stats(bugs, a.since))
        return 0
    rows = [b for b in bugs if is_open(b)] if a.open else bugs
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not (root / FILE).is_file():
        print(f"帳簿（{FILE}）はまだありません。--add で最初の 1 件を足すと作られます")
        return 0
    order = {s: i for i, s in enumerate(SEVERITIES)}
    for b in sorted(rows, key=lambda b: (not is_open(b), order.get(b["重さ"], 1), b["id"])):
        print(f"{b['id']}  {b['重さ'] or '重さなし':<4} {b['状態'] or '状態なし':<5} {b['見つけた日'][:10]}  {b['title']}")
    print(f"{len(rows)} 件（直していない {sum(1 for b in rows if is_open(b))}、うち致命的 {len(open_fatal(rows))}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
