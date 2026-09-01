import os, subprocess, sys, unittest
from pathlib import Path

from scripts.core.changes import (ChangeContext, ChangeError, apply_plan,
                                  create_remove_plan, plan_digest, write_plan)
from scripts.core.fingerprint import instance_id, tree_hash
from scripts.core.io import FileLock
from scripts.core.models import ChangePlan, Location
from tests.helpers import temp_home, write_skill

REPO_ROOT = Path(__file__).resolve().parents[1]


def change_env(testcase):
    """最小变更环境:shared 位置一个 demo skill + 完整 ChangeContext(全部在临时目录)。"""
    home = temp_home(testcase)
    data = home / "data"
    plans_dir = data / "change-plans"
    shared_root = home / ".agents/skills"
    demo = write_skill(shared_root, "demo", body="demo")
    (demo / "run.py").write_text("x", encoding="utf-8")
    loc = Location("shared", "shared", str(shared_root), "user", True, ("t",))
    iid = instance_id("shared", "demo", str(demo))
    inventory = {"schema_version": 2, "locations": [loc.to_dict()],
                 "instances": [{"instance_id": iid, "location_id": "shared",
                                "directory_name": "demo", "path": str(demo),
                                "real_path": str(demo), "tree_hash": tree_hash(demo),
                                "mutable": True, "is_symlink": False, "is_skill": True,
                                "logical_name": "demo"}]}
    ctx = ChangeContext(data_dir=data, plans_dir=plans_dir, backup_dir=home / "backups",
                        audit_path=data / "audit-v2.jsonl", lock_path=data / ".change.lock",
                        load_inventory=lambda: inventory)
    env = type("Env", (), {})()
    env.home, env.data, env.plans_dir = home, data, plans_dir
    env.inventory, env.context, env.iid = inventory, ctx, iid
    env.agents_root, env.skill_path = shared_root, demo
    env.remove_plan = lambda: create_remove_plan([env.iid], env.inventory, "test", env.plans_dir)
    env.last_audit = lambda: read_audit_tail(ctx.audit_path)
    return env


def read_audit_tail(path, n=1):
    from scripts.core.audit import read_audit
    rows = read_audit(path)
    return rows[-n:][0]


class ChangeRemoveTests(unittest.TestCase):
    def test_arbitrary_names_cannot_be_removed(self):
        env = change_env(self)
        for raw in (".", "..", "/tmp/x", "a/b", "x;touch-pwn", "", None, "demo"):
            with self.assertRaises(ChangeError, msg=repr(raw)):
                create_remove_plan([raw], env.inventory, "test", env.plans_dir)
        self.assertTrue(env.agents_root.exists())
        self.assertTrue(env.skill_path.exists(), "任意目录名绝不能成为删除目标")

    def test_immutable_targets_only(self):
        env = change_env(self)
        inv = dict(env.inventory)
        inv["instances"] = [dict(env.inventory["instances"][0], mutable=False)]
        with self.assertRaises(ChangeError):
            create_remove_plan([env.iid], inv, "test", env.plans_dir)

    def test_builtin_app_registered_target_refuses_individual_removal(self):
        """登记为 builtin-app 的 skill 不能单独删除;处置走所属客户端(更新或卸载)。"""
        env = change_env(self)
        with self.assertRaises(ChangeError):
            create_remove_plan([env.iid], env.inventory, "test", env.plans_dir,
                               known_sources={"demo": {"type": "builtin-app"}})
        self.assertTrue(env.skill_path.exists(), "builtin-app 目标不得生成删除计划")
        # 未登记时照常可计划(保护只针对客户端托管身份)
        plan = create_remove_plan([env.iid], env.inventory, "test", env.plans_dir,
                                  known_sources={"other": {"type": "builtin-app"}})
        self.assertEqual(plan.target_ids, (env.iid,))

    def test_update_plan_refuses_builtin_app_target(self):
        from scripts.core.changes import create_update_plan
        env = change_env(self)
        staging = env.home / "data/staging/cand"
        staging.mkdir(parents=True)
        write_skill(staging, "cand", body="v2")
        snap = {"instance_id": env.iid, "staging_path": str(staging),
                "candidate_hash": tree_hash(staging), "repo": "example/demo"}
        with self.assertRaises(ChangeError):
            create_update_plan(env.iid, snap, env.inventory, env.plans_dir,
                               known_sources={"demo": {"type": "builtin-app"}})

    def test_apply_requires_exact_digest_and_rolls_back_on_verify_failure(self):
        env = change_env(self)
        plan = env.remove_plan()
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, "wrong", True, env.context)
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, "true", env.context)  # 伪确认
        self.assertTrue(env.skill_path.exists())
        env.context.verify_after_apply = lambda: False
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue(env.skill_path.exists(), "验证失败必须自动回滚")
        self.assertEqual(env.last_audit()["rollback_status"], "restored")
        self.assertEqual(env.last_audit()["status"], "failed")

    def test_successful_removal_audited_and_targets_gone(self):
        env = change_env(self)
        plan = env.remove_plan()
        result = apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertFalse(env.skill_path.exists())
        self.assertTrue(env.agents_root.exists(), "只删除目标实体,位置根目录保留")
        self.assertEqual(env.last_audit()["status"], "success")
        self.assertEqual(result["backup_id"], env.last_audit()["backup_id"])
        self.assertTrue(result["backup_path"])

    def test_expired_plan_is_rejected(self):
        env = change_env(self)
        iid = env.iid
        row = {"plan_id": "plan-old", "action": "remove", "target_ids": [iid],
               "preconditions": [["tree_hash:" + iid, env.inventory["instances"][0]["tree_hash"]],
                                 ["path:" + iid, str(env.skill_path)]],
               "summary": "t", "created_at": "2020-01-01 00:00:00",
               "expires_at": "2020-01-01 00:30:00"}
        row["digest"] = plan_digest(row)
        write_plan(ChangePlan.from_dict(row), env.plans_dir)
        with self.assertRaises(ChangeError):
            apply_plan("plan-old", row["digest"], True, env.context)
        self.assertTrue(env.skill_path.exists())

    def test_second_concurrent_change_fails_safely(self):
        env = change_env(self)
        plan = env.remove_plan()
        with FileLock(env.context.lock_path):
            with self.assertRaises(ChangeError):
                apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue(env.skill_path.exists())

    def test_precondition_hash_change_blocks_apply(self):
        env = change_env(self)
        plan = env.remove_plan()
        (env.skill_path / "run.py").write_text("changed-after-plan", encoding="utf-8")
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue(env.skill_path.exists())

    def test_cli_old_usage_is_disabled(self):
        env = change_env(self)
        r = subprocess.run([sys.executable, "scripts/remove_skill.py", "demo"],
                           capture_output=True, text=True, env=dict(os.environ, HOME=str(env.home)),
                           cwd=str(REPO_ROOT))
        self.assertEqual(r.returncode, 2)
        self.assertTrue(env.skill_path.exists(), "旧式目录名删除必须被禁用")


if __name__ == "__main__":
    unittest.main()
