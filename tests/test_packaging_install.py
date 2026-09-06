"""Task 1(v4):打包安装合同 —— `pip install .` 到临时环境后可离线运行统一 CLI。

安装只发生在测试开头(一次性);装好后的 doctor/scan/report 全部离线运行。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class PackagingInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory(prefix="sk-pkg-")
        venv = Path(cls._td.name) / "venv"
        built = subprocess.run([sys.executable, "-m", "venv", str(venv)],
                               capture_output=True, text=True, timeout=300)
        if built.returncode != 0:
            raise AssertionError("venv 创建失败:\n" + built.stderr[-800:])
        bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
        exe = ".exe" if os.name == "nt" else ""
        cls.skill_keeper = bin_dir / ("skill-keeper" + exe)
        venv_python = bin_dir / ("python" + exe)
        installed = subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--disable-pip-version-check",
             "--no-deps", str(REPO_ROOT)],
            capture_output=True, text=True, timeout=600, cwd=str(REPO_ROOT))
        if installed.returncode != 0:
            raise AssertionError("pip install . 失败:\n"
                                 + installed.stdout[-1200:] + installed.stderr[-1200:])
        cls._home = Path(cls._td.name) / "home"
        cls._home.mkdir()
        cls._venv_python = venv_python

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _env(self, **extra):
        e = dict(os.environ)
        e.pop("SKILL_KEEPER_DATA", None)
        e.pop("SKILL_KEEPER_STAGING", None)
        e["HOME"] = str(self._home)
        e.update(extra)
        return e

    def _run(self, *args, **kw):
        return subprocess.run([str(self.skill_keeper), *args], capture_output=True,
                              text=True, timeout=240, cwd=tempfile.gettempdir(), **kw)

    def test_installed_cli_help_runs_outside_repo(self):
        r = self._run("--help", env=self._env())
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        for cmd in ("scan", "report", "manage", "doctor"):
            self.assertIn(cmd, r.stdout)

    def test_installed_doctor_uses_new_default_layout(self):
        r = self._run("doctor", "--json", env=self._env())
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        text = r.stdout
        payload = json.loads(text[text.index("{"):])
        self.assertEqual(payload["layout"], "new", "安装态没有 v2/v3 标记,必须走新默认")
        self.assertTrue(payload["paths"]["data_dir"].replace("\\", "/")
                        .endswith(".skill-keeper/data"))
        self.assertTrue(payload["paths"]["backup_dir"].replace("\\", "/")
                        .endswith(".skill-keeper/backups"))

    def test_installed_scan_report_offline(self):
        from tests.helpers import copy_private_v311_fixture
        home, data = copy_private_v311_fixture(self)
        env = self._env(HOME=str(home), SKILL_KEEPER_DATA=str(data))
        r = self._run("scan", "--json", env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        text = r.stdout
        summary = json.loads(text[text.index("{"):])
        self.assertEqual(summary["total"], 7)
        r = self._run("report", "--json", env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        text = r.stdout
        self.assertEqual(json.loads(text[text.index("{"):])["total"], 7)

    def test_installed_package_ships_report_js(self):
        """F01:报告交互脚本(.js 资源)必须随 pip install 分发并可加载。"""
        code = ("import scripts.report as r, pathlib;"
                "p = pathlib.Path(r.__file__).parent / 'assets' / 'report.js';"
                "print(int(p.is_file()), len(r.JS_BLOB))")
        r = subprocess.run([str(self._venv_python), "-c", code],
                           capture_output=True, text=True, timeout=60,
                           cwd=tempfile.gettempdir())
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        shipped, blob_len = r.stdout.strip().split()
        self.assertEqual(shipped, "1", "scripts/assets/report.js 未进安装包")
        self.assertGreater(int(blob_len), 0, "安装态 JS_BLOB 必须能从资源加载")

    def test_installed_new_default_layout_full_roundtrip(self):
        """任务2:真安装态(BASE 无 v2/v3 标记)下新默认 ~/.skill-keeper 布局
        完整入口回归——doctor→scan→report→plan→apply→备份可见→restore。"""
        from tests.helpers import write_skill
        demo = write_skill(self._home / ".agents/skills", "demo", description="layout")
        env = self._env()
        r = self._run("doctor", "--json", env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        doc = json.loads(r.stdout[r.stdout.index("{"):])
        self.assertEqual(doc["layout"], "new")
        backup_dir = Path(doc["paths"]["backup_dir"])
        data_dir = Path(doc["paths"]["data_dir"])

        r = self._run("scan", "--json", env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        inv = json.loads((data_dir / "inventory.json").read_text())
        iid = inv["instances"][0]["instance_id"]
        r = self._run("report", "--json", env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])

        r = self._run("manage", "plan", "remove", "--instance-id", iid,
                      "--reason", "layout roundtrip", "--json", env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        plan = json.loads(r.stdout[r.stdout.index("{"):])
        r = self._run("manage", "apply", plan["plan_id"], "--digest", plan["digest"],
                      "--confirm", "--json", env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertFalse(demo.exists())

        backups = sorted(backup_dir.glob("backup-*.tar.gz"))
        self.assertTrue(backups, "新默认布局的备份必须落在 ~/.skill-keeper/backups")
        r = self._run("manage", "plan", "restore",
                      "--backup-id", backups[-1].name[len("backup-"):-len(".tar.gz")],
                      "--json", env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        plan = json.loads(r.stdout[r.stdout.index("{"):])
        r = self._run("manage", "apply", plan["plan_id"], "--digest", plan["digest"],
                      "--confirm", "--json", env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertTrue((demo / "SKILL.md").exists(), "恢复必须把实体放回原位")


if __name__ == "__main__":
    unittest.main()
