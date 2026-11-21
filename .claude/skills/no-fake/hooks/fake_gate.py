#!/usr/bin/env python3
"""Stop フック: このターンの編集に見せかけ・弱めた検査・消した物が残っているのに、返答に 1 行も無いとき、差し戻す。

ユーザーの指摘: コーディングが出来ない。出来ているように見せるだけ。バグも放置。わかっていてわざと壊す。
見るのは 4 つ。どれも、このターンの Edit・Write・MultiEdit の中身と、今のファイルを比べて決める（語ではなく、編集の中身で見る）。
  (a) 重大の形（握りつぶし、偽の検査、省略の置き場、テストのときだけの分岐、いつも通るテスト）を足して、今も残っている
      → 直すか、返答に「❕ 見せかけ: <何>（理由）」
  (b) テストを消した・弱めた（skip や only を足した、assert を減らした、期待値を書き換えた、スナップショットを更新した）、
      検査を弱めた（まるごとの抑止、--no-verify、continue-on-error、しきい値、フックの登録を消した）
      → 戻すか、返答に「❕ 弱めた: <何>（理由）」（テストを消したときは「❕ 消した:」でもよい）
  (c) 関数・クラス・export・ルート・ファイルを消した → 戻すか、返答に「❕ 消した: <何>（理由。頼まれた、または置き換え先）」
  (d) コードのファイルを変えたのに、変えたあとに 1 回も動かしていない（テスト、起動、スクリプトの実行）
      → 動かすか、返答に「動かしていない」と理由
偽の差し戻しを減らすことを先にする。足した行だけを見て、動かしただけの行と、ターンの途中で直した形は数えない。別のファイルに移した物は消した物に数えない。
ユーザーが置き場や削除を頼んだターン（一言に、骨組み・スタブ・あとで実装、消して・削除・置き換えて・リファクタ）は、その形では差し戻さない。
一時の場所（リポジトリの外）のファイルは見ない。Bash で書き換えた中身は見ない（precheck の fake_scan.py --diff が拾う）。

verify-before-done の done_check.py は、完了の語をツールの出力と照合する。reread_check.py は、編集のあとの読み直しか実行を求める。
このフックは、語ではなく編集の中身を見る。(d) は、コードのファイルに限って、読み直しではなく実行を求める（動かして出力を読めば、両方通る）。

人の発言・通知・差し戻しの見分けは、common-sense の _cs.py を使う。common-sense が無いリポジトリでは、黙って通す。
最後の返答は、入力の last_assistant_message を先に見る（会話の記録は遅れて書かれる）。stop_hook_active が真なら通す。
このターンの差分と会話の記録だけを証拠にする形は、moelsaka01/honest-done（MIT License）を参考にした。コードは取り込んでいない（docs/SOURCES.md）。

環境変数:
  NO_FAKE_OFF=1      止める
  NO_FAKE_STATE=dir  数の置き場（既定は ~/.claude/state/no-fake）
"""

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import fake_scan as fs  # noqa: E402
import removed as rm  # noqa: E402

MISSING = object()
EDIT_TOOLS = ("Edit", "Write", "MultiEdit")
RUN_EXT = fs.PY | fs.JS | {".vue", ".svelte", ".sh", ".bash", ".rs", ".go", ".rb", ".java", ".kt", ".swift", ".c", ".cc", ".cpp", ".cs", ".php"}
SHAM_IDS = {"P04", "P06", "S01", "S03", "D03", "T02"}
WEAK_IDS = {"T03", "T04", "T05", "T07", "T08", "W01", "W02", "W03", "W04", "W05", "W06", "W07", "W08"}
GONE_KINDS = {"関数", "クラス", "export", "ルート", "ファイル"}
SHAM_LINE = re.compile(r"^\s*[❕!]\s*見せかけ[:：]\s*\S", re.M)
WEAK_LINE = re.compile(r"^\s*[❕!]\s*(?:弱めた|消した)[:：]\s*\S", re.M)
GONE_LINE = re.compile(r"^\s*[❕!]\s*消した[:：]\s*\S", re.M)
NOT_RUN = re.compile(r"動かしていない|動かしていません|動かせていない|動かせない|動かせません|実行していない|実行していません|起動していない|起動していません"
                     r"|テストしていない|テストしていません|流していない|流していません|試していない|試していません|未実行")
