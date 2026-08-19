"""アプリの中でモデルを動かす。

端末に新しいソフトを入れられない職場がある。サーバーを別に立てる形（Ollama や
llama.cpp の server）は、実行ファイルが1つ増えるため、そこで止まる。

そこで、NetVision が自分の中でモデルを読む道を用意する。増えるのは
`lib/` に入るライブラリ1つだけで、新しい実行ファイルは増えない。外にも出ない。

モデルの実体（GGUF ファイル）は同梱しない。

  - 数 GB あり、リポジトリに置けない（GitHub は1ファイル 100MB まで）
  - 数か月で新しいものが出る。アプリと一緒に固定すると、入れ替えのたびに
    アプリごと配り直すことになる
  - 用途で使い分けたい（要約は小さいモデル、Config の下書きは大きいモデル）

置き場所に GGUF があれば使い、無ければ規則ベースだけで動く。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: モデルの置き場所。ここに GGUF を置くと使う
MODEL_DIR_ENV = "NETVISION_MODEL_DIR"
#: 1つだけ名指ししたい場合
MODEL_PATH_ENV = "NETVISION_MODEL_PATH"

#: 役割ごとに、名前に含まれていたら選ぶ手がかり（小さいモデルを quick に回す）
_QUICK_HINTS = ("0.5b", "1.5b", "3b", "mini", "small")


def model_dirs() -> list[Path]:
    """モデルを探す場所。

    アプリの隣と、データの置き場所の両方を見る。アプリのフォルダを入れ替えても
    モデルが残るようにするため、また、アプリ側が書けない場所に置かれていても
    データ側に置けば使えるようにするため。
    """
    named = os.environ.get(MODEL_DIR_ENV)
    if named:
        return [Path(named).expanduser()]

    from scripts.standalone import default_data_dir

    return [
        Path(__file__).resolve().parents[3] / "models",
        default_data_dir() / "models",
    ]


def model_dir() -> Path:
    """探す場所のうち、代表の1つ（画面の案内に使う）。"""
    return model_dirs()[0]


def available() -> list[Path]:
    """置いてあるモデルのファイル。"""
    named = os.environ.get(MODEL_PATH_ENV)
    if named:
        path = Path(named).expanduser()
        return [path] if path.is_file() else []

    found: list[Path] = []
    for folder in model_dirs():
        if folder.is_dir():
            found.extend(path for path in folder.glob("*.gguf") if path.is_file())
    return sorted(found)


def pick_file(role: str) -> Path | None:
    """その役割に使うモデルのファイル。

    要約に大きいモデルを使うと待ち時間だけが伸びるので、名前から小さい方を選ぶ。
    1つしか無ければそれを使う。
    """
    files = available()
    if not files:
        return None
    if len(files) == 1:
        return files[0]

    small = [path for path in files if any(hint in path.name.lower() for hint in _QUICK_HINTS)]
    large = [path for path in files if path not in small]
    if role == "quick":
        return (small or large)[0]
    return (large or small)[0]


def usable() -> bool:
    """アプリの中で動かせるか（ライブラリとモデルの両方がある）。"""
    return runtime_ready() and bool(available())


def runtime_ready() -> bool:
    try:
        import llama_cpp  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class _Loaded:
    path: Path
    model: object


_cache: _Loaded | None = None


def _load(path: Path, context: int):
    """読み込んだモデルは持ち回す。毎回読むと数秒〜数十秒かかる。"""
    global _cache
    if _cache is not None and _cache.path == path:
        return _cache.model

    from llama_cpp import Llama

    logger.info("モデルを読み込みます: %s", path.name)
    model = Llama(model_path=str(path), n_ctx=context, verbose=False)
    _cache = _Loaded(path=path, model=model)
    return model


def ask(
    prompt: str,
    *,
    system: str | None = None,
    role: str = "quick",
    temperature: float = 0.1,
    context: int = 8192,
    max_tokens: int = 1024,
) -> tuple[str, str] | None:
    """1問1答。使えないときは None を返す。

    例外は投げない。モデルの読み込みに失敗しても、道具そのものは動き続ける。
    """
    path = pick_file(role)
    if path is None or not runtime_ready():
        return None

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        model = _load(path, context)
        answer = model.create_chat_completion(  # type: ignore[attr-defined]
            messages=messages, temperature=temperature, max_tokens=max_tokens
        )
        text = answer["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001  何が起きても規則ベースへ落ちる
        logger.warning("モデル（%s）を動かせませんでした（%s）", path.name, exc)
        return None
    return str(text).strip(), path.stem
