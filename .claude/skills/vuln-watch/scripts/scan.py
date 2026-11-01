#!/usr/bin/env python3
"""脆弱性の調査。棚卸し（inventory.py）を OSV と CISA の悪用済み一覧（KEV）と JVN のニュースに突き合わせ、報告を書く。

気づかずに更新が遅れるのを防ぐ。毎日 1 回、機械で全部を見て、新しく出たものだけを目立たせる。
- OSV（osv.dev）: npm・PyPI・Go・crates.io・GitHub Actions の版ごとの脆弱性。GitHub Advisory の中身も入っている
- CISA KEV: 実際に悪用されている脆弱性の一覧。自分の依存に当たれば「悪用済み」として最上位に出す
- JVN: 日本語の注意喚起（RSS）。依存の名前と references/watch.md の見張る語に当たるものをニュースとして出す
GitHub Advisory の一覧 API は、このセッションの GitHub の経路では使えない（リポジトリ単位の経路だけ）ので OSV で代える。

使い方:
  scan.py --roots <ルート>... [--days 7] [--out 報告.md] [--json 結果.json] [--mark-seen] [--offline] [--strict]
  --mark-seen  今回の結果を「見た」として ~/.claude/state/vuln-watch/seen.json に残す。次からは新しいものだけ「新規」が付く
  --offline    ネットワークを使わず、~/.cache/vuln-watch の写しだけで動く（テストと、止まったときの読み直し）
  --strict     重大か悪用済みが 1 つでもあれば終了コード 1
"""

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inventory import inventory  # noqa: E402

HERE = Path(__file__).resolve().parent
OSV_ECOS = {"npm", "PyPI", "Go", "crates.io", "GitHub Actions"}
OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/"
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
JVN_URL = "https://jvn.jp/rss/jvn.rdf"
CACHE = Path(os.environ.get("VULN_WATCH_CACHE") or Path.home() / ".cache" / "vuln-watch")
STATE = Path(os.environ.get("VULN_WATCH_STATE") or Path.home() / ".claude" / "state" / "vuln-watch")
LABEL = {"CRITICAL": "重大", "HIGH": "高", "MODERATE": "中", "MEDIUM": "中", "LOW": "低"}
ORDER = {"重大": 0, "高": 1, "中": 2, "低": 3, "不明": 4}
OFFLINE = False


# ---------- 取ってくる（写しを残す） ----------

