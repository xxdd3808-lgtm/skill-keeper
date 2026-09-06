"""Task 7:manage.py CLI 合同——rescan/plan/apply/status 与网页共用同一引擎与路径。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class ManageCliTests(unittest.TestCase):
    def _run(self, env, *args):
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "manage.py"), *args],
            capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=120)

    def _env(self, td):
        return dict(os.environ, HOME=str(td), SKILL_KEEPER_DATA=str(td / "data"))

    def test_rescan_plan_apply_status_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            env = self._env(Path(td))
            demo = Path(td) / ".agents/skills/demo"
            demo.mkdir(parents=True)
            (demo / "SKILL.md").write_text(
                "---\nname: demo\ndescription: d\nversion: 1.0.0\n---\nbody\n",
                encoding="utf-8")
            r = self._run(env, "rescan", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            inv = json.loads((Path(td) / "data/inventory.json").read_text())
            iid = inv["instances"][0]["instance_id"]

            r = self._run(env, "plan", "remove", "--instance-id", iid,
                          "--reason", "cli test", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            plan = json.loads(r.stdout[r.stdout.index("{"):])
            self.assertTrue(plan["ok"])

            # digest 错误必须失败
            r = self._run(env, "apply", plan["plan_id"], "--digest", "0" * 64,
                          "--confirm", "--json")
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue(demo.exists())

            r = self._run(env, "apply", plan["plan_id"], "--digest", plan["digest"],
                          "--confirm", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            self.assertFalse(demo.exists(), "apply 后目标必须消失")
            result = json.loads(r.stdout[r.stdout.index("{"):])
            self.assertEqual(result["transaction_status"], "committed")
            self.assertEqual(result["snapshot_status"], "fresh")

            r = self._run(env, "status", plan["plan_id"], "--json")
            self.assertEqual(r.returncode, 0)
            status = json.loads(r.stdout[r.stdout.index("{"):])
            self.assertEqual(status["transaction"]["phase"], "committed")

            # 重放:已知结果,不再发生物理变更
            r2 = self._run(env, "apply", plan["plan_id"], "--digest", plan["digest"],
                           "--confirm", "--json")
            self.assertEqual(r2.returncode, 0)
            self.assertTrue(json.loads(r2.stdout[r2.stdout.index("{"):])["already_applied"])

    def test_recover_requires_existing_transaction(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._run(self._env(Path(td)), "recover", "plan-nope", "--json")
            self.assertNotEqual(r.returncode, 0)

    def test_vet_subcommand_feeds_update_plan_apply(self):
        """F09:CLI 安检记账绑定当前计划;plan → vet → apply 全程走统一入口。"""
        from scripts.core.fingerprint import tree_hash

        def write_skill_md(path, body):
            path.mkdir(parents=True, exist_ok=True)
            (path / "SKILL.md").write_text(
                "---\nname: demo\ndescription: d\nversion: 1.0.0\n---\n" + body,
                encoding="utf-8")
            return path

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            env = self._env(td)
            demo = write_skill_md(td / ".agents/skills/demo", "v1")
            r = self._run(env, "rescan", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            inv = json.loads((td / "data/inventory.json").read_text())
            iid = inv["instances"][0]["instance_id"]
            # 已暂存候选 v2(模拟 check_updates 产物)
            staging = write_skill_md(td / "stage" / "cand-v2", "v2 body")
            (td / "data/updates.json").write_text(json.dumps({
                "schema_version": 2,
                "differs": [{"name": "demo", "instance_id": iid,
                             "repo": "example/demo", "commit_sha": "head-x",
                             "candidate_hash": tree_hash(staging),
                             "source_dir": "skills/demo",
                             "staging_path": str(staging)}],
            }), encoding="utf-8")

            r = self._run(env, "plan", "update", "--instance", iid, "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            plan = json.loads(r.stdout[r.stdout.index("{"):])

            # 未安检不得应用
            r = self._run(env, "apply", plan["plan_id"], "--digest", plan["digest"],
                          "--confirm", "--json")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("v1", (demo / "SKILL.md").read_text(encoding="utf-8"))

            r = self._run(env, "vet", plan["plan_id"], "--verdict", "safe",
                          "--evidence", "CLI 复核:已阅读候选全文与差异", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            vet = json.loads(r.stdout[r.stdout.index("{"):])
            self.assertTrue(vet["ok"])

            r = self._run(env, "apply", plan["plan_id"], "--digest", plan["digest"],
                          "--confirm", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            result = json.loads(r.stdout[r.stdout.index("{"):])
            self.assertEqual(result["transaction_status"], "committed")
            self.assertIn("v2 body", (demo / "SKILL.md").read_text(encoding="utf-8"),
                          "更新后安装的必须是已安检候选")


if __name__ == "__main__":
    unittest.main()
