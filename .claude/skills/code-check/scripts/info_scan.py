#!/usr/bin/env python3
"""情報の扱いや漏洩: 鍵や個人情報がどこに在り、どこへ出て行くかを機械で見る。

実績のあるツールを先に使う（detect-secrets、gitleaks）。鍵の型そのものは secret-leak-check、非公開の名前や費用は
content-audit に任せ、ここはその 2 つの要約を取り込んだ上で、「情報の扱い」を見る。
追跡してはいけないファイル、鍵を出力している行、URL に入った鍵、鍵を書き出す行、鍵のファイルの権限、外に送る先、
扱っている個人情報、クリップボードと計測の SDK。

使い方: info_scan.py [root] [--diff <base>] [--json] [--history]
  --history  gitleaks があれば履歴も見る（公開の前）
終了コード: 重大があれば 1
抑止: 行末に `code-check: ignore`、ファイルの先頭に `code-check: ignore-file`
"""

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tools  # noqa: E402
from common import (CIEL_SKILLS, Finding, code_files, file_ignored, is_test, line_ignored, near, parse_args, read_text,  # noqa: E402
                    report, run, scope_to_diff, strip_strings, target_files, tracked)

CODE = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".bash", ".rb", ".go", ".php", ".java", ".kt", ".cs"}
SECRET_ID = r"\b(password|passwd|pwd|token|secret|api_key|apikey|authorization|auth_token|access_key|secret_key|client_secret|private_key|cookie|session_id|credential)s?\b"  # code-check: ignore 検出の正規表現であり、鍵ではない
SENSITIVE_FILE = re.compile(r"(^|/)(\.env(\..+)?|\.netrc|\.pypirc|\.npmrc|credentials\.json|service[-_]account.*\.json|secrets?\.(json|ya?ml|toml|ini|txt))$"
                            r"|\.(pem|key|p12|pfx|kdbx|jks|keystore)$|(^|/)id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$", re.I)
SENSITIVE_OK = re.compile(r"\.env\.(example|sample|template|dist)$|\.pub$|(^|/)public\.pem$", re.I)
DATA_FILE = re.compile(r"\.(sqlite3?|db|log|dump|bak)$|(^|/)dump\.(sql|rdb)$", re.I)
LOG_CALL = re.compile(r"\b(print|console\.(log|error|warn|info|debug)|logger\.(info|debug|warning|warn|error|critical|exception)|logging\.(info|debug|warning|error|critical)|log\.(info|debug|warn|error)|echo|puts|printf|fmt\.Print\w*)\b")
URL_KEY = re.compile(r"https?://[^\s\"'`]*[?&](token|api_key|apikey|access_token|password|secret|key|auth)=(?!\{|\$|%|<)", re.I)
URL_BASIC = re.compile(r"https?://[^\s\"'`/@:]+:[^\s\"'`/@]+@[^\s\"'`/]+", re.I)
WRITE_CALL = re.compile(r"\b(json\.dump|yaml\.(safe_)?dump|\.write\(|writeFileSync|writeFile\(|fs\.write|open\([^)]*[\"']w)")
CHMOD = re.compile(r"chmod\s*\(?[^)\n]*\b0o?([67][0-7][0-7])\b|\bchmod\s+(-R\s+)?([67][0-7][0-7])\b")
HOST_CALL = re.compile(r"\b(requests\.|fetch\(|axios[.(]|urlopen\(|urllib\.request|httpx\.|http\.client|aiohttp|XMLHttpRequest|WebSocket\(|\bcurl\b|\bwget\b|net/http|HttpClient)")
HOST = re.compile(r"https?://([a-z0-9][a-z0-9.-]*\.[a-z]{2,})(?::\d+)?", re.I)
LOCAL = re.compile(r"^(localhost|127\.|0\.0\.0\.0|example\.(com|org|net)|.*\.local|.*\.example\.com)$", re.I)
PII = [("メールアドレス", r"\b(e[-_]?mail(_address)?|mail_addr)\b|メールアドレス"), ("電話番号", r"\b(phone(_number)?|tel(ephone)?|mobile)\b|電話番号"),
       ("住所", r"\b(address|postal_code|zip_code|zipcode)\b|住所|郵便番号"), ("氏名", r"\b(full_name|first_name|last_name|family_name|given_name|real_name)\b|氏名|本名"),
       ("生年月日・年齢", r"\b(birth(day|_date)?|date_of_birth|dob|age)\b|生年月日"), ("公的な番号", r"\b(ssn|my_number|passport(_no)?|driver_?licen[cs]e)\b|マイナンバー|個人番号"),
       ("支払いの情報", r"\b(credit_card|card_number|cvv|iban|bank_account)\b|クレジットカード|口座番号"), ("位置", r"\b(latitude|longitude|geolocation|gps)\b|位置情報")]
