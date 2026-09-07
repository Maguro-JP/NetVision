"""対応表が、実際にできることと合っていること。

対応表は「何を任せられるか」を人が読む場所なので、実装より広く書くと
そのまま誤解になる。ここでは、書いてあることと実装のずれを見る。
"""

from __future__ import annotations


def test_the_table_says_which_vendors_the_commands_can_be_written_for() -> None:
    """出せる機種と出せない機種を、対応表でも言うこと。

    「差分は出るのに投入コマンドは出ない」は、画面で初めて気づくと戸惑う。
    先に書いてあれば、渡す前に分かる。
    """
    from app.services import capabilities
    from app.services.configdiff.dialects import UNSUPPORTED
    from app.services.configparse import VENDOR_LABELS

    entry = next(
        item for item in capabilities.CAPABILITIES if item.name == "Config 差分・投入コマンド"
    )
    for vendor in UNSUPPORTED:
        label = VENDOR_LABELS[vendor]
        # 出せない機種は、名前を挙げて理由まで書く
        assert any(word in entry.note for word in label.split()), label
    assert "確かめ" in entry.note