def fetch(url: str, data: bytes = None, cache_name: str = None, max_age: int = 0) -> bytes:
    """URL を取る。cache_name があれば ~/.cache/vuln-watch に写しを置き、max_age 秒以内ならそれを使う。"""
    p = CACHE / cache_name if cache_name else None
    if p and p.exists() and (OFFLINE or time.time() - p.stat().st_mtime < max_age):
        return p.read_bytes()
    if OFFLINE:
        raise RuntimeError(f"offline で写しが無い: {cache_name or url}")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "ciel-vuln-watch", **({"Content-Type": "application/json"} if data else {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    if p:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    return body


def osv_query(rows):
    """棚卸しの行ごとに OSV の脆弱性 ID の一覧を返す。GitHub Actions は版の形が揃わないので名前だけで聞く。"""
    queries, keys = [], []
    for r in rows:
        if r["ecosystem"] not in OSV_ECOS or (r["ecosystem"] != "GitHub Actions" and not r["version"]):
            continue
        q = {"package": {"name": r["name"], "ecosystem": r["ecosystem"]}}
        if r["ecosystem"] != "GitHub Actions":
            q["version"] = r["version"]
        key = (r["ecosystem"], r["name"], r["version"] if r["ecosystem"] != "GitHub Actions" else None)
        if key in keys:
            continue
        keys.append(key)
        queries.append(q)
    found = {}
    for i in range(0, len(queries), 500):
        chunk = queries[i:i + 500]
        name = "batch-" + hashlib.sha1(json.dumps(chunk, sort_keys=True).encode()).hexdigest()[:16] + ".json"
        body = fetch(OSV_BATCH, json.dumps({"queries": chunk}).encode(), name, max_age=20 * 3600)
        for key, res in zip(keys[i:i + 500], json.loads(body).get("results", [])):
            found[key] = [v["id"] for v in res.get("vulns", [])]
    return found


def osv_vuln(vid: str) -> dict:
    return json.loads(fetch(OSV_VULN + vid, None, f"vulns/{vid}.json", max_age=7 * 86400))


def kev() -> dict:
    """CVE → KEV の行。"""
    d = json.loads(fetch(KEV_URL, None, "kev.json", max_age=20 * 3600))
    return {v["cveID"]: v for v in d.get("vulnerabilities", [])}


def jvn_items():
    s = fetch(JVN_URL, None, "jvn.rdf", max_age=20 * 3600).decode("utf-8", "replace")
    out = []
    for item in re.findall(r"<item[^>]*>(.*?)</item>", s, re.S):
        g = lambda tag: (re.search(rf"<{tag}>(.*?)</{tag}>", item, re.S) or [None, ""])[1].strip()
        out.append({"title": re.sub(r"\s+", " ", g("title")), "link": g("link"), "date": g("dc:date")[:10], "description": re.sub(r"\s+", " ", g("description"))[:200]})
    return out


# ---------- 判定 ----------

def cvss3_score(vector: str):
    """CVSS 3.x の基本値。ベクトル文字列から標準の式で出す。読めなければ None。"""
    m = dict(re.findall(r"(\w+):(\w)", vector.split("/", 1)[1] if vector.startswith("CVSS:3") else vector))
    try:
        av = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}[m["AV"]]
        ac = {"L": 0.77, "H": 0.44}[m["AC"]]
        ui = {"N": 0.85, "R": 0.62}[m["UI"]]
        changed = m["S"] == "C"
        pr = {"N": 0.85, "L": 0.68 if changed else 0.62, "H": 0.5 if changed else 0.27}[m["PR"]]
        cia = {"H": 0.56, "L": 0.22, "N": 0.0}
        iss = 1 - (1 - cia[m["C"]]) * (1 - cia[m["I"]]) * (1 - cia[m["A"]])
    except KeyError:
        return None
    impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15 if changed else 6.42 * iss
    expl = 8.22 * av * ac * pr * ui
    if impact <= 0:
        return 0.0
    raw = min(1.08 * (impact + expl), 10) if changed else min(impact + expl, 10)
    return math.ceil(raw * 10 - 1e-9) / 10


def severity(v: dict):
    """(ラベル, 点)。GitHub の判定があればそれ、無ければ CVSS から。"""
    score = None
    for s in v.get("severity") or []:
        if s.get("type", "").startswith("CVSS_V3") and s.get("score"):
            score = cvss3_score(s["score"])
        elif s.get("type") == "CVSS_V4" and score is None:
            m = re.search(r"/AV:", s.get("score", ""))
            score = None if m is None else score
    label = LABEL.get(str((v.get("database_specific") or {}).get("severity", "")).upper())
    if not label and score is not None:
        label = "重大" if score >= 9 else "高" if score >= 7 else "中" if score >= 4 else "低"
    return label or "不明", score


def vkey(v: str):
    parts = re.split(r"[.+-]", (v or "").lstrip("v"))
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts)


def fixed_version(v: dict, eco: str, name: str, current: str):
    """自分の版より上で、いちばん小さい fixed。無ければ None（直す版がまだ無い）。"""
    fixes = []
    for a in v.get("affected") or []:
        pkg = a.get("package") or {}
        if pkg.get("ecosystem") != eco or pkg.get("name", "").lower() != name.lower():
            continue
        for r in a.get("ranges") or []:
            for e in r.get("events") or []:
                if e.get("fixed"):
                    fixes.append(e["fixed"])
    fixes = [f for f in fixes if not current or vkey(f) > vkey(current)]
    return min(fixes, key=vkey) if fixes else None


SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")
FLOATING = re.compile(r"^v?\d+(\.\d+)?$")


