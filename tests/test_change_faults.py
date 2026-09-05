"""Task 3 故障矩阵(F03):删除/更新在备份、移动、验证、审计各环节失败后,
必须回到原状(内容/权限/链接/非目标哨兵完好)或明确 recovery-required;
已提交/已回滚的计划重放不得再次发生物理变更;审计失败不得丢事实。
"""
import json
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.core import changes, transactions
from scripts.core.backup import create_backup
from scripts.core.changes import (ChangeError, apply_plan, read_transaction,
                                  record_candidate_vet)
from scripts.core.fingerprint import instance_id, tree_hash
from tests.test_change_remove import change_env
from tests.test_change_update import update_env


def two_target_env(testcase):
    """demo 目录 + 指向它的 alias 符号链接,两个实例一起作为删除目标。"""
    env = change_env(testcase)
    link = env.agents_root / "alias"
    os.symlink("demo", link)
    iid = instance_id("shared", "alias", str(link))
    env.inventory["instances"].append(
        {"instance_id": iid, "location_id": "shared", "directory_name": "alias",
         "path": str(link), "real_path": str(env.skill_path),
         "tree_hash": tree_hash(env.skill_path),
         "mutable": True, "is_symlink": True, "is_skill": True})
    env.alias_iid = iid
    env.alias_path = link
    return env


def txn_state(env, plan_id):
    return read_transaction(plan_id, env.context)


def no_holdings_left(root):
    return [p.name for p in Path(root).iterdir() if p.name.startswith(".sk-txn-")]


