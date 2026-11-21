#!/usr/bin/env python3
"""見せかけの形を機械で拾って一覧にする（出来ているように見せるだけの実装とテスト、握りつぶし、弱めた検査）。

ユーザーの指摘: コーディングが出来ない。出来ているように見せるだけ。バグも放置。わかっていてわざと壊す。
読んで気づく形に頼ると、書いた本人は気づかない。置き場のままの実装、握りつぶし、偽のデータ、テストの見せかけ、
配線されていない物、弱めた検査を、形で拾う。形の表は references/patterns.md。

使い方:
  fake_scan.py [root]                リポジトリ全体（追跡しているファイル）を見る
  fake_scan.py [root] --diff <base>  base との分かれ目（git merge-base）から後に足した行と消した行だけを見る。
                                     base が進んだ分は、自分の変更に数えない
  fake_scan.py --stats [--days 7]    フックと検査が拾った数を、種類ごとに数える（週 1 回の skill-upkeep が読む）
  --json                             機械で読む形で出す
終了コード: 重大があれば 1
抑止: 行の末尾に `no-fake: ignore（理由）`。ファイルの先頭 4,000 文字以内に `no-fake: ignore-file（理由）`。
      消した物（行が残らない）は、コミットメッセージに `no-fake: ignore <パスか名前>（理由）` を 1 行書く
環境変数: NO_FAKE_STATE=dir  数の置き場（既定は ~/.claude/state/no-fake）

形の一覧と、偽の当たりを減らす考え（足した行だけを見る、動かしただけの行は足した行に数えない、abstract や Protocol の中身の無い関数は
見ない、条件つきの skip は見ない、規則を名指しした抑止は注意に留める）は、moelsaka01/honest-done と catfish-1234/proctor と
atoslins/dod-guard（どれも MIT License）を読んで取り込んだ。コードは取り込まず、Python は ast で書き直した（docs/SOURCES.md）。
"""

import argparse
import ast
import difflib
import io
import json
import os
import re
import subprocess
import sys
import time
import tokenize
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

PY = {".py", ".pyi"}
JS = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts"}
MARKUP = {".jsx", ".tsx", ".vue", ".svelte", ".html", ".htm"}
SH = {".sh", ".bash"}
YML = {".yml", ".yaml"}
CONF = {".json", ".toml", ".cfg", ".ini"}
SKIP_DIRS = {"node_modules", ".venv", "venv", "dist", "build", "vendor", "__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache",
             ".tox", ".next", "coverage", "site-packages", "third_party", "target", "fixtures", "testdata", "__fixtures__", "__snapshots__",
             "__mocks__", "worktrees"}
IGNORE_LINE = "no-fake: ignore"
IGNORE_FILE = "no-fake: ignore-file"
SEV_ORDER = {"重大": 0, "注意": 1}
MAX_BYTES = 1_000_000  # これより大きいファイルは、作られた物か資料なので見ない
NOTES = []             # 見なかった物。黙って落とさず、出力の最後に出す

# 検査の名前（中身が無い・いつも True なら、検査の見せかけ）
CHECK_NAME = re.compile(r"^_*(?:check|validate|verify|is_|has_|is(?=[A-Z])|has(?=[A-Z]))")
SUCCESS = re.compile(r"成功|完了しました|保存しました|送信しました|\bsuccess|\bsucceeded\b|\bsaved\b|\bsent\b|\bcompleted\b", re.I)
# 検査のコマンド。失敗を握りつぶすと、検査が無いのと同じになる
CHECK_CMD = (r"(?:pytest|unittest|tox|nox|ruff|flake8|mypy|pyright|pylint|bandit|semgrep|pip-audit|shellcheck|eslint|tsc|jest|vitest|mocha|playwright"
             r"|validate\.sh|precheck\.py|hooks-smoke\.py|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|lint|typecheck|check|build|audit)\b"
             r"|cargo\s+(?:test|clippy|check)|go\s+(?:test|vet)|make\s+(?:test|check|lint))")

# コメントの中の、置き場の語。テストのファイルでは見ない（stub や dummy は、テストでは普通の語）
PLACE_WORDS = re.compile(
    r"(?-i:\b(?:TODO|FIXME|XXX|HACK)\b)|\btodo\s*[:：]|仮実装|仮置き|仮の(?:実装|値|データ|処理|対応|もの|物)|[（(]仮[）)]|ダミー|\bdummy\b|\bplaceholder\b|\bstub(?:bed)?\b"
    r"|とりあえず|ひとまず|未実装|(?:あと|後)で(?:実装|直す|書く)|\bnot (?:yet )?implemented\b|\bto be implemented\b|\bfor now\b"
    r"|\bin a real (?:implementation|app|application|scenario|system|world)\b|本番では|\b(?:mock|fake|sample) data\b|サンプルデータ", re.I)
# 省略の置き場。本物のコードが消えて、コメントだけが残る
ELISION = re.compile(
    r"(?:\.\.\.|…)\s*(?:rest\b|existing\b|remaining\b|unchanged\b|same\b|previous\b|other \w+ (?:here|unchanged)|省略|以下|残り|既存|同じ|同様|中略|略)"
    r"|\brest of (?:the )?(?:code|file|implementation|function|class|methods?) (?:here|unchanged|remains|stays|as before|goes here|is the same|omitted)\b"
    r"|\b(?:existing|remaining|previous|original) (?:code|implementation|methods?|functions?) (?:here|unchanged|remains?|stays?|as before|goes here|omitted)\b"
    r"|\b(?:code|implementation) (?:remains|stays) (?:the same|unchanged)\b|\b(?:implementation|your code|logic) (?:goes )?here\b"
    r"|以下省略|以下同じ|以下同様|残りは(?:同じ|省略|そのまま)|既存のコード(?:は)?(?:そのまま|省略)|[（(](?:中略|省略|略)[）)]|ここに(?:実装|処理|コード)(?:を書く|が入る)", re.I)

# 検査を黙らせるコメント。(ID, 重さ, コメントの形, 正規表現, 何)。コメントの形は hash（# で始まる）か slash（// や /* */）。
# 規則を名指しした物は、理由が読めるので注意に留める（実際のリポジトリで偽の当たりが多かった、という honest-done の記録に合わせた）
SUPPRESS = [
    ("W03", "重大", "hash", re.compile(r"#\s*type:\s*ignore(?!\s*\[)"), "型の検査をまるごと黙らせる（# type: ignore）"),
    ("W03", "重大", "hash", re.compile(r"#\s*(?:ruff|flake8)\s*:\s*noqa(?!\s*:\s*[A-Za-z])|#\s*mypy:\s*ignore-errors|#\s*pylint:\s*(?:disable\s*=\s*all|skip-file)|#\s*pyright:\s*ignore(?!\s*\[)"),
     "ファイルの検査をまるごと黙らせる"),
    ("W03", "重大", "hash", re.compile(r"#\s*(?:noqa|NOQA)(?!\s*:\s*[A-Za-z])"), "lint をまるごと黙らせる（# noqa）"),
    ("W13", "注意", "hash", re.compile(r"#\s*type:\s*ignore\s*\[|#\s*(?:(?:ruff|flake8)\s*:\s*)?(?:noqa|NOQA)\s*:\s*[A-Za-z]|#\s*pylint:\s*disable\s*=\s*(?!all)|#\s*nosec\b"
                                      r"|#\s*pragma:\s*no\s*cover|#\s*pyright:\s*ignore\s*\[|#\s*shellcheck\s+disable="), "規則を名指しして検査を黙らせる"),
    ("W03", "重大", "slash", re.compile(r"@ts-nocheck|@ts-ignore"), "型の検査を黙らせる（@ts-ignore、@ts-nocheck）"),
    ("W03", "重大", "slash", re.compile(r"eslint-disable(?:-next-line|-line)?\s*(?:\*/|-->|$)"), "lint をまるごと黙らせる（eslint-disable）"),
    ("W13", "注意", "slash", re.compile(r"@ts-expect-error|eslint-disable(?:-next-line|-line)?\s+[\w@]|(?:istanbul|c8|v8)\s+ignore|//\s*nolint|biome-ignore"), "規則を名指しして検査を黙らせる"),
]

TEST_ENV_PY = re.compile(r"PYTEST_CURRENT_TEST|['\"](?:pytest|unittest|_pytest)['\"]\s+(?:not\s+)?in\s+sys\.modules|sys\.modules\.get\(\s*['\"](?:pytest|unittest)['\"]")
TEST_ENV_JS = re.compile(r"\bprocess\.env\.(?:JEST_WORKER_ID|VITEST|VITEST_WORKER_ID|PLAYWRIGHT_TEST)\b|\bimport\.meta\.(?:env\.)?(?:VITEST|vitest)\b")
NODE_ENV_TEST = re.compile(r"NODE_ENV\s*[!=]==?\s*['\"]test['\"]|['\"]test['\"]\s*[!=]==?\s*process\.env\.NODE_ENV")