# ユーザーが頼んだ形。頼まれた置き場と、頼まれた削除は差し戻さない
STUB_ASKED = re.compile(r"骨組み|スタブ|仮実装|プレースホルダ|ひな形|雛形|枠だけ|あとで実装|後で実装|\bstubs?\b|\bplaceholders?\b|\bskeleton\b|\bscaffold|\bTODO\b|not implemented|NotImplementedError", re.I)
GONE_ASKED = re.compile(r"消し|消す|消せ|削除|外し|外す|外せ|取り除|不要|要らない|いらない|やめ|置き換|書き直|作り直|名前を変|移し|移す|移動|統合|まとめ|整理|リファクタ"
                        r"|\b(?:remove|delete|drop|rename|refactor|replace|move|merge|clean ?up)\b", re.I)
# 動かした呼び出し（テスト、起動、スクリプトの実行）。lint と型の検査は、動かしたことに数えない
RUN = re.compile(
    r"\b(?:pytest|unittest|tox|nox|jest|vitest|mocha|ava|playwright|cypress|phpunit|rspec)\b"
    r"|\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+\S+|test|start|dev|exec)\b|\b(?:npx|bunx|pnpx|uvx)\s+\S+|\buv\s+run\b"
    r"|\b(?:python3?(?:\.\d+)?|node|deno|bun|ruby|php|tsx|ts-node)\s+(?!--version\b|-V\b|--help\b|--check\b|-m\s+(?:pip|venv|ensurepip|py_compile|compileall|json\.tool)\b)\S"
    r"|\b(?:bash|sh|zsh)\s+(?!-n\b)\S|(?:^|[;&|(]\s*)(?:\w+=\S+\s+)*\.{1,2}/[\w./-]+"
    r"|\b(?:cargo|go)\s+(?:run|test)\b|(?:^|[;&|(]\s*)make\b|\buvicorn\b|\bgunicorn\b|\bflask\s+run\b|\bmanage\.py\b|\belectron\b|\btauri\s+dev\b|\bdocker\s+(?:run|compose\s+up)\b"
    r"|\bcurl\b[^\n|;&]*(?:localhost|127\.0\.0\.1)|validate\.sh|precheck\.py|hooks-smoke\.py|\btest_\w+\.py\b")
RUN_TOOL = re.compile(r"^mcp__.*(?:browser|playwright|puppeteer|chrome|computer|preview|screenshot)|^(?:Agent|Task|Skill)$", re.I)
NO_VERIFY = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?(?:commit|push)\b[^\n|;&]*--no-verify")
SNAP_UPDATE = re.compile(r"\b(?:jest|vitest|playwright|pytest|npm\s+(?:run\s+)?test|pnpm\s+test|yarn\s+test)\b[^\n|;&]*(?:\s-u\b|--updateSnapshot|--update-snapshots|--snapshot-update|--update\b)")
RM = re.compile(r"(?:^|[;&|(]\s*)(?:git\s+(?:-C\s+\S+\s+)?rm|rm)\s+((?:-[\w-]+\s+)*)([^;&|\n]+)")
QUOTED = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'[^']*'")  # 引用の中の語（コミットの文の中の pytest など）は、動かしたことに数えない
ASK_LIMIT = 600  # これより長い一言（手順書や、別のセッションへの指示）は、頼まれた形の語を探さない。決まりの説明の中の語に当たるため
BLOCKED = re.compile(r"hook error|PreToolUse[^\n]{0,60}(?:blocked|denied|returned)|blocking hook|<tool_use_error>", re.I)


