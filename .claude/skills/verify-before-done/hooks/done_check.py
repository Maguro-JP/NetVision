#!/usr/bin/env python3
"""Stop フック: 「完了」「見た」と書いた返答を、このターンのツールの出力と照合し、根拠が無ければ差し戻す。

「直しました」「通りました」「見ました」と書いただけで、動かした出力も見た形跡も無い返答が、嘘と同じ結果を生む。
指示だけでは直らなかったので、止まる直前に機械で見る。
根拠は自分の文ではなく、このターンのツールの出力（tool_result）と呼んだツールの名前から取る。
自分の文に書いた URL やハッシュは、書けば通ってしまうので根拠にしない。
完了の語ごとに要る出力を決める。マージした→マージの結果、push した→push の出力、コミットした→コミットの出力、
通った→OK や passed や誤り 0、直した・作った→書き込むツールの呼び出し、見た・確認した→ツールの呼び出し。
ツールの出力に FAILED があるのに「通った」と書いた返答も差し戻す。
「確かめていない」「やっていない」「見ていない」と書いてあれば通す。正直に書く方が良い。
撤回（「間違えていました」）は差し戻さずに数だけ残す。正直に言い直すのを止めない。
❔ が 1 ターンに 2 つ以上、訂正への返事に反論の形、指示が 2 つ以上あるのに ★ も ↩ も無い返事、も差し戻す。
返答ごとの数は metrics.jsonl に残し、週の報告（skill-upkeep）が読む。

環境変数:
  DONE_CHECK_OFF=1        止める
  DONE_CHECK_STATE=dir    数の保持先（既定は ~/.claude/state/verify-before-done）
"""

import json
import os
import re
import sys
import time
from pathlib import Path