JS_SKIP = re.compile(r"\b(?:it|test|describe|context|suite|specify)\.(?:skip|only|todo|fixme|failing)\b|\b(?:xit|xtest|xdescribe|fit|fdescribe)\s*\(")
JS_SKIP_COND = re.compile(r"\b(?:it|test|describe)\.(?:skip|fixme)\(\s*(?![\"'`])[^)]")  # 最初の引数が条件（playwright の test.skip(条件, 理由)）
JS_TAUTOLOGY = re.compile(
    r"\bexpect\(\s*(?:true|false|1|0|null|undefined|\"\"|''|``)\s*\)\s*\.\s*(?:not\s*\.\s*)?(?:toBe\w*|toEqual|toStrictEqual)\b"
    r"|\bassert(?:\.ok|\.isOk|\.isTrue)?\(\s*(?:true|1)\s*[,)]|\bassert\.(?:equal|strictEqual|deepEqual|deepStrictEqual)\(\s*([\w.$]+)\s*,\s*\1\s*[,)]"
    r"|\bexpect\(\s*([\w.$]+)\s*\)\s*\.\s*(?:toBe|toEqual|toStrictEqual)\(\s*\2\s*\)")
JS_ASSERTS = re.compile(r"\bexpect\w*\s*\(|\bassert\w*\b|\.should\b|\bshould\(|toThrow|\.rejects\b|\.resolves\b|\bverify\w*\(|\bcheck\w*\(|\bmust\w*\("
                        r"|\bt\.(?:is|not|true|false|truthy|falsy|deepEqual|throws|pass|fail|like|regex|snapshot)\b|\bcy\.|\bwaitFor\w*\(|\b(?:get|find)(?:All)?By\w+\(")
JS_CATCH_EMPTY = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}")
JS_CATCH_OPEN = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*$")
JS_PROMISE_SWALLOW = re.compile(r"\.catch\(\s*(?:(?:async\s*)?(?:\(\s*[\w$]*\s*\)|[\w$]+)\s*=>\s*(?:\{\s*\}|null|undefined|void 0|\(\s*\)|\[\s*\]|false)"
                                r"|function\s*\(\s*[\w$]*\s*\)\s*\{\s*\}|noop|_noop|\(\s*\)\s*=>\s*\{\s*\})\s*\)")
JS_NOT_IMPL = re.compile(r"throw\s+new\s+\w*Error\(\s*['\"`][^'\"`\n]*(?:not (?:yet )?implemented|unimplemented|\bTODO\b|未実装)", re.I)
JS_MOCK = re.compile(r"\b(?:jest|vi)\.(?:mock|doMock)\(\s*['\"`]([^'\"`]+)['\"`]")
JS_EMPTY_HANDLER = re.compile(r"\bon[A-Z]\w*=\{\s*(?:\(\s*\w*\s*\)\s*=>\s*\{\s*\}|undefined|null|\(\s*\)\s*=>\s*(?:null|undefined|void 0))\s*\}"
                              r"|\bonclick=(?:\"\"|'')|[@:]click=(?:\"\"|'')|\bon:click=\{\s*\(\s*\)\s*=>\s*\{\s*\}\s*\}")

PY_TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\(")
JS_TEST_DEF = re.compile(r"\b(?:it|test)(?:\.\w+)*\s*\(\s*(['\"`])(.+?)\1")
ASSERT_LINE = re.compile(r"\bassert\w*\b|\bexpect\s*\(|\.should\b|\bt\.(?:Error|Errorf|Fatal|Fatalf|Fail)\b|\brequire\.\w+\(")
SNAPSHOT = re.compile(r"(?:^|/)__snapshots__/|\.snap$|\.ambr$|(?:^|/)snapshots?/")
THRESH_KEY = re.compile(r"(fail_under|fail-under|cov-fail-under|coverageThreshold|min(?:imum)?[_-]?coverage|\b(?:branches|functions|lines|statements)\b|threshold|max-warnings|maxWarnings)", re.I)
HOOK_REG = re.compile(r"\"command\"\s*:\s*\"(.+)\"\s*,?\s*$")
HOOK_SCRIPT = re.compile(r"[\w-][\w.-]*\.[A-Za-z]{1,5}\b")  # フックの登録の中の、ファイルの名前（スクリプトと、その場で読むファイル）
ROUTER_DEF = re.compile(r"^(\w+)\s*=\s*(?:\w+\.)?(?:Blueprint|APIRouter)\(|\b(?:const|let|var)\s+(\w+)\s*=\s*(?:express\.)?Router\(\)", re.M)
ROUTER_USE = r"(?:register_blueprint|include_router|\.use|\.route|mount)\([^)\n]*\b{name}\b"
ENTRY_NAMES = {"main", "setup", "teardown", "handler", "lambda_handler", "default", "App", "Page", "Layout", "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD",
               "OPTIONS", "getServerSideProps", "getStaticProps", "getStaticPaths", "generateMetadata", "generateStaticParams", "loader", "action",
               "middleware", "config", "metadata", "activate", "deactivate", "setUp", "tearDown", "setUpModule", "tearDownModule", "run", "cli", "app"}
ENTRY_FILES = re.compile(r"(?:^|/)(?:__init__|__main__|main|index|app|cli|setup|conftest|manage|wsgi|asgi|settings|config|preload|renderer|background|sw|worker)\.\w+$"
                         r"|\.config\.[cm]?[jt]s$|\.d\.ts$|(?:^|/)(?:migrations|bin|hooks|\.github|\.husky|public|static)/")
# 設定のファイルと、その中のキー。ハイフンのあるキーは、コードの中の名前と突き合わせられないので見ない
CONF_FILE = re.compile(r"(?:^|/)(?:\.env\.(?:example|sample|template)|(?:config|settings|defaults?)[\w.-]*\.(?:json|ya?ml|toml|ini)|[\w-]+\.config\.json)$")
CONF_KEY = re.compile(r"^[ \t]*(?:export\s+)?[\"']?([A-Za-z_]\w{3,})[\"']?\s*[:=]")
ROUTE_DIRS = re.compile(r"(?:^|/)(?:pages|app|routes)/")  # ファイルの場所がそのままルートになる枠組み（JS・TS だけ）


@dataclass
class Finding:
    sev: str            # 重大 / 注意
    path: str           # root からの相対パス
    line: int           # 0 は行の無い物（消した物、ファイル全体の話）
    id: str             # 形の ID（references/patterns.md）
    kind: str           # 種類（置き場、省略、偽の検査、握りつぶし、偽のデータ、テスト、配線、弱めた）
    what: str
    fix: str
    end: int = 0        # 形の終わりの行（関数の中身の終わり）。足した行との重なりを見るため
    key: str = ""       # 消した物の名前かパス（コミットメッセージの抑止と突き合わせる）
    new_only: bool = False  # 新しく足したときだけ意味のある形（リポジトリ全体の検査では出さない）

    def text(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else (self.path or "-")
        return f"{self.sev}  {loc}  {self.id} {self.kind}: {self.what}｜直し方: {self.fix}"

    def as_dict(self) -> dict:
        return {"sev": self.sev, "path": self.path, "line": self.line, "id": self.id, "kind": self.kind, "what": self.what, "fix": self.fix}


@dataclass
class FileDiff:
    path: str
    status: str = "M"                              # A 足した / M 変えた / D 消した / R 名前を変えた
    old_path: str = ""
    added: list = field(default_factory=list)      # (新しい行番号, 文, かたまりの番号)
    removed: list = field(default_factory=list)    # (古い行番号, 文, かたまりの番号)


# ---- 場所の見分け ----------------------------------------------------------
def norm(rel: str) -> str:
    return str(rel).replace("\\", "/")


def is_skipped(rel: str) -> bool:
    rel = norm(rel)
    parts = rel.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return True
    return rel.endswith((".min.js", ".min.css", ".lock", "-lock.json", "-lock.yaml")) or "no-fake/references/" in rel or "code-check/references/" in rel


def is_test(rel: str) -> bool:
    rel = norm(rel)
    parts, name = rel.split("/")[:-1], rel.split("/")[-1]
    if any(p in ("tests", "test", "__tests__", "spec", "specs", "e2e", "testing") for p in parts):
        return True
    return bool(re.match(r"test_.*\.py$|.*_test\.py$|.*\.(?:test|spec)\.[cm]?[jt]sx?$|conftest\.py$", name))


def ext_of(rel: str) -> str:
    return Path(norm(rel)).suffix.lower()


def is_shellish(rel: str) -> bool:
    rel = norm(rel)
    name = rel.split("/")[-1]
    return ext_of(rel) in SH | YML or name in ("Makefile", "package.json") or name.startswith("Dockerfile") or "/.husky/" in "/" + rel


def is_scanned(rel: str) -> bool:
    return ext_of(rel) in PY | JS | MARKUP | SH | YML | CONF or is_shellish(rel)


def file_ignored(text: str) -> bool:
    return IGNORE_FILE in text[:4000]


# ---- コメントとコードの切り分け ---------------------------------------------
def py_comments(text: str) -> dict:
    """行番号 → コメント。文字列の中の # はコメントではないので、tokenize で取る。"""
    out = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                out[tok.start[0]] = tok.string
    except (tokenize.TokenError, SyntaxError, ValueError):
        return out  # 構文が壊れたファイルは、読めた所までで見る（構文の誤りは code-check が言う）
    return out


def js_strip(text: str):
    """(文字列とコメントの中身を空白にした文, 行番号 → コメント)。長さと改行は元と同じなので、位置がそのまま使える。"""
    out, comments, i, n, line = [], {}, 0, len(text), 1
    blank = lambda s: re.sub(r"[^\n]", " ", s)  # noqa: E731 1 行の小さな置き換え
    while i < n:
        c, two = text[i], text[i:i + 2]
        if two == "//" and (i == 0 or text[i - 1] != ":"):
            j = text.find("\n", i)
            j = n if j < 0 else j
            comments[line] = comments.get(line, "") + text[i:j]
            out.append(" " * (j - i))
            i = j
        elif two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            seg = text[i:j]
            for k, part in enumerate(seg.split("\n")):
                comments[line + k] = comments.get(line + k, "") + part
            out.append(blank(seg))
            line += seg.count("\n")
            i = j
        elif c in "\"'`":
            j = i + 1
            while j < n and text[j] != c and not (text[j] == "\n" and c != "`"):
                j += 2 if text[j] == "\\" else 1
            closed = j < n and text[j] == c
            seg = text[i:j + 1] if closed else text[i:j]
            out.append(c + blank(seg[1:-1]) + c if closed else c + blank(seg[1:]))
            line += seg.count("\n")
            i += len(seg)
        else:
            if c == "\n":
                line += 1
            out.append(c)
            i += 1
    return "".join(out), comments


def hash_comments(text: str) -> dict:
    """シェルと YAML の、行番号 → コメント。引用の中の # は数えない（粗い切り分け）。"""
    out = {}
    for no, line in enumerate(text.splitlines(), 1):
        bare = re.sub(r"\"[^\"\n]*\"|'[^'\n]*'", lambda m: " " * len(m.group(0)), line)
        m = re.search(r"(?:^|\s)#(?!!)", bare)
        if m:
            out[no] = line[m.start():]
    return out


# ---- Python（ast）---------------------------------------------------------
def _unparse(node) -> str:
    try:
        return ast.unparse(node)
    except (ValueError, TypeError, AttributeError, RecursionError):
        return ""


def _is_const(node) -> bool:
    if node is None or isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp):
        return _is_const(node.operand)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_const(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(_is_const(k) for k in node.keys) and all(_is_const(v) for v in node.values)
    if isinstance(node, ast.JoinedStr):
        return not any(isinstance(v, ast.FormattedValue) for v in node.values)
    return False


def _call_name(node) -> str:
    return _unparse(node.func) if isinstance(node, ast.Call) else ""


def _is_hollow(stmt) -> bool:
    return isinstance(stmt, ast.Pass) or (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis)


def _body(fn) -> list:
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]  # docstring
    return body


def _strings(stmts) -> str:
    return " ".join(str(n.value) for s in stmts for n in ast.walk(s) if isinstance(n, ast.Constant) and isinstance(n.value, str))


def body_shape(fn):
    """関数の中身の形。stub / notimpl / const / sleep / success / None。const のときは返す値の文も返す。"""
    body = _body(fn)
    if all(_is_hollow(s) for s in body):
        return "stub", ""
    if len(body) == 1 and isinstance(body[0], ast.Raise) and "NotImplementedError" in _unparse(body[0].exc or ast.Pass()):
        return "notimpl", ""

    def is_sleep(s) -> bool:
        return isinstance(s, ast.Expr) and bool(re.search(r"(?:^|\.)sleep$", _call_name(s.value.value if isinstance(s.value, ast.Await) else s.value)))

    def is_print(s) -> bool:
        return isinstance(s, ast.Expr) and bool(re.search(r"^print$|^(?:logging|logger|log)\.(?:info|debug|warning)$", _call_name(s.value)))

    sleeps = [s for s in body if is_sleep(s)]
    prints = [s for s in body if is_print(s)]
    rest = [s for s in body if not is_sleep(s) and not is_print(s) and not _is_hollow(s)]
    returns = [s for s in rest if isinstance(s, ast.Return) and _is_const(s.value)]
    if len(rest) != len(returns) or len(returns) > 1:
        return None, ""
    value = _unparse(returns[0].value) if returns and returns[0].value is not None else ""
    if sleeps:
        return "sleep", value
    if prints and SUCCESS.search(_strings(prints) + " " + _strings(returns)):
        return "success", value
    if returns and not prints:
        return ("const", value) if value and value != "None" else ("stub", "")
    return None, ""


def _abstract(fn, cls) -> bool:
    """中身が無くてよい関数（abstract、overload、Protocol や ABC の中）。"""
    decos = " ".join(_unparse(d) for d in fn.decorator_list)
    if re.search(r"abstract|overload|override|\bprotocol\b", decos, re.I):
        return True
    if cls is not None:
        bases = " ".join(_unparse(b) for b in cls.bases + [k.value for k in cls.keywords])
        return bool(re.search(r"\b(?:Protocol|ABC|ABCMeta|Interface|TypedDict|NamedTuple)\b", bases))
    return False


def _has_assert(fn) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, (ast.Assert, ast.Raise)):
            return True
        if isinstance(n, ast.Call) and re.search(r"(?:^|\.)(?:assert\w*|fail\w*|raises|warns|expect\w*|should\w*|check\w*|verify\w*|validate\w*|ensure\w*|must\w*|approx|skipTest)$", _call_name(n)):
            return True
    return False