TELEMETRY = re.compile(r"\b(pyperclip|navigator\.clipboard|clipboard\.(write|copy)|sentry_sdk|@sentry|mixpanel|amplitude|segment\.com|analytics\.js|google-analytics|googletagmanager|gtag\(|posthog|hotjar|datadog|newrelic|appinsights)\b", re.I)


LLM_TOKEN = re.compile(r"(max|num|n|total|input|output|cache|cached|used|remaining|prompt|completion)_?tokens?\b|\btokens\b|tokens?\(|token_count|token数|トークン", re.I)


def secret_word(bare: str) -> bool:
    """鍵を指す語があるか。token だけのときは、LLM のトークン数の話（token-efficiency など）を外す。"""
    words = {m.group(1).lower() for m in re.finditer(SECRET_ID, bare, re.I)}
    if not words:
        return False
    if words <= {"token"} and LLM_TOKEN.search(bare):
        return False
    return True


def doc_file(rel: str) -> bool:
    return Path(rel).suffix.lower() in (".md", ".rst", ".txt")


def skip_line(src_line: str) -> bool:
    return line_ignored(src_line) or "secret-leak-check: ignore" in src_line


def tracked_files(root: Path, files: list, only_changed: bool = False) -> list:
    """追跡してはいけないファイルと、データのファイル。木全体で見る（安い）。
    差分の検査（only_changed）では変えたファイルだけ見る。前からある物で PR を止めないため。"""
    out = []
    for rel in (files if only_changed else tracked(root)):
        if SENSITIVE_FILE.search(rel) and not SENSITIVE_OK.search(rel):
            out.append(Finding("重大", rel, 0, "I01", "鍵や設定の秘密を追跡している", "git rm --cached で外し .gitignore に足す。push 済みならローテートして履歴から消す"))
        elif DATA_FILE.search(rel) and not is_test(rel):
            out.append(Finding("注意", rel, 0, "I02", "データベースや記録のファイルを追跡している（中に個人情報が入りうる）", "追跡から外し .gitignore に足す。要るなら中身を見てから"))
    if only_changed:
        return out
    gi = read_text(root / ".gitignore") or ""
    has_code = any(rel.endswith((".py", ".js", ".ts", "package.json", "pyproject.toml", "requirements.txt")) for rel in files[:2000])
    if has_code and not re.search(r"^\s*\*?\.env\b", gi, re.M):
        out.append(Finding("注意", ".gitignore", 0, "I03", ".env が .gitignore に無い", "`.env` と `*.pem` と `*.key` を足す"))
    return out


