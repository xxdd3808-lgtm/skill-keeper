import os, unittest
from pathlib import Path

from scripts.core.changes import (ChangeContext, ChangeError, apply_plan,
                                  create_update_plan, record_candidate_vet)
from scripts.core.fingerprint import instance_id, tree_hash
from scripts.core.models import Location
from tests.helpers import temp_home, write_skill


def update_env(testcase):
    """最小更新环境:shared 位置 demo v1,staging 里放 v2 候选完整树。"""
    home = temp_home(testcase)
    data = home / "data"
    plans_dir = data / "change-plans"
    shared_root = home / ".agents/skills"
    demo = write_skill(shared_root, "demo", body="v1")
    (demo / "run.py").write_text("old", encoding="utf-8")
    staging = write_skill(data / "staging", "cand-v2", body="v2")
    (staging / "run.py").write_text("new", encoding="utf-8")
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
    env.skill_path, env.staging = demo, staging
    env.v2_hash = tree_hash(staging)
    env.remote_head = "cafe-head"
    env.local_hash = tree_hash(demo)

    def create_plan(candidate="v2"):
        return create_update_plan(env.iid, {
            "instance_id": env.iid, "staging_path": str(staging),
            "candidate_hash": tree_hash(staging),
            "source": "github", "repo": "example/demo",
            "source_dir": "skills/demo", "commit_sha": env.remote_head,
        }, env.inventory, plans_dir)
    env.create_plan = create_plan

    from scripts.core.audit import read_audit
    env.last_audit = lambda: read_audit(ctx.audit_path)[-1]
    return env


class ChangeUpdateTests(unittest.TestCase):
    def test_apply_uses_reviewed_staged_hash_not_refetched_head(self):
        env = update_env(self)
        plan = env.create_plan(candidate="v2")
        env.remote_head = "v3-malicious"  # 远端 HEAD 之后变了也绝不影响已审查候选
        record_candidate_vet(plan.plan_id, env.v2_hash, "safe", ["fixture-review"],
                             plans_dir=env.plans_dir)
        apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertEqual(tree_hash(env.skill_path), env.v2_hash, "安装的必须是已审查的固定候选")
        self.assertEqual(env.last_audit()["status"], "success")
        leftovers = [p.name for p in env.skill_path.parent.iterdir() if ".rollback-" in p.name]
        self.assertEqual(leftovers, [], "成功后不保留回滚临时目录")

    def test_unvetted_or_changed_candidate_is_rejected(self):
        env = update_env(self)
        plan = env.create_plan(candidate="v2")
        with self.assertRaises(ChangeError, msg="未安检的候选不得应用"):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        (env.staging / "run.py").write_text("changed", encoding="utf-8")
        record_candidate_vet(plan.plan_id, env.v2_hash, "safe", ["fixture-review"],
                             plans_dir=env.plans_dir)
        with self.assertRaises(ChangeError, msg="staging 被改后必须拒绝"):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertEqual(tree_hash(env.skill_path), env.local_hash, "原版本保持不变")

    def test_warning_verdict_needs_second_explicit_confirm(self):
        env = update_env(self)
        plan = env.create_plan(candidate="v2")
        record_candidate_vet(plan.plan_id, env.v2_hash, "warning", ["fixture-warning"],
                             plans_dir=env.plans_dir)
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        apply_plan(plan.plan_id, plan.digest, True, env.context, accept_warning=True)
        self.assertEqual(tree_hash(env.skill_path), env.v2_hash)

    def test_vet_hash_must_match_plan_candidate(self):
        env = update_env(self)
        plan = env.create_plan(candidate="v2")
        with self.assertRaises(ChangeError):
            record_candidate_vet(plan.plan_id, "0" * 64, "safe", ["x"], plans_dir=env.plans_dir)
        with self.assertRaises(ChangeError):
            record_candidate_vet(plan.plan_id, env.v2_hash, "danger", ["x"],
                                 plans_dir=env.plans_dir)

    def test_failed_verify_swaps_back_old_version(self):
        env = update_env(self)
        plan = env.create_plan(candidate="v2")
        record_candidate_vet(plan.plan_id, env.v2_hash, "safe", ["fixture-review"],
                             plans_dir=env.plans_dir)
        env.context.verify_after_apply = lambda: False
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertEqual(tree_hash(env.skill_path), env.local_hash, "验证失败必须换回旧版本")
        self.assertEqual(env.last_audit()["rollback_status"], "restored")

    def test_verify_crash_still_rolls_back_to_old_version(self):
        """验证函数自身崩溃(抛异常而非返回 False)同样必须换回旧版本。"""
        env = update_env(self)
        plan = env.create_plan(candidate="v2")
        record_candidate_vet(plan.plan_id, env.v2_hash, "safe", ["fixture-review"],
                             plans_dir=env.plans_dir)

        def boom():
            raise RuntimeError("rescan crashed")
        env.context.verify_after_apply = boom
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertEqual(tree_hash(env.skill_path), env.local_hash,
                         "验证崩溃后原版本必须还在位")
        self.assertEqual(env.last_audit()["rollback_status"], "restored")


if __name__ == "__main__":
    unittest.main()