def _tautology(node) -> bool:
    if isinstance(node, ast.Assert):
        t = node.test
        if isinstance(t, ast.Constant) and t.value:
            return True
        if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], (ast.Eq, ast.Is)) and _unparse(t.left) == _unparse(t.comparators[0]):
            return True
    if isinstance(node, ast.Call):
        name, args = _call_name(node).split(".")[-1], node.args
        if name in ("assertTrue", "assert_", "assertIsNotNone") and args and isinstance(args[0], ast.Constant) and args[0].value:
            return True
        if name in ("assertEqual", "assertEquals", "assertIs") and len(args) >= 2 and _is_const(args[0]) and _unparse(args[0]) == _unparse(args[1]):
            return True
    return False


BRANCH = (ast.If, ast.Try, ast.ExceptHandler, ast.IfExp, ast.While, ast.For) + ((ast.Match,) if hasattr(ast, "Match") else ())


def scan_python(rel: str, text: str, test: bool) -> list:
    out = []
    comments = py_comments(text)
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        tree = None  # 構文の誤りは code-check が言う。ここではコメントだけを見る

    def add(sev, node, pid, kind, what, fix, end=0):
        out.append(Finding(sev, rel, node.lineno, pid, kind, what, fix, end=end or getattr(node, "end_lineno", None) or node.lineno))

    def mocked(call, fn):
        """テストの対象そのものを、モックで置き換えていないか。"""
        if not isinstance(call, ast.Call) or not re.search(r"(?:^|\.)patch(?:\.object)?$", _call_name(call)):
            return
        consts = [a.value for a in call.args[:2] if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        target = consts[-1].split(".")[-1] if consts else ""
        if len(target) > 3 and re.search(rf"^test_?{re.escape(target)}(?:_|$)", fn.name):
            add("注意", call, "T06", "テスト", f"テストの対象 {target} そのものをモックで置き換えている疑い（{fn.name}）", "対象は本物を呼び、外の物（通信、時計、ファイル）だけを置き換える", end=call.lineno)

    def function(fn, cls):
        shape, value = body_shape(fn)
        name = fn.name
        decos = " ".join(_unparse(d) for d in fn.decorator_list)
        if test:
            if not name.startswith("test"):
                return
            if shape == "stub":
                add("重大", fn, "T02", "テスト", f"中身の無いテスト {name}()", "何を確かめるテストかを書く。まだ書けないなら、テストを足さない")
            elif not _has_assert(fn):
                add("注意", fn, "T01", "テスト", f"assert の無いテスト {name}()", "結果を assert で確かめる。落ちないことだけを見るなら、そう名前に書く")
            for d in fn.decorator_list:
                if re.search(r"(?:^|\.)(?:skip|xfail)\b", _unparse(d)):  # 足した行で拾えるように、デコレータの行に付ける（def の行は前からある）
                    add("重大", d, "T03", "テスト", f"テストを飛ばしている（{name} の skip か xfail）", "飛ばさずに通す。飛ばす理由があるなら ❕ 弱めた: に書く", end=d.lineno)
                mocked(d, fn)
            return
        if shape is None or (name.startswith("__") and name.endswith("__")) or _abstract(fn, cls) or rel.endswith(".pyi"):
            return
        checker = bool(CHECK_NAME.search(name))
        params = [a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs if a.arg not in ("self", "cls")]
        if shape == "stub":
            if checker:
                add("重大", fn, "P04", "偽の検査", f"検査の名前なのに中身が無い {name}()", "検査する中身を書く。書けないなら、呼ぶ側が通ったと思わない形（例外）にする")
            else:
                add("注意", fn, "P01", "置き場", f"中身が pass か ... だけの関数 {name}()", "本物の処理を書く。まだ書かないなら、関数を足さない")
        elif shape == "notimpl":
            add("注意", fn, "P02", "置き場", f"NotImplementedError を投げるだけの関数 {name}()", "本物の処理を書く。意図した未実装なら、呼ぶ側に見える形で残す")
        elif shape == "const" and "property" not in decos:
            if checker and value == "True":
                add("重大", fn, "P04", "偽の検査", f"検査の名前なのに、いつも True を返す {name}()", "検査する中身を書く。見ないで True を返さない")
            elif params:
                add("注意", fn, "P03", "置き場", f"引数を使わずに固定の値（{value[:30]}）を返すだけの関数 {name}()", "本物の処理を書く。固定でよいなら、定数にする")
        elif shape == "sleep":
            add("注意", fn, "D01", "偽のデータ", f"sleep して固定の値を返すだけの関数 {name}()（処理の振り）", "本物の処理を書く")
        elif shape == "success":
            add("注意", fn, "D02", "偽のデータ", f"条件なしに成功を出す関数 {name}()", "結果を確かめてから出す。失敗の道を書く")

    def checks(node, test_fn, conditional):
        if isinstance(node, ast.ExceptHandler) and node.body and all(_is_hollow(s) for s in node.body):
            names = _unparse(node.type) if node.type is not None else ""
            end = node.body[-1].end_lineno or node.lineno
            head = ("except " + names).strip()
            if node.type is None or re.search(r"\b(?:Exception|BaseException)\b", names):
                add("重大", node, "S01", "握りつぶし", f"例外を握りつぶしている（{head}: pass）", "記録するか、捕まえる型を絞るか、投げ直す", end=end)
            elif not any(len(re.sub(r"#\s*(?:noqa|type:|pragma|pylint)\S*", "", comments.get(k, "")).strip("# \t")) > 1 for k in range(node.lineno, end + 1)):
                add("注意", node, "S02", "握りつぶし", f"例外を黙って捨てている（{head}: pass）", "捨ててよい理由を同じ行のコメントに書くか、記録する", end=end)
        elif isinstance(node, ast.With):
            for item in node.items:
                if re.search(r"(?:^|\.)suppress$", _call_name(item.context_expr)) and re.search(r"\b(?:Exception|BaseException)\b", _unparse(item.context_expr)):
                    add("重大", node, "S01", "握りつぶし", "例外を握りつぶしている（suppress(Exception)）", "捕まえる型を絞る", end=node.lineno)
                if test_fn is not None:
                    mocked(item.context_expr, test_fn)
        elif not test and (isinstance(node, (ast.Compare, ast.Subscript)) or re.search(r"environ\.get$|getenv$|modules\.get$", _call_name(node))):
            if TEST_ENV_PY.search(_unparse(node)):
                add("重大", node, "D03", "偽のデータ", "テストのときだけ別の道を通る分岐（PYTEST_CURRENT_TEST、sys.modules の pytest）", "テストと本番で同じ道を通す。差し替えは引数か設定で渡す", end=node.lineno)
        if not test:
            return
        if _tautology(node):
            add("重大", node, "T02", "テスト", f"いつも通る assert（{_unparse(node)[:40]}）", "結果を確かめる assert に直す", end=node.lineno)
        if test_fn is not None and not conditional and isinstance(node, ast.Expr) and re.search(r"(?:^|\.)(?:skipTest|skip)$", _call_name(node.value)):
            add("重大", node, "T03", "テスト", f"テストを条件なしに飛ばしている（{_call_name(node.value)}）", "飛ばさずに通す。飛ばす理由があるなら ❕ 弱めた: に書く", end=node.lineno)
        if isinstance(node, ast.Assign):
            target, value = _unparse(node.targets[0]), _unparse(node.value)
            if (target == "pytestmark" and re.search(r"\b(?:skip|xfail)\b", value)) or (target == "__test__" and value == "False") or target.startswith("collect_ignore"):
                add("重大", node, "T03", "テスト", f"テストをまとめて外している（{_unparse(node)[:50]}）", "外さずに通す。外す理由があるなら ❕ 弱めた: に書く", end=node.lineno)

    def visit(node, cls, test_fn, conditional):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function(child, cls)
                visit(child, None, child if test and child.name.startswith("test") else test_fn, False)
            elif isinstance(child, ast.ClassDef):
                visit(child, child, test_fn, False)
            else:
                checks(child, test_fn, conditional)
                visit(child, cls, test_fn, conditional or isinstance(child, BRANCH))

    if tree is not None:
        visit(tree, None, None, False)
    return out + comment_findings(rel, comments, test, hash_style=True)


# ---- コメントの中の語（どの言語も同じ）---------------------------------------
def comment_findings(rel: str, comments: dict, test: bool, hash_style: bool) -> list:
    out = []
    for no, c in sorted(comments.items()):
        if IGNORE_LINE in c:
            continue
        for pid, sev, style, rx, what in SUPPRESS:
            if (style == "hash") != hash_style:
                continue
            m = rx.search(c)
            if m:
                out.append(Finding(sev, rel, no, pid, "弱めた", f"{what}: {m.group(0).strip()[:30]}", "元の誤りを直す。残すなら規則を名指しして理由を書き、❕ 弱めた: に書く", new_only=(pid == "W13")))
                break
        if test:
            continue
        m = ELISION.search(c)
        if m:
            out.append(Finding("重大", rel, no, "P06", "省略", f"省略の置き場（{c.strip()[:40]}）。本物のコードが無い", "省略せずに、動くコードを全部書く"))
            continue
        m = PLACE_WORDS.search(c)
        if m:
            out.append(Finding("注意", rel, no, "P05", "置き場", f"置き場の語（{m.group(0)}）: {c.strip()[:40]}", "書き切るか、残す理由と残っている物を報告に書く"))
    return out


# ---- JavaScript・TypeScript（文字列とコメントを落としてから、形で見る）------------
JS_FUNC = re.compile(
    r"\bfunction\s*\*?\s*(?P<n1>[A-Za-z_$][\w$]*)\s*(?:<[^>(]*>)?\s*\((?P<a1>[^)]*)\)"
    r"|\b(?:const|let|var)\s+(?P<n2>[A-Za-z_$][\w$]*)\s*(?::[^=;\n]+?)?=\s*(?:async\s*)?(?:\((?P<a2>[^)]*)\)|(?P<a2b>[A-Za-z_$][\w$]*))\s*(?::[^=>{\n]+?)?=>"
    r"|^[ \t]*(?:(?:public|private|protected|static|async|override|readonly|export|get|set)\s+)*(?P<n3>[A-Za-z_$][\w$]*)\s*\((?P<a3>[^)]*)\)\s*(?::[^{;=\n]+?)?(?=\s*\{)", re.M)
JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "constructor", "super", "with", "else", "do", "try", "typeof", "await", "new"}
JS_CONST = {"true", "false", "null", "undefined", "NaN", "Infinity", "void", "as", "const"}


