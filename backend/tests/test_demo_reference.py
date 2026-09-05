"""デモで配る資料（テンプレート・弱点への対処）が、そのまま使える形か。

画面に並ぶだけでは足りない。実際に描画して、出てきたコマンドが機器に
当てられる形になっているかまで見る。
"""

import json
import pathlib

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

FOLDER = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "demo_reference"


def _load(name: str) -> list[dict]:
    return json.loads((FOLDER / name).read_text(encoding="utf-8"))


def test_every_template_renders_with_its_own_declared_variables() -> None:
    """宣言した変数だけで描画できること。

    足りないと、画面で選んだ人が「何を入れればいいのか」を推測することに
    なる。余っていると、入れた値がどこにも出ない。
    """
    env = SandboxedEnvironment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    for entry in _load("config_templates.json"):
        declared = {item["name"]: item for item in entry["variables"]}
        values = {name: item.get("default") or "1" for name, item in declared.items()}
        rendered = env.from_string(entry["body"]).render(**values)
        assert rendered.strip(), entry["name"]
        # 本文が使う変数は、すべて宣言されている
        used = set()
        for token in entry["body"].split("{{")[1:]:
            used.add(token.split("}}")[0].strip())
        assert used <= set(declared), (entry["name"], used - set(declared))


def test_no_template_carries_a_secret() -> None:
    """合言葉や鍵をテンプレートに書かない。配ると、そのまま漏れる。"""
    for entry in _load("config_templates.json"):
        body = entry["body"]
        for line in body.splitlines():
            if line.lstrip().startswith("!"):
                continue  # 注意書きは対象外
            assert "snmp-server community" not in line, entry["name"]
            assert "password 7 " not in line, entry["name"]
            assert "key-string" not in line, entry["name"]


def test_every_remediation_points_at_a_known_weakness() -> None:
    """対処が、登録されていない CVE を指していないこと。"""
    cve_ids = {v["cve_id"] for v in _load("vulnerabilities.json")}
    for entry in _load("remediations.json"):
        assert entry["cve_id"] in cve_ids, entry["cve_id"]
        assert entry["summary"].strip(), entry["cve_id"]
        assert entry["kind"] in {
            "upgrade",
            "workaround",
            "mitigation",
            "accepted",
            "not_applicable",
        }, entry["kind"]
