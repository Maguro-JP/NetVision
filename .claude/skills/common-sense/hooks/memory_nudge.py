#!/usr/bin/env python3
"""PostToolUse フック（Edit、Write、MultiEdit）: 外の物への新しい依存を、調べずに記憶で書いたときに 1 行で知らせる。

ユーザーの指摘: 自分の知識は数年前の物だという認識がない。自分の認識だけで済ませて調べようとしない。
見るのは 2 つがそろったとき。止めはしない。
  1. 書いた中身に、前の中身には無かった外の物がある
     （外のパッケージの import、版の固定、uses: x/y@v、モデルの ID、フラグ付きの外のコマンド、外のリポジトリの URL）
  2. 直近 3 ターンに、外の事実を見に行く呼び出しが無い
     （WebSearch、WebFetch、Context7、Exa、--help、--version、pip show、npm view、入っている版のファイルを読む）
同じセッションで同じ依存には 1 回だけ知らせる。調べたあとに書いた依存は、確かめた物として覚え、あとで知らせない。
テストのファイル（例として依存が並ぶ）と、調べ物の記録（docs/knowledge/）は見ない。

環境変数:
  COMMON_SENSE_OFF=1          止める
  COMMON_SENSE_MAX_NUDGE=N    1 セッションで知らせる回数の上限（既定 8）
  COMMON_SENSE_STATE=dir      知らせた依存の置き場
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cs  # noqa: E402

WINDOW = 3  # 何ターン前の調べ物まで、確かめたと見るか
SKIP_PATH = re.compile(r"(?:^|/)tests?/|(?:^|/)test_[^/]*$|\.(?:test|spec)\.|_test\.|(?:^|/)docs/knowledge/|-lock\.|\.lock$|(?:^|/)node_modules/")
STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
NODE_BUILTIN = {"assert", "buffer", "child_process", "cluster", "crypto", "dns", "events", "fs", "http", "http2", "https", "module", "net", "os", "path",
                "perf_hooks", "process", "querystring", "readline", "stream", "string_decoder", "timers", "tls", "url", "util", "vm", "worker_threads", "zlib"}
JS_EXT = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")

PY_IMPORT = re.compile(r"^\s*(?:from\s+([A-Za-z_]\w*)[\w.]*\s+import\b|import\s+([A-Za-z_]\w*))", re.M)
JS_IMPORT = re.compile(r"""(?:\bfrom\s+|\brequire\(\s*|\bimport\s*\(?\s*)["']([^"'./\s][^"'\s]*)["']""")
REQ_PIN = re.compile(r"^\s*([A-Za-z0-9][\w.-]*)\s*(?:\[[^\]]*\])?\s*(?:==|~=|>=|<=)\s*([\w.*+!-]+)", re.M)
QUOTED_PIN = re.compile(r"""["']([A-Za-z0-9][\w.-]*)\s*(?:==|~=|>=|\^)\s*(\d[\w.*]*)["']""")
JSON_PIN = re.compile(r'"(@?[\w.-]+(?:/[\w.-]+)?)"\s*:\s*"([~^]?\d+\.[^"]*)"')
PIP_PIN = re.compile(r"\bpip3?\s+install\b[^\n]*?\b([A-Za-z0-9][\w.-]*)==(\d[\w.]*)")
NPM_PIN = re.compile(r"\b(?:npm\s+(?:install|i|add)|pnpm\s+add|yarn\s+add|npx|bunx)\b[^\n]*?\s(@?[A-Za-z][\w.-]*(?:/[\w.-]+)?)@(\d[\w.-]*)")
FROM_PIN = re.compile(r"^\s*FROM\s+([\w./-]+):([\w.-]+)", re.M)
USES = re.compile(r"""\buses:\s*["']?([\w.-]+/[\w./-]+)@([\w.-]+)""")
MODEL = re.compile(r"(?<![\w./-])((?:[a-z]{2}\.)?(?:anthropic\.)?claude-(?:opus|sonnet|haiku|fable|instant|\d)[a-z0-9.:-]*|gpt-\d[a-z0-9.-]*|gemini-\d[a-z0-9.-]*)")
CLI = ("gh|docker|kubectl|helm|aws|gcloud|az|terraform|npm|npx|pnpm|yarn|bun|pip|pip3|uv|cargo|claude|vercel|supabase|wrangler"
       "|ruff|bandit|semgrep|shellcheck|gitleaks|eslint|pytest|markdownlint|textlint|detect-secrets|pip-audit|osv-scanner|ffmpeg")
CLI_FLAG = re.compile(rf"(?<![\w/.-])({CLI})\s+((?:(?!-)\S+\s+){{0,4}}?)(--[a-z][\w-]+)")
SUBCOMMAND = re.compile(r"^[a-z][a-z-]*$")
REPO_URL = re.compile(r"github\.com/([A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*)")


def local_module(mod: str, file: Path, root: Path) -> bool:
    """このリポジトリの中のモジュールか。外のパッケージだけを依存と数える。"""
    if mod.startswith("_"):
        return True
    here = file.parent
    for d in (here, here.parent / "scripts", here.parent / "hooks", here.parent, root, root / "src", root / "scripts", root / "lib"):
        if (d / f"{mod}.py").is_file() or (d / mod).is_dir():
            return True
    return False


def declared(root: Path) -> str:
    """入っている依存の一覧（manifest）の中身。ここに名前のある物は、新しい依存ではない。"""
    out = []
    for name in ("requirements.txt", "requirements-dev.txt", "pyproject.toml", "package.json", "setup.py", "setup.cfg"):
        try:
            out.append((root / name).read_text(encoding="utf-8", errors="replace")[:200_000].lower())
        except OSError:
            continue
    return "\n".join(out)


def own_repo(root: Path) -> str:
    try:
        m = REPO_URL.search((root / ".git" / "config").read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    return re.sub(r"\.git$", "", m.group(1)).lower() if m else ""


def deps(text: str, path: str, root: Path) -> dict:
    """書いた中身から、外の物への依存を 鍵 → 見せる名前 で取り出す。"""
    out = {}
    file = Path(path)
    name, ext = file.name, file.suffix.lower()
    if ext == ".py":
        for m in PY_IMPORT.finditer(text):
            mod = m.group(1) or m.group(2)
            if len(STDLIB) > 50 and mod not in STDLIB and not local_module(mod, file, root):  # 標準の一覧が取れない版では import を見ない
                out[f"import:{mod}"] = f"import {mod}"
    elif ext in JS_EXT:
        for m in JS_IMPORT.finditer(text):
            spec = m.group(1)
            if spec.startswith("node:"):
                continue
            pkg = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
            if pkg not in NODE_BUILTIN:
                out[f"import:{pkg}"] = f"import {pkg}"
    if re.match(r"(?:requirements|constraints)[\w.-]*\.txt$", name):
        for m in REQ_PIN.finditer(text):
            out[f"pin:{m.group(1).lower()}"] = f"{m.group(1)} の版 {m.group(2)}"
    elif name == "package.json":
        for m in JSON_PIN.finditer(text):
            if m.group(1) != "version":
                out[f"pin:{m.group(1).lower()}"] = f"{m.group(1)} の版 {m.group(2)}"
    elif name in ("pyproject.toml", "setup.py", "setup.cfg"):
        for m in QUOTED_PIN.finditer(text):
            out[f"pin:{m.group(1).lower()}"] = f"{m.group(1)} の版 {m.group(2)}"
    elif name.startswith("Dockerfile"):
        for m in FROM_PIN.finditer(text):
            out[f"pin:{m.group(1).lower()}"] = f"{m.group(1)} の版 {m.group(2)}"
    for pat in (PIP_PIN, NPM_PIN):
        for m in pat.finditer(text):
            out[f"pin:{m.group(1).lower()}"] = f"{m.group(1)} の版 {m.group(2)}"
    for m in USES.finditer(text):
        out[f"uses:{m.group(1).lower()}@{m.group(2)}"] = f"uses: {m.group(1)}@{m.group(2)}"
    for m in MODEL.finditer(text):
        mid = m.group(1).rstrip(".:-")
        out[f"model:{mid}"] = f"モデルの ID {mid}"
    for line in text.splitlines():
        if "--" not in line:
            continue
        for m in CLI_FLAG.finditer(re.sub(r"""["',\[\]()]""", " ", line)):
            sub = [w for w in m.group(2).split() if SUBCOMMAND.match(w)][:2]  # 引数（番号、パス）は落とし、サブコマンドだけ残す
            cmd = " ".join([m.group(1)] + sub)
            out[f"cmd:{cmd} {m.group(3)}"] = f"{cmd} {m.group(3)}"
    own = own_repo(root)
    for m in REPO_URL.finditer(text):
        repo = re.sub(r"\.git$", "", m.group(1).rstrip(".")).lower()
        if repo != own:
            out[f"repo:{repo}"] = f"github.com/{repo}"
    return out


def texts(d: dict):
    """(書いた中身, 前の中身)。前の中身は、結果の originalFile（ファイルの全体）を先に使う。"""
    inp = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    resp = d.get("tool_response") if isinstance(d.get("tool_response"), dict) else {}
    edits = inp.get("edits") if isinstance(inp.get("edits"), list) else []
    new = "\n".join([str(inp.get("content") or ""), str(inp.get("new_string") or "")] + [str(e.get("new_string") or "") for e in edits if isinstance(e, dict)])
    old = resp.get("originalFile")
    if not isinstance(old, str):
        old = "\n".join([str(inp.get("old_string") or "")] + [str(e.get("old_string") or "") for e in edits if isinstance(e, dict)])
    return new, old


def main() -> int:
    if _cs.off():
        return 0
    d = _cs.read_event()
    if d.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        return 0
    inp = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    path = str(inp.get("file_path") or "").replace("\\", "/")
    if not path or SKIP_PATH.search(path):
        return 0
    root = Path.cwd()
    new, old = texts(d)
    fresh = deps(new, path, root)
    if not fresh:
        return 0
    before = deps(old, path, root) if old.strip() else {}
    sid = d.get("session_id")
    st = _cs.load(sid, "memory")
    known = list(st.get("known") or [])
    listed = declared(root)
    fresh = {k: v for k, v in fresh.items() if k not in before and k not in known
             and not (k.startswith("import:") and re.search(rf"(?<![\w-]){re.escape(k[7:].lower().replace('_', '-'))}(?![\w-])", listed.replace("_", "-")))}
    if not fresh:
        return 0
    looked = False
    tp = d.get("transcript_path")
    if tp and os.path.isfile(tp):
        try:
            looked = any(_cs.is_external_lookup(c["name"], c["input"]) for t in _cs.last_turns(tp, WINDOW) for c in t["calls"])
        except OSError:
            looked = False
    try:
        cap = max(0, int(os.environ.get("COMMON_SENSE_MAX_NUDGE") or 8))
    except ValueError:
        cap = 8
    n = int(st.get("n") or 0)
    say = (not looked) and n < cap
    _cs.save(sid, "memory", {"known": (known + list(fresh))[-400:], "n": n + (1 if say else 0)})
    if say:
        names = "、".join(list(fresh.values())[:3]) + (" ほか" if len(fresh) > 3 else "")
        _cs.emit("PostToolUse", f"記憶で書いている（{names}）。版と仕様を一次情報で確かめる。入っている版（--help、--version、pip show、npm view）か"
                                "公式の文書（Context7、WebFetch）を見て、確かめた版か日付を書く。記憶と違ったら記憶を捨てる（common-sense）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 知らせるだけのフックなので、壊れても書き込みの結果を止めない
        sys.exit(0)