# 一般の完了の語。下の BACKED に当たらないものは、文の中の場所や URL を根拠として受ける
CLAIM = re.compile(
    r"完了|完成しました|できました|出来ました|終わりました|終えました|直しました|直りました|通りました|通っています"
    r"|マージしました|入りました|入っています|入れました|動きました|動いています|作りました|足しました|消しました"
    r"|確かめました|確認しました|検証しました|テストが通|反映しました|登録しました|設定しました|デプロイしました"
)
EVIDENCE = re.compile(
    r"https?://|\b[0-9a-f]{7,40}\b|`[^`\n]+`|^```|exit=|#\d+|確かめていない|確認していない|未確認|未検証"
    r"|[\w./-]+\.(?:md|py|sh|json|ya?ml|ts|tsx|js|go|rs|java|rb|toml|txt|csv)\b|[\w./-]+:\d+",
    re.M,
)
# 正直な書き方。これがあれば、その返答の完了の語は差し戻さない
HONEST = re.compile(r"確かめていない|確認していない|未確認|未検証|やっていない|やっていません|見ていない|見ていません|できていない|できていません|動かしていない|前のターン|前の返答|先ほど|さきほど")
# 撤回。差し戻さずに数える
RETRACTION = re.compile(r"間違えていました|間違っていました|誤りでした|誤っていました|訂正します|できていませんでした|出来ていませんでした|見ていませんでした|実際は(?:違|そうでは|まだ)")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"}
PASS_OUT = re.compile(r"\bOK\b|\d+ passed|誤り 0|All checks passed|exit=0|検出なし|重大 0|:success|succeeded|\bPASS\b")
FAIL_OUT = re.compile(r"\bFAILED\b|Traceback \(most recent|誤り [1-9]|重大 [1-9]|exit=[1-9]|:failure\b|\bFAIL\b")
# (名前, 完了の語, 要る根拠（呼んだツールの集合, 出力）→真偽, 差し戻しの文)
BACKED = [
    ("look", re.compile(r"(?<!と)読みました|見ました|確認しました|調べました|確かめました|目を通しました|見終えました|読み終えました"),
     lambda tools, out: bool(tools),
     "見た・確認した・調べたと書いているが、このターンでツールを 1 つも呼んでいない"),
    ("merge", re.compile(r"マージ(?:しました|できました|済みです|が済みました|しています|されました)"),
     lambda tools, out: bool(re.search(r'"merged":\s*true|successfully merged|Merged pull request|Merge made by|Fast-forward', out)),
     "マージしたと書いているが、ツールの出力にマージの結果が無い"),
    ("push", re.compile(r"push\s*(?:しました|できました|済みです|しています)|プッシュしました|配りました|配布しました|配れました|配り終えました"),
     lambda tools, out: bool(re.search(r"\S+\s*->\s*\S+|\[new branch\]|push:\s|Everything up-to-date|Branch .* set up to track", out)),
     "push した・配ったと書いているが、ツールの出力に push の結果が無い"),
    ("commit", re.compile(r"コミット(?:しました|できました|済みです|しています)"),
     lambda tools, out: bool(re.search(r"files? changed|insertions?\(|\[[^\]\n]+ [0-9a-f]{7,}\]|^[0-9a-f]{7,40}\b", out, re.M)),
     "コミットしたと書いているが、ツールの出力にコミットの結果が無い"),
    ("test", re.compile(r"テスト(?:が|は|も)?(?:\s*\d+\s*件)?(?:が|も)?\s*通|通りました|通っています|validate\s*(?:が|も)?\s*通|検査(?:が|を|も)?\s*通|全部通|緑です|green です"),
     lambda tools, out: bool(PASS_OUT.search(out)),
     "通ったと書いているが、ツールの出力に通った結果（OK、passed、誤り 0、success）が無い"),
    ("edit", re.compile(r"直しました|作りました|足しました|書きました|消しました|入れました|変えました|更新しました|登録しました|設定しました|置きました|直せました"),
     lambda tools, out: bool(tools & EDIT_TOOLS),
     "直した・作った・足したと書いているが、このターンで書き込むツール（Edit、Write、Bash）を呼んでいない"),
]
MARKS = "★⭐↩⏎❕❗❔❓"
CORRECTION = re.compile(
    r"違う|ちがう|やめて|やめろ|勝手に|言った(?:通り|とおり|のに|だろ|でしょ|はず)|言ってる|言ってない"
    r"|聞いて(?:ない|いない|た\?|たか)|聞け|聞かない|それじゃない|そうじゃない|じゃなくて|ではなくて"
    r"|戻して|元に戻|しないで|するな|使うな|やるな|書くな|禁止|またか|また同じ|何度も|何回|何度"
    r"|反抗|従わ|改めない|貫く|頼んでない|余計な|指示通り|指示どおり|従え"
)
IMPERATIVE = re.compile(r"(?:しろ|せよ|やれ|やめろ|なさい|ください|下さい|くれ|しとけ|とけ|すること|ように|して|で|な|[ろけせてねめれげべ])$")
UNDONE = re.compile(r"やっていない|やっていません|できていない|できていません|未着手|手を付けていない|残り")
PLAN = re.compile(r"(?:し|やり|進め|配り|通し|残し|作り|直し|見|調べ|確かめ|書き|入れ|送り|始め|続け|回し|試し|読み|探し|組み立て|足し|消し|外し)ます。?$")
PUSHBACK = re.compile(r"しかし|ただし|とはいえ|おすすめしません|お勧めしません|の方が良い|のほうが良い|べきではない|やめた方が|やめたほうが|反対です|賛成できません|AI の判断で|私の判断で")
OUT_LIMIT = 400_000  # 照合するツールの出力の上限（文字）


def text_of(c) -> str:
    if isinstance(c, str):
        return c
    return "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")


def result_text(c) -> str:
    """tool_result の中身。文字列でも、text の並びでも受ける。"""
    parts = []
    for x in c:
        if isinstance(x, dict) and x.get("type") == "tool_result":
            inner = x.get("content", "")
            parts.append(inner if isinstance(inner, str) else text_of(inner))
    return "\n".join(parts)


def this_turn(path: str):
    """(自分の文, 人の発言, 呼んだツールの名前, ツールの出力)。最後の人の発言から後だけ。"""
    texts, user, tools, outs = [], "", set(), []
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        c = e.get("message", {}).get("content", [])
        if e.get("type") == "user":
            if isinstance(c, str) or not any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                texts, tools, outs = [], set(), []
                user = text_of(c)
            else:
                outs.append(result_text(c))
            continue
        if e.get("type") != "assistant":
            continue
        if not isinstance(c, str):
            for x in c:
                if isinstance(x, dict) and x.get("type") == "tool_use":
                    tools.add(str(x.get("name", "")))
        t = text_of(c)
        if t.strip():
            texts.append(t)
    out = "\n".join(outs)
    return texts, user, tools, out[-OUT_LIMIT:]