def _match_brace(s: str, i: int) -> int:
    """s[i] が { のとき、対応する } の位置。無ければ -1。"""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _js_const_expr(expr: str) -> bool:
    expr = re.sub(r"[A-Za-z_$][\w$]*\s*:", "", expr)  # オブジェクトのキー
    return bool(expr.strip()) and not (set(re.findall(r"[A-Za-z_$][\w$]*", expr)) - JS_CONST) and "(" not in expr.replace("({", "{").strip("()")


def js_body_shape(body: str, raw: str):
    """JS の関数の中身（文字列を落とした物と、元の文）の形。"""
    if not body.strip():
        return "stub", ""
    if JS_NOT_IMPL.search(raw):
        return ("notimpl", "") if len([s for s in re.split(r"[;\n]", body) if s.strip()]) <= 1 else (None, "")
    stmts = [s.strip() for s in re.split(r"[;\n]", body) if s.strip()]
    sleeps = [s for s in stmts if re.match(r"(?:await\s+)?(?:sleep|delay|wait|pause|timeout)\s*\(|await\s+new\s+Promise\s*\([^)]*setTimeout", s)]
    prints = [s for s in stmts if re.match(r"console\.(?:log|info|debug)\s*\(", s)]
    rest = [s for s in stmts if s not in sleeps and s not in prints]
    returns = [s for s in rest if re.match(r"return\b", s) and (s == "return" or _js_const_expr(s[6:]))]
    if len(rest) != len(returns) or len(returns) > 1:
        return None, ""
    value = returns[0][6:].strip() if returns else ""
    if sleeps:
        return "sleep", value
    if prints and SUCCESS.search(raw):
        return "success", value
    if returns and not prints:
        return ("const", value) if value and value not in ("undefined", "null") else ("stub", "")
    return None, ""


