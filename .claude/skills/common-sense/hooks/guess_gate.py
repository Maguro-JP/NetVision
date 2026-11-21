#!/usr/bin/env python3
"""Stop フック: 調べずに、推測の語で済ませた返答を差し戻す。

ユーザーの指摘: 疑問を調べようとしない。自分の認識だけで済ませて調べようとしない。
見るのは 3 つがそろったとき。
  1. 最後の返答の、「❕ 疑い」の行の外に推測の語（たぶん、おそらく、はず、と思います、と思われ、記憶では、確か〜だった）がある
  2. このターンに、調べる呼び出し（Read、Grep、Glob、WebSearch、WebFetch、Context7、Exa、手元を見る・動かす Bash）が 1 回も無い
  3. 返答に「❕ 疑い: …は調べられない（理由）」の行が無い
偽の差し戻しを減らすことを先にする。コード、引用、言われたことのコピー（↩ の行、人の発言と同じ並び）、
予想と実際を書く「❕ おかしい」の行は見ない。人が予想や意見を聞いたターンも見ない。

最後の返答は、入力の last_assistant_message を先に見る（会話の記録は遅れて書かれる）。無ければ記録から読む。
推測の語を Stop フックで見る形は、florianbuetow/claude-code の agent-guardrails と skymanbp/cc-enforcer（どちらも MIT License）を参考にした。
コードは取り込んでいない。語は日本語で書き直し、語だけでは止めない形にした（docs/SOURCES.md）。

環境変数:
  COMMON_SENSE_OFF=1   止める
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cs  # noqa: E402

# 推測の語。断定の形だけを拾う（「はずがない」「はずだった」「確かに」「確かめる」は推測ではない）
GUESS = [
    ("たぶん", re.compile(r"たぶん|多分(?!野)")),
    ("おそらく", re.compile(r"おそらく|恐らく")),
    ("はず", re.compile(r"(?<!手)はず(?:です|だ(?!っ)|なので|ですし|。|$)", re.M)),
    ("と思われ", re.compile(r"と思われ(?:ます|る|た)")),
    ("記憶では", re.compile(r"記憶では|記憶によれば|記憶している限り|覚えている限り")),
    ("確か〜だった", re.compile(r"(?:確か|たしか)(?![めにな])[^。\n]{1,40}?(?:だった|でした|はず)")),
]
THINK = re.compile(r"と思(?:います|いました|っています|う(?=[。、\n]|$))", re.M)
SURE_GUESS = ("だろう", "でしょう", "であろう", "かろう")
VOLITION = re.compile(r"(?:たい|たく|[おこごそとのぼもろよ]う)$")  # 「直そうと思います」「進めたいと思います」は予定で、推測ではない

DOUBT_LINE = re.compile(r"^\s*[❕!]\s*(?:疑い|おかしい)[:：]")
ESCAPE = re.compile(r"^\s*[❕!]\s*疑い[:：][^\n]*(?:調べられ|確かめられ|調べようが|確かめようが)", re.M)
# 人が予想や意見を聞いたターン。推測で答えるのが頼まれたこと
ASKED = re.compile(r"どう思|思う[?？か]|思います[?？か]|予想(?:して|は[?？]|を(?:教|聞|出))|推測(?:して|でいい|で良い|でよい|を(?:教|聞|出))"
                   r"|見込みは|見立ては|意見(?:は|を)|感想(?:は|を)|印象(?:は|を)|勘で|ざっくり|だいたいで")


def prose(text: str) -> str:
    """文章だけを残す。コード、引用、言われたことのコピー、疑いとおかしいの行を落とす。"""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", " ", text)
    text = re.sub(r"「[^」\n]*」|『[^』\n]*』|“[^”\n]*”|\"[^\"\n]*\"", " ", text)
    keep = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith((">", "↩")) or DOUBT_LINE.match(s):
            continue
        keep.append(line)
    return "\n".join(keep)


def guesses(text: str, prompt: str) -> list:
    """文章の中の推測の語。人の発言と同じ並びは、言われたことのコピーなので数えない。"""
    body = prose(text)
    found = []

    def copied(a: int, b: int) -> bool:
        """語の前か後ろの 4 文字ごと、人の発言にあるか。語だけの一致はコピーと見ない。"""
        right, left = body[a:b + 4].strip(), body[max(0, a - 4):b].strip()
        return any(len(w) > b - a and w in prompt for w in (right, left))

    for name, pat in GUESS:
        for m in pat.finditer(body):
            if not copied(m.start(), m.end()):
                found.append(name)
                break
    for m in THINK.finditer(body):
        before = body[max(0, m.start() - 4):m.start()]
        if VOLITION.search(before) and not before.endswith(SURE_GUESS):
            continue
        if not copied(m.start(), m.end()):
            found.append("と思います")
            break
    return found


def main() -> int:
    if _cs.off():
        return 0
    d = _cs.read_event()
    path = d.get("transcript_path")
    if d.get("stop_hook_active") or not path or not os.path.isfile(path):
        return 0
    try:
        turns = _cs.last_turns(path, 1)
    except OSError:
        return 0
    if not turns:
        return 0
    turn = turns[-1]
    last = d.get("last_assistant_message")
    if not isinstance(last, str) or not last.strip():
        last = turn["texts"][-1][1] if turn["texts"] else ""
    if not last.strip() or ESCAPE.search(last) or ASKED.search(turn["prompt"]):
        return 0
    found = guesses(last, turn["prompt"])
    if not found:
        return 0
    if any(_cs.is_lookup(c["name"], c["input"]) for c in turn["calls"]):
        return 0
    print(f"推測の語（{found[0]}）があるのに、このターンで 1 回も調べていない。手元のコードと出力、--help と入っている版、公式の文書の順に調べ、"
          "分かったことを ❕ で 1 行足す。調べられないなら「❕ 疑い: <何>は調べられない（理由）」を 1 行足す（common-sense）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
