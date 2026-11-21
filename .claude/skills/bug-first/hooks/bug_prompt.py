#!/usr/bin/env python3
"""UserPromptSubmit と SessionStart のフック: バグの報告と、直していないバグを 1 行で差し込む。

ユーザーの決まり: 何よりもバグ修正を優先させる。特に致命的なバグ、クラッシュや操作不能、破損や漏洩等は即修正版を出す。
バグの報告を受けても、いま進めている作業を続けてしまう。報告のターンに、優先の順番を 1 行で思い出させる。

UserPromptSubmit: ユーザーの発言に、バグの症状の言い方（落ちる、固まる、動かない、消えた、エラーが出る、など）があるとき 1 行。
  出さないもの（偽の差し込みを減らす）:
  - 通知、Stop フックの差し戻し、子のセッションへの依頼文、長い貼り付け
  - バグがあるかを聞く問い（「バグは無い?」「クラッシュしない?」「バグが無いか見て」）
  - 決まりの話（「落ちたら直して」「クラッシュのときは」）と、語そのものの話（引用の中、「という語」「検出」「判定」）
  - テストや Actions が落ちた話（ci-triage の場面。skill-nudge.sh が案内する）
SessionStart: 帳簿（docs/reports/bugs.md）に、直していない致命的か重いがあるとき 1 行。帳簿が無いリポジトリでは何も出さない。

環境変数:
  BUG_FIRST_OFF=1   止める
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bf  # noqa: E402

LINE = "バグ修正が最優先。重さを決めて帳簿に書き、致命的は今の作業を止めて修正版を出す（bug-first）"
MAX_LEN = 4000  # これより長い発言は、依頼文や資料の貼り付けとみなす
SYSTEMISH = re.compile(r"^\s*<|wake reason|task-notification|SYSTEM NOTIFICATION|system-reminder|Stop hook feedback|This session is being continued")
# 親が子に渡す依頼文のマーク（feedback-session の fanout.py）
CHILD = re.compile(r"セッションやルーティンを作らない|親のセッションから分けた")

# バグの症状の言い方。名詞だけ（「クラッシュや破損」）は決まりの話に多いので、起きた形（動詞）で当てる
SYMPTOM = re.compile(
    r"クラッシュ(?:し(?!な)|する|が起|が発生|で落)|異常終了|強制終了"
    r"|落ち(?:る|た|ます|ました|て(?:る|いる|しま|くる|き)|ちゃ)"
    r"|固ま(?:る|った|って|ります|りま)|フリーズ|ハング(?:し|する)|応答(?:しな|がな|なし)|無限ループ"
    r"|操作不能(?:に|で|だ|な|$)|操作(?:が|を|も)?(?:でき|出来)な|動か(?:ない|なく|ねえ|ん)|動きません|起動(?:しな|できな|しませ|せず|すらし)|立ち上がらな|開(?:か|け)な(?:い|く)|反応(?:しな|がな)"
    r"|壊れ(?:た|て|る|ます|ました)|破損(?:し|する)|消え(?:た|て(?:る|いる|しま|い[るた])|ました|ちゃ)|保存(?:され|でき)な|失われ"
    r"|漏れて(?:る|いる|た|ます|いま)|漏れ(?:た|ました)|漏洩(?:し|する)|流出(?:し|する)|丸見え"
    r"|文字化け|真っ白|真っ黒|エラー(?:が|を|に|で)?(?:出|吐|起き|発生|なる|なっ|止ま|落ち|終了)"
    r"|バグ(?:って|った|る(?!の)|があ(?:る|った|り)|が出|が発生|だ(?:ろ|よ|と|ね|。|$)|です|を直|を見つけ|っぽい|じゃ(?:ない|ね|ん)|かも|修正して|報告)"
    r"|不具合(?:があ|が出|が発生|です|だ|を直|を見つけ)"
    r"|Traceback \(most recent|panicked at|Segmentation fault|core dumped|Unhandled(?:PromiseRejection| exception| rejection)|Uncaught \w*(?:Error|Exception)|FATAL ERROR", re.M)
# 起きたことではなく、決まりや条件を言っている続き（「落ちたら」「落ちるときは」「固まっても」「動かないように」「バグがあるか見て」）。
# 当たった語の最後の 1 文字から見る（「固まった」+「ら」）。離れた所の「たら」は、別の文節なので見ない
COND = re.compile(r"^(?:ても|.{0,3}?(?:たら|だら|なら(?!ない)|れば|とき(?:は|に|の|、|$)|時(?:は|に)|場合|ように|かどうか|か(?:を)?(?:見|確|調|チェ|探|試)))")
# バグがあるかを聞いている続き（「バグがある?」「バグがあるのか?」）
ASKING = re.compile(r"^(?:の|のか|か|かな|よね|っけ)?\s*[?？]")
# 語そのものの話（フックやスキルの語の一覧を直す相談）と、テストや Actions が落ちた話
META = re.compile(r"という(?:語|言葉|言い方)|(?:の|な)(?:語|言葉)(?:を|が|は|に|で|も)|語を足|検出|判定|拾[うっいわ]|当て[るた]|当た[るっら]|正規表現|パターン|description|のような(?:語|問い|言い方)|みたいな(?:語|言い方)")
CI = re.compile(r"(?:テスト|CI|Actions|チェック|検査|precheck|validate|ジョブ|workflow|ビルド|lint)[^。\n]{0,10}$", re.I)
# 語の前を見て外すもの。同じ語でも、前に来る物でバグの報告ではなくなる
BEFORE = [
    ("落ち", CI),  # テストや Actions が落ちた（ci-triage の場面）
    ("消え", re.compile(r"(?:エラー|警告|注意|誤り|バグ|不具合|問題|症状|ブランチ|セッション|ルーティン|通知)[^。\n]{0,6}$")),  # 「エラーが消えた」は良い知らせ。ブランチやセッションは整理整頓の話
    ("固ま", re.compile(r"(?:方針|仕様|設計|予定|計画|考え|案|構成|日程|内容)[^。\n]{0,4}$")),  # 「方針が固まった」は、決まったという意味
]
QUOTED = re.compile(r"「[^」\n]*」|『[^』\n]*』|`[^`\n]*`|\"[^\"\n]*\"|“[^”\n]*”")


def report(prompt: str) -> str:
    """発言の中の、バグの報告に当たる断片。無ければ空。"""
    for line in re.split(r"[\n。]", QUOTED.sub("", prompt)):
        if META.search(line):
            continue
        for m in SYMPTOM.finditer(line):
            if COND.match(line[m.end() - 1:]):
                continue
            if m.group(0).startswith(("バグ", "不具合")) and ASKING.match(line[m.end():]):
                continue
            if any(m.group(0).startswith(word) and pat.search(line[:m.start()]) for word, pat in BEFORE):
                continue
            return m.group(0)
    return ""


def start_line(root=None) -> str:
    """セッションの始めに出す 1 行。直していない致命的か重いがあるときだけ。"""
    rows = _bf.ledger_bugs(root)
    if not rows:
        return ""
    block = _bf.bugs.blocking_fatal(rows)
    wait = [b for b in _bf.bugs.open_fatal(rows) if b not in block]
    heavy = [b for b in rows if _bf.bugs.is_open(b) and b["重さ"].startswith("重い")]
    parts = []
    if block:
        parts.append(f"直していない致命的なバグが {len(block)} 件（{_bf.short(block[0])}）。何よりも先に直し、修正版を出す")
    if wait:
        parts.append(f"修正版の確認待ちが {len(wait)} 件（{_bf.short(wait[0])}）。返事があり次第、PR・マージ・Release まで出す")
    if heavy:
        parts.append(f"重いバグが {len(heavy)} 件（{_bf.short(heavy[0])}）。今日の開発の最初に直す")
    return ("バグの帳簿: " + "。".join(parts) + "（bug-first）") if parts else ""


def main() -> int:
    if _bf.off():
        return 0
    d = _bf.read_event()
    event = str(d.get("hook_event_name") or ("UserPromptSubmit" if "prompt" in d else "SessionStart"))
    if event == "SessionStart":
        line = start_line()
        if line:
            print(line)
        return 0
    prompt = str(d.get("prompt") or "")
    if not prompt.strip() or len(prompt) > MAX_LEN or SYSTEMISH.search(prompt) or CHILD.search(prompt):
        return 0
    if report(prompt):
        print(LINE)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 思い出させるだけのフックなので、壊れてもユーザーの発言を止めない
        sys.exit(0)
