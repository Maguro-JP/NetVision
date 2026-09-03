"""AI 周りが本当に動くかを、手元に立てたモデルの口で通しで確かめる。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.conftest import API

LOG: list[dict] = []
REPLY = {"text": "OK"}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: ANN002
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("content-length", 0))
        LOG.append(json.loads(self.rfile.read(n) or b"{}"))
        data = json.dumps({"choices": [{"message": {"content": REPLY["text"]}}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def model(monkeypatch, tmp_path):
    from app.config import settings

    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(settings, "llm_url", f"http://127.0.0.1:{srv.server_address[1]}/v1")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(settings, "document_storage_path", str(tmp_path / "documents"))
    LOG.clear()
    yield srv
    srv.shutdown()


CONFIG = """hostname core-01
!
interface Vlan10
 ip address 10.10.10.1 255.255.255.0
!
snmp-server community public RO
"""


def _device(client, device_payload):
    device = client.post(f"{API}/devices", json=device_payload(vendor="Cisco")).json()
    client.post(
        f"{API}/configs/upload",
        files={"file": ("core-01.txt", CONFIG.encode(), "text/plain")},
        data={"device_id": device["id"]},
    )
    return device["id"]


def test_the_status_screen_sees_the_model(client, model):
    body = client.get(f"{API}/llm/status").json()
    assert body["enabled"] is True
    assert body["configured"] is True
    assert any(m["model"] == "test-model" and m["local"] for m in body["models"])


def test_a_draft_actually_goes_through_the_model(client, model, device_payload):
    device_id = _device(client, device_payload)
    REPLY["text"] = (
        "interface Vlan30\n description guest-from-model\n ip address 10.30.30.1 255.255.255.0"
    )
    res = client.post(
        f"{API}/devices/{device_id}/config-draft",
        json={"request": "ゲスト用に VLAN30 を足したい"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert LOG, "モデルに問い合わせていません"
    # 渡すのは現行 config そのものではなく、規則で埋めた結果と足りないところ
    material = LOG[-1]["messages"][-1]["content"]
    assert "ゲスト用に VLAN30 を足したい" in material
    assert "足りないところ" in material
    assert "guest-from-model" in body["body"], "モデルの答えが下書きに入っていません"


def test_the_question_screen_answers_through_the_model(client, model):
    # 用語集に載っている言葉は、モデルへ行く前に用語集が答える
    known = client.post(
        f"{API}/config-check/ask", json={"question": "SNMP とは", "context": CONFIG}
    )
    assert known.json()["source"] == "glossary"
    assert not LOG

    # 用語集に無いことは、モデルへ回る
    REPLY["text"] = "この機器では Vlan10 だけが設定されています"
    res = client.post(
        f"{API}/config-check/ask",
        json={"question": "この機器で使っている帯を教えてください", "context": CONFIG},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "model", body
    assert LOG, "モデルに問い合わせていません"
    assert "Vlan10" in body["answer"]


def test_the_pipeline_runs_with_the_model(client, model, device_payload):
    device_id = _device(client, device_payload)
    REPLY["text"] = "VLAN30 を追加する"
    res = client.post(
        f"{API}/devices/{device_id}/pipeline",
        json={"request": "ゲスト用に VLAN30 を足して"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["stages"]


def test_everything_still_works_when_the_model_is_down(client, monkeypatch, device_payload):
    from app.config import settings

    monkeypatch.setattr(settings, "llm_url", "http://127.0.0.1:1/v1")
    monkeypatch.setattr(settings, "llm_model", "dead")
    monkeypatch.setattr(settings, "llm_enabled", True)
    device_id = _device(client, device_payload)
    res = client.post(
        f"{API}/devices/{device_id}/config-draft",
        json={"request": "ゲスト用に VLAN30 を足したい"},
    )
    assert res.status_code == 200, res.text
    ask = client.post(f"{API}/config-check/ask", json={"question": "SNMP とは", "context": CONFIG})
    assert ask.status_code == 200, ask.text


def test_a_broken_answer_does_not_break_the_tool(client, model, device_payload):
    device_id = _device(client, device_payload)
    REPLY["text"] = "{壊れた JSON " + "あ" * 500
    res = client.post(
        f"{API}/devices/{device_id}/config-draft",
        json={"request": "ゲスト用に VLAN30 を足したい"},
    )
    assert res.status_code == 200, res.text