def scan_js(rel: str, text: str, test: bool) -> list:
    out = []
    stripped, comments = js_strip(text)
    lines, slines = text.splitlines(), stripped.splitlines()
    line_of = lambda pos: stripped.count("\n", 0, pos) + 1  # noqa: E731

    def add(sev, line, pid, kind, what, fix, end=0, **kw):
        out.append(Finding(sev, rel, line, pid, kind, what, fix, end=end or line, **kw))

    for m in JS_FUNC.finditer(stripped):
        name = m.group("n1") or m.group("n2") or m.group("n3")
        args = (m.group("a1") or m.group("a2") or m.group("a2b") or m.group("a3") or "").strip()
        if name in JS_KEYWORDS:
            continue
        rest = stripped[m.end():]
        block = re.match(r"\s*\{", rest) if m.group("n2") else re.match(r"\s*(?::\s*[^{;=]+?)?\s*\{", rest)
        if block:
            start = m.end() + block.end() - 1
            close = _match_brace(stripped, start)
            if close < 0:
                continue
            shape, value = js_body_shape(stripped[start + 1:close], text[start + 1:close])
            end = line_of(close)
        elif m.group("n2"):
            stop = re.search(r";|\n(?![ \t]*[.?:&|+])", rest)
            expr = rest[:stop.start()] if stop else rest
            shape, value = ("const", expr.strip()) if _js_const_expr(expr) and expr.strip() not in ("null", "undefined") else (None, "")
            end = line_of(m.end() + len(expr))
        else:
            continue
        line = line_of(m.start() + (len(m.group(0)) - len(m.group(0).lstrip())))
        if shape is None or test or name in ("noop", "_noop", "constructor") or rel.endswith(".d.ts"):
            continue
        checker = bool(CHECK_NAME.search(name))
        if shape == "stub":
            if checker:
                add("重大", line, "P04", "偽の検査", f"検査の名前なのに中身が無い {name}()", "検査する中身を書く", end)
            else:
                add("注意", line, "P01", "置き場", f"中身の無い関数 {name}()", "本物の処理を書く。まだ書かないなら、関数を足さない", end)
        elif shape == "notimpl":
            add("注意", line, "P02", "置き場", f"not implemented を投げるだけの関数 {name}()", "本物の処理を書く", end)
        elif shape == "const":
            if checker and value.strip("() ") == "true":
                add("重大", line, "P04", "偽の検査", f"検査の名前なのに、いつも true を返す {name}()", "検査する中身を書く。見ないで true を返さない", end)
            elif args and not re.match(r"^[A-Z]", name):
                add("注意", line, "P03", "置き場", f"引数を使わずに固定の値を返すだけの関数 {name}()", "本物の処理を書く。固定でよいなら、定数にする", end)
        elif shape == "sleep":
            add("注意", line, "D01", "偽のデータ", f"待ってから固定の値を返すだけの関数 {name}()（処理の振り）", "本物の処理を書く", end)
        elif shape == "success":
            add("注意", line, "D02", "偽のデータ", f"条件なしに成功を出す関数 {name}()", "結果を確かめてから出す", end)

    subject = re.sub(r"\.(?:test|spec)$", "", Path(norm(rel)).stem)
    for no, (raw, s) in enumerate(zip(lines, slines), 1):
        if IGNORE_LINE in raw:
            continue
        nxt = slines[no].strip() if no < len(slines) else ""
        explained = no in comments  # 同じ行にコメントがあれば、捨てる理由が書いてあると見る（中のコメントは、文字列と一緒に空白にしてある）
        if (JS_CATCH_EMPTY.search(s) and not explained) or (JS_CATCH_OPEN.search(s) and nxt == "}" and (no + 1) not in comments and not explained):
            add("重大", no, "S03", "握りつぶし", "例外を握りつぶしている（中身の無い catch）", "記録するか、呼び元に返す。捨ててよいなら理由をコメントに書く", no + 1)
        elif JS_PROMISE_SWALLOW.search(s) and not explained:
            add("重大", no, "S03", "握りつぶし", "失敗を握りつぶしている（.catch(() => {})）", "記録するか、呼び元に返す")
        if not test:
            m = TEST_ENV_JS.search(s)
            if m:
                add("重大", no, "D03", "偽のデータ", f"テストのときだけ別の道を通る分岐（{m.group(0)}）", "テストと本番で同じ道を通す。差し替えは引数か設定で渡す")
            else:
                m = NODE_ENV_TEST.search(raw)
                if m and "NODE_ENV" in s:
                    add("注意", no, "D04", "偽のデータ", "NODE_ENV が test のときだけ別の道を通る分岐", "テストと本番で同じ道を通す")
            continue
        m = JS_SKIP.search(s)
        if m and not JS_SKIP_COND.search(s):
            add("重大", no, "T03", "テスト", f"テストを飛ばす・絞る印（{m.group(0).rstrip('( ')}）", "飛ばさずに通す。only は外す。飛ばす理由があるなら ❕ 弱めた: に書く")
        m = JS_TAUTOLOGY.search(s)
        if m:
            add("重大", no, "T02", "テスト", f"いつも通る assert（{raw.strip()[:40]}）", "結果を確かめる assert に直す")
        m = JS_MOCK.search(raw)
        if m and "mock" in s and Path(m.group(1)).stem == subject and len(subject) > 2:
            add("注意", no, "T06", "テスト", f"テストの対象 {subject} そのものをモックで置き換えている疑い", "対象は本物を呼び、外の物だけを置き換える")
    if test:
        for m in re.finditer(r"\b(?:it|test)(?:\.each\([^)]*\))?\s*\(\s*([\"'`])", stripped):
            head = re.search(r"(?:=>|\bfunction\s*\([^)]*\))\s*\{", stripped[m.end():m.end() + 400])
            if not head:
                continue
            start = m.end() + head.end() - 1
            close = _match_brace(stripped, start)
            if close < 0 or (IGNORE_LINE in lines[line_of(m.start()) - 1]):
                continue
            body = stripped[start + 1:close]
            if not body.strip():
                add("重大", line_of(m.start()), "T02", "テスト", "中身の無いテスト", "何を確かめるテストかを書く。まだ書けないなら、テストを足さない", line_of(close))
            elif not JS_ASSERTS.search(body):
                add("注意", line_of(m.start()), "T01", "テスト", "expect の無いテスト", "結果を expect で確かめる", line_of(close))
    out += comment_findings(rel, comments, test, hash_style=False)
    if ext_of(rel) in MARKUP:
        out += scan_markup(rel, text)
    return out


def scan_markup(rel: str, text: str) -> list:
    """ハンドラの無いボタンと、中身の無いハンドラ。"""
    out = []
    html = ext_of(rel) in (".html", ".htm")
    for m in re.finditer(r"<(?:button|Button)\b", text):
        i, depth, quote = m.end(), 0, ""
        while i < len(text):
            c = text[i]
            if quote:
                quote = "" if c == quote else quote
            elif c in "\"'":
                quote = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == ">" and depth <= 0 and text[i - 1] != "=":
                break
            i += 1
        attrs = text[m.end():i]
        before = text[max(0, m.start() - 4000):m.start()]
        eol = text.find("\n", i)
        if IGNORE_LINE in text[m.start():eol if eol > 0 else len(text)]:
            continue
        wired = re.search(r"\bon[A-Z]\w*\s*=|\bonclick\s*=|[@:]click|\bon:click|v-on:|\(click\)|\btype\s*=\s*[\"']?(?:submit|reset)|\{\s*\.\.\.|\basChild\b|\bform\s*=|\bdisabled\b"
                          r"|\bhref\s*=|\bto\s*=|popovertarget|commandfor|data-bs-|data-action|hx-|x-on:|formAction|\bas\s*=", attrs)
        # form の中のボタンは送信のボタン。Trigger や Link に包まれたボタンは、外側が押したときの処理を持つ
        wired = wired or before.rfind("<form") > before.rfind("</form") or re.search(r"<\w*(?:Trigger|Close|Link|Slot|Cancel|Action)\b[^<]*>\s*$", before)
        if html:
            wired = wired or re.search(r"\b(?:id|class|name|data-[\w-]+)\s*=", attrs)  # HTML は、id や class からスクリプトがつなぐ
        if not wired:
            out.append(Finding("注意", rel, text.count("\n", 0, m.start()) + 1, "U02", "配線", "ハンドラの無いボタン（押しても何も起きない）", "押したときの処理をつなぐ。まだ無いなら、ボタンを出さない"))
    for no, raw in enumerate(text.splitlines(), 1):
        if JS_EMPTY_HANDLER.search(raw) and IGNORE_LINE not in raw:
            out.append(Finding("注意", rel, no, "U03", "配線", "中身の無いハンドラ（押しても何も起きない）", "押したときの処理を書く"))
    return out