def affected_by(v: dict, eco: str, name: str, version: str) -> bool:
    """その版が、脆弱性の affected の範囲に入るか。introduced <= 版 < fixed の範囲が 1 つでもあれば当たり。"""
    for a in v.get("affected") or []:
        pkg = a.get("package") or {}
        if pkg.get("ecosystem") != eco or pkg.get("name", "").lower() != name.lower():
            continue
        if version in (a.get("versions") or []):
            return True
        for r in a.get("ranges") or []:
            if r.get("type") == "GIT":
                continue
            lo, hit = None, False
            for e in r.get("events") or []:
                if "introduced" in e:
                    lo = e["introduced"]
                    hit = lo == "0" or vkey(version) >= vkey(lo)
                elif "fixed" in e and hit and vkey(version) >= vkey(e["fixed"]):
                    hit = False
                elif "last_affected" in e and hit and vkey(version) > vkey(e["last_affected"]):
                    hit = False
            if hit:
                return True
    return False


def findings(rows, found, kev_map):
    """棚卸しの行と OSV の結果から、1 件 1 行の所見。同じ CVE の別名（GHSA と PYSEC）は 1 つにまとめ、
    同じリポジトリの同じ部品・同じ版は 1 行にする（ファイルは並べる）。
    GitHub Actions は名前で聞くので版を自分で当てる。v4 のような浮いたタグと SHA は版が分からないので所見にせず、floating に返す。"""
    out, floating, merged = [], [], {}
    for r in rows:
        key = (r["ecosystem"], r["name"], r["version"] if r["ecosystem"] != "GitHub Actions" else None)
        ids = found.get(key) or []
        if r["ecosystem"] == "GitHub Actions" and ids:
            ref = r["version"] or ""
            if not SEMVER.match(ref):
                if FLOATING.match(ref):
                    floating.append({"repo": r["repo"], "name": r["name"], "ref": ref, "file": r["file"], "advisories": len(ids)})
                continue
        seen_alias = set()
        for vid in ids:
            v = osv_vuln(vid)
            if r["ecosystem"] == "GitHub Actions" and not affected_by(v, "GitHub Actions", r["name"], SEMVER.match(r["version"]).group(0).lstrip("v")):
                continue
            aliases = set(v.get("aliases") or []) | {vid}
            if aliases & seen_alias:
                continue
            seen_alias |= aliases
            label, score = severity(v)
            cves = sorted(a for a in aliases if a.startswith("CVE-"))
            in_kev = [c for c in cves if c in kev_map]
            gh = next((a for a in aliases if a.startswith("GHSA-")), vid)
            mk = (r["repo"], r["ecosystem"], r["name"], r["version"], gh)
            if mk in merged:
                if r["file"] not in merged[mk]["files"]:
                    merged[mk]["files"].append(r["file"])
                merged[mk]["dev"] = merged[mk]["dev"] and r["dev"]
                continue
            merged[mk] = {
                "repo": r["repo"], "files": [r["file"]], "ecosystem": r["ecosystem"], "name": r["name"], "version": r["version"],
                "exact": r["exact"], "dev": r["dev"], "id": gh, "cves": cves, "severity": label, "score": score,
                "kev": bool(in_kev), "fixed": fixed_version(v, r["ecosystem"], r["name"], r["version"]),
                "summary": (v.get("summary") or v.get("details") or "")[:120].replace("\n", " "),
                "published": (v.get("published") or "")[:10], "url": f"https://osv.dev/vulnerability/{gh}",
            }
    out = list(merged.values())
    out.sort(key=lambda f: (not f["kev"], ORDER.get(f["severity"], 9), f["repo"], f["name"]))
    seen_f = set()
    floating = [f for f in floating if not ((f["repo"], f["name"], f["ref"]) in seen_f or seen_f.add((f["repo"], f["name"], f["ref"])))]
    return out, floating