class RemoveFaultTests(unittest.TestCase):
    def test_remove_validator_exception_restores_original(self):
        env = change_env(self)
        expected = tree_hash(env.skill_path)
        plan = env.remove_plan()

        def fail_validation():
            raise RuntimeError("fixture validator crash")

        env.context.verify_after_apply = fail_validation
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue(env.skill_path.is_dir())
        self.assertEqual(tree_hash(env.skill_path), expected)
        self.assertEqual(env.last_audit()["rollback_status"], "restored")
        self.assertEqual(txn_state(env, plan.plan_id)["phase"], "rolled-back")
        self.assertEqual(no_holdings_left(env.agents_root), [])

    def test_remove_second_target_move_failure_restores_all(self):
        from scripts.core.changes import create_remove_plan
        env = two_target_env(self)
        demo_hash = tree_hash(env.skill_path)
        plan = create_remove_plan([env.iid, env.alias_iid], env.inventory, "test",
                                  env.plans_dir)
        real_rename = os.rename
        calls = {"n": 0}

        def flaky_rename(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("fixture second move fails")
            return real_rename(src, dst)

        with patch("os.rename", flaky_rename):
            with self.assertRaises(ChangeError):
                apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue(env.skill_path.is_dir(), "第一个已移走目标必须回滚")
        self.assertEqual(tree_hash(env.skill_path), demo_hash)
        self.assertTrue(env.alias_path.is_symlink(), "链接目标保持原状")
        self.assertEqual(os.readlink(env.alias_path), "demo")
        self.assertEqual(no_holdings_left(env.agents_root), [])
        self.assertEqual(env.last_audit()["rollback_status"], "restored")
        self.assertEqual(txn_state(env, plan.plan_id)["phase"], "rolled-back")

    def test_backup_failure_leaves_no_transaction_and_no_change(self):
        env = change_env(self)
        plan = env.remove_plan()
        sentinel = env.skill_path / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")

        def broken_backup(*a, **k):
            raise ChangeError("fixture backup failure")

        with patch.object(changes, "create_backup", broken_backup):
            with self.assertRaises(ChangeError):
                apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue(env.skill_path.exists())
        self.assertTrue(sentinel.exists())
        self.assertIsNone(txn_state(env, plan.plan_id), "备份失败不得留下事务状态")
        self.assertEqual(no_holdings_left(env.agents_root), [])

    def test_committed_plan_replay_returns_known_result(self):
        env = change_env(self)
        plan = env.remove_plan()
        first = apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue(first["ok"])
        backups = list(Path(env.context.backup_dir).glob("backup-*.tar.gz"))
        second = apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue(second.get("already_applied"), "重放必须返回已知结果")
        self.assertEqual(second["transaction_status"], "committed")
        self.assertFalse(env.skill_path.exists())
        self.assertEqual(list(Path(env.context.backup_dir).glob("backup-*.tar.gz")),
                         backups, "重放不得创建第二次备份/物理变更")

    def test_rolled_back_plan_cannot_replay(self):
        env = change_env(self)
        plan = env.remove_plan()
        env.context.verify_after_apply = lambda: False
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        env.context.verify_after_apply = None
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue(env.skill_path.exists(), "已回滚计划重放不得发生物理变更")

    def test_audit_failure_after_commit_keeps_facts(self):
        env = change_env(self)
        plan = env.remove_plan()
        real_append = changes.append_audit

        def broken_audit(event, path):
            raise OSError("fixture audit disk full")

        with patch.object(changes, "append_audit", broken_audit):
            result = apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue(result["ok"], "文件事务已提交,审计失败不得谎报失败或回滚")
        self.assertTrue(result.get("audit_pending"), "必须明确标注审计待补写")
        self.assertFalse(env.skill_path.exists())
        state = txn_state(env, plan.plan_id)
        self.assertEqual(state["phase"], "committed")
        self.assertTrue(state["audit_pending"])
        self.assertEqual(state["backup_id"], result["backup_id"],
                         "审计写不出时,事务文件必须保留事实")

    def test_remove_result_hashes_and_audit_are_real(self):
        env = change_env(self)
        plan = env.remove_plan()
        result = apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertEqual(result["result_hashes"], {env.iid: ""}, "删除的 resulting hash 是空位")
        audit = env.last_audit()
        self.assertEqual(json.loads(audit["resulting_hash"]), {env.iid: ""})
        self.assertNotEqual(audit["resulting_hash"], audit["backup_id"],
                            "resulting_hash 不得再塞 backup ID")
        self.assertEqual(audit["backup_id"], result["backup_id"])


class UpdateFaultTests(unittest.TestCase):
    def test_update_swap_failure_restores_old(self):
        env = update_env(self)
        plan = env.create_plan()
        record_candidate_vet(plan.plan_id, env.v2_hash, "safe", ["fixture-review"],
                             plans_dir=env.plans_dir)
        real_rename = os.rename
        calls = {"n": 0}

        def flaky_rename(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:  # 第一次:旧→保管;第二次:新→目标
                raise OSError("fixture second rename fails")
            return real_rename(src, dst)

        with patch("os.rename", flaky_rename):
            with self.assertRaises(ChangeError):
                apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertEqual(tree_hash(env.skill_path), env.local_hash, "旧版本必须回到原位")
        self.assertEqual(env.last_audit()["rollback_status"], "restored")
        state = txn_state(env, plan.plan_id)
        self.assertEqual(state["phase"], "rolled-back")
        leftovers = [p.name for p in env.skill_path.parent.iterdir()
                     if ".sk-txn-" in p.name]
        self.assertEqual(leftovers, [])

    def test_engine_disk_check_not_bypassed_by_verify_true(self):
        env = update_env(self)
        plan = env.create_plan()
        record_candidate_vet(plan.plan_id, env.v2_hash, "safe", ["fixture-review"],
                             plans_dir=env.plans_dir)
        env.context.verify_after_apply = lambda: True  # 回调说好不算数
        real_tree_hash = changes.tree_hash
        calls = {"n": 0}

        def lying_tree_hash(path):
            calls["n"] += 1
            if calls["n"] >= 3:  # 前两次:前置校验/物化;第三次:交换后目标
                return "f" * 64
            return real_tree_hash(path)

        with patch.object(changes, "tree_hash", lying_tree_hash):
            with self.assertRaises(ChangeError):
                apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertEqual(tree_hash(env.skill_path), env.local_hash,
                         "回调 True 不得绕过引擎自身的落盘 hash 校验")

    def test_update_success_records_transaction_and_hashes(self):
        env = update_env(self)
        plan = env.create_plan()
        record_candidate_vet(plan.plan_id, env.v2_hash, "safe", ["fixture-review"],
                             plans_dir=env.plans_dir)
        result = apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertEqual(result["transaction_status"], "committed")
        self.assertEqual(result["result_hashes"], {env.iid: env.v2_hash})
        state = txn_state(env, plan.plan_id)
        self.assertEqual(state["phase"], "committed")
        self.assertEqual(state["targets"][0]["original_hash"], env.local_hash)
        leftovers = [p.name for p in env.skill_path.parent.iterdir()
                     if ".sk-txn-" in p.name]
        self.assertEqual(leftovers, [], "提交后事务保管目录必须清理")


if __name__ == "__main__":
    unittest.main()
