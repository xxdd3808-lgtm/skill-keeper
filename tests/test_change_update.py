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


class WebVetFlowTests(unittest.TestCase):
    """F09 回归:网页每次点击都新建计划,安检记录绑定 plan_id——
    所以必须先对"本计划"记账再执行;CLI 与网页走同一 AppService 入口。"""

    @staticmethod
    def _materialize_inventory(env):
        """AppService/ServiceContext 从磁盘读 inventory:update_env 的内存态先落盘。"""
        import json as _json
        env.data.mkdir(parents=True, exist_ok=True)
        (env.data / "inventory.json").write_text(
            _json.dumps(env.inventory, ensure_ascii=False), encoding="utf-8")

    def test_service_vet_binds_new_plan_and_flow_completes(self):
        from unittest import mock
        from scripts.core.service import AppService
        from scripts.core.runtime import RuntimePaths
        env = update_env(self)
        self._materialize_inventory(env)
        paths = RuntimePaths(home=env.home, data_dir=env.data,
                             backup_dir=env.home / "backups")
        svc = AppService(paths)
        plan = env.create_plan(candidate="v2")
        # 未安检先执行 → 引擎拒绝(现状不变)
        with self.assertRaises(ChangeError):
            svc.apply_action(plan.plan_id, plan.digest, True)
        # 对本计划记账后执行成功
        vet = svc.vet_candidate(plan.plan_id, "safe", ["阅读了候选 SKILL.md 与差异"])
        self.assertEqual(vet["candidate_hash"], env.v2_hash)
        with mock.patch("scripts.core.service.publish_snapshot",
                        return_value={"ok": True, "status": "fresh",
                                      "snapshot_id": "x"}):
            result = svc.apply_action(plan.plan_id, plan.digest, True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["transaction_status"], "committed")
        self.assertEqual(tree_hash(env.skill_path), env.v2_hash)

    def test_service_vet_rejects_unknown_plan_and_bad_verdict(self):
        from scripts.core.service import AppService
        from scripts.core.runtime import RuntimePaths
        env = update_env(self)
        self._materialize_inventory(env)
        paths = RuntimePaths(home=env.home, data_dir=env.data,
                             backup_dir=env.home / "backups")
        svc = AppService(paths)
        with self.assertRaises(ChangeError):
            svc.vet_candidate("plan-nothing", "safe", ["x"])
        plan = env.create_plan(candidate="v2")
        with self.assertRaises(ChangeError):
            svc.vet_candidate(plan.plan_id, "danger", ["x"])
        with self.assertRaises(ChangeError, msg="安检证据不能为空"):
            svc.vet_candidate(plan.plan_id, "safe", [])

    def test_serve_vet_endpoint_records_for_the_new_plan(self):
        """网页流程:POST /api/plan 建新计划 → /api/vet 对该计划记账 → /api/apply 成功。"""
        import json as _json
        import threading
        from http.client import HTTPConnection
        from scripts import serve
        env = update_env(self)
        self._materialize_inventory(env)
        (env.data / "report.html").write_text("<html><body>ok</body></html>",
                                              encoding="utf-8")
        httpd, token, ctx = serve.create_server(env.data, home=env.home)
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        self.addCleanup(httpd.shutdown)

        def post(path, body):
            conn = HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
            conn.request("POST", path + "?t=" + token,
                         body=_json.dumps(body).encode(),
                         headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            payload = _json.loads(r.read() or b"{}")
            conn.close()
            return r.status, payload

        # updates.json 里登记已暂存候选(模拟 check_updates 已跑完)
        (env.data / "updates.json").write_text(_json.dumps({
            "schema_version": 2,
            "differs": [{"name": "demo", "instance_id": env.iid, "repo": "example/demo",
                         "commit_sha": env.remote_head, "candidate_hash": env.v2_hash,
                         "source_dir": "skills/demo",
                         "staging_path": str(env.staging)}],
        }), encoding="utf-8")
        status, plan = post("/api/plan", {"action": "update", "instance_id": env.iid})
        self.assertEqual(status, 200, plan)
        self.assertEqual(plan["candidate_hash"], env.v2_hash,
                         "计划公开字段必须带候选哈希,供安检步骤展示")
        status, vet = post("/api/vet", {"plan_id": plan["plan_id"], "verdict": "safe",
                                        "confirm": True})
        self.assertEqual(status, 200, vet)
        self.assertEqual(vet["candidate_hash"], env.v2_hash)
        # 快照刷新会以 realpath 重算 instance_id(测试 fixture 与之不同口径),
        # 本测试聚焦"安检续办",apply 期间把刷新 mock 成成功
        from unittest import mock
        with mock.patch("scripts.core.service.publish_snapshot",
                        return_value={"ok": True, "status": "fresh",
                                      "snapshot_id": "x"}):
            status, applied = post("/api/apply", {"plan_id": plan["plan_id"],
                                                  "digest": plan["digest"],
                                                  "confirm": True})
        self.assertEqual(status, 200, applied)
        self.assertTrue(applied["ok"], applied)
        self.assertEqual(applied["transaction_status"], "committed")
        self.assertEqual(tree_hash(env.skill_path), env.v2_hash)
        # 缺 confirm 的安检必须拒绝
        status, plan2 = post("/api/plan", {"action": "update", "instance_id": env.iid})
        self.assertEqual(status, 200)
        status, _ = post("/api/vet", {"plan_id": plan2["plan_id"], "verdict": "safe"})
        self.assertEqual(status, 400, "安检记账同样要求明确确认")


if __name__ == "__main__":
    unittest.main()