def news(rows, days: int, kev_map, watch_words):
    """最近のニュース。JVN の RSS と KEV の新しい行から、依存の名前と見張る語に当たるものを拾う。"""
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    names = {r["name"].lower() for r in rows} | {w.lower() for w in watch_words}
    short = {n.split("/")[-1] for n in names if len(n.split("/")[-1]) >= 4}
    hits = []
    try:
        for it in jvn_items():
            text = (it["title"] + " " + it["description"]).lower()
            if it["date"] >= since and any(w in text for w in short):
                hits.append({"source": "JVN", "date": it["date"], "title": it["title"], "url": it["link"]})
    except Exception as e:
        hits.append({"source": "JVN", "date": "", "title": f"（取れなかった: {e.__class__.__name__}）", "url": JVN_URL})
    for c, v in kev_map.items():
        text = (v.get("vendorProject", "") + " " + v.get("product", "") + " " + v.get("vulnerabilityName", "")).lower()
        if v.get("dateAdded", "") >= since and any(w in text for w in short):
            hits.append({"source": "KEV", "date": v["dateAdded"], "title": f"{c} {v.get('vendorProject')} {v.get('product')}: {v.get('vulnerabilityName')}", "url": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"})
    hits.sort(key=lambda h: h["date"], reverse=True)
    return hits


def load_watch(path: Path):
    words = []
    if path.exists():
        for l in path.read_text(encoding="utf-8").splitlines():
            if l.startswith("- "):
                words.append(l[2:].split("（")[0].split("(")[0].strip())
    return words


def seen_set() -> set:
    p = STATE / "seen.json"
    try:
        return set(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def fkey(f) -> str:
    return f"{f['repo']}|{f['ecosystem']}|{f['name']}|{f['id']}"


# ---------- 報告 ----------

def sev(f) -> str:
    return f["severity"] + ("" if f["score"] is None else f" {f['score']}")


def cur(f) -> str:
    """今の版。>= や ^ の下限しか分からないものは（下限）と添える。実際に入っている版はもっと新しいことがある。"""
    return (f["version"] or "不定") + ("" if f["exact"] or not f["version"] else "（下限）")


def report_md(rows, fs, nw, days: int, new_keys: set, floating=()) -> str:
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    repos = sorted({r["repo"] for r in rows})
    counts = {k: sum(1 for f in fs if f["severity"] == k) for k in ("重大", "高", "中", "低", "不明")}
    kevn = sum(1 for f in fs if f["kev"])
    L = [f"# 脆弱性の調査 {today}（日本時間）", "",
         f"リポジトリ {len(repos)}、依存 {len(rows)} 件（版が決まっていない {sum(1 for r in rows if not r['exact'])}）。",
         f"脆弱性 重大 {counts['重大']} / 高 {counts['高']} / 中 {counts['中']} / 低 {counts['低']} / 不明 {counts['不明']}、悪用済み {kevn}、新規 {len(new_keys)}、ニュース {len(nw)}（{days} 日以内）。", ""]
    L += ["## 今すぐ直すもの（悪用済み・重大・高）", ""]
    top = [f for f in fs if f["kev"] or f["severity"] in ("重大", "高")]
    if top:
        L += ["| 新規 | リポジトリ | 部品 | 今の版 | 直す版 | 重さ | 悪用 | 何 | ID |", "|---|---|---|---|---|---|---|---|---|"]
        for f in top:
            L.append(f"| {'★' if fkey(f) in new_keys else ''} | {f['repo']} | {f['ecosystem']} {f['name']}{'（開発用）' if f['dev'] else ''} | {cur(f)} | {f['fixed'] or 'まだ無い'} | {sev(f)} | {'悪用済み' if f['kev'] else ''} | {f['summary']} | [{f['id']}]({f['url']}) |")
    else:
        L.append("無し。")
    L += ["", "## 直すもの（中・低）", ""]
    rest = [f for f in fs if f not in top]
    if rest:
        L += ["| 新規 | リポジトリ | 部品 | 今の版 | 直す版 | 重さ | 何 | ID |", "|---|---|---|---|---|---|---|---|"]
        for f in rest:
            L.append(f"| {'★' if fkey(f) in new_keys else ''} | {f['repo']} | {f['ecosystem']} {f['name']}{'（開発用）' if f['dev'] else ''} | {cur(f)} | {f['fixed'] or 'まだ無い'} | {sev(f)} | {f['summary']} | [{f['id']}]({f['url']}) |")
    else:
        L.append("無し。")
    L += ["", f"## ニュース（{days} 日以内。依存の名前と見張る語に当たったもの）", ""]
    if nw:
        L += ["| 日付 | 出どころ | 何 |", "|---|---|---|"]
        for h in nw:
            L.append(f"| {h['date']} | {h['source']} | [{h['title']}]({h['url']}) |")
    else:
        L.append("無し。")
    L += ["", "## 浮いたタグの Actions（脆弱性のある部品を v4 のようなタグで使っている。版が確かめられないので、直した版のタグか SHA に固定する）", ""]
    if floating:
        L += ["| リポジトリ | 部品 | 今の指定 | 過去の脆弱性 |", "|---|---|---|---|"]
        for f in floating:
            L.append(f"| {f['repo']} | {f['name']} | {f['ref']} | {f['advisories']} 件 |")
    else:
        L.append("無し。")
    loose = [r for r in rows if not r["exact"] and r["ecosystem"] in ("npm", "PyPI", "Docker")]
    L += ["", "## 版が決まっていないもの（調べきれない。lock か固定を勧める）", ""]
    if loose:
        by = {}
        for r in loose:
            by.setdefault((r["repo"], r["ecosystem"], Path(r["file"]).name), []).append(r["name"])
        L += ["| リポジトリ | 種類 | ファイル | 件数 | 例 |", "|---|---|---|---|---|"]
        for (repo, eco, fn), ns in sorted(by.items()):
            L.append(f"| {repo} | {eco} | {fn} | {len(ns)} | {', '.join(ns[:4])} |")
    else:
        L.append("無し。")
    L += ["", "## 直し方", "",
          "- 「（下限）」の版は requirements や pyproject の `>=` の値で、実際に入っている版はもっと新しいことがある。lock か `pip freeze` で確かめてから直す",
          "- npm: そのディレクトリで `npm install <部品>@<直す版> --package-lock-only --ignore-scripts` を流し、テストを通してから PR にする",
          "- PyPI: requirements.txt の `==` を直す版にする（pyproject なら下限を上げる）。`scripts/bump.py <リポジトリ> <種類> <部品> <版>` が書き換える",
          "- GitHub Actions: `uses:` を直す版のタグか、40 桁のコミットの SHA に固定する",
          "- 直す版が「まだ無い」ものは、回避策があるかを ID のページで見て、無ければ使う所を外すか別の部品に替える",
          "- PR を作る前とマージの前はユーザーの確認が要る（solo-pr-flow「出す前の確認」）。悪用済みと重大は、その日のうちに ❔ を出す", ""]
    return "\n".join(L)


def main() -> int:
    global OFFLINE
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", nargs="+", required=True)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out")
    ap.add_argument("--json")
    ap.add_argument("--mark-seen", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--watch", default=str(HERE.parent / "references" / "watch.md"))
    a = ap.parse_args()
    OFFLINE = a.offline
    rows = inventory(a.roots)
    found = osv_query(rows)
    kev_map = kev()
    fs, floating = findings(rows, found, kev_map)
    nw = news(rows, a.days, kev_map, load_watch(Path(a.watch)))
    seen = seen_set()
    new_keys = {fkey(f) for f in fs} - seen
    md = report_md(rows, fs, nw, a.days, new_keys, floating)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(md, encoding="utf-8")
    if a.json:
        Path(a.json).write_text(json.dumps({"rows": rows, "findings": fs, "floating": floating, "news": nw, "new": sorted(new_keys)}, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.mark_seen:
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "seen.json").write_text(json.dumps(sorted(seen | {fkey(f) for f in fs}), ensure_ascii=False), encoding="utf-8")
    counts = {k: sum(1 for f in fs if f["severity"] == k) for k in ("重大", "高", "中", "低")}
    kevn = sum(1 for f in fs if f["kev"])
    print(f"脆弱性 重大 {counts['重大']} / 高 {counts['高']} / 中 {counts['中']} / 低 {counts['低']}、悪用済み {kevn}、新規 {len(new_keys)}、ニュース {len(nw)}（リポジトリ {len({r['repo'] for r in rows})}、依存 {len(rows)}）")
    return 1 if a.strict and (kevn or counts["重大"]) else 0


if __name__ == "__main__":
    sys.exit(main())