def load_cs():
    """common-sense の、人の発言の見分け。無いリポジトリでは None（黙って通す）。"""
    sys.path.insert(0, str(HERE.parent.parent / "common-sense" / "hooks"))
    try:
        import _cs
    except ImportError:
        return None
    return _cs


def tail_lines(path: str, tail):
    size = os.path.getsize(path)
    cut = bool(tail) and size > tail
    with open(path, "rb") as f:
        if cut:
            f.seek(size - tail)
            f.readline()  # 途中から読んだ最初の行は欠けている
        data = f.read()
    return data.decode("utf-8", errors="replace").splitlines(), cut


def parse_turn(lines, cs, sidechain=False) -> dict:
    """最後の人の発言から後ろを、1 ターンとして取り出す。編集の結果の originalFile（書く前の中身）も持つ。"""
    turn = {"prompt": "", "human": False, "calls": [], "results": {}, "texts": []}
    for line in lines:
        if '"user"' not in line and '"assistant"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or (e.get("isSidechain") and not sidechain):
            continue
        c = (e.get("message") or {}).get("content", [])
        if e.get("type") == "user":
            if cs.is_human(e):
                turn = {"prompt": cs.text_of(c), "human": True, "calls": [], "results": {}, "texts": []}
                continue
            extra = e.get("toolUseResult") if isinstance(e.get("toolUseResult"), dict) else {}
            for x in cs.blocks(c):
                if x.get("type") == "tool_result":
                    turn["results"][x.get("tool_use_id")] = {"error": bool(x.get("is_error")), "text": cs.result_text(x)[:2000],
                                                             "original": extra.get("originalFile", MISSING)}
        elif e.get("type") == "assistant":
            t = cs.text_of(c)
            if t.strip():
                turn["texts"].append(t)
            for x in cs.blocks(c):
                if x.get("type") == "tool_use":
                    inp = x.get("input")
                    turn["calls"].append({"id": x.get("id"), "name": str(x.get("name", "")), "input": inp if isinstance(inp, dict) else {}})
    return turn


def last_turn(path: str, cs) -> dict:
    turn = None
    for tail in (1 << 20, 8 << 20, None):
        lines, cut = tail_lines(path, tail)
        turn = parse_turn(lines, cs)
        if not cut or turn["human"]:
            break
    return turn


def done(turn: dict, call: dict) -> bool:
    r = turn["results"].get(call["id"])
    return bool(r) and not r["error"] and not BLOCKED.search(r["text"][:600])


def edits_of(turn: dict) -> dict:
    """このターンで成功した編集を、ファイルごとにまとめる。{絶対パス: {first, original, old, new}}"""
    files = {}
    for i, c in enumerate(turn["calls"]):
        if c["name"] not in EDIT_TOOLS or not done(turn, c):
            continue
        inp = c["input"]
        path = str(inp.get("file_path") or "")
        if not path:
            continue
        rec = files.setdefault(path, {"first": i, "original": turn["results"][c["id"]]["original"], "old": [], "new": [], "created": False})
        if c["name"] == "Write":
            rec["new"].append(str(inp.get("content") or ""))
            rec["created"] = rec["created"] or (rec["first"] == i)
        for e in (inp.get("edits") if isinstance(inp.get("edits"), list) else [inp]) if c["name"] != "Write" else []:
            if isinstance(e, dict):
                rec["old"].append(str(e.get("old_string") or ""))
                rec["new"].append(str(e.get("new_string") or ""))
    return files


def disk(path: str):
    """今のファイルの中身。(あるか, 中身)。大きすぎるか文字ではないファイルは、中身が None。"""
    if not os.path.isfile(path):
        return False, None
    return True, fs.read_text(Path(path))


