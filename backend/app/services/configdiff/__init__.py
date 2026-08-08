"""現行 config / 将来 config / その差分を扱う。

- 現行フル … 取り込んだ running-config そのもの。機器故障時の復旧に使う
- 将来フル … 更改後の機器に入れる config。新機種にそのまま投入する
- 差分   … 現行を将来に合わせるための投入コマンド。既存機器に当てる

差分は「将来フルと同じ状態になること」を機械で検証してから返す。
検証が通らなければコマンドは返さない。
"""

from app.services.configdiff.engine import Command, DiffResult, apply_script, build
from app.services.configdiff.tree import ConfigNode, parse

__all__ = ["Command", "ConfigNode", "DiffResult", "apply_script", "build", "parse"]
