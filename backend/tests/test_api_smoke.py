"""引数の要らない GET が、どれも開くこと。

画面はここを叩いて描く。1つでも 500 になると、その画面だけが白くなる。
機能ごとの試験では、その機能を触ったときしか気づけない。読み手や判断層に
手を入れたときに、関係の無い画面が落ちていないかをここで押さえる。

中身は見ない。開くことだけを見る。中身は各機能の試験で確かめている。
"""

from __future__ import annotations

import pytest

#: 引数が要るので、ここでは呼べないもの。呼べないことに理由があるものだけ挙げる
NEEDS_ARGUMENTS = {"/api/v1/documents/search"}


def _paths(client) -> list[str]:
    schema = client.get("/openapi.json").json()
    return sorted(
        path
        for path, operations in schema["paths"].items()
        if "get" in operations and "{" not in path and path not in NEEDS_ARGUMENTS
    )


def test_every_get_without_arguments_opens(client):
    broken: list[tuple[str, int]] = []
    for path in _paths(client):
        response = client.get(path)
        if response.status_code != 200:
            broken.append((path, response.status_code))
    assert not broken, broken


@pytest.mark.parametrize("path", ["/api/v1/documents/search"])
def test_a_search_without_a_word_is_refused_not_crashed(client, path):
    # 引数が要るものは、落ちるのではなく断ること
    assert client.get(path).status_code == 422
