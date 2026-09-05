"""Task 4(v4):apply 前真实目标预检 + 模型声明来源的两层拒绝。

- 预检在锁后、备份与任何移动之前,于目标同目录真实验证创建/rename/replace/fsync;
- 失败(含第二目标、rename 故障注入)必须零残留、零备份、零事务、目标原状;
- 模型临时位置声明在 policy(check_action)与 service(plan_action)两层拒绝 plan;
- 锁竞争沿用文件锁非阻塞语义;现有 POSIX 中断恢复测试继续全绿。
"""
import json
import os
import stat
import tempfile
import threading
import unittest
from unittest import mock

from scripts.core.changes import (ChangeContext, LockBusy, apply_plan,      # noqa: E402
                                  create_remove_plan, create_restore_plan)
from scripts.core.io import FileLock                                        # noqa: E402
from scripts.core.policy import check_action, load_policy                   # noqa: E402
from scripts.core.preflight import (PreflightError,                         # noqa: E402
                                    preflight_target_directory)
from scripts.core.provenance import load_user_config                        # noqa: E402
from scripts.core.runtime import RuntimePaths                               # noqa: E402
from scripts.core.service import AppService                                 # noqa: E402
from scripts.scan import build_inventory                                    # noqa: E402
from tests.helpers import temp_home, write_skill                            # noqa: E402


def _apply_env(testcase):
    """最小 remove 流程环境:共享库里两个技能,一个作为删除目标。"""
    home = temp_home(testcase)
    data = home / "data"
    data.mkdir()
    target = write_skill(home / ".agents" / "skills", "victim", body="预检目标")
    write_skill(home / ".agents" / "skills", "keeper", body="留下")
    ctx = ChangeContext(
        data_dir=data, plans_dir=data / "change-plans", backup_dir=home / "backups",
        audit_path=data / "audit-v2.jsonl", lock_path=data / ".change.lock",
        load_inventory=lambda: build_inventory(home, data))
    return home, data, target, ctx


def _plan_for(ctx, iids):
    if isinstance(iids, str):
        iids = [iids]
    return create_remove_plan(list(iids), ctx.load_inventory(), "预检测试",
                              ctx.plans_dir, known_sources=load_user_config(ctx.data_dir))


def _assert_no_side_effects(testcase, data, backup_dir, target, plan_id):
    testcase.assertTrue(target.exists(), "预检失败后目标必须原样存在")
    backups = list(Path(backup_dir).glob("backup-*")) if Path(backup_dir).is_dir() else []
    testcase.assertEqual(backups, [], "预检失败不得产生备份")
    txn = Path(data) / "transactions" / (plan_id + ".json")
    testcase.assertFalse(txn.exists(), "预检失败不得产生事务状态")


from pathlib import Path  # noqa: E402


