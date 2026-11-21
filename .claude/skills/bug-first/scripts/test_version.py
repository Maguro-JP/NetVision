#!/usr/bin/env python3
"""version.py（版のある場所を探し、patch を 1 つ上げる）を、作ったリポジトリで確かめる。

修正版で版を一部だけ上げると、インストーラーと中身の版がずれる。場所を全部見つけること、版の文字だけを変えること、
X.Y.Z でない版に触らないこと、版の無いリポジトリで何もしないことを見る。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import version  # noqa: E402


def run(root, *args):
    r = subprocess.run([sys.executable, str(HERE / "version.py"), "--root", str(root), *args], capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def put(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def get(self, rel):
        return (self.root / rel).read_text(encoding="utf-8")

    def bumped(self):
        rows = version.plan(self.root)
        version.apply(self.root, rows)
        return {r["path"]: (r["version"], r["new"]) for r in rows}


class Bump(unittest.TestCase):
    def test_patch(self):
        self.assertEqual(version.bump("1.2.3"), "1.2.4")
        self.assertEqual(version.bump("0.9.9"), "0.9.10")
        self.assertEqual(version.bump("2026.11.0"), "2026.11.1")

    def test_other_shapes_are_left(self):
        for v in ("1.2.3-beta.1", "1.2", "v1.2.3", "2026.11.21.1", "", "workspace"):
            self.assertIsNone(version.bump(v), v)


class Node(Base):
    PKG = '{\n  "name": "app",\n  "version": "1.2.3",\n  "dependencies": {\n    "left": "1.2.3"\n  },\n  "volta": {\n    "version": "1.2.3"\n  }\n}\n'
    LOCK = ('{\n  "name": "app",\n  "version": "1.2.3",\n  "lockfileVersion": 3,\n  "packages": {\n    "": {\n      "name": "app",\n      "version": "1.2.3"\n    },\n'
            '    "node_modules/left": {\n      "version": "1.2.3"\n    }\n  }\n}\n')

    def test_package_and_lock(self):
        self.put("package.json", self.PKG)
        self.put("package-lock.json", self.LOCK)
        self.put("node_modules/left/package.json", '{"name": "left", "version": "1.2.3"}')
        got = self.bumped()
        self.assertEqual(got, {"package.json": ("1.2.3", "1.2.4"), "package-lock.json": ("1.2.3", "1.2.4")})
        pkg = json.loads(self.get("package.json"))
        self.assertEqual(pkg["version"], "1.2.4")
        self.assertEqual(pkg["dependencies"]["left"], "1.2.3")   # 依存の版には触らない
        self.assertEqual(pkg["volta"]["version"], "1.2.3")       # 入れ子の version にも触らない
        lock = json.loads(self.get("package-lock.json"))
        self.assertEqual((lock["version"], lock["packages"][""]["version"]), ("1.2.4", "1.2.4"))
        self.assertEqual(lock["packages"]["node_modules/left"]["version"], "1.2.3")
        self.assertEqual(self.get("node_modules/left/package.json"), '{"name": "left", "version": "1.2.3"}')

    def test_old_lockfile_keeps_dependencies(self):
        # packages の無い古い形の package-lock.json。同じ番号の依存が近くにあっても触らない
        self.put("package.json", self.PKG)
        self.put("package-lock.json", '{\n  "name": "app",\n  "version": "1.2.3",\n  "dependencies": {\n    "left": {\n      "version": "1.2.3"\n    }\n  }\n}\n')
        self.bumped()
        lock = json.loads(self.get("package-lock.json"))
        self.assertEqual((lock["version"], lock["dependencies"]["left"]["version"]), ("1.2.4", "1.2.3"))

    def test_only_the_version_text_changes(self):
        self.put("package.json", self.PKG)
        self.bumped()
        self.assertEqual(self.get("package.json"), self.PKG.replace('"version": "1.2.3",\n  "dependencies"', '"version": "1.2.4",\n  "dependencies"'))


class Python(Base):
    def test_pyproject_and_dunder(self):
        self.put("pyproject.toml", '[build-system]\nrequires = ["hatchling"]\n\n[project]\nname = "tool"\nversion = "0.4.9"  # 版\n\n[tool.other]\nversion = "9.9.9"\n')
        self.put("src/tool/__init__.py", '"""tool"""\n__version__ = "0.4.9"\n')
        self.put("tests/__init__.py", '__version__ = "0.0.1"\n')
        got = self.bumped()
        self.assertEqual(got, {"pyproject.toml": ("0.4.9", "0.4.10"), "src/tool/__init__.py": ("0.4.9", "0.4.10")})
        self.assertIn('version = "0.4.10"  # 版', self.get("pyproject.toml"))
        self.assertIn('[tool.other]\nversion = "9.9.9"', self.get("pyproject.toml"))
        self.assertEqual(self.get("tests/__init__.py"), '__version__ = "0.0.1"\n')

    def test_dynamic_version_and_setup_files(self):
        self.put("pyproject.toml", '[project]\nname = "tool"\ndynamic = ["version"]\n')
        self.put("tool/_version.py", "__version__ = '1.0.0'\n")
        self.put("setup.cfg", "[metadata]\nname = tool\nversion = 1.0.0\n")
        self.put("setup.py", 'from setuptools import setup\nsetup(name="tool", version="1.0.0")\n')
        got = self.bumped()
        self.assertEqual(set(got), {"tool/_version.py", "setup.cfg", "setup.py"})
        self.assertIn("__version__ = '1.0.1'", self.get("tool/_version.py"))
        self.assertIn("version = 1.0.1", self.get("setup.cfg"))
        self.assertIn('version="1.0.1"', self.get("setup.py"))

    def test_attr_version_is_left(self):
        self.put("setup.cfg", "[metadata]\nversion = attr: tool.__version__\n")
        self.assertEqual(version.places(self.root), [])


class Tauri(Base):
    def test_all_places(self):
        self.put("package.json", '{\n  "name": "app",\n  "version": "0.3.1"\n}\n')
        self.put("src-tauri/Cargo.toml", '[package]\nname = "app"\nversion = "0.3.1"\nedition = "2021"\n\n[dependencies]\nserde = { version = "1.0.1" }\n')
        self.put("src-tauri/Cargo.lock", '[[package]]\nname = "serde"\nversion = "0.3.1"\n\n[[package]]\nname = "app"\nversion = "0.3.1"\ndependencies = [\n "serde",\n]\n')
        self.put("src-tauri/tauri.conf.json", '{\n  "productName": "App",\n  "version": "0.3.1",\n  "bundle": {\n    "windows": {\n      "wix": {\n        "version": "0.3.1"\n      }\n    }\n  }\n}\n')
        got = self.bumped()
        self.assertEqual(set(got), {"package.json", "src-tauri/Cargo.toml", "src-tauri/Cargo.lock", "src-tauri/tauri.conf.json"})
        self.assertIn('name = "app"\nversion = "0.3.2"\nedition', self.get("src-tauri/Cargo.toml"))
        self.assertIn('serde = { version = "1.0.1" }', self.get("src-tauri/Cargo.toml"))
        lock = self.get("src-tauri/Cargo.lock")
        self.assertIn('name = "app"\nversion = "0.3.2"', lock)
        self.assertIn('name = "serde"\nversion = "0.3.1"', lock)  # 同じ番号の別のパッケージには触らない
        conf = json.loads(self.get("src-tauri/tauri.conf.json"))
        self.assertEqual((conf["version"], conf["bundle"]["windows"]["wix"]["version"]), ("0.3.2", "0.3.1"))

    def test_v1_config_and_pointer(self):
        self.put("src-tauri/tauri.conf.json", '{\n  "package": {\n    "productName": "App",\n    "version": "1.0.0"\n  }\n}\n')
        self.assertEqual(self.bumped(), {"src-tauri/tauri.conf.json": ("1.0.0", "1.0.1")})
        self.put("src-tauri/tauri.conf.json", '{\n  "version": "../package.json"\n}\n')
        rows = version.plan(self.root)
        self.assertEqual((rows[0]["new"], rows[0]["note"]), ("", "別のファイルの版を指している"))

    def test_workspace_version_is_left(self):
        self.put("Cargo.toml", '[package]\nname = "app"\nversion.workspace = true\n')
        self.assertEqual(version.places(self.root), [])


class Edges(Base):
    def test_no_version_anywhere(self):
        self.put("README.md", "# x\n")
        self.put("scripts/run.py", "print('x')\n")
        self.assertEqual(version.plan(self.root), [])
        code, out = run(self.root, "--bump", "--apply")
        self.assertEqual(code, 0)
        self.assertIn("版のある場所は見つかりません", out)
        self.assertIn("上げる場所がありません", out)

    def test_odd_shape_is_reported_and_untouched(self):
        self.put("package.json", '{\n  "version": "1.2.3-beta.1"\n}\n')
        rows = version.plan(self.root)
        self.assertEqual((rows[0]["new"], rows[0]["note"]), ("", "X.Y.Z の形ではないので触らない"))
        version.apply(self.root, rows)
        self.assertIn("1.2.3-beta.1", self.get("package.json"))

    def test_dry_run_does_not_write(self):
        self.put("VERSION", "2.0.0\n")
        code, out = run(self.root, "--bump")
        self.assertEqual(code, 0)
        self.assertIn("2.0.0 → 2.0.1", out)
        self.assertIn("書いていません", out)
        self.assertEqual(self.get("VERSION"), "2.0.0\n")
        code, out = run(self.root, "--bump", "--apply")
        self.assertIn("1 か所の版を上げました", out)
        self.assertEqual(self.get("VERSION"), "2.0.1\n")

    def test_mismatch_is_reported(self):
        self.put("package.json", '{\n  "version": "1.2.3"\n}\n')
        self.put("VERSION", "1.2.2\n")
        code, out = run(self.root)
        self.assertIn("版が場所によって違います（1.2.2、1.2.3）", out)

    def test_two_crates_in_one_lock_with_longer_version(self):
        # 同じ Cargo.lock に 2 行あり、桁が増える（0.1.9 → 0.1.10）。後ろから置き換えないと、位置がずれる
        self.put("a/Cargo.toml", '[package]\nname = "a"\nversion = "0.1.9"\n')
        self.put("b/Cargo.toml", '[package]\nname = "b"\nversion = "0.1.9"\n')
        self.put("Cargo.lock", '[[package]]\nname = "a"\nversion = "0.1.9"\n\n[[package]]\nname = "b"\nversion = "0.1.9"\n')
        self.bumped()
        self.assertEqual(self.get("Cargo.lock"), '[[package]]\nname = "a"\nversion = "0.1.10"\n\n[[package]]\nname = "b"\nversion = "0.1.10"\n')

    def test_json_output_tag_and_workflow(self):
        self.put("package.json", '{\n  "version": "1.2.3"\n}\n')
        self.put(".github/workflows/release.yml", "name: release\non:\n  push:\n    tags:\n      - 'v*'\n  workflow_dispatch:\njobs: {}\n")
        self.put(".github/workflows/ci.yml", "name: ci\non:\n  pull_request:\njobs: {}\n")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        git = ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "x"], check=True, capture_output=True)
        code, out = run(self.root, "--json")
        d = json.loads(out)
        self.assertEqual(d["places"], [{"path": "package.json", "version": "1.2.3", "new": "1.2.4", "note": ""}])
        self.assertEqual(d["release_workflows"], [{"path": ".github/workflows/release.yml", "trigger": "タグの push で動く、手で動かせる"}])
        self.assertEqual((d["last_tag"], d["next_tag"]), ("", "v1.2.4"))
        code, out = run(self.root)
        self.assertIn("リリースの workflow: .github/workflows/release.yml（タグの push で動く、手で動かせる）", out)
        self.assertIn("最後のタグ: 無し", out)
        # 今までのタグの形（v の有無）に、次のタグを合わせる
        for name in ("1.2.2", "1.2.3", "nightly"):
            subprocess.run(git + ["tag", name], check=True, capture_output=True)
        d = json.loads(run(self.root, "--json")[1])
        self.assertEqual((d["last_tag"], d["next_tag"]), ("1.2.3", "1.2.4"))
        self.assertIn("最後のタグ: 1.2.3（次のタグの形: 1.2.4）", run(self.root)[1])


if __name__ == "__main__":
    unittest.main()
