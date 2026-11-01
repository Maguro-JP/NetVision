#!/usr/bin/env python3
"""inventory.py、scan.py、bump.py を、作ったファイルと写しのデータで確かめる（ネットワークは使わない）。"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scan  # noqa: E402
from inventory import inventory, spec_version  # noqa: E402

SCAN = HERE / "scan.py"
BUMP = HERE / "bump.py"


def make_repo(root: Path):
    repo = root / "app"
    (repo / ".git").mkdir(parents=True)
    (repo / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {
        "": {"name": "app"}, "node_modules/lodash": {"version": "4.17.20"}, "node_modules/a/node_modules/minimist": {"version": "0.0.8", "dev": True}}}), encoding="utf-8")
    (repo / "package.json").write_text(json.dumps({"dependencies": {"lodash": "^4.17.20"}}), encoding="utf-8")
    (repo / "requirements.txt").write_text("requests==2.25.0\nflask>=2.0  # comment\n-r other.txt\nnumpy\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\ndependencies = ["pillow>=9.0", "httpx (>=0.27)"]\n', encoding="utf-8")
    (repo / "Dockerfile").write_text("FROM python:3.11-slim\nFROM scratch\n", encoding="utf-8")
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / ".github/workflows/ci.yml").write_text("steps:\n  - uses: actions/checkout@v4\n  - uses: actions/setup-node@abcdef0123456789abcdef0123456789abcdef01\n", encoding="utf-8")
    (repo / "node_modules/x").mkdir(parents=True)
    (repo / "node_modules/x/package.json").write_text('{"dependencies": {"evil": "1.0.0"}}', encoding="utf-8")
    return repo


class Inventory(unittest.TestCase):
    def test_parses_each_manifest_and_skips_node_modules(self):
        with tempfile.TemporaryDirectory() as d:
            rows = inventory([d])
            make_repo(Path(d))
            rows = inventory([d])
            by = {(r["ecosystem"], r["name"]): r for r in rows}
            self.assertEqual(by[("npm", "lodash")]["version"], "4.17.20")
            self.assertTrue(by[("npm", "lodash")]["exact"])
            self.assertTrue(by[("npm", "minimist")]["dev"])
            self.assertNotIn(("npm", "evil"), by)
            self.assertEqual((by[("PyPI", "requests")]["version"], by[("PyPI", "requests")]["exact"]), ("2.25.0", True))
            self.assertEqual((by[("PyPI", "flask")]["version"], by[("PyPI", "flask")]["exact"]), ("2.0", False))
            self.assertIsNone(by[("PyPI", "numpy")]["version"])
            self.assertEqual(by[("PyPI", "httpx")]["version"], "0.27")
            self.assertEqual(by[("Docker", "python")]["version"], "3.11-slim")
            self.assertNotIn(("Docker", "scratch"), by)
            self.assertFalse(by[("GitHub Actions", "actions/checkout")]["exact"])
            self.assertTrue(by[("GitHub Actions", "actions/setup-node")]["exact"])
            self.assertTrue(all(r["repo"] == "app" for r in rows))

    def test_spec_version(self):
        self.assertEqual(spec_version("^1.2.3"), ("1.2.3", False))
        self.assertEqual(spec_version("==2.0"), ("2.0", True))
        self.assertEqual(spec_version("1.0.0"), ("1.0.0", True))
        self.assertEqual(spec_version("*"), (None, False))
        self.assertEqual(spec_version("1.x"), (None, False))


class Scan(unittest.TestCase):
    def test_cvss_and_severity(self):
        self.assertEqual(scan.cvss3_score("CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:C/C:H/I:N/A:N"), 6.1)
        self.assertEqual(scan.cvss3_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"), 9.8)
        self.assertEqual(scan.severity({"database_specific": {"severity": "HIGH"}}), ("高", None))
        self.assertEqual(scan.severity({"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}]}), ("重大", 9.8))

    def test_affected_by_ranges(self):
        v = {"affected": [{"package": {"ecosystem": "GitHub Actions", "name": "actions/download-artifact"}, "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "4.0.0"}, {"fixed": "4.1.3"}]}]}]}
        self.assertTrue(scan.affected_by(v, "GitHub Actions", "actions/download-artifact", "4.1.2"))
        self.assertFalse(scan.affected_by(v, "GitHub Actions", "actions/download-artifact", "4.1.3"))
        self.assertFalse(scan.affected_by(v, "GitHub Actions", "actions/download-artifact", "3.0.0"))

    def test_fixed_version_picks_smallest_above_current(self):
        v = {"affected": [{"package": {"ecosystem": "PyPI", "name": "requests"}, "ranges": [{"events": [{"introduced": "0"}, {"fixed": "2.20.0"}]}, {"events": [{"introduced": "2.3.0"}, {"fixed": "2.31.0"}]}]}]}
        self.assertEqual(scan.fixed_version(v, "PyPI", "requests", "2.25.0"), "2.31.0")
        self.assertIsNone(scan.fixed_version(v, "npm", "requests", "2.25.0"))

    def test_offline_scan_uses_cache_and_reports(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = make_repo(root / "src")
            cache, state = root / "cache", root / "state"
            (cache / "vulns").mkdir(parents=True)
            # OSV の答えの写し。requests 2.25.0 に 1 件、ほかは無し
            rows = inventory([str(root / "src")])
            queries, keys = [], []
            for r in rows:
                if r["ecosystem"] not in scan.OSV_ECOS or (r["ecosystem"] != "GitHub Actions" and not r["version"]):
                    continue
                q = {"package": {"name": r["name"], "ecosystem": r["ecosystem"]}}
                if r["ecosystem"] != "GitHub Actions":
                    q["version"] = r["version"]
                key = (r["ecosystem"], r["name"], r["version"] if r["ecosystem"] != "GitHub Actions" else None)
                if key not in keys:
                    keys.append(key)
                    queries.append(q)
            results = [{"vulns": [{"id": "GHSA-j8r2-6x86-q33q"}]} if k[1] == "requests" else {"vulns": [{"id": "GHSA-cxww-7g56-2vh6"}]} if k[1] == "actions/checkout" else {} for k in keys]
            name = "batch-" + hashlib.sha1(json.dumps(queries, sort_keys=True).encode()).hexdigest()[:16] + ".json"
            (cache / name).write_text(json.dumps({"results": results}), encoding="utf-8")
            (cache / "vulns/GHSA-j8r2-6x86-q33q.json").write_text(json.dumps({
                "id": "GHSA-j8r2-6x86-q33q", "aliases": ["CVE-2023-32681"], "summary": "Proxy-Authorization leak", "published": "2023-05-26T18:15:14Z",
                "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:C/C:H/I:N/A:N"}], "database_specific": {"severity": "MODERATE"},
                "affected": [{"package": {"ecosystem": "PyPI", "name": "requests"}, "ranges": [{"events": [{"introduced": "2.3.0"}, {"fixed": "2.31.0"}]}]}]}), encoding="utf-8")
            (cache / "kev.json").write_text(json.dumps({"vulnerabilities": [{"cveID": "CVE-2023-32681", "vendorProject": "Python", "product": "requests", "vulnerabilityName": "x", "dateAdded": "2099-01-01"}]}), encoding="utf-8")
            (cache / "jvn.rdf").write_text("<item><title>Node.js に複数の脆弱性</title><link>https://jvn.jp/x</link><dc:date>2099-01-01T00:00+09:00</dc:date><description>d</description></item>", encoding="utf-8")
            (root / "watch.md").write_text("- node.js（実行環境）\n", encoding="utf-8")
            env = dict(os.environ, VULN_WATCH_CACHE=str(cache), VULN_WATCH_STATE=str(state), PYTHONHASHSEED="0")
            out, js = root / "r.md", root / "r.json"
            cmd = [sys.executable, str(SCAN), "--roots", str(root / "src"), "--offline", "--out", str(out), "--json", str(js), "--watch", str(root / "watch.md"), "--mark-seen", "--strict"]
            r = subprocess.run(cmd, capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)  # 悪用済みがあるので --strict は 1
            self.assertIn("悪用済み 1", r.stdout)
            md = out.read_text(encoding="utf-8")
            self.assertIn("| ★ | app | PyPI requests | 2.25.0 | 2.31.0 | 中 6.1 | 悪用済み |", md)
            self.assertIn("Node.js に複数の脆弱性", md)
            self.assertIn("requirements.txt | 2 |", md)  # 版が決まっていない flask と numpy
            data = json.loads(js.read_text(encoding="utf-8"))
            self.assertEqual(len(data["findings"]), 1)
            self.assertEqual([(f["repo"], f["name"], f["ref"]) for f in data["floating"]], [("app", "actions/checkout", "v4")])
            self.assertIn("| app | actions/checkout | v4 | 1 件 |", md)
            self.assertEqual(len(data["new"]), 1)
            # 2 回目は「見た」になり、新規 0
            r = subprocess.run(cmd, capture_output=True, text=True, env=env)
            self.assertIn("新規 0", r.stdout)


class Bump(unittest.TestCase):
    def test_bump_requirements_and_workflow(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            r = subprocess.run([sys.executable, str(BUMP), str(repo), "PyPI", "requests", "2.31.0"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("requests==2.31.0", (repo / "requirements.txt").read_text(encoding="utf-8"))
            r = subprocess.run([sys.executable, str(BUMP), str(repo), "GitHub Actions", "actions/checkout", "v5"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("actions/checkout@v5", (repo / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
            r = subprocess.run([sys.executable, str(BUMP), str(repo), "PyPI", "nothere", "1.0"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