# ---- シェル、ワークフロー、設定 ------------------------------------------------
def scan_shellish(rel: str, text: str) -> list:
    out = []
    comments = hash_comments(text) if ext_of(rel) in SH | YML or rel.split("/")[-1] == "Makefile" else {}
    for no, raw in enumerate(text.splitlines(), 1):
        code = raw[:len(raw) - len(comments[no])] if no in comments and raw.endswith(comments[no]) else raw
        if IGNORE_LINE in raw or not code.strip():
            continue
        forced = re.search(r"\|\|\s*(?:true|:|exit\s+0|echo\b[^|&;{]*)(?=\s*(?:$|[\"';)&|]))|;\s*(?:true|exit\s+0)\s*(?:$|[\"'])", code)
        if forced and re.search(CHECK_CMD, code[:forced.start()]):
            out.append(Finding("重大", rel, no, "W06", "弱めた", f"検査の失敗を握りつぶしている（{code.strip()[:50]}）", "失敗を失敗のまま返す。握りつぶす理由があるなら ❕ 弱めた: に書く"))
        elif re.search(r"\|\|\s*(?:true|:)(?=\s*(?:$|[\"';)&|]))", code):
            out.append(Finding("注意", rel, no, "S04", "握りつぶし", f"失敗を握りつぶしている（|| true）: {code.strip()[:40]}", "失敗してよい理由をコメントに書くか、失敗を扱う", new_only=True))
        elif re.search(r"(?:2|&)>\s*/dev/null|>\s*/dev/null\s+2>&1", code):
            out.append(Finding("注意", rel, no, "S05", "握りつぶし", f"エラーの出力を捨てている（2>/dev/null）: {code.strip()[:40]}", "捨ててよい理由をコメントに書くか、出力を残す", new_only=True))
        if re.search(r"\bgit\s+(?:commit|push)\b[^\n|;&]*--no-verify", code):
            out.append(Finding("重大", rel, no, "W01", "弱めた", "フックの検査を飛ばしている（--no-verify）", "フックを通す。飛ばす理由があるなら ❕ 弱めた: に書く"))
        if ext_of(rel) in YML and re.search(r"^\s*continue-on-error\s*:\s*true\b", code):
            out.append(Finding("重大", rel, no, "W02", "弱めた", "失敗しても先へ進む設定（continue-on-error: true）", "失敗で止める。進める理由があるなら ❕ 弱めた: に書く"))
        if rel.split("/")[-1] == "package.json" and re.search(r"\"(?:test|lint|typecheck|check)\"\s*:\s*\"(?:echo\b[^\"]*|true|exit 0|:)?\"", code):
            out.append(Finding("重大", rel, no, "W06", "弱めた", f"何も確かめない検査の台本（{code.strip()[:50]}）", "本物の検査を呼ぶ"))
    return out + comment_findings(rel, comments, True, hash_style=True)


def scan_conf(rel: str, text: str) -> list:
    out = []
    name = rel.split("/")[-1]
    for no, raw in enumerate(text.splitlines(), 1):
        if IGNORE_LINE in raw:
            continue
        if re.match(r"tsconfig.*\.json$", name) and re.search(r"\"(?:strict|noImplicitAny|strictNullChecks)\"\s*:\s*false", raw):
            out.append(Finding("重大", rel, no, "W07", "弱めた", f"型の検査を緩めている（{raw.strip()[:40]}）", "型の誤りを直す。緩める理由があるなら ❕ 弱めた: に書く", new_only=True))
        elif re.search(r"^\s*ignore_errors\s*=\s*[Tt]rue", raw):
            out.append(Finding("重大", rel, no, "W07", "弱めた", "型の検査を黙らせている（ignore_errors = true）", "型の誤りを直す", new_only=True))
        elif re.search(r"(?:addopts|testpaths|args)\b.*(?:--ignore[= ]|--deselect[= ]|-k\s+[\"']?not\b)", raw):
            out.append(Finding("重大", rel, no, "W08", "弱めた", f"テストを設定で外している（{raw.strip()[:50]}）", "外さずに通す", new_only=True))
    return out


def scan_text(rel: str, text: str, test=None) -> list:
    """1 ファイルの中身を、形で見る。"""
    rel = norm(rel)
    if is_skipped(rel) or file_ignored(text) or len(text) > MAX_BYTES:
        return []
    test = is_test(rel) if test is None else test
    ext = ext_of(rel)
    out = []
    if ext in PY:
        out += scan_python(rel, text, test)
    elif ext in JS or ext in (".vue", ".svelte"):
        out += scan_js(rel, text, test)
    elif ext in (".html", ".htm"):
        out += scan_markup(rel, text)
    if is_shellish(rel):
        out += scan_shellish(rel, text)
    if ext in CONF:
        out += scan_conf(rel, text)
    lines = text.splitlines()
    # 抑止の印は、形の始めから終わりまでのどの行にあってもよい
    return [f for f in out if not any(IGNORE_LINE in lines[k - 1] for k in range(max(f.line, 1), min(max(f.end, f.line), len(lines)) + 1))]


# ---- 差分 -----------------------------------------------------------------
def diff_texts(path: str, old, new) -> FileDiff:
    """前の中身と今の中身から、足した行と消した行を作る（フックが使う。git が無くても動く）。"""
    fd = FileDiff(norm(path), "A" if old is None else ("D" if new is None else "M"))
    a, b = (old or "").splitlines(), (new or "").splitlines()
    for hunk, (tag, i1, i2, j1, j2) in enumerate(difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()):
        if tag == "equal":
            continue
        fd.removed += [(i + 1, a[i], hunk) for i in range(i1, i2)]
        fd.added += [(j + 1, b[j], hunk) for j in range(j1, j2)]
    return fd


def parse_diff(text: str) -> list:
    files, cur, new_ln, old_ln, hunk = [], None, 0, 0, 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r'diff --git "?a/(.*?)"? "?b/(.*?)"?$', line)
            cur = FileDiff(m.group(2) if m else line.split(" b/")[-1], old_path=m.group(1) if m else "")
            files.append(cur)
        elif cur is None:
            continue
        elif line.startswith("new file mode"):
            cur.status = "A"
        elif line.startswith("deleted file mode"):
            cur.status = "D"
        elif line.startswith("rename from "):
            cur.status = "R"
        elif line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if m:
                old_ln, new_ln, hunk = int(m.group(1)), int(m.group(2)), hunk + 1
        elif line.startswith(("+++", "---")):
            continue
        elif line.startswith("+"):
            cur.added.append((new_ln, line[1:], hunk))
            new_ln += 1
        elif line.startswith("-"):
            cur.removed.append((old_ln, line[1:], hunk))
            old_ln += 1
    return files


def git(root, *args, timeout=120):
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=str(root), capture_output=True, text=True, timeout=timeout, errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return r.returncode, r.stdout


def merge_base(root, base: str) -> str:
    """base との分かれ目。base が進んだ分を、自分の変更と数えないため。分かれ目が取れなければ base のまま。"""
    code, out = git(root, "merge-base", base, "HEAD")
    return out.strip() if code == 0 and out.strip() else base


def git_diffs(root, base: str) -> list:
    """分かれ目から今の作業ツリーまでの差分（コミット済みと、まだコミットしていない物と、追跡していない新しいファイル）。"""
    code, out = git(root, "diff", "-U0", "-M", "--no-color", "--no-ext-diff", merge_base(root, base))
    files = parse_diff(out) if code == 0 else []
    code, out = git(root, "ls-files", "-z", "--others", "--exclude-standard")
    for rel in [p for p in out.split("\0") if p] if code == 0 else []:
        text = read_text(Path(root) / rel)
        if text is not None and not is_skipped(rel):
            files.append(FileDiff(rel, "A", added=[(i, l, 0) for i, l in enumerate(text.splitlines(), 1)]))
    return files


def commit_ignores(root, base: str) -> set:
    code, out = git(root, "log", f"{merge_base(root, base)}..HEAD", "--format=%B")
    return set(re.findall(r"no-fake:\s*ignore\s+([^\s（(]+)", out)) if code == 0 else set()


def read_text(path: Path):
    try:
        b = path.read_bytes()
    except OSError:
        return None
    if b"\0" in b[:4096] or len(b) > MAX_BYTES:
        return None
    return b.decode("utf-8", errors="replace")


def repo_root(path) -> Path:
    """ファイルのあるリポジトリの根（.git のある所まで上がる）。フックが、worktree の中のファイルもその根からの相対で見るため。無ければ None。"""
    p = Path(path).resolve()
    for d in [p] + list(p.parents) if p.is_dir() else list(p.parents):
        if (d / ".git").exists():
            return d
    return None


_HOME = {}


def skills_home(root) -> bool:
    """このリポジトリが、スキルの元か（scripts/validate.sh がスキルの検査をしている）。"""
    key = str(root)
    if key not in _HOME:
        try:
            head = (Path(root) / "scripts" / "validate.sh").read_text(encoding="utf-8", errors="replace")[:400]
        except OSError:
            head = ""
        _HOME[key] = "skill" in head.lower()
    return _HOME[key]


def is_vendored(root, rel: str) -> bool:
    """配った先の .claude/skills/ の中か。そこにあるのは元のリポジトリのコピーなので、見ない。
    コピーの中の形で、配った先の検査と返答を止めないため（直すのは元のリポジトリ。配った先の全部で同じ検査が落ちるのを避ける）。"""
    return norm(rel).startswith(".claude/skills/") and root is not None and not skills_home(root)


def rel_to_root(path) -> str:
    """リポジトリの根からの相対パス。リポジトリの外のファイル（一時の場所の物）は空文字。"""
    root = repo_root(path)
    if root is None:
        return ""
    try:
        return norm(Path(path).resolve().relative_to(root))
    except ValueError:
        return ""