def scan_text(rel: str, text: str, test: bool = False) -> list:
    """1 ファイルの中身。テストのファイルは出力の行と URL だけ見る。"""
    if file_ignored(text):
        return []
    ext = Path(rel).suffix.lower()
    lines = text.splitlines()
    out = []
    hosts = {}
    for i, line in enumerate(lines):
        if line_ignored(line) or re.match(r"\s*(#|//|\*|/\*)", line):
            continue
        bare = strip_strings(line)
        if ext in CODE and LOG_CALL.search(line) and secret_word(bare) and not re.search(r"\b(len|mask|redact|\*\*\*|hidden|\[REDACTED\])", line, re.I):
            out.append(Finding("重大", rel, i + 1, "I04", "鍵やパスワードを出力している（記録や画面に残る）", "出さない。要るなら長さか末尾 4 文字だけ"))
        doc_sev = "注意" if doc_file(rel) else "重大"  # 文書の中は例が多い。例なら行末に secret-leak-check: ignore
        if URL_KEY.search(line) and "secret-leak-check: ignore" not in line:
            out.append(Finding(doc_sev, rel, i + 1, "I05", "URL のクエリに鍵が入っている（記録や履歴に残る）", "ヘッダ（Authorization）か本文で渡す"))
        if URL_BASIC.search(line) and not re.search(r"\{|\$\{|\$[A-Z_]|%s|<", line) and "secret-leak-check: ignore" not in line:
            out.append(Finding(doc_sev, rel, i + 1, "I05", "URL にユーザー名とパスワードが入っている", "環境変数から読み、URL に入れない"))
        if not test and ext in CODE:
            if WRITE_CALL.search(line) and re.search(SECRET_ID, bare, re.I):
                out.append(Finding("注意", rel, i + 1, "I06", "鍵やパスワードをファイルに書き出している", "書くなら 0o600 で、場所を README に書く。できれば鍵の保管庫"))
            m = CHMOD.search(line)
            if m:
                mode = m.group(1) or m.group(3)
                if near(lines, i, r"\b(key|secret|token|pem|credential|password)\b"):
                    out.append(Finding("重大", rel, i + 1, "I07", f"鍵のファイルを {mode} にしている（他のユーザーが読める）", "0o600 にする"))
                elif mode in ("666", "777"):
                    out.append(Finding("注意", rel, i + 1, "I07", f"誰でも書ける {mode}", "0o644 か 0o755 にする"))
            if TELEMETRY.search(line):
                out.append(Finding("注意", rel, i + 1, "I10", "クリップボードか計測の SDK を使っている（何を外に送るかを書く）", "README の「情報の扱い」に、何を・どこへ送るかを書く"))
        if ext in CODE and HOST_CALL.search(line):
            for h in HOST.findall(line):
                if not LOCAL.match(h):
                    hosts.setdefault(h.lower(), i + 1)
    for h, ln in sorted(hosts.items()):
        out.append(Finding("情報", rel, ln, "I08", f"外に送る先: {h}", "README の「情報の扱い」に、何を送るかを書く"))
    if not test and (ext in CODE or ext in (".sql", ".csv", ".json", ".yaml", ".yml", ".prisma", ".graphql")):
        head = "\n".join(lines[:1] if ext == ".csv" else lines)
        for name, rx in PII:
            n = len(re.findall(rx, head, re.I))
            if n:
                out.append(Finding("情報", rel, 0, "I09", f"扱っている個人情報: {name}（{n} 箇所）", "保存先・期間・暗号化を README の「情報の扱い」に書く"))
    return out


def secret_tools(root: Path, files: list, history: bool, used: list, missing: list) -> list:
    """detect-secrets と gitleaks。どちらも無ければ無しと出し、secret-leak-check の scan.py が補う。"""
    out = []
    ds = tools.find("detect-secrets")
    if ds:
        used.append("detect-secrets")
        targets = [f for f in files if not is_test(f)] if files else []
        for k in range(0, max(1, len(targets)), 400):
            chunk = targets[k:k + 400]
            code, text = run([ds, "scan", *(chunk if chunk else ["--all-files"])], root, timeout=600)
            try:
                res = json.loads(text[text.index("{"):]).get("results", {}) if "{" in text else {}
            except ValueError:
                res = {}
            for rel, hits in res.items():
                src = (read_text(root / rel) or "").splitlines()
                for h in hits:
                    row = int(h.get("line_number", 0))
                    if row and row <= len(src) and skip_line(src[row - 1]):
                        continue
                    # エントロピーだけの検出（Hex、Base64）は、要約の digest や commit の SHA にも鳴る。鍵の名前が無ければ注意に落とす
                    entropy_only = "Entropy" in h.get("type", "")
                    sev = "注意" if entropy_only or doc_file(rel) else "重大"
                    out.append(Finding(sev, rel, row, "I11", f"鍵らしき物: {h.get('type', '')}（detect-secrets）", "鍵なら外して環境変数に（push 済みならローテート）。digest や例なら行末に secret-leak-check: ignore"))
    else:
        missing.append("detect-secrets")
    gl = tools.find("gitleaks")
    if gl:
        used.append("gitleaks")
        with tempfile.TemporaryDirectory() as d:
            rep = Path(d) / "r.json"
            sub = ["git", str(root)] if history else ["dir", str(root)]
            run([gl, *sub, "-f", "json", "-r", str(rep), "--exit-code", "0", "--no-banner"], root, timeout=900)
            try:
                data = json.loads(rep.read_text(encoding="utf-8")) if rep.exists() and rep.stat().st_size else []
            except ValueError:
                data = []
        for h in data:
            rel = h.get("File", "")
            if is_test(rel) or (files and rel not in files and not history):
                continue
            src = (read_text(root / rel) or "").splitlines()
            row = int(h.get("StartLine", 0))
            if row and row <= len(src) and skip_line(src[row - 1]):
                continue
            where = f"履歴 {h.get('Commit', '')[:7]}" if history and h.get("Commit") else ""
            out.append(Finding("注意" if doc_file(rel) and not history else "重大", rel, row, "I11", f"鍵らしき物: {h.get('RuleID', '')}（gitleaks{'、' + where if where else ''}）", "鍵を外して環境変数に。push 済みならローテートして履歴から消す"))
    else:
        missing.append("gitleaks")
    return out