def file_change(path: str, rec: dict, reader=disk):
    """(相対パス, 状態, 前の中身, 今の中身, 足した行と消した行)。見ない物は None。
    reader は、今の中身の読み方（実際の記録に当てるときは、そのターンの終わりの中身を渡す）。"""
    rel = fs.rel_to_root(path)
    if not rel or fs.is_skipped(rel) or fs.is_vendored(fs.repo_root(path), rel):
        return None
    exists, now = reader(path)
    if exists and now is None:
        return None  # 大きすぎるか、文字ではないファイル
    orig = rec["original"]
    if orig is None or isinstance(orig, str):  # 記録に書く前の中身がある（None は、このターンで作ったファイル）
        if orig is None and now is None:
            return None  # このターンで作って、このターンで消した
        return rel, ("A" if orig is None else "D" if now is None else "M"), orig, now, fs.diff_texts(rel, orig, now)
    # 書く前の中身が記録に無いとき。置き換えの前後の文から、足した行と消した行を作る
    if now is None:
        return None
    if rec["created"] and not rec["old"]:
        return rel, "A", None, now, fs.diff_texts(rel, None, now)
    now_lines = {l.strip() for l in now.splitlines()}
    old_lines = [l for t in rec["old"] for l in t.splitlines()]
    new_set = {l.strip() for t in rec["new"] for l in t.splitlines() if l.strip()} - {l.strip() for l in old_lines}
    fd = fs.FileDiff(rel, "M")
    fd.added = [(i, l, 0) for i, l in enumerate(now.splitlines(), 1) if l.strip() in new_set]
    fd.removed = [(0, l, 0) for l in old_lines if l.strip() and l.strip() not in now_lines]
    return rel, "M", "\n".join(rec["old"]), now, fd


def bash_deleted(turn: dict, cwd: str, created: set) -> list:
    """Bash の rm と git rm で消した、追跡していたコードとテストのファイル。"""
    out = []
    for c in turn["calls"]:
        if c["name"] != "Bash" or not done(turn, c):
            continue
        for m in RM.finditer(str(c["input"].get("command", ""))):
            for tok in m.group(2).split():
                tok = tok.strip("\"'")
                if tok.startswith("-") or fs.ext_of(tok) not in fs.PY | fs.JS | fs.MARKUP | fs.SH or "*" in tok:
                    continue
                path = tok if os.path.isabs(tok) else os.path.join(cwd, tok)
                if os.path.exists(path) or os.path.abspath(path) in created:
                    continue
                root = fs.repo_root(os.path.dirname(path))
                if root is None:
                    continue
                rel = fs.norm(os.path.relpath(path, root))
                code, log = fs.git(root, "log", "-1", "--format=%h", "--", rel, timeout=10)
                if code == 0 and log.strip() and not fs.is_skipped(rel):
                    out.append({"kind": "テスト" if fs.is_test(rel) else "ファイル", "path": rel, "name": rel})
    return out