def record(session_id: str, row: dict) -> None:
    base = os.environ.get("DONE_CHECK_STATE")
    root = Path(base) if base else Path.home() / ".claude" / "state" / "verify-before-done"
    try:
        root.mkdir(parents=True, exist_ok=True)
        row = {"ts": int(time.time()), "session": re.sub(r"[^A-Za-z0-9_-]", "", session_id or "")[:40], **row}
        with open(root / "metrics.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def strip_quotes(s: str) -> str:
    """引用（「…」）とコード（`…`）の中の語は、主張ではなく説明なので外す。"""
    return re.sub(r"「[^」]*」|`[^`]*`", "", s)


def main() -> int:
    if os.environ.get("DONE_CHECK_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    if d.get("stop_hook_active"):
        return 0
    path = d.get("transcript_path")
    if not path or not os.path.isfile(path):
        return 0
    try:
        texts, user, tools, out = this_turn(path)
    except OSError:
        return 0
    if not texts:
        return 0
    text = "\n".join(texts)
    said = strip_quotes(text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    questions = sum(1 for l in lines if l[0] in "❔❓")
    outside = sum(1 for l in lines if l[0] not in MARKS)
    claim = bool(CLAIM.search(said))
    evidence = bool(EVIDENCE.search(text))
    honest = bool(HONEST.search(said))
    bare = claim and not evidence and not honest
    corrected = bool(CORRECTION.search(user or ""))
    directives = sum(1 for p in re.split(r"[\s。]+", user or "") if p and IMPERATIVE.search(p)) if len(user or "") <= 400 else 0
    marked = sum(1 for l in lines if l[0] in "★⭐↩⏎")
    plan = sum(1 for l in lines if l[0] in MARKS and PLAN.search(l))
    uncovered = directives >= 2 and marked == 0 and not UNDONE.search(text)
    pushback = [l for l in lines if PUSHBACK.search(strip_quotes(l)) and "権限" not in l] if corrected else []
    # 完了の語を、このターンのツールの出力と照合する
    unbacked = [] if honest else [(name, msg) for name, pat, ok, msg in BACKED if pat.search(said) and not ok(tools, out)]
    contradiction = bool(BACKED[4][1].search(said)) and bool(FAIL_OUT.search(out)) and not PASS_OUT.search(out) and not honest
    retraction = bool(RETRACTION.search(said))
    record(d.get("session_id", ""), {"lines": len(lines), "questions": questions, "outside_marks": outside,
                                     "claim": claim, "claim_without_evidence": bare, "corrected": corrected, "pushback": len(pushback),
                                     "directives": directives, "marked": marked, "plan_lines": plan,
                                     "tools": len(tools), "unbacked": [n for n, _ in unbacked], "contradiction": contradiction, "retraction": retraction})
    reasons = []
    if pushback:
        reasons.append(f"訂正への返事に反論の形がある（「{PUSHBACK.search(strip_quotes(pushback[0])).group(0)}」）。1文で認めて、言われた通りに直したものだけを出す。"
                       "代案は直したあとに ❔ で1行だけ（follow-instructions。反抗の禁止）。")
    if contradiction:
        reasons.append("ツールの出力に FAILED や誤りがあるのに「通った」と書いている。落ちた出力をそのまま貼り、直っていないなら「直っていない」と書く（verify-before-done）。")
    for name, msg in unbacked:
        reasons.append(f"{msg}。やったなら、その出力が出るツールを呼んで結果を貼る。やっていないなら「やっていない」「見ていない」と書く（verify-before-done）。")
    if bare:
        m = CLAIM.search(said)
        reasons.append(f"「{m.group(0)}」と書いているが、根拠（動かした出力、URL、コミットのハッシュ、ファイルの場所）が無い。"
                       "根拠を足すか、確かめていないなら「確かめていない」と書く（verify-before-done）。")
    if uncovered:
        reasons.append(f"指示が {directives} 個あるのに、★ か ↩ の行が無い。指示ごとに1行ずつ返し、やらなかったものは「やっていない」と書く（follow-instructions）。")
    if questions > 1:
        reasons.append(f"❔ が {questions} 行ある。1 ターンに 1 つまで。残りは決めて進めるか、ファイルに書いて ★ で渡す（follow-instructions）。")
    if reasons:
        print("\n".join(reasons), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
