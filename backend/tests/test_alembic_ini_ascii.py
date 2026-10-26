"""alembic.ini は ASCII だけで書く。

Alembic は alembic.ini を OS の既定の文字コードで読む（`encoding="locale"`）。英語の Windows
（cp1252）では日本語のコメントで UnicodeDecodeError になり、起動の途中でサーバーが立たなかった。
Python の UTF-8 モード（-X utf8）でも、この読み方は OS の文字コードのままで直らない。
Windows の実機試験（.github/workflows/windows-smoke.yml）で見つけた。
"""

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_alembic_ini_is_ascii_only():
    data = (BACKEND / "alembic.ini").read_bytes()
    bad = [n for n, line in enumerate(data.splitlines(), 1) if any(b > 0x7F for b in line)]
    assert not bad, f"alembic.ini の {bad} 行目に ASCII でない文字がある"


def test_alembic_ini_reads_under_an_ascii_locale():
    """OS の文字コードが ASCII でも、Alembic の設定が読める。

    英語の Windows と同じ条件（OS の文字コードが UTF-8 でない）を Linux で作る。
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LC_", "LANG"))}
    env.update(LC_ALL="POSIX", PYTHONCOERCECLOCALE="0", PYTHONUTF8="0")
    code = "from app.schema_alembic import alembic_config; alembic_config().file_config"
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr[-800:]
