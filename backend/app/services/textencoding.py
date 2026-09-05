"""現場のファイルの文字コードを当てる。

Config も台帳も、渡されるファイルの文字コードはまちまちである。UTF-8 が
増えてはいるが、Windows で開いて保存したものは Shift_JIS（cp932）になり、
古い機器や古い手順書からは EUC-JP も出てくる。

厄介なのは、cp932 と EUC-JP がお互いのバイト列を「エラーを出さずに」
読めてしまうことである。順に試して最初に通ったものを採ると、化けた文字が
そのまま通る。読めなかったことにも気づけないので、description が化けた
まま台帳に入り、あとから誰も直せない。

そこで、通った候補すべてを見て、化けていないほうを選ぶ。
"""

from __future__ import annotations

#: 試す順。UTF-8 は他の日本語コードと取り違えにくいので先に見る
ENCODINGS = ("utf-8-sig", "utf-8", "cp932", "euc_jp")

#: 取り違えたときに増える文字。私用領域は変換表に無い文字の受け皿で、
#: 半角カナは日本語のテキストにこれほど並ばない
_SUSPICIOUS = (
    (0xE000, 0xF8FF),  # 私用領域
    (0xFF61, 0xFF9F),  # 半角カナ
    (0x0080, 0x009F),  # 制御文字
)


def _oddness(text: str) -> int:
    """取り違えていそうな度合い。小さいほど、そのまま読める文章に近い。"""
    return sum(1 for ch in text for low, high in _SUSPICIOUS if low <= ord(ch) <= high)


def decode(raw: bytes) -> str:
    """バイト列を文字にする。

    どれでも読めないときは、置き換え文字を混ぜてでも読めた分を返す。
    show tech のように制御文字が混ざるものを、丸ごと落としたくないため。
    """
    best: tuple[int, int, str] | None = None
    for rank, encoding in enumerate(ENCODINGS):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding in ("utf-8-sig", "utf-8"):
            # UTF-8 として通るバイト列が、たまたま他のコードだったことは
            # ほとんど無い。ここで決めてよい
            return text
        score = (_oddness(text), rank, text)
        if best is None or score[:2] < best[:2]:
            best = score
    if best is not None:
        return best[2]
    return raw.decode("utf-8", errors="replace")
