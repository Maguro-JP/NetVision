"""閉域の中に置いたモデルへ聞く口。

置いていない環境でも、道具として成立していなければならない。ここは
「あれば使う、無ければ規則で済ませる」ための1枚で、呼び出し側は
`ask()` が None を返した場合の道を必ず持つ。

繋ぐ先は OpenAI 互換の API（llama.cpp の server、Ollama、vLLM など）。
社内に立てたものを想定していて、外の事業者へは出さない。設定していなければ
どこへも接続しない（閉域の前提を壊さない）。

  NETVISION_LLM_URL    例: http://127.0.0.1:11434/v1
  NETVISION_LLM_MODEL  例: qwen2.5-coder:7b
  NETVISION_LLM_TIMEOUT  秒。既定 60
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Answer:
    text: str
    model: str


def configured() -> bool:
    """モデルに聞ける状態か。画面に「AI で下書きする」を出すかの判断に使う。"""
    return bool(settings.llm_url and settings.llm_model)


def ask(prompt: str, *, system: str | None = None, temperature: float = 0.1) -> Answer | None:
    """1問1答。使えないときは None を返し、呼び出し側は規則ベースへ落ちる。

    例外は投げない。モデルが落ちていても、道具そのものは動き続ける必要がある。
    """
    if not configured():
        return None

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = json.dumps(
        {
            "model": settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
    ).encode("utf-8")

    url = settings.llm_url.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.llm_timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning("モデルに聞けませんでした（%s）。規則ベースで続けます", exc)
        return None

    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("モデルの返事を読めませんでした。規則ベースで続けます")
        return None
    return Answer(text=str(text).strip(), model=str(payload.get("model") or settings.llm_model))