def _skeleton(line: str) -> str:
    return re.sub(r"\"[^\"]*\"|'[^']*'|\b\d+(?:\.\d+)?\b|\b(?:True|False|None|true|false|null)\b", "§", line.strip())


def scan_removed(diffs: list) -> list:
    """消した行から分かる形（テストを消した、assert を消した、期待値の書き換え、しきい値、フックと CI の登録）。"""
    out = []
    readded = {t.strip() for fd in diffs for _, t, _ in fd.added if len(t.strip()) > 8}
    readded_bare = {t.rstrip(",") for t in readded}
    added_tests = {m.group(1) for fd in diffs for _, t, _ in fd.added for m in [PY_TEST_DEF.match(t)] if m}
    added_tests |= {m.group(2) for fd in diffs for _, t, _ in fd.added for m in [JS_TEST_DEF.search(t)] if m}
    snaps = [fd.path for fd in diffs if SNAPSHOT.search(fd.path) and fd.status in ("M", "D")]
    if snaps:
        out.append(Finding("注意", snaps[0], 0, "T07", "テスト", f"スナップショットを {len(snaps)} 件書き換えた", "1 件ずつ差分を見て、意図した変化かを確かめる。まとめて更新しない", key=snaps[0]))
    for fd in diffs:
        rel, name = fd.path, fd.path.split("/")[-1]
        code = ext_of(rel) in PY | JS
        if is_test(rel) and code and not SNAPSHOT.search(rel):
            body = [t.strip() for _, t, _ in fd.removed if len(t.strip()) > 8]
            if fd.status == "D":
                if not body or sum(t in readded for t in body) < len(body) / 2:  # 半分以上が別の所に足されていれば、移しただけ
                    n = sum(1 for _, t, _ in fd.removed if ASSERT_LINE.search(t))
                    out.append(Finding("重大", rel, 0, "T04", "テスト", f"テストのファイルを消した（assert {n} 行）", "戻す。消す理由があるなら ❕ 消した: に書く", key=rel))
                continue
            hunks_with_new_test = {h for _, t, h in fd.added if PY_TEST_DEF.match(t) or JS_TEST_DEF.search(t)}
            gone = []
            for _, t, h in fd.removed:
                m = PY_TEST_DEF.match(t)
                tname = m.group(1) if m else (JS_TEST_DEF.search(t).group(2) if JS_TEST_DEF.search(t) else "")
                if tname and tname not in added_tests and h not in hunks_with_new_test:
                    gone.append(tname)
            if gone:
                out.append(Finding("重大", rel, 0, "T04", "テスト", f"テストを消した（{'、'.join(gone[:3])}{' ほか' if len(gone) > 3 else ''}）", "戻す。消す理由があるなら ❕ 消した: に書く", key=gone[0]))
            rem = [t for _, t, _ in fd.removed if ASSERT_LINE.search(t) and not t.lstrip().startswith(("#", "//")) and t.strip() not in readded]
            add = [t for _, t, _ in fd.added if ASSERT_LINE.search(t)]
            lost = len(rem) - len(add)
            if not gone and (lost >= 2 or (rem and not add)):
                out.append(Finding("重大", rel, 0, "T05", "テスト", f"assert を {lost} 行消した（例: {rem[0].strip()[:40]}）", "戻すか、同じ強さの assert に置き換える。理由は ❕ 弱めた: に書く", key=rel))
            by_hunk = {}
            for _, t, h in fd.removed:
                if ASSERT_LINE.search(t):
                    by_hunk.setdefault(h, ([], []))[0].append(t)
            for _, t, h in fd.added:
                if ASSERT_LINE.search(t) and h in by_hunk:
                    by_hunk[h][1].append(t)
            rewritten = [(r, a) for rs, as_ in by_hunk.values() for r in rs for a in as_ if r.strip() != a.strip() and _skeleton(r) == _skeleton(a) and "§" in _skeleton(r)]
            if rewritten:
                r, a = rewritten[0]
                out.append(Finding("注意", rel, 0, "T08", "テスト", f"期待値を書き換えた（{len(rewritten)} 行。例: {r.strip()[:30]} を {a.strip()[:30]} に）",
                                   "仕様が変わったのか、出力に合わせただけかを確かめ、理由を ❕ 弱めた: に書く", key=rel))
        pairs = {}
        for _, t, h in fd.removed:
            pairs.setdefault(h, ([], []))[0].append(t)
        for _, t, h in fd.added:
            pairs.setdefault(h, ([], []))[1].append(t)
        for rs, as_ in pairs.values():
            for r in rs:
                k = THRESH_KEY.search(r)
                rn = re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", r[k.end():]) if k else []
                for a in as_ if k and rn else []:
                    k2 = THRESH_KEY.search(a)
                    an = re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", a[k2.end():]) if k2 and k2.group(1).lower() == k.group(1).lower() else []
                    if not an:
                        continue
                    old, new = float(rn[0]), float(an[0])
                    looser = new > old if "warning" in k.group(1).lower() else new < old
                    if looser and IGNORE_LINE not in a:
                        out.append(Finding("重大", rel, 0, "W04", "弱めた", f"しきい値を緩めた（{k.group(1)}: {rn[0]} を {an[0]} に）", "戻す。緩める理由があるなら ❕ 弱めた: に書く", key=rel))
        if re.search(r"(?:^|/)settings(?:\.local)?\.json$", rel) or name == ".pre-commit-config.yaml":
            # 登録は、動かすスクリプトの名前で突き合わせる。書き方を変えただけ（起動役を通す形にした、並べ替えた）の登録は、消した物に数えない
            kept = {s for _, t, _ in fd.added for s in HOOK_SCRIPT.findall(t)}
            gone = []
            for _, t, _ in fd.removed:
                m = HOOK_REG.search(t) or re.search(r"^\s*-\s*id:\s*(\S+)", t)
                if not m or t.strip().rstrip(",") in readded_bare:
                    continue
                scripts = [s for s in HOOK_SCRIPT.findall(m.group(1)) if not s.endswith("run.sh")]
                if scripts and all(s in kept for s in scripts):
                    continue
                gone.append((scripts[-1] if scripts else m.group(1).replace('\\"', "").strip())[:40])
            if gone:
                out.append(Finding("重大", rel, 0, "W05", "弱めた", f"フックの登録を消した（{'、'.join(gone[:3])}）", "戻す。外すのはユーザーが決める", key=gone[0]))
        if fd.status == "D" and (re.search(r"(?:^|/)\.github/workflows/[^/]+\.ya?ml$|(?:^|/)\.husky/[^/._][^/]*$|(?:^|/)\.pre-commit-config\.yaml$", rel)):
            out.append(Finding("重大", rel, 0, "W05", "弱めた", "CI やフックのファイルを消した", "戻す。消すのはユーザーが決める", key=rel))
        elif re.search(r"(?:^|/)\.github/workflows/[^/]+\.ya?ml$", rel):
            still = " ".join(t for _, t, _ in fd.added)
            gone = [t.strip() for _, t, _ in fd.removed if re.search(CHECK_CMD, t) and t.strip() not in readded
                    and not any(tool in still for tool in re.findall(CHECK_CMD, t))]
            if gone:
                out.append(Finding("重大", rel, 0, "W05", "弱めた", f"CI から検査の段を消した（{gone[0][:40]}）", "戻す。外すのはユーザーが決める", key=rel))
    return out


# ---- 配線されていない物 -------------------------------------------------------
def _defs(rel: str, text: str) -> list:
    """(名前, 行) の一覧。いちばん外側の関数だけ（メソッドは、枠組みが呼ぶ物が多くて当たりが偽になる）。"""
    out = []
    if ext_of(rel) in PY:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            return out
        exported = _strings([n for n in tree.body if isinstance(n, ast.Assign) and "__all__" in _unparse(n.targets[0])])
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.decorator_list and n.name not in exported.split():
                out.append((n.name, n.lineno))
    elif ext_of(rel) in JS:
        stripped, _ = js_strip(text)
        for m in re.finditer(r"^(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)|^(?:export\s+)?(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*=>", stripped, re.M):
            if not re.match(r"export\s+default", stripped[m.start():m.start() + 20]):
                out.append((m.group(1) or m.group(2), stripped.count("\n", 0, m.start()) + 1))
    return [(n, l) for n, l in out if n not in ENTRY_NAMES and not n.startswith(("test", "Test", "__")) and len(n) > 2]


