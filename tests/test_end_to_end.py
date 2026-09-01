import unittest
from pathlib import Path

from scripts.core.audit import read_audit
from scripts.core.backup import verify_backup
from scripts.core.changes import (ChangeContext, apply_plan,
                                  create_remove_plan, create_restore_plan)
from scripts.core.fingerprint import tree_hash
from scripts.core.io import atomic_write_json
from scripts.core.migrations import migrate_runtime_state
from scripts.core.reviews import build_review_queue, record_review
from scripts.scan import build_inventory
from tests.helpers import temp_home, write_skill


def full_v2_fixture(testcase):
    """端到端环境:真实代码路径串起 scan → queue → review → plan → apply → restore。"""
    home = temp_home(testcase)
    data = home / "data"
    data.mkdir(parents=True, exist_ok=True)
    target = write_skill(home / ".agents/skills", "victim", body="端到端删除目标")
    (target / "scripts").mkdir()
    (target / "scripts" / "helper.py").write_text("print('hi')\n", encoding="utf-8")
    write_skill(home / ".agents/skills", "keeper", body="留下的 skill")
    original_hash = tree_hash(target)

    ctx = ChangeContext(
        data_dir=data, plans_dir=data / "change-plans", backup_dir=home / "backups",
        audit_path=data / "audit-v2.jsonl", lock_path=data / ".change.lock",
        load_inventory=lambda: build_inventory(home, data))
    env = type("Env", (), {})()
    env.home, env.data, env.ctx = home, data, ctx
    env.target = target
    env.original_hash = original_hash
    env.scan = lambda: build_inventory(home, data)
    env.build_queue = lambda inv: build_review_queue(inv, {}, {})
    env.record_remove_recommendation = lambda queue: _record_and_save(
        queue, data, target_name="victim")
    env.plan_remove = lambda iid, review_id: create_remove_plan(
        [iid], build_inventory(home, data), "价值审查 {} 建议删除".format(review_id),
        ctx.plans_dir)
    env.apply = lambda plan: apply_plan(plan.plan_id, plan.digest, True, ctx)
    env.restore = lambda backup_id: _restore(env, backup_id)
    env.audit = lambda: [x for x in read_audit(ctx.audit_path)
                         if x.get("status") in ("success", "failed")]
    return env


def _record_and_save(queue, data, target_name):
    item = next(x for x in queue["items"] if x["name"] == target_name)
    installed = (queue.get("index") or {}).get("installed_logical_ids") or []
    alternative = next(lg for lg in installed if lg != item["logical_id"])
    payload = {
        "instance_id": item["instance_id"], "verdict": "建议删除",
        "reason": "功能与已保留 skill 重复,无独特价值(端到端演示)",
        "alternatives": [alternative], "unique_capabilities": [],
        "loss_if_removed": "仅失去演示目录,无功能损失",
        "confidence": "高",
        "evidence": ["overlap:与保留能力重叠", "source:unknown 来源不明"],
    }
    record = record_review(queue, payload, "end-to-end-model")
    atomic_write_json(data / "value-reviews.json",
                      {"schema_version": 2, "reviews": [record]})
    return record


def _restore(env, backup_id):
    plan = create_restore_plan(backup_id, env.ctx.backup_dir, env.ctx.plans_dir)
    apply_plan(plan.plan_id, plan.digest, True, env.ctx)
    return {"ok": True}


class EndToEndTests(unittest.TestCase):
    def test_full_review_remove_restore_flow(self):
        env = full_v2_fixture(self)
        inv = env.scan()
        self.assertTrue(inv["operational_ok"])
        queue = env.build_queue(inv)
        target_item = next(x for x in queue["items"] if x["name"] == "victim")
        review = env.record_remove_recommendation(queue)
        self.assertEqual(review["instance_id"], target_item["instance_id"])
        plan = env.plan_remove(review["instance_id"], review["review_id"])
        removed = env.apply(plan)
        self.assertFalse(env.target.exists(), "合法计划执行后目标必须被删除")
        info = verify_backup(Path(removed["backup_path"]))
        self.assertTrue(info["ok"])
        restored = env.restore(removed["backup_id"])
        self.assertTrue(restored["ok"])
        self.assertTrue(env.target.exists(), "恢复后目标必须回来")
        self.assertEqual(tree_hash(env.target), env.original_hash,
                         "恢复必须逐字节一致(完整树哈希相同)")
        self.assertEqual([x["status"] for x in env.audit()], ["success", "success"])
        self.assertEqual(env.audit()[0]["rollback_status"], None)
        # 迁移器不碰这次产生的个人数据
        result = migrate_runtime_state(env.data, inv)
        self.assertTrue(env.data.joinpath("value-reviews.json").exists())

    def test_update_flow_uses_staged_candidate(self):
        from scripts.core.changes import create_update_plan, record_candidate_vet
        from tests.test_change_update import update_env
        env = update_env(self)
        plan = env.create_plan(candidate="v2")
        env.remote_head = "v3-malicious"
        record_candidate_vet(plan.plan_id, env.v2_hash, "safe", ["e2e-review"],
                             plans_dir=env.plans_dir)
        apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertEqual(tree_hash(env.skill_path), env.v2_hash)
        self.assertEqual(env.last_audit()["status"], "success")


if __name__ == "__main__":
    unittest.main()