def ciel_scans(root: Path, used: list) -> list:
    """secret-leak-check の scan.py と content-audit の audit.py の要約を取り込む（鍵の型と、非公開の名前・費用）。"""
    out = []
    for name, rel, label in (("secret-leak-check", "secret-leak-check/scripts/scan.py", "鍵"), ("content-audit", "content-audit/scripts/audit.py", "公開の精査")):
        s = next((b / rel for b in (root / ".claude" / "skills", CIEL_SKILLS) if (b / rel).is_file()), None)
        if not s:
            out.append(Finding("注意", "", 0, "I12", f"{name} が入っていない", "skill-sync で Ciel のスキルを入れる"))
            continue
        used.append(name)
        code, text = run([sys.executable, str(s), "--tree"], root, timeout=600)
        tail = ([l for l in text.strip().splitlines() if l.strip()] or ["?"])[-1][:120]
        if name == "secret-leak-check":
            if "検出なし" in text:
                continue
            out.append(Finding("重大", "", 0, "I12", f"{label}: {tail}", "scan.py --tree の行を直す。push 済みならローテート"))
        else:
            m = re.search(r"重大 (\d+) / 注意 (\d+)", text)
            if m and int(m.group(1)):
                out.append(Finding("重大", "", 0, "I12", f"{label}: {tail}", "audit.py --tree の行を伏せる"))
            elif m and int(m.group(2)):
                out.append(Finding("注意", "", 0, "I12", f"{label}: {tail}", "audit.py --tree の注意を見る"))
    return out


def scan(root: Path, files: list, history: bool = False, diff_mode: bool = False):
    """diff_mode では、木全体の検査（追跡しているファイル、secret-leak-check と content-audit の --tree）をしない。
    precheck は secret-leak-check と content-audit を差分で別に回すので、ここで木全体を重ねると前からある物で止まる。"""
    used, missing = [], []
    findings = tracked_files(root, files, diff_mode)
    for rel in files:
        if Path(rel).suffix.lower() not in CODE | {".sql", ".csv", ".json", ".yaml", ".yml", ".prisma", ".graphql", ".toml", ".ini", ".cfg", ".env", ".md"}:
            continue
        text = read_text(root / rel)
        if text is None:
            continue
        findings += scan_text(rel, text, is_test(rel))
    findings += secret_tools(root, files, history, used, missing)
    if not diff_mode:
        findings += ciel_scans(root, used)
    return findings, used, missing


def main() -> int:
    a = parse_args(__doc__, [(["--history"], {"action": "store_true", "help": "gitleaks で履歴も見る"})])
    files = target_files(a.root, a.diff)
    findings, used, missing = scan(a.root, files, a.history, bool(a.diff))
    if a.diff:
        findings = scope_to_diff(findings, a.root, a.diff)
    return report("情報の扱いや漏洩", findings, a.json, extra={"files": len(files), "tools": tools.tools_line(sorted(set(used)), sorted(set(missing)))})


if __name__ == "__main__":
    sys.exit(main())
