"""画面に出てくる言葉の説明を返す口。

用語を知らないまま使えるほうが正しい。分からない言葉が1つあると、
そこで手が止まり、結局くわしい人に聞くことになる。
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import glossary as service

router = APIRouter(tags=["glossary"])


class TermRead(BaseModel):
    term: str
    reading: str
    plain: str
    matters: str
    also: list[str]


@router.get("/glossary", response_model=list[TermRead])
def list_terms(q: str | None = Query(default=None, description="語で絞る")) -> list[TermRead]:
    """言葉の一覧。説明の中身にも当たるので、言葉を知らなくても探せる。"""
    return [
        TermRead(
            term=term.term,
            reading=term.reading,
            plain=term.plain,
            matters=term.matters,
            also=list(term.also),
        )
        for term in service.search(q or "")
    ]
