"""Task 1(v4):统一 CLI 与运行态解析 —— `python -m scripts.cli`、doctor 与默认路径合同。

优先级合同:显式参数 > 环境变量 > 可识别旧仓库运行态(v2/v3 标记)> 新默认 ~/.skill-keeper。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]

from scripts.core.runtime import (BASE, RuntimePaths, default_layout_dirs,   # noqa: E402
                                  detect_repo_layout)
from tests.helpers import copy_private_v311_fixture                          # noqa: E402


def _cli_env(home=None, **extra):
    e = dict(os.environ)
    e.pop("SKILL_KEEPER_DATA", None)
    e.pop("SKILL_KEEPER_STAGING", None)
    if home is not None:
        e["HOME"] = str(home)
    e.update(extra)
    return e


def _run_cli(args, env=None, cwd=REPO_ROOT, timeout=240):
    return subprocess.run([sys.executable, "-m", "scripts.cli", *args],
                          capture_output=True, text=True, env=env or _cli_env(),
                          cwd=str(cwd), timeout=timeout)


def _json_out(text):
    return json.loads(text[text.index("{"):])


class UnifiedCliTests(unittest.TestCase):
    def test_python_m_cli_help_lists_commands(self):
        r = _run_cli(["--help"])
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        for cmd in ("scan", "report", "manage", "doctor"):
            self.assertIn(cmd, r.stdout)

    def test_doctor_json_reports_version_python_paths_lock_locations(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run_cli(["doctor", "--json"], env=_cli_env(home=td))
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            payload = _json_out(r.stdout)
        for key in ("version", "python", "layout", "paths", "lock_backend",
                    "registered_locations"):
            self.assertIn(key, payload, key)
        self.assertTrue(payload["version"])
        self.assertTrue(payload["python"])
        self.assertIn(payload["lock_backend"], ("fcntl", "msvcrt", "none"))
        # 与本仓库实际状态一致:真实 v2/v3 运行态 → 旧仓库布局;否则新默认
        if detect_repo_layout(BASE) == "old-repo":
            self.assertEqual(Path(payload["paths"]["data_dir"]), BASE / "data")
            self.assertEqual(payload["layout"], "old-repo")
        else:
            self.assertEqual(Path(payload["paths"]["data_dir"]),
                             Path(td) / ".skill-keeper" / "data")
            self.assertEqual(payload["layout"], "new")

    def test_doctor_counts_registered_locations(self):
        home, data = copy_private_v311_fixture(self)
        (data / "client-locations.json").write_text(json.dumps({
            "locations": [{"location_id": "legacy-user", "client": "legacy-tool",
                           "path": str(home / "legacy/skills"), "kind": "user",
                           "mutable": False}]}), encoding="utf-8")
        r = _run_cli(["doctor", "--json"], env=_cli_env(home=home, SKILL_KEEPER_DATA=str(data)))
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        payload = _json_out(r.stdout)
        self.assertEqual(payload["registered_locations"]["count"], 1)
        self.assertEqual(payload["registered_locations"]["ids"], ["legacy-user"])

    def test_cli_scan_and_report_match_legacy_semantics(self):
        home, data = copy_private_v311_fixture(self)
        env = _cli_env(home=home, SKILL_KEEPER_DATA=str(data))
        r = _run_cli(["scan", "--json"], env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        summary = _json_out(r.stdout)
        self.assertEqual((summary["total"], summary["instances"], summary["locations"]),
                         (7, 9, 4))
        r = _run_cli(["report", "--json"], env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        view = _json_out(r.stdout)
        self.assertEqual(view["total"], 7)

    def test_cli_manage_loop_uses_same_engine(self):
        home, data = copy_private_v311_fixture(self)
        env = _cli_env(home=home, SKILL_KEEPER_DATA=str(data))
        self.assertEqual(_run_cli(["scan"], env=env).returncode, 0)
        inv = json.loads((data / "inventory.json").read_text(encoding="utf-8"))
        wb = next(i for i in inv["instances"] if i["directory_name"] == "wb-only")
        wb_path = Path(wb["path"])

        r = _run_cli(["manage", "plan", "remove", "--instance-id", wb["instance_id"],
                      "--reason", "cli v4 冒烟", "--json"], env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        plan = _json_out(r.stdout)
        self.assertTrue(plan["ok"])

        r = _run_cli(["manage", "apply", plan["plan_id"], "--digest", "0" * 64,
                      "--confirm", "--json"], env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(wb_path.exists())

        r = _run_cli(["manage", "apply", plan["plan_id"], "--digest", plan["digest"],
                      "--confirm", "--json"], env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertFalse(wb_path.exists())

        r = _run_cli(["manage", "status", plan["plan_id"], "--json"], env=env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(_json_out(r.stdout)["transaction"]["phase"], "committed")


class RuntimeResolutionTests(unittest.TestCase):
    def test_default_layout_without_markers_uses_skill_keeper_home(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            bare = Path(td) / "checkout"
            bare.mkdir()
            empty_data = Path(td) / "empty-data-repo"
            (empty_data / "data").mkdir(parents=True)
            for base in (bare, empty_data):
                d = default_layout_dirs(base=base, home=home)
                self.assertEqual(d["layout"], "new")
                self.assertEqual(d["data_dir"], home / ".skill-keeper" / "data")
                self.assertEqual(d["staging_dir"], home / ".skill-keeper" / "cache" / "staging")
                self.assertEqual(d["backup_dir"], home / ".skill-keeper" / "backups")

    def test_default_layout_with_v2_markers_keeps_repo(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            base = Path(td) / "repo"
            (base / "data").mkdir(parents=True)
            expected_staging = (home / "Library/Caches/skill-keeper/staging"
                                if sys.platform == "darwin"
                                else home / ".cache/skill-keeper/staging")
            markers = [("inventory.json", "file"), ("audit-v2.jsonl", "file"),
                       ("change-plans", "dir")]
            for name, kind in markers:
                path = base / "data" / name
                if kind == "file":
                    path.write_text("{}", encoding="utf-8")
                else:
                    path.mkdir()
                d = default_layout_dirs(base=base, home=home)
                self.assertEqual(d["layout"], "old-repo", name)
                self.assertEqual(d["data_dir"], base / "data")
                self.assertEqual(d["backup_dir"], base / "backups")
                self.assertEqual(d["staging_dir"], expected_staging)
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()

    def test_runtime_paths_priority_explicit_over_env(self):
        with mock.patch.dict(os.environ, {"SKILL_KEEPER_DATA": "/env-data",
                                          "SKILL_KEEPER_STAGING": "/env-stage"}, clear=True):
            explicit = RuntimePaths(data_dir="/x-data", staging_dir="/x-stage")
            self.assertEqual((explicit.data_dir, explicit.staging_dir),
                             (Path("/x-data"), Path("/x-stage")))
            self.assertEqual(explicit.backup_dir, Path("/x-data") / "backups")
            self.assertEqual(explicit.layout, "explicit")
            env_row = RuntimePaths()
            self.assertEqual((env_row.data_dir, env_row.staging_dir),
                             (Path("/env-data"), Path("/env-stage")))
            self.assertEqual(env_row.backup_dir, Path("/env-data") / "backups")
            self.assertEqual(env_row.layout, "env")

    def test_runtime_paths_layout_resolution(self):
        layout = detect_repo_layout(BASE)
        with mock.patch.dict(os.environ, {}, clear=True):
            row = RuntimePaths()
            self.assertEqual(row.layout, layout)
            if layout == "old-repo":
                self.assertEqual(row.data_dir, BASE / "data")
                self.assertEqual(row.backup_dir, BASE / "backups")
            else:
                self.assertEqual(row.data_dir, row.home / ".skill-keeper" / "data")
                self.assertEqual(row.backup_dir, row.home / ".skill-keeper" / "backups")

    def test_runtime_paths_new_layout_for_injected_base(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "fresh-checkout"
            base.mkdir()
            home = Path(td) / "home"
            home.mkdir()
            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch("scripts.core.runtime.BASE", base):
                    row = RuntimePaths(home=home)
            self.assertEqual(row.layout, "new")
            self.assertEqual(row.data_dir, home / ".skill-keeper" / "data")
            self.assertEqual(row.staging_dir, home / ".skill-keeper" / "cache" / "staging")
            self.assertEqual(row.backup_dir, home / ".skill-keeper" / "backups")

    def test_entry_scripts_share_resolution(self):
        """scan/report 等入口的 data_dir 默认值必须与 RuntimePaths 同一解析链。"""
        from scripts.scan import data_dir as scan_data_dir
        from scripts.report import data_dir as report_data_dir
        with mock.patch.dict(os.environ, {}, clear=True):
            layout = detect_repo_layout(BASE)
            if layout == "old-repo":
                self.assertEqual(scan_data_dir(), BASE / "data")
                self.assertEqual(report_data_dir(), BASE / "data")
            else:
                self.assertEqual(scan_data_dir(),
                                 Path(os.path.expanduser("~")) / ".skill-keeper" / "data")


if __name__ == "__main__":
    unittest.main()
