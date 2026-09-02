#!/usr/bin/env python3
"""機密情報の混入を走査する。

  scan.py --tree              作業ツリー全体
  scan.py --diff              未コミットの変更（staged + unstaged）
  scan.py --diff <base>..<head>  指定範囲の差分
  scan.py --history           全コミットの履歴（公開前・公開後の確認用）

検出があれば終了コード 1。誤検知は行末に `secret-leak-check: ignore` を書くか、
.secretignore に glob を並べて抑止する。
"""
import argparse, math, re, subprocess, sys, os
from pathlib import Path

# ---- ルール ---------------------------------------------------------------
# (名前, 正規表現, 深刻度)  深刻度: high=ほぼ確実に本物 / medium=要確認
RULES = [
    ("Anthropic API key",   r"sk-ant-(?:api|admin)[A-Za-z0-9_\-]{20,}", "high"),
    ("OpenAI API key",      r"sk-(?:proj-)?[A-Za-z0-9]{40,}",           "high"),
    ("GitHub token",        r"gh[pousr]_[A-Za-z0-9]{36,}",              "high"),
    ("GitHub fine-grained", r"github_pat_[A-Za-z0-9_]{50,}",            "high"),
    ("AWS access key",      r"(?<![A-Z0-9])A(?:KIA|SIA)[0-9A-Z]{16}(?![A-Z0-9])", "high"),
    ("Google API key",      r"AIza[0-9A-Za-z_\-]{35}",                  "high"),
    ("Slack token",         r"xox[baprs]-[0-9A-Za-z\-]{10,}",           "high"),
    ("Slack webhook",       r"https://hooks\.slack\.com/services/[A-Za-z0-9/]{20,}", "high"),
    ("Stripe secret key",   r"(?<![A-Za-z0-9])[sr]k_live_[A-Za-z0-9]{20,}", "high"),
    ("SendGrid key",        r"SG\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}", "high"),
    ("Private key block",   r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----", "high"),
    ("JWT",                 r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", "medium"),
    ("Basic auth in URL",   r"[a-z][a-z0-9+.\-]*://[^/\s:@]+:[^/\s:@]{3,}@[^\s/]+", "high"),
    ("Postgres/MySQL URL",  r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s]*:[^\s@]+@[^\s]+", "high"),
    ("npm token",           r"npm_[A-Za-z0-9]{36}",                     "high"),
    ("Hugging Face token",  r"hf_[A-Za-z0-9]{30,}",                     "high"),
]

# 変数名から拾う（値が十分に無作為なときだけ）
ASSIGN = re.compile(
    r"""(?ix)
    \b(?P<key>[A-Za-z0-9_.\-]*(?:secret|token|passwd|password|api[_\-]?key|
       access[_\-]?key|private[_\-]?key|credential|auth)[A-Za-z0-9_.\-]*)
    \s*[:=]\s*
    (?P<q>["']?)(?P<val>[A-Za-z0-9+/=_\-]{16,})(?P=q)
    """)

# ---- 誤検知の抑止 ---------------------------------------------------------
PLACEHOLDER = re.compile(
    r"(?i)(your[_\-]?|my[_\-]?|the[_\-]?)?(api[_\-]?key|secret|token|password|xxx+|"
    r"placeholder|example|sample|dummy|changeme|redacted|<[^>]+>|\$\{[^}]+\}|"
    r"\{\{[^}]+\}\}|foo|bar|test|fake|abc123|000+|111+|aaa+)")

# ドキュメントで形式を説明するときの役割名。値の位置にこれが出たら例示である。
DOC_WORDS = re.compile(
    r"(?i)\b(user(name)?|pass(wd|word)?|host(name)?|localhost|scheme|domain|"
    r"dbname|myuser|mypass|someone|admin:admin)\b")

# URL の認証情報部分だけを取り出す（scheme://ここ@host）
URL_CRED = re.compile(r"^[a-z][a-z0-9+.\-]*://([^/\s@]+)@", re.I)

SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "__pycache__",
             ".venv", "venv", ".next", "target", "corpus"}
SKIP_SUFFIX = {".lock", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
               ".woff", ".woff2", ".ttf", ".zip", ".gz", ".tar", ".mp4", ".svg"}
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
              "Cargo.lock", "go.sum", "Gemfile.lock", "composer.lock"}
IGNORE_MARK = "secret-leak-check: ignore"


def entropy(s: str) -> float:
    if not s:
        return 0.0
    return -sum((n := s.count(c) / len(s)) * math.log2(n) for c in set(s))