class PreflightUnitTests(unittest.TestCase):
    def test_passes_on_normal_dir_and_leaves_no_trace(self):
        with tempfile.TemporaryDirectory() as td:
            preflight_target_directory(td, "plan-abcdef12")
            self.assertEqual(os.listdir(td), [], "预检后不得残留任何临时对象")

    def test_rejects_non_directory_parent(self):
        with tempfile.TemporaryDirectory() as td:
            fake = os.path.join(td, "not-a-dir.txt")
            with open(fake, "w", encoding="utf-8") as f:
                f.write("x")
            with self.assertRaises(PreflightError):
                preflight_target_directory(fake, "plan-abcdef12")

    def test_rejects_readonly_dir_on_posix(self):
        if os.name != "posix":
            # Windows 上目录只读位不阻止创建:改用真实存在的拒绝路径(非目录父级)再验一次
            with tempfile.TemporaryDirectory() as td:
                fake = os.path.join(td, "f")
                open(fake, "w").close()
                with self.assertRaises(PreflightError):
                    preflight_target_directory(fake, "plan-abcdef12")
            return
        with tempfile.TemporaryDirectory() as td:
            os.chmod(td, stat.S_IRUSR | stat.S_IXUSR)
            try:
                with self.assertRaises(PreflightError, msg="只读目录必须被预检拒绝"):
                    preflight_target_directory(td, "plan-abcdef12")
            finally:
                os.chmod(td, stat.S_IRWXU)
            self.assertEqual(os.listdir(td), [], "失败后不得残留临时对象")

    def test_rename_failure_cleans_up_all_temporaries(self):
        """rename 故障注入(如 Windows 占用文件):立即拒绝且零残留。"""
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("scripts.core.preflight.os.rename",
                            side_effect=PermissionError(13, "file in use")):
                with self.assertRaises(PreflightError):
                    preflight_target_directory(td, "plan-abcdef12")
            self.assertEqual([n for n in os.listdir(td)
                              if n.startswith(".sk-preflight-")], [])

    def test_replace_failure_cleans_up_all_temporaries(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("scripts.core.preflight.os.replace",
                            side_effect=OSError(13, "denied")):
                with self.assertRaises(PreflightError):
                    preflight_target_directory(td, "plan-abcdef12")
            self.assertEqual([n for n in os.listdir(td)
                              if n.startswith(".sk-preflight-")], [])


class ApplyPreflightIntegrationTests(unittest.TestCase):
    def test_preflight_failure_aborts_before_backup(self):
        home, data, target, ctx = _apply_env(self)
        inv = ctx.load_inventory()
        victim = next(i for i in inv["instances"] if i["directory_name"] == "victim")
        plan = _plan_for(ctx, victim["instance_id"])
        with mock.patch("scripts.core.preflight.preflight_target_directory",
                        side_effect=PreflightError("injected boom")):
            with self.assertRaises(Exception) as caught:
                apply_plan(plan.plan_id, plan.digest, True, ctx)
        self.assertIn("预检失败", str(caught.exception))
        _assert_no_side_effects(self, data, home / "backups", target, plan.plan_id)

    def test_second_target_preflight_failure_keeps_first_intact(self):
        home = temp_home(self)
        data = home / "data"
        data.mkdir()
        first = write_skill(home / ".agents" / "skills", "first-target")
        second_parent = home / "elsewhere"
        second = write_skill(second_parent, "second-target")
        # 把 second 登记为可变位置,才能进同一份删除计划
        (data / "client-locations.json").write_text(json.dumps({
            "locations": [{"location_id": "extra-user", "client": "extra",
                           "path": str(second_parent), "kind": "user",
                           "mutable": True}]}), encoding="utf-8")
        ctx = ChangeContext(
            data_dir=data, plans_dir=data / "change-plans",
            backup_dir=home / "backups", audit_path=data / "audit-v2.jsonl",
            lock_path=data / ".change.lock",
            load_inventory=lambda: build_inventory(home, data))
        inv = ctx.load_inventory()
        ids = [i["instance_id"] for i in inv["instances"]
               if i["directory_name"] in ("first-target", "second-target")]
        plan = _plan_for(ctx, ids)

        real = preflight_target_directory

        def only_second_fails(directory, plan_id):
            if os.path.abspath(str(directory)) == os.path.abspath(str(second_parent)):
                raise PreflightError("second parent injected failure")
            return real(directory, plan_id)

        with mock.patch("scripts.core.preflight.preflight_target_directory",
                        side_effect=only_second_fails):
            with self.assertRaises(Exception) as caught:
                apply_plan(plan.plan_id, plan.digest, True, ctx)
        self.assertIn("预检失败", str(caught.exception))
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        _assert_no_side_effects(self, data, home / "backups", first, plan.plan_id)

    def test_posix_readonly_second_parent_rejected_with_real_fs(self):
        if os.name != "posix":
            return
        home = temp_home(self)
        data = home / "data"
        data.mkdir()
        first = write_skill(home / ".agents" / "skills", "first-target")
        second_parent = home / "elsewhere"
        second = write_skill(second_parent, "second-target")
        (data / "client-locations.json").write_text(json.dumps({
            "locations": [{"location_id": "extra-user", "client": "extra",
                           "path": str(second_parent), "kind": "user",
                           "mutable": True}]}), encoding="utf-8")
        ctx = ChangeContext(
            data_dir=data, plans_dir=data / "change-plans",
            backup_dir=home / "backups", audit_path=data / "audit-v2.jsonl",
            lock_path=data / ".change.lock",
            load_inventory=lambda: build_inventory(home, data))
        inv = ctx.load_inventory()
        ids = [i["instance_id"] for i in inv["instances"]
               if i["directory_name"] in ("first-target", "second-target")]
        plan = _plan_for(ctx, ids)
        os.chmod(second_parent, stat.S_IRUSR | stat.S_IXUSR)
        try:
            with self.assertRaises(Exception) as caught:
                apply_plan(plan.plan_id, plan.digest, True, ctx)
            self.assertIn("预检失败", str(caught.exception))
        finally:
            os.chmod(second_parent, stat.S_IRWXU)
        self.assertTrue(first.exists() and second.exists())
        _assert_no_side_effects(self, data, home / "backups", first, plan.plan_id)

    def test_lock_competition_blocks_apply(self):
        home, data, target, ctx = _apply_env(self)
        inv = ctx.load_inventory()
        victim = next(i for i in inv["instances"] if i["directory_name"] == "victim")
        plan = _plan_for(ctx, victim["instance_id"])
        with FileLock(ctx.lock_path):
            with self.assertRaises(LockBusy):
                apply_plan(plan.plan_id, plan.digest, True, ctx)
        self.assertTrue(target.exists())
        # 释放后同一计划可正常执行
        result = apply_plan(plan.plan_id, plan.digest, True, ctx)
        self.assertEqual(result["transaction_status"], "committed")

    def test_restore_flow_preflights_destination_parent(self):
        home, data, target, ctx = _apply_env(self)
        inv = ctx.load_inventory()
        victim = next(i for i in inv["instances"] if i["directory_name"] == "victim")
        plan = _plan_for(ctx, victim["instance_id"])
        removed = apply_plan(plan.plan_id, plan.digest, True, ctx)
        self.assertFalse(target.exists())
        restore = create_restore_plan(removed["backup_id"], ctx.backup_dir, ctx.plans_dir)
        calls = []
        real = preflight_target_directory

        def spy(directory, plan_id):
            calls.append(str(directory))
            return real(directory, plan_id)

        with mock.patch("scripts.core.preflight.preflight_target_directory",
                        side_effect=spy):
            result = apply_plan(restore.plan_id, restore.digest, True, ctx)
        self.assertEqual(result["transaction_status"], "committed")
        self.assertTrue(any(str(target.parent) in c for c in calls),
                        "恢复流程也必须在目标同目录做真实预检")
        self.assertTrue(target.exists())


class ModelDeclarationTwoLayerDenialTests(unittest.TestCase):
    def test_policy_layer_denies_model_declared_instances(self):
        home = temp_home(self)
        data = home / "data"
        data.mkdir()
        policy = load_policy(data)
        model_inst = {"instance_id": "i-model", "mutable": True,
                      "evidence": ["model-declaration", "load-state:reported"],
                      "location_id": "model-x", "directory_name": "demo"}
        loc = {"location_id": "model-x", "client": "my-agent", "mutable": False}
        denied = check_action("remove", model_inst, loc, policy)
        self.assertFalse(denied["allowed"])
        self.assertEqual(denied["reason_code"], "model-declared-location")
        upd = check_action("update", model_inst, loc, policy)
        self.assertFalse(upd["allowed"])
        # restore 也不放行
        res = check_action("restore", model_inst, None, policy)
        self.assertFalse(res["allowed"])
        normal = dict(model_inst, evidence=("client-locations.json",))
        self.assertTrue(check_action("remove", normal, dict(loc, mutable=True),
                                     policy)["allowed"])

    def test_service_layer_refuses_model_declared_plan(self):
        home = temp_home(self)
        data = home / "data"
        data.mkdir()
        model_inst = {"instance_id": "i-model-1", "location_id": "model-x-1",
                      "client": "my-agent", "kind": "user", "directory_name": "demo",
                      "path": str(home / "demo"), "display_path": "~/demo",
                      "real_path": str(home / "demo"), "is_symlink": False,
                      "mutable": False, "evidence": ["model-declaration"],
                      "load_priority": 6, "tree_hash": "", "is_skill": True,
                      "issue_codes": [], "content_status": "complete"}
        (data / "inventory.json").write_text(json.dumps({
            "schema_version": 2, "scanned_at": "2026-09-05 00:00:00",
            "locations": [], "instances": [model_inst], "logical_skills": [],
            "client_load": {}, "findings": [], "config_issues": [],
            "observation": {"complete": True, "issues": []},
            "total": 1, "by_source": {}, "operational_ok": True,
            "health_status": "ok"}), encoding="utf-8")
        paths = RuntimePaths(home=home, data_dir=data,
                             staging_dir=home / "cache", backup_dir=home / "backups")
        svc = AppService(paths)
        with self.assertRaises(Exception) as caught:
            svc.plan_action("remove", {"instance_ids": ["i-model-1"],
                                       "reason": "必须被拒"})
        self.assertIn("模型临时位置声明", str(caught.exception))
        self.assertEqual(list((data / "change-plans").glob("*.json")), [],
                         "service 层拒绝后不得产生计划文件")


if __name__ == "__main__":
    unittest.main()
