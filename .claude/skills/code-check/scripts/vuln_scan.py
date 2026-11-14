#!/usr/bin/env python3
"""不具合や脆弱性: 自分のコードの穴を型で見つける。依存の CVE は vuln-watch、鍵の有無は secret-leak-check。

型の一覧は references/patterns.md。元は Bandit（PyCQA、Apache-2.0）の B 番号と OWASP Top 10 の分類。
ruff があれば flake8-bandit の S の規則も使い（astral-sh/ruff、MIT）、無くてもここの正規表現だけで動く。
正規表現を必ず動かすのは、ruff の無いリポジトリ（配った先）でも同じ穴を見つけるため。

実績のあるツールを先に使う（bandit、semgrep、ruff の S、pip-audit、npm audit）。ツールの無い言語だけ、ここの正規表現で補う。
--regex を付けると正規表現も全部動かす（ツールの取りこぼしを見たいとき）。

使い方: vuln_scan.py [root] [--diff <base>] [--json] [--no-ruff] [--regex]
終了コード: 重大があれば 1
抑止: 行末に `code-check: ignore`、ファイルの先頭に `code-check: ignore-file`
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tools  # noqa: E402
from common import (Finding, code_files, file_ignored, is_test, line_ignored, near, parse_args, read_text, relpath, report,  # noqa: E402
                    run, scope_to_diff, target_files)

PY = {".py"}
JS = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}
SH = {".sh", ".bash"}
ALL = PY | JS | SH

# (ID, 拡張子, 正規表現, 重さ, 何, 直し方)。重さが callable のときは (lines, i, m) を受けて重さを返す
NONLIT = r"\(\s*(?!\s*[\"'`]|\s*\))"  # 引数が文字列リテラルでも空でもない
SECRET_NEAR = r"\b(password|passwd|pwd|token|secret|otp|nonce|session|api_key|apikey|salt|key)\b"  # code-check: ignore 検出の正規表現であり、鍵ではない
DYN_PY = r"(\bf[\"']|\.format\(|%\s*\(|%\s*[a-zA-Z_]|\+\s*[a-zA-Z_]|[a-zA-Z_\]\)]\s*\+)"
DYN_JS = r"(`[^`]*\$\{|\+\s*[a-zA-Z_]|[a-zA-Z_\]\)]\s*\+)"
# 英語の update や delete を含む f-string を SQL と取り違えないよう、SQL の形（FROM、INTO、SET、WHERE x =）まで見る
SQL = r"(\bSELECT\s+[\w*,.\s]+\s+FROM\s|\bINSERT\s+INTO\s|\bUPDATE\s+\w+\s+SET\s|\bDELETE\s+FROM\s|\bWHERE\s+[\w.]+\s*(=|LIKE|IN)\b)"


def sev_weak_hash(lines, i, m):
    return "重大" if near(lines, i, SECRET_NEAR) else "注意"


def sev_random(lines, i, m):
    return "重大" if near(lines, i, SECRET_NEAR) else None


def sev_dom(lines, i, m):
    rhs = (m.group("rhs") or "").strip().rstrip(";").strip()
    if rhs in ('""', "''", "``"):
        return None  # 空にするだけ
    return "注意" if re.match(r"^[\"'`][^$`]*[\"'`]$", rhs) else "重大"


def sev_shell_py(lines, i, m):
    return "重大" if re.search(DYN_PY, lines[i]) else "注意"


def sev_shell_js(lines, i, m):
    return "重大" if re.search(DYN_JS, lines[i]) else "注意"


def sev_assert(lines, i, m):
    return "注意" if re.search(r"\b(request|input|argv|args|param|user|data|body|query|form|payload)\b", lines[i]) else None


PATTERNS = [
    ("V01", PY, r"\b(eval|exec)" + NONLIT, "重大", "eval/exec に文字列でない物を渡している（任意のコードの実行。B307/B102）", "ast.literal_eval か、許す値の表に置き換える"),
    ("V01", JS, r"\b(eval" + NONLIT + r"|new\s+Function\s*\()", "重大", "eval/new Function（任意のコードの実行。OWASP A03）", "JSON.parse か、許す値の表に置き換える"),
    ("V02", PY, r"shell\s*=\s*True", sev_shell_py, "shell=True（コマンドの注入。B602）", "引数を配列で渡し shell=False にする。組み立てるなら shlex.quote"),
    ("V02", PY, r"\bos\.(system|popen)\(", sev_shell_py, "os.system/os.popen に組み立てた文字列を渡している（コマンドの注入。B605）", "subprocess.run に配列で渡す"),
    ("V02", JS, r"\b(exec|execSync)\s*\(\s*(?![\"'])", sev_shell_js, "child_process.exec に組み立てた文字列を渡している（コマンドの注入。OWASP A03）", "execFile か spawn に配列で渡す"),
    ("V03", PY, r"\b(pickle|cPickle|dill|shelve)\.(load|loads)\(|\bmarshal\.loads?\(", "重大", "pickle/marshal の読み込み（細工したデータでコードが動く。B301/B302）", "json か、署名を検証した上で読む"),
    ("V04", PY, r"\byaml\.load\((?![^)]*(SafeLoader|CSafeLoader|BaseLoader))", "重大", "yaml.load に Loader の指定が無い（任意のオブジェクトの生成。B506）", "yaml.safe_load にする"),
    ("V05", ALL, r"verify\s*=\s*False|_create_unverified_context|CERT_NONE|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*[\"']?0|rejectUnauthorized\s*:\s*false|\bcurl\b[^\n]*(\s-k\b|--insecure)|\bwget\b[^\n]*--no-check-certificate|check_hostname\s*=\s*False", "重大", "TLS の検証を切っている（中間者に読まれる。B501）", "検証を戻し、自前の CA なら verify に CA の束を渡す"),
    ("V06", ALL, r"(requests\.|fetch\(|axios\.|urlopen\(|httpx\.|http\.client|\bcurl\b|\bwget\b)[^\n]*[\"'`]http://(?!(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|example\.(com|org|net)))", "注意", "http:// で外に送っている（経路で読まれる。OWASP A02）", "https:// にする"),
    ("V07", PY, r"\btempfile\.mktemp\(", "重大", "tempfile.mktemp（作る前に横取りされる。B306）", "tempfile.mkstemp か NamedTemporaryFile にする"),
    ("V08", PY, r"\bhashlib\.(md5|sha1)\(|\bhashlib\.new\(\s*[\"'](md5|sha1)[\"']", sev_weak_hash, "md5/sha1（衝突が作れる。B303/B324）", "パスワードは hashlib.scrypt か bcrypt、それ以外は sha256"),
    ("V08", JS, r"createHash\(\s*[\"'](md5|sha1)[\"']", sev_weak_hash, "md5/sha1（衝突が作れる。OWASP A02）", "パスワードは bcrypt か scrypt、それ以外は sha256"),
    ("V09", PY, r"\brandom\.(random|randint|choice|choices|randrange|getrandbits|uniform)\(", sev_random, "random で鍵やトークンを作っている（予測できる。B311）", "secrets.token_hex か secrets.choice にする"),
    ("V09", JS, r"\bMath\.random\(", sev_random, "Math.random で鍵やトークンを作っている（予測できる。OWASP A02）", "crypto.randomBytes か crypto.getRandomValues にする"),
    ("V10", PY, SQL + r"[^\n]*" + DYN_PY + r"|" + DYN_PY + r"[^\n]*" + SQL, "重大", "SQL を文字列で組み立てている（SQL の注入。B608）", "プレースホルダ（?、%s、:name）と引数で渡す"),
    ("V10", JS, SQL + r"[^\n]*" + DYN_JS + r"|" + DYN_JS + r"[^\n]*" + SQL, "重大", "SQL を文字列で組み立てている（SQL の注入。OWASP A03）", "プレースホルダと引数で渡す"),
    ("V11", PY, r"\.run\([^)]*debug\s*=\s*True", "重大", "debug=True で動かしている（デバッガから任意のコード。B201）", "debug は環境変数で切り替え、既定は False"),
    ("V11", PY, r"host\s*=\s*[\"']0\.0\.0\.0[\"']", "注意", "0.0.0.0 で全部の口を開いている（B104）", "127.0.0.1 にし、外に出すときだけ明示する"),
    ("V12", ALL, r"allow_origins\s*=\s*\[\s*[\"']\*[\"']|Access-Control-Allow-Origin[\"']?\s*[:,]\s*[\"']\*|origin\s*:\s*[\"']\*[\"']|CORS_ORIGIN_ALLOW_ALL\s*=\s*True", "注意", "CORS がどこからでも（OWASP A05）", "許す origin を列挙する"),
    ("V13", PY, r"autoescape\s*=\s*False|\bmark_safe\(|\bMarkup\(", "注意", "HTML のエスケープを切っている（XSS。B701/B703）", "エスケープを戻し、必要な所だけ検査した値を通す"),
    ("V13", ALL, r"\{\{[^}]*\|\s*safe\s*\}\}", "注意", "テンプレートの | safe（XSS）", "safe を外し、必要なら検査した値だけ通す"),
    ("V14", JS, r"\.(innerHTML|outerHTML)\s*(\+?=)\s*(?P<rhs>[^;\n]*)", sev_dom, "innerHTML に変数を入れている（DOM の XSS。OWASP A03）", "textContent か、DOM を組み立てる。HTML なら DOMPurify を通す"),
    ("V14", JS, r"document\.write\(|insertAdjacentHTML\(|dangerouslySetInnerHTML", "重大", "HTML を文字列で差し込んでいる（DOM の XSS。OWASP A03）", "textContent か、DOM を組み立てる。HTML なら DOMPurify を通す"),
    ("V15", PY, r"\.extractall\((?![^)]*filter\s*=)", "注意", "展開に filter が無い（パスの外へ書く。B202）", "extractall(path, filter=\"data\") にし、展開先を検査する"),
    ("V16", ALL, r"chmod\([^)]*\b0o?777\b|\bchmod\s+(-R\s+)?777\b|os\.umask\(\s*0\s*\)", "重大", "誰でも書ける権限（B103）", "0o755 か 0o644 にし、鍵は 0o600"),
    ("V17", PY, r"^\s*except(\s*:|\s+(Exception|BaseException)\s*:)\s*$", lambda L, i, m: "注意" if i + 1 < len(L) and re.match(r"\s*pass\s*(#.*)?$", L[i + 1]) else None, "except して pass（不具合が黙って消える。B110）", "例外を記録するか、捕まえる型を絞る"),
    ("V17", JS, r"catch\s*(\([^)]*\))?\s*\{\s*\}", "注意", "catch して何もしない（不具合が黙って消える）", "記録するか、呼び元に返す"),
    ("V18", PY, r"^\s*assert\b", sev_assert, "入力の検査を assert でしている（-O で消える。B101）", "if で検査して例外を投げる"),
    ("V19", ALL, r"\bgit\s+(commit|push)\b[^\n]*--no-verify", "注意", "git の --no-verify（フックの検査を飛ばす）", "フックを通す。飛ばす理由があるなら 1 行書く"),
    ("V20", ALL, r"\b(password|passwd|pwd|secret|api_key|apikey|token|private_key)\s*[:=]\s*[\"'](?!\s*[\"']|<|\{|\$\{|\$|%|xxx|your[_-]|example|changeme|dummy|test|placeholder|none|null|redacted|\*)[^\"'\n]{6,}[\"']", "重大", "鍵やパスワードをコードに直書きしている（B105/B106）", "環境変数か鍵の保管庫から読む。すでに push していればローテート"),
]

RUFF_HIGH = {"S102", "S105", "S106", "S107", "S108", "S301", "S302", "S307", "S501", "S502", "S503", "S504", "S506", "S602", "S604", "S605", "S608", "S609", "S701", "S702"}
RUFF_SKIP = {"S101", "S110", "S112", "S311", "S303", "S324", "S404", "S603", "S607", "S104", "S113"}  # 正規表現側で狭く見る物と、呼び出しごとに鳴る物
RUFF_FIX = {"S102": "exec を使わない", "S105": "環境変数か保管庫から読む", "S106": "環境変数か保管庫から読む", "S107": "既定値を空にして環境変数から読む",
            "S108": "tempfile を使う", "S301": "json にする", "S302": "json にする", "S307": "ast.literal_eval にする", "S501": "verify を戻す",
            "S506": "yaml.safe_load にする", "S602": "配列で渡し shell=False", "S604": "shell=False", "S605": "subprocess.run に配列で渡す",
            "S608": "プレースホルダと引数で渡す", "S701": "autoescape=True"}


def scan_text(rel: str, text: str, test: bool = False) -> list:
    """1 ファイルの中身を型で見る。テストのファイルは見ない（悪い例を書くため）。"""
    if test or file_ignored(text):
        return []
    ext = Path(rel).suffix.lower()
    if ext not in ALL:
        return []
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        if line_ignored(line) or re.match(r"\s*(#|//|\*|/\*)", line):
            continue
        for pid, exts, rx, sev, what, fix in PATTERNS:
            if ext not in exts:
                continue
            m = re.search(rx, line, re.I if pid in ("V10", "V05", "V06", "V20") else 0)
            if not m:
                continue
            s = sev(lines, i, m) if callable(sev) else sev
            if s:
                out.append(Finding(s, rel, i + 1, pid, what, fix))
    return out


def ruff_findings(root: Path, files: list) -> list:
    """ruff の S（flake8-bandit）。無ければ空。設定は --isolated で無視し、リポジトリ側の除外に左右されない。"""
    exe = tools.find("ruff")
    py = [f for f in files if f.endswith(".py") and not is_test(f)]
    if not exe or not py:
        return []
    out = []
    for k in range(0, len(py), 200):
        code, text = run([exe, "check", "--select", "S", "--isolated", "--no-cache", "--exit-zero", "--output-format", "json", *py[k:k + 200]], root, timeout=300)
        try:
            data = json.loads(text[text.index("["):]) if "[" in text else []
        except ValueError:
            continue
        for d in data:
            c = d.get("code") or ""
            if c in RUFF_SKIP or not c.startswith("S"):
                continue
            rel = relpath(root, d.get("filename", ""))
            row = int(d.get("location", {}).get("row", 0))
            try:
                src = (root / rel).read_text(encoding="utf-8", errors="replace").splitlines()[row - 1]
                if line_ignored(src):
                    continue
            except (OSError, IndexError):
                pass
            sev = "重大" if c in RUFF_HIGH else "注意"
            out.append(Finding(sev, rel, row, c, f"{d.get('message', '')[:90]}（ruff {c}）", RUFF_FIX.get(c, "references/patterns.md を見る")))
    return out


def bandit_findings(root: Path, files: list) -> list:
    """bandit（PyCQA、Apache-2.0）。HIGH と MEDIUM は重大、LOW は注意。テストのファイルは見ない。"""
    exe = tools.find("bandit")
    py = [f for f in files if f.endswith(".py") and not is_test(f)]
    if not exe or not py:
        return []
    out = []
    for k in range(0, len(py), 300):
        code, text = run([exe, "-f", "json", "-q", "--exit-zero", *py[k:k + 300]], root, timeout=600)
        try:
            data = json.loads(text[text.index("{"):]).get("results", []) if "{" in text else []
        except ValueError:
            continue
        for d in data:
            rel = relpath(root, d.get("filename", ""))
            row = int(d.get("line_number", 0))
            src = (read_text(root / rel) or "").splitlines()
            if row and row <= len(src) and line_ignored(src[row - 1]):
                continue
            sev = "重大" if d.get("issue_severity") in ("HIGH", "MEDIUM") else "注意"
            tid = d.get("test_id", "B")
            out.append(Finding(sev, rel, row, tid, f"{d.get('issue_text', '')[:90]}（bandit {tid}）", RUFF_FIX.get("S" + tid[1:], "references/patterns.md の同じ番号を見る")))
    return out


def semgrep_findings(root: Path, files: list) -> list:
    """semgrep（LGPL-2.1）。registry の p/security-audit と p/secrets を試し、取れなければ同梱の規則で動かす。"""
    exe = tools.find("semgrep")
    targets = [f for f in files if Path(f).suffix.lower() in PY | JS and not is_test(f)]
    if not exe or not targets:
        return []
    local = Path(__file__).resolve().parent.parent / "references" / "semgrep-rules.yml"
    base = [exe, "scan", "--json", "--quiet", "--metrics=off", "--timeout", "60", "--disable-version-check", "--no-git-ignore"]
    out, data, used_cfg = [], None, ""
    for cfg in (["--config", "p/security-audit", "--config", "p/secrets"], ["--config", str(local)]):
        code, text = run(base + cfg + targets, root, timeout=900)
        try:
            data = json.loads(text[text.index("{"):]) if "{" in text else None
        except ValueError:
            data = None
        if data is not None and data.get("results") is not None and not any("could not" in str(e).lower() or "network" in str(e).lower() for e in data.get("errors", [])):
            used_cfg = "registry" if "p/security-audit" in cfg else "同梱の規則"
            break
    if data is None:
        return []
    for r in data.get("results", []):
        rel = relpath(root, r.get("path", ""))
        row = int(r.get("start", {}).get("line", 0))
        src = (read_text(root / rel) or "").splitlines()
        if row and row <= len(src) and line_ignored(src[row - 1]):
            continue
        ex = r.get("extra", {})
        lv = (ex.get("severity") or "").upper()
        if lv == "INFO":
            continue
        rid = (r.get("check_id") or "semgrep").split(".")[-1][:40]
        out.append(Finding("重大" if lv == "ERROR" else "注意", rel, row, rid, f"{(ex.get('message') or '')[:90]}（semgrep {used_cfg}）", (ex.get("metadata") or {}).get("fix", "") or "規則の説明を見て直す"))
    return out


def dependency_findings(root: Path) -> list:
    """依存の脆弱性。pip-audit と npm audit があれば使う。週 1 回の本番は vuln-watch なので、ここは注意（high と critical は重大）。"""
    out = []
    pa = tools.find("pip-audit")
    reqs = [p for p in ("requirements.txt", "requirements-dev.txt") if (root / p).is_file()]
    if pa and reqs:
        for rq in reqs:
            code, text = run([pa, "-r", rq, "-f", "json", "--progress-spinner", "off", "--no-deps"], root, timeout=600)
            try:
                deps = json.loads(text[text.index("{"):]).get("dependencies", []) if "{" in text else []
            except ValueError:
                deps = []
            for d in deps:
                for v in d.get("vulns", []):
                    fix = "、".join(v.get("fix_versions") or []) or "直す版が無い"
                    out.append(Finding("注意", rq, 0, "D01", f"依存 {d.get('name')} {d.get('version')}: {v.get('id')}（pip-audit）", f"{fix} に上げる（vuln-watch の bump.py）"))
    if tools.find("npm") and (root / "package-lock.json").is_file():
        code, text = run(["npm", "audit", "--json", "--audit-level=moderate"], root, timeout=600)
        try:
            vulns = json.loads(text[text.index("{"):]).get("vulnerabilities", {}) if "{" in text else {}
        except ValueError:
            vulns = {}
        for name, v in vulns.items():
            sev = (v.get("severity") or "").lower()
            if sev in ("low", "info"):
                continue
            out.append(Finding("重大" if sev in ("high", "critical") else "注意", "package-lock.json", 0, "D02", f"依存 {name} {v.get('range', '')}: {sev}（npm audit）", "npm audit fix か版を上げる（vuln-watch）"))
    return out


MANIFESTS = {"requirements.txt", "requirements-dev.txt", "package.json", "package-lock.json"}


def scan(root: Path, files: list, use_ruff: bool = True, force_regex: bool = False, diff_mode: bool = False):
    """ツールを先に使い、ツールの無い言語だけ正規表現で補う。
    差分の検査（diff_mode）では、依存は manifest を変えたときだけ見て、注意に留める（前からある依存の穴で PR を止めない。週 1 回の vuln-watch が持つ）。"""
    used, missing = [], []
    findings = []
    have = {"bandit": bool(tools.find("bandit")), "semgrep": bool(tools.find("semgrep")), "ruff": use_ruff and bool(tools.find("ruff"))}
    py = [f for f in code_files(files, PY)]
    js = [f for f in code_files(files, JS)]
    sh = [f for f in code_files(files, SH)]
    if py:
        if have["bandit"]:
            used.append("bandit"); findings += bandit_findings(root, files)
        else:
            missing.append("bandit")
        if have["ruff"]:
            used.append("ruff"); findings += ruff_findings(root, files)
        elif use_ruff:
            missing.append("ruff")
    if py or js:
        if have["semgrep"]:
            used.append("semgrep"); findings += semgrep_findings(root, files)
        else:
            missing.append("semgrep")
    covered = {".py": have["bandit"] or have["ruff"]} | {e: have["semgrep"] for e in JS}
    regex_exts = {e for e in ALL if force_regex or not covered.get(e, False)}  # .sh は常に（ツールの穴の型は shellcheck が見ない）
    if regex_exts:
        for rel in code_files(files, regex_exts):
            text = read_text(root / rel)
            if text is None:
                continue
            findings += scan_text(rel, text, is_test(rel))
        used.append("正規表現（" + "、".join(sorted(e.lstrip(".") for e in regex_exts)) + "）")
    deps = dependency_findings(root) if not diff_mode or any(Path(f).name in MANIFESTS for f in files) else []
    if diff_mode:
        for f in deps:
            f.sev = "注意"
    if deps:
        used.append("pip-audit/npm audit")
    findings += deps
    return findings, used, missing


def main() -> int:
    a = parse_args(__doc__, [(["--no-ruff"], {"action": "store_true", "help": "ruff を使わない"}),
                             (["--regex"], {"action": "store_true", "help": "ツールがあっても正規表現を全部動かす"})])
    files = target_files(a.root, a.diff)
    findings, used, missing = scan(a.root, files, not a.no_ruff, a.regex, bool(a.diff))
    if a.diff:
        findings = scope_to_diff(findings, a.root, a.diff)
    return report("不具合や脆弱性", findings, a.json, with_info=False,
                  extra={"files": len(files), "tools": tools.tools_line(used, missing)})


if __name__ == "__main__":
    sys.exit(main())