def scan_wiring(root: Path, files: list, only: dict = None) -> list:
    """定義しただけで、どこからも呼ばれない関数と、どこからも読まれない新しいファイルと、どこも読んでいない足した設定。
    only は {パス: 足した行の集合}（差分のとき）。"""
    root = Path(root)
    targets = [f for f in files if ext_of(f) in PY | JS and not is_test(f) and not is_skipped(f)]
    targets = [f for f in targets if only is None or f in only]
    cands, new_files, keys = {}, [], {}
    for rel in [f for f in files if only is not None and f in only and CONF_FILE.search("/" + norm(f)) and not norm(f).startswith(".claude/") and not is_skipped(f)]:
        text = read_text(root / rel)  # 足した設定のキー（差分のときだけ。前からあるキーは見ない）
        for no, line in enumerate((text or "").splitlines(), 1):
            m = CONF_KEY.match(line)
            if m and (only[rel] is None or no in only[rel]) and IGNORE_LINE not in line:
                keys.setdefault(m.group(1), (rel, no))
    for rel in targets:
        text = read_text(root / rel)
        if text is None or file_ignored(text):
            continue
        lines = text.splitlines()
        for name, line in _defs(rel, text):
            if (only is None or only[rel] is None or line in only[rel]) and IGNORE_LINE not in lines[line - 1]:
                cands.setdefault(name, []).append((rel, line))
        for m in ROUTER_DEF.finditer(text):
            name = m.group(1) or m.group(2)
            if not re.search(ROUTER_USE.format(name=re.escape(name)), text) and (only is None or only[rel] is None):
                new_files.append((rel, f"ルートの束 {name}", "U05"))
        runnable = text.startswith("#!") or "__main__" in text
        routed = ext_of(rel) in JS and ROUTE_DIRS.search("/" + rel)
        if only is not None and only[rel] is None and not ENTRY_FILES.search("/" + rel) and not runnable and not routed:
            new_files.append((rel, "新しいファイル", "U04"))
    if not cands and not new_files and not keys:
        return []
    code, out = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    tracked = [p for p in out.split("\0") if p and not is_skipped(p)] if code == 0 else list(files)
    if len(tracked) > 6000:
        NOTES.append(f"配線は数えていない（追跡しているファイルが {len(tracked)} 件で多い）")
        return []
    names, read_keys = Counter(), set()
    stems = {Path(rel).stem: 0 for rel, _, _ in new_files}
    word = re.compile(r"[A-Za-z_$][\w$]*")
    for rel in tracked:
        if ext_of(rel) in (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".woff", ".woff2", ".ttf", ".mp3", ".mp4", ".gguf"):
            continue
        text = read_text(root / rel)
        if text is None:
            continue
        if ext_of(rel) != ".md":
            names.update(w for w in word.findall(text) if w in cands)
        if keys and ext_of(rel) in PY | JS | MARKUP | SH:
            read_keys.update(k for k in keys if k not in read_keys and k in text)
        for stem in stems:
            if stem in text and norm(rel) not in [r for r, _, _ in new_files if Path(r).stem == stem]:
                stems[stem] += 1
    out = []
    for name, places in cands.items():
        if names[name] <= len(places):
            for rel, line in places:
                out.append(Finding("注意", rel, line, "U01", "配線", f"定義しただけで、どこからも呼ばれていない疑い {name}()", "呼ぶ所をつなぐ。使わないなら足さない"))
    for key, (rel, line) in keys.items():
        if key not in read_keys:
            out.append(Finding("注意", rel, line, "U06", "配線", f"足した設定 {key} を、コードのどこも読んでいない疑い", "読む所をつなぐ。使わないなら足さない"))
    for rel, what, pid in new_files:
        if stems.get(Path(rel).stem, 1) == 0:
            msg = "どこにも登録されていない疑い" if pid == "U05" else "どこからも読まれていない疑い"
            out.append(Finding("注意", rel, 0, pid, "配線", f"{what}が、{msg}（{Path(rel).name}）", "import か登録でつなぐ。使わないなら足さない", key=rel))
    return out


# ---- まとめ ---------------------------------------------------------------
def scan_diffs(diffs: list, reader, wiring_root=None, ignores=()) -> list:
    """差分の一覧から、足した行の形と、消した行の形を出す。reader はパスから今の中身を返す。"""
    out = []
    all_removed = {t.strip() for fd in diffs for _, t, _ in fd.removed if len(t.strip()) > 8}
    only = {}
    for fd in diffs:
        if fd.status == "D" or is_skipped(fd.path):
            continue
        text = reader(fd.path)
        if text is None:
            continue
        mine = {t.strip() for _, t, _ in fd.removed}
        added = {ln for ln, t, _ in fd.added if t.strip() and t.strip() not in mine and t.strip() not in all_removed}  # 動かしただけの行は、足した行に数えない
        only[fd.path] = None if fd.status == "A" else added
        if not is_scanned(fd.path):
            continue
        for f in scan_text(fd.path, text):
            if any(k in added for k in range(f.line, max(f.end, f.line) + 1)):
                out.append(f)
    out += scan_removed([fd for fd in diffs if not is_skipped(fd.path) or SNAPSHOT.search(fd.path)])
    if wiring_root is not None:
        out += scan_wiring(wiring_root, [p for p in only], only)
    return [f for f in out if not (f.key and (f.key in ignores or f.path in ignores))]


def scan_tree(root: Path) -> list:
    root = Path(root)
    code, out = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if code == 0:
        files = sorted(p for p in set(out.split("\0")) if p and (root / p).is_file())
    else:
        files = []
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            files += [norm(os.path.relpath(os.path.join(dp, f), root)) for f in fns]
        files.sort()
    files = [f for f in files if not is_skipped(f) and not is_vendored(root, f)]
    found = []
    for rel in files:
        if not is_scanned(rel):
            continue
        text = read_text(root / rel)
        if text is not None:
            found += [f for f in scan_text(rel, text) if not f.new_only]
    return found + scan_wiring(root, files)


def dedupe(findings: list) -> list:
    seen, out = set(), []
    for f in findings:
        k = (f.path, f.line, f.id, f.what)
        if k not in seen:
            seen.add(k)
            out.append(f)
    out.sort(key=lambda f: (SEV_ORDER.get(f.sev, 9), f.path, f.line))
    return out


def counts(findings) -> dict:
    return {k: sum(1 for f in findings if f.sev == k) for k in ("重大", "注意")}


# ---- 数の記録（常に改善するための数）--------------------------------------------
def state_dir() -> Path:
    base = os.environ.get("NO_FAKE_STATE")
    return Path(base) if base else Path.home() / ".claude" / "state" / "no-fake"


def record(src: str, findings, session: str = "", blocked=None) -> None:
    """拾った数を、種類ごとに 1 行ずつ足す。書けなくても止めない（数は目安）。"""
    kinds = Counter(findings) if isinstance(findings, dict) else Counter((f.kind, f.sev) for f in findings or [])
    if not any(kinds.values()):
        return
    try:
        root = state_dir()
        root.mkdir(parents=True, exist_ok=True)
        sid = re.sub(r"[^A-Za-z0-9_-]", "", session or "")[:40]
        with open(root / "metrics.jsonl", "a", encoding="utf-8") as f:
            for (kind, sev), n in sorted(kinds.items()):
                if not n:
                    continue
                row = {"ts": int(time.time()), "session": sid, "src": src, "kind": kind, "sev": sev, "n": n}
                if blocked is not None:
                    row["blocked"] = bool(blocked)
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return


def stats(days: int) -> int:
    path = state_dir() / "metrics.jsonl"
    since = time.time() - days * 86400
    table = Counter()
    try:
        rows = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        rows = []
    for line in rows:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("ts", 0) >= since:
            table[(r.get("src", "?"), r.get("kind", "?"), r.get("sev", "?"))] += int(r.get("n", 1))
            if r.get("blocked"):
                table[(r.get("src", "?"), r.get("kind", "?"), "差し戻し")] += 1
    print(f"== no-fake: この {days} 日に拾った数 ==")
    for (src, kind, sev), n in sorted(table.items()):
        print(f"  {src:6} {kind}（{sev}） {n}")
    print(f"拾った数: 合計 {sum(n for (_, _, s), n in table.items() if s != '差し戻し')} / 差し戻し {sum(n for (_, _, s), n in table.items() if s == '差し戻し')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--diff", metavar="BASE")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    if a.stats:
        return stats(a.days)
    root = Path(a.root).resolve()
    if a.diff:
        if git(root, "rev-parse", "--git-dir")[0] != 0:
            print("見せかけの確認: git のリポジトリではないので、差分は見ない")
            return 0
        diffs = [fd for fd in git_diffs(root, a.diff) if not is_vendored(root, fd.path)]
        found = scan_diffs(diffs, lambda rel: read_text(root / rel), wiring_root=root, ignores=commit_ignores(root, a.diff))
        label = f"差分（{a.diff} との分かれ目から）"
    else:
        found = scan_tree(root)
        label = "リポジトリ全体"
    found = dedupe(found)
    record("scan", found)
    c = counts(found)
    if a.json:
        print(json.dumps({"label": label, "findings": [f.as_dict() for f in found], "counts": c}, ensure_ascii=False, indent=1))
    else:
        print(f"== no-fake: {label} ==")
        for f in found:
            print("  " + f.text())
        if not found:
            print("  検出なし")
        for note in NOTES:
            print(f"  （{note}）")
        print(f"見せかけの確認: 重大 {c['重大']} / 注意 {c['注意']}")
    return 1 if c["重大"] else 0


if __name__ == "__main__":
    sys.exit(main())