def is_placeholder(val: str) -> bool:
    v = val.strip()
    if PLACEHOLDER.fullmatch(v):
        return True
    # URL は認証情報の部分だけで判断する。ホスト名やパスまで含めて
    # エントロピーを測ると、長いだけの例示が実物に見えてしまう。
    m = URL_CRED.match(v)
    if m:
        cred = m.group(1)
        if DOC_WORDS.search(cred) or PLACEHOLDER.fullmatch(cred):
            return True
        return entropy(cred) < 2.5
    if DOC_WORDS.search(v) and entropy(v) < 3.8:
        return True
    return bool(PLACEHOLDER.search(v)) and entropy(v) < 3.5


def load_ignores() -> list:
    p = Path(".secretignore")
    if not p.exists():
        return []
    return [l.strip() for l in p.read_text().splitlines()
            if l.strip() and not l.startswith("#")]


def ignored(path: str, globs: list) -> bool:
    q = Path(path)
    if any(part in SKIP_DIRS for part in q.parts):
        return True
    if q.suffix.lower() in SKIP_SUFFIX or q.name in SKIP_NAMES:
        return True
    if ".example" in q.name or q.name.endswith(".sample"):
        return True
    return any(q.match(g) for g in globs)


def scan_line(line: str):
    """1行を調べて (ルール名, 深刻度, 一致文字列) を返す。

    同じ箇所に複数のルールが当たったら最初の1つだけ報告する。
    1つの秘密が2件に化けると、対応すべき件数が見えなくなるため。
    """
    if IGNORE_MARK in line:
        return
    taken = []

    def overlaps(a, b):
        return any(not (b <= x or a >= y) for x, y in taken)

    for name, pat, sev in RULES:
        for m in re.finditer(pat, line):
            hit = m.group(0)
            if is_placeholder(hit) or overlaps(*m.span()):
                continue
            taken.append(m.span())
            yield name, sev, hit
    for m in ASSIGN.finditer(line):
        val = m.group("val")
        if is_placeholder(val) or entropy(val) < 3.6:
            continue
        if overlaps(*m.span("val")):
            continue          # 上のルールで既に出ている
        taken.append(m.span("val"))
        yield f"高エントロピーな値 ({m.group('key')})", "medium", val


def redact(s: str) -> str:
    return s[:6] + "…" + s[-4:] if len(s) > 14 else s[:3] + "…"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace").stdout


def iter_tree(globs):
    files = run(["git", "ls-files"]).splitlines() or [
        str(p) for p in Path(".").rglob("*") if p.is_file()]
    for f in files:
        if ignored(f, globs):
            continue
        try:
            for i, line in enumerate(Path(f).read_text(errors="replace").splitlines(), 1):
                yield f, i, line
        except (OSError, IsADirectoryError):
            continue


def iter_diff(globs, rng=None):
    cmd = ["git", "diff", "--unified=0"] + ([rng] if rng else ["HEAD"])
    path, ln = "?", 0
    for line in run(cmd).splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            ln = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++"):
            if not ignored(path, globs):
                yield path, ln, line[1:]
            ln += 1


def iter_history(globs):
    for sha in run(["git", "rev-list", "--all"]).split():
        for line in run(["git", "show", "--unified=0", "--format=", sha]).splitlines():
            if line.startswith("+++ b/"):
                path = line[6:]
            elif line.startswith("+") and not line.startswith("+++"):
                if not ignored(locals().get("path", "?"), globs):
                    yield f"{sha[:8]}:{locals().get('path','?')}", 0, line[1:]


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--tree", action="store_true")
    g.add_argument("--diff", nargs="?", const="", metavar="RANGE")
    g.add_argument("--history", action="store_true")
    a = ap.parse_args()

    globs = load_ignores()
    if a.history:
        src, label = iter_history(globs), "履歴（全コミット）"
    elif a.diff is not None:
        src, label = iter_diff(globs, a.diff or None), f"差分 {a.diff or 'HEAD'}"
    else:
        src, label = iter_tree(globs), "作業ツリー"

    seen, findings = set(), []
    for path, ln, line in src:
        for name, sev, hit in scan_line(line):
            k = (path, name, hit)
            if k in seen:
                continue
            seen.add(k)
            findings.append((sev, path, ln, name, hit))

    print(f"== secret-leak-check: {label} ==")
    if not findings:
        print("  検出なし")
        return 0

    findings.sort(key=lambda f: (f[0] != "high", f[1]))
    for sev, path, ln, name, hit in findings:
        mark = "!!" if sev == "high" else " ?"
        loc = f"{path}:{ln}" if ln else path
        print(f"  {mark} [{sev:6}] {name}\n       {loc}\n       {redact(hit)}")
    high = sum(1 for f in findings if f[0] == "high")
    print(f"\n  high {high} / medium {len(findings)-high}")
    print("  本物なら まず鍵をローテート、次に履歴から除去。順序を逆にしない。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
