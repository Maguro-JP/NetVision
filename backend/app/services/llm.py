"""モデルへ聞く口。

置いていない環境でも、道具として成立していなければならない。ここは
「あれば使う、無ければ規則で済ませる」ための1枚で、呼び出し側は
`ask()` が None を返した場合の道を必ず持つ。

繋ぐ先は OpenAI 互換の API（Ollama / llama.cpp の server / vLLM など）。
社内・手元に立てたものを想定している。外向きの宛先は、人が明示したときだけ
使う。設定を1つ書き換えただけで実機の Config が社外へ出ていく、という事故を
防ぐため。

## 役割ごとに分ける

用途で必要な賢さが違う。資料の要約や分類は小さいモデルで足り、Config の
下書きやレビューは大きいモデルが要る。全部を大きいモデルに投げると、
1台のサーバーでは待ち時間が伸びて使い物にならない。

  quick … 要約・分類・言い換え。小さいモデル
  deep  … Config の下書き・構成レビュー。大きいモデル

置いていない役割は、置いてある方へ回す（deep が無ければ quick で答える）。

## 設定

1つだけ置く場合:

  NETVISION_LLM_URL      例: http://127.0.0.1:11434/v1
  NETVISION_LLM_MODEL    例: qwen2.5-coder:7b

役割ごとに分ける場合（こちらが優先）:

  NETVISION_LLM_PROFILES=[
    {"role": "quick", "url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:3b"},
    {"role": "deep",  "url": "http://10.0.0.50:8000/v1",  "model": "qwen2.5-coder:14b"}
  ]

共通:

  NETVISION_LLM_TIMEOUT         秒。既定 60
  NETVISION_LLM_API_KEY         事業者の API を使う場合の鍵（閉域では空のまま）
  NETVISION_LLM_ALLOW_EXTERNAL  外向きの宛先を許すか。既定は不可
"""

from __future__ import annotations

import ipaddress
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)

#: 役割。増やすときはここに足す
QUICK = "quick"
DEEP = "deep"


@dataclass(frozen=True)
class Answer:
    text: str
    model: str
    role: str


@dataclass(frozen=True)
class Profile:
    role: str
    url: str
    model: str
    api_key: str | None = None

    @property
    def local(self) -> bool:
        return is_local(self.url)

    @property
    def usable(self) -> bool:
        return bool(self.url and self.model) and (self.local or settings.llm_allow_external)


def profiles() -> list[Profile]:
    """置いてあるモデルの一覧。設定が壊れていても道具は動かす。"""
    raw = settings.llm_profiles
    if raw:
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            logger.warning("NETVISION_LLM_PROFILES を読めません。1つ置きの設定で続けます")
            items = []
        out = [
            Profile(
                role=str(item.get("role") or QUICK),
                url=str(item.get("url") or ""),
                model=str(item.get("model") or ""),
                api_key=item.get("api_key") or settings.llm_api_key,
            )
            for item in items
            if isinstance(item, dict)
        ]
        if out:
            return out

    if settings.llm_url and settings.llm_model:
        # 1つしか置いていないなら、どの役割もそれで受ける
        return [
            Profile(
                role=role,
                url=settings.llm_url,
                model=settings.llm_model,
                api_key=settings.llm_api_key,
            )
            for role in (QUICK, DEEP)
        ]
    return []


def pick(role: str = QUICK) -> Profile | None:
    """その役割に使えるモデル。無ければ他の役割で代用する。"""
    usable = [item for item in profiles() if item.usable]
    for item in usable:
        if item.role == role:
            return item
    return usable[0] if usable else None


def configured(role: str = QUICK) -> bool:
    """モデルに聞ける状態か。画面に「AI で下書きする」を出すかの判断に使う。"""
    return pick(role) is not None


def is_local(url: str) -> bool:
    """閉域の中の宛先か。社内のサーバーもここに含む。"""
    host = urllib.parse.urlparse(url).hostname or ""
    if host in {"localhost", "::1"} or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # 名前で書かれた社内サーバー。ドメインが付かない名前は内部とみなす
        return "." not in host
    return address.is_private or address.is_loopback


def status() -> dict[str, object]:
    """画面に出す状態。鍵そのものは返さない。"""
    items = profiles()
    return {
        "configured": any(item.usable for item in items),
        "models": [
            {
                "role": item.role,
                "model": item.model,
                "endpoint": item.url,
                "local": item.local,
                "usable": item.usable,
                "blocked_external": bool(item.url)
                and not item.local
                and not settings.llm_allow_external,
            }
            for item in items
        ],
    }


def ask(
    prompt: str,
    *,
    system: str | None = None,
    role: str = QUICK,
    temperature: float = 0.1,
) -> Answer | None:
    """1問1答。使えないときは None を返し、呼び出し側は規則ベースへ落ちる。

    例外は投げない。モデルが落ちていても、道具そのものは動き続ける必要がある。
    """
    profile = pick(role)
    if profile is None:
        return None

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = json.dumps(
        {
            "model": profile.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
    ).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if profile.api_key:
        headers["Authorization"] = f"Bearer {profile.api_key}"

    url = profile.url.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=settings.llm_timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning(
            "モデル（%s）に聞けませんでした（%s）。規則ベースで続けます", profile.model, exc
        )
        return None

    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("モデルの返事を読めませんでした。規則ベースで続けます")
        return None
    return Answer(
        text=str(text).strip(),
        model=str(payload.get("model") or profile.model),
        role=profile.role,
    )