def judge(turn: dict, reply: str, cwd: str, reader=disk):
    """(差し戻しの文の一覧, 数えた物)。"""
    files = edits_of(turn)
    changes, diffs, texts = [], [], {}
    for path, rec in files.items():
        got = file_change(path, rec, reader)
        if got is None:
            continue
        rel, status, old, now, fd = got
        if not fd.added and not fd.removed:
            continue  # 元に戻っている
        changes.append((rel, status, old, now, path, rec["first"]))
        diffs.append(fd)
        texts[rel] = now
    found = fs.dedupe(fs.scan_diffs(diffs, lambda rel: texts.get(rel))) if diffs else []
    gone = [i for i in rm.removed_items([(rel, status, old, now) for rel, status, old, now, _, _ in changes]) if i["kind"] in GONE_KINDS | {"テスト"}]
    gone += bash_deleted(turn, cwd, {os.path.abspath(p) for p, r in files.items() if r["created"]})
    commands = [QUOTED.sub('""', str(c["input"].get("command", ""))) for c in turn["calls"] if c["name"] == "Bash" and done(turn, c)]
    prompt = turn["prompt"] or ""

    asked = prompt if len(prompt) <= ASK_LIMIT else ""
    sham = [f for f in found if f.id in SHAM_IDS and f.sev == "重大" and not (f.id == "P04" and STUB_ASKED.search(asked))]
    weak = [f"{f.path} {f.what}" for f in found if f.id in WEAK_IDS]
    weak += [f"{i['path']} のテストを消した" for i in gone if i["kind"] == "テスト" and not any(i["path"] in w for w in weak)]
    weak += ["git の --no-verify" for cmd in commands if NO_VERIFY.search(cmd)][:1]
    weak += ["スナップショットの更新" for cmd in commands if SNAP_UPDATE.search(cmd)][:1]
    lost = [i for i in gone if i["kind"] in GONE_KINDS] if not GONE_ASKED.search(asked) else []

    code_edits = [(first, rel) for rel, _, _, _, path, first in changes if fs.ext_of(rel) in RUN_EXT]
    ran = True
    if code_edits:
        start = min(first for first, _ in code_edits)
        ran = any((c["name"] == "Bash" and RUN.search(QUOTED.sub('""', str(c["input"].get("command", "")))) and not BLOCKED.search((turn["results"].get(c["id"]) or {"text": ""})["text"][:600]))
                  or RUN_TOOL.search(c["name"]) for c in turn["calls"][start + 1:])

    reasons = []
    if sham and not SHAM_LINE.search(reply):
        f = sham[0]
        more = f"、ほか {len(sham) - 1} 件" if len(sham) > 1 else ""
        reasons.append(f"見せかけの形が残っている（{f.path}:{f.line} {f.what[:50]}{more}）。直すか「❕ 見せかけ: <何>（理由）」を足す")
    if weak and not WEAK_LINE.search(reply):
        more = f"、ほか {len(weak) - 1} 件" if len(weak) > 1 else ""
        reasons.append(f"テストか検査を弱めた（{weak[0][:70]}{more}）。戻すか「❕ 弱めた: <何>（理由）」を足す")
    if lost and not GONE_LINE.search(reply):
        i = lost[0]
        name = i["name"] if i["name"] != i["path"] else ""
        more = f"、ほか {len(lost) - 1} 件" if len(lost) > 1 else ""
        reasons.append(f"消した物がある（{i['kind']} {i['path']} {name}".rstrip() + f"{more}）。戻すか「❕ 消した: <何>（理由。頼まれた、または置き換え先）」を足す")
    if not ran and not NOT_RUN.search(reply):
        reasons.append(f"コードを変えたあとに 1 回も動かしていない（{code_edits[0][1]}）。テストか起動で動かして出力を読むか、「動かしていない」と理由を足す")
    seen = Counter((f.kind, f.sev) for f in found if f.id in SHAM_IDS | WEAK_IDS)
    if lost:
        seen[("消した", "重大")] = len(lost)
    if not ran:
        seen[("動かしていない", "重大")] = 1
    return reasons, seen


def main() -> int:
    if os.environ.get("NO_FAKE_OFF") == "1":
        return 0
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        d = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return 0
    path = d.get("transcript_path") if isinstance(d, dict) else None
    if not path or d.get("stop_hook_active") or not os.path.isfile(path):
        return 0
    cs = load_cs()
    if cs is None:
        return 0
    try:
        turn = last_turn(path, cs)
    except OSError:
        return 0
    if not turn or not any(c["name"] in EDIT_TOOLS or c["name"] == "Bash" for c in turn["calls"]):
        return 0
    last = d.get("last_assistant_message")
    reply = "\n".join(turn["texts"] + [last if isinstance(last, str) else ""])
    reasons, seen = judge(turn, reply, str(d.get("cwd") or os.getcwd()))
    fs.record("gate", dict(seen), str(d.get("session_id") or ""), blocked=bool(reasons))
    if not reasons:
        return 0
    print("no-fake: " + "／".join(reasons) + "。足りない行だけを足す。前の行を出し直さない（no-fake）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 no-fake: ignore（判定の中で壊れても、返答を止めない。止めるのは、形を見つけたときだけ）
        sys.exit(0)
