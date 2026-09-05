"""Task 2 统一执行策略与输入校验(F04 + F07 清理边界)。

- 计划与执行共用同一策略函数;保护白名单以 data_dir 权威配置为准,
  调用方传入的 known_sources 只能加保护、不能减保护;
- 计划生成后环境变化(登记 builtin-app、mutable 翻转、实体类型/父链接变化、配置损坏)
  一律要求重新计划,不得继续执行;
- 候选安检 verdict 枚举严格校验,非 safe/warning 一律拒绝应用;
- staging 清理只处理本工具登记所有且无有效引用的候选,不相关目录一律保留。
"""
import json
import os
import shutil
import unittest
from pathlib import Path

from scripts.core.changes import (ChangeError, apply_plan, create_remove_plan,
                                  create_update_plan, vet_path)
from scripts.core.fingerprint import tree_hash
from scripts.core.io import atomic_write_json
from scripts.core.policy import (PolicyError, check_action, load_policy,
                                 validate_candidate_vet)
from scripts.core.staging import (StagingBoundaryError, cleanup_staging,
                                  record_ownership, validate_staging_root)
from tests.helpers import temp_home
from tests.test_change_remove import change_env
from tests.test_change_update import update_env


def register(env, doc, filename="known-sources.json"):
    env.data.mkdir(parents=True, exist_ok=True)
    (env.data / filename).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


class ApplyTimePolicyTests(unittest.TestCase):
    def test_builtin_app_registered_after_plan_blocks_apply(self):
        env = change_env(self)
        plan = env.remove_plan()
        register(env, {"demo": {"type": "builtin-app"}})
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue(env.skill_path.exists(), "计划后登记的保护必须在执行期生效")

    def test_mutable_flip_after_plan_blocks_apply(self):
        env = change_env(self)
        plan = env.remove_plan()
        env.inventory["instances"][0]["mutable"] = False
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue(env.skill_path.exists())

    def test_entity_type_change_blocks_apply(self):
        env = change_env(self)
        plan = env.remove_plan()
        # 同内容,但目录实体被替换成指向外部副本的符号链接
        shutil.copytree(env.skill_path, env.agents_root / "demo-copy")
        shutil.rmtree(env.skill_path)
        os.symlink(env.agents_root / "demo-copy", env.skill_path)
        inst = env.inventory["instances"][0]
        inst["is_symlink"] = True
        inst["real_path"] = str(env.agents_root / "demo-copy")
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue((env.agents_root / "demo-copy" / "SKILL.md").exists(),
                        "实体类型变化后旧计划不得执行")

    def test_symlink_parent_swap_blocks_apply(self):
        env = change_env(self)
        plan = env.remove_plan()
        outside = env.home / "outside"
        outside.mkdir()
        shutil.move(str(env.agents_root), str(outside / "skills"))
        os.symlink(outside / "skills", env.agents_root)
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue((outside / "skills" / "demo" / "SKILL.md").exists(),
                        "父目录换成外链后不得穿过链接删除")

    def test_corrupt_policy_blocks_plan_and_apply(self):
        env = change_env(self)
        env.data.mkdir(parents=True, exist_ok=True)
        (env.data / "known-sources.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(ChangeError):
            env.remove_plan()
        with self.assertRaises(ChangeError):
            create_update_plan(env.iid, {
                "instance_id": env.iid, "staging_path": str(env.data / "x"),
                "candidate_hash": "0" * 64, "repo": "example/demo"},
                env.inventory, env.plans_dir)
        self.assertTrue(env.skill_path.exists(), "策略损坏时不得有任何写操作")

    def test_passed_known_sources_cannot_weaken_authoritative(self):
        env = change_env(self)
        register(env, {"demo": {"type": "builtin-app"}})
        with self.assertRaises(ChangeError):
            create_remove_plan([env.iid], env.inventory, "test", env.plans_dir,
                               known_sources={"demo": {"type": "github"}})

    def test_self_built_refuses_remove_and_update_by_default(self):
        env = change_env(self)
        register(env, {}, filename="known-sources.json")
        (env.data / "self-built.txt").write_text("demo\n", encoding="utf-8")
        with self.assertRaises(ChangeError):
            env.remove_plan()
        with self.assertRaises(ChangeError):
            create_update_plan(env.iid, {
                "instance_id": env.iid, "staging_path": str(env.data),
                "candidate_hash": "0" * 64, "repo": "example/demo"},
                env.inventory, env.plans_dir)
        self.assertTrue(env.skill_path.exists())

    def test_policy_reports_missing_vs_corrupt(self):
        env = change_env(self)
        policy = load_policy(env.data)
        self.assertTrue(policy["healthy"])
        self.assertEqual(policy["issues"], [], "可选文件未创建不算损坏")
        env.data.mkdir(parents=True, exist_ok=True)
        (env.data / "known-sources.json").write_text("not json", encoding="utf-8")
        policy = load_policy(env.data)
        self.assertFalse(policy["healthy"])
        self.assertTrue(policy["issues"])

    def test_check_action_contract(self):
        env = change_env(self)
        policy = load_policy(env.data)
        inst = env.inventory["instances"][0]
        loc = env.inventory["locations"][0]
        allowed = check_action("remove", inst, loc, policy)
        self.assertTrue(allowed["allowed"])
        self.assertTrue(allowed["policy_hash"])
        register(env, {"demo": {"type": "builtin-app"}})
        policy = load_policy(env.data)
        denied = check_action("remove", inst, loc, policy)
        self.assertFalse(denied["allowed"])
        self.assertIn("builtin-app", denied["reason_code"])
        unknown = check_action("explode", inst, loc, policy)
        self.assertFalse(unknown["allowed"])


class CandidateVetContractTests(unittest.TestCase):
    def test_unknown_vet_verdict_never_activates_candidate(self):
        env = update_env(self)
        plan = env.create_plan()
        atomic_write_json(vet_path(plan.plan_id, env.plans_dir), {
            "plan_id": plan.plan_id,
            "candidate_hash": env.v2_hash,
            "verdict": "typo",
            "evidence": ["fixture"],
        })
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertEqual(tree_hash(env.skill_path), env.local_hash)

    def test_danger_verdict_and_missing_evidence_rejected(self):
        env = update_env(self)
        plan = env.create_plan()
        for verdict in ("danger", "", None, "SAFE"):
            atomic_write_json(vet_path(plan.plan_id, env.plans_dir), {
                "plan_id": plan.plan_id, "candidate_hash": env.v2_hash,
                "verdict": verdict, "evidence": ["fixture"]})
            with self.assertRaises(ChangeError, msg=repr(verdict)):
                apply_plan(plan.plan_id, plan.digest, True, env.context)
        atomic_write_json(vet_path(plan.plan_id, env.plans_dir), {
            "plan_id": plan.plan_id, "candidate_hash": env.v2_hash,
            "verdict": "safe", "evidence": []})
        with self.assertRaises(ChangeError, msg="空证据"):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        atomic_write_json(vet_path(plan.plan_id, env.plans_dir), {
            "plan_id": plan.plan_id, "candidate_hash": env.v2_hash,
            "verdict": "safe", "evidence": ["", "  "]})
        with self.assertRaises(ChangeError, msg="空白证据"):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertEqual(tree_hash(env.skill_path), env.local_hash)

    def test_vet_record_hash_or_plan_mismatch_rejected(self):
        env = update_env(self)
        plan = env.create_plan()
        for bad in ({"plan_id": "other", "candidate_hash": env.v2_hash,
                     "verdict": "safe", "evidence": ["x"]},
                    {"plan_id": plan.plan_id, "candidate_hash": "f" * 64,
                     "verdict": "safe", "evidence": ["x"]}):
            atomic_write_json(vet_path(plan.plan_id, env.plans_dir), bad)
            with self.assertRaises(ChangeError):
                apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertEqual(tree_hash(env.skill_path), env.local_hash)

    def test_validate_candidate_vet_direct_contract(self):
        record = {"plan_id": "p", "candidate_hash": "a" * 64,
                  "verdict": "safe", "evidence": ["source", "coverage"]}
        self.assertEqual(validate_candidate_vet(record, "p", "a" * 64)["verdict"], "safe")
        with self.assertRaises(PolicyError):
            validate_candidate_vet(record, "other-plan", "a" * 64)
        with self.assertRaises(PolicyError):
            validate_candidate_vet(record, "p", "b" * 64)


class PlanStructureTests(unittest.TestCase):
    def _row(self, env, **over):
        plan = env.remove_plan()
        row = json.loads((env.plans_dir / (plan.plan_id + ".json")).read_text(encoding="utf-8"))
        row.update(over)
        return row

    def test_tampered_targets_are_rejected_at_load(self):
        from scripts.core.changes import apply_plan, plan_digest, write_plan
        from scripts.core.models import ChangePlan
        env = change_env(self)
        row = self._row(env, target_ids=["../escape"])
        row["digest"] = plan_digest(row)
        write_plan(ChangePlan.from_dict(row), env.plans_dir)
        with self.assertRaises(ChangeError):
            apply_plan(row["plan_id"], row["digest"], True, env.context)

    def test_duplicate_and_unknown_precondition_keys_rejected(self):
        from scripts.core.changes import apply_plan, plan_digest, write_plan
        from scripts.core.models import ChangePlan
        env = change_env(self)
        iid = env.iid
        row = self._row(env, preconditions=[["tree_hash:" + iid, "x" * 64],
                                            ["tree_hash:" + iid, "y" * 64]])
        row["digest"] = plan_digest(row)
        write_plan(ChangePlan.from_dict(row), env.plans_dir)
        with self.assertRaises(ChangeError, msg="重复前置键"):
            apply_plan(row["plan_id"], row["digest"], True, env.context)
        row2 = self._row(env, preconditions=[["weird-key", "v"]])
        row2["digest"] = plan_digest(row2)
        write_plan(ChangePlan.from_dict(row2), env.plans_dir)
        with self.assertRaises(ChangeError, msg="未知前置键"):
            apply_plan(row2["plan_id"], row2["digest"], True, env.context)

    def test_reason_and_recommendation_id_saved_in_plan(self):
        env = change_env(self)
        plan = create_remove_plan([env.iid], env.inventory, "替换说明理由",
                                  env.plans_dir, recommendation_id="rev-123")
        row = json.loads((env.plans_dir / (plan.plan_id + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(row.get("recommendation_id"), "rev-123")
        self.assertIn("替换说明理由", row.get("reason") or row.get("summary", ""))
        result = apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue(result["ok"])


class StagingBoundaryTests(unittest.TestCase):
    def test_cleanup_keeps_unowned_and_referenced(self):
        home = temp_home(self)
        root = home / "staging"
        owned_unreferenced = root / "cand-aaa000000000"
        owned_unreferenced.mkdir(parents=True)
        record_ownership(root, owned_unreferenced.name, {"candidate_hash": "a" * 64})
        sentinel = root / "my-important-notes"
        sentinel.mkdir()
        owned_referenced = root / "cand-bbb000000000"
        owned_referenced.mkdir()
        record_ownership(root, owned_referenced.name, {"candidate_hash": "b" * 64})
        legacy = root / "cand-ccc000000000"  # 无所有权记录的历史目录
        legacy.mkdir()
        result = cleanup_staging(root, {owned_referenced.name})
        self.assertEqual(result["removed"], [owned_unreferenced.name])
        self.assertTrue(sentinel.exists(), "不相关目录绝不能被清理")
        self.assertTrue(legacy.exists(), "无所有权记录的历史目录只能保留待清理")
        self.assertIn(legacy.name, result["unowned"])

    def test_stage_candidate_writes_ownership_record(self):
        import tempfile
        from scripts.check_updates import stage_candidate
        from tests.test_check_updates import head_gh
        with tempfile.TemporaryDirectory() as td:
            staged = stage_candidate("example/dupe", "skills/dupe", "headsha",
                                     Path(td) / "staging", head_gh())
            self.assertTrue(staged["ok"])
            root = Path(td) / "staging"
            name = Path(staged["staging_path"]).name
            self.assertTrue((root / "ownership" / (name + ".json")).is_file())
            result = cleanup_staging(root, set())
            self.assertEqual(result["removed"], [name], "本工具登记且无引用的候选可清理")

    def test_validate_staging_root_refuses_protected_and_links(self):
        home = temp_home(self)
        skills = home / ".agents/skills"
        skills.mkdir(parents=True)
        with self.assertRaises(StagingBoundaryError):
            validate_staging_root(skills / "staging", [skills])
        with self.assertRaises(StagingBoundaryError):
            validate_staging_root(home, [skills])
        outside = home / "outside"
        outside.mkdir()
        link_root = home / "link-staging"
        os.symlink(outside, link_root)
        with self.assertRaises(StagingBoundaryError):
            validate_staging_root(link_root, [skills])
        validate_staging_root(home / "staging", [skills])  # 普通同级目录允许

    def test_check_cleanup_keeps_unowned_sentinel(self):
        from scripts.check_updates import check
        from tests.test_check_updates import head_gh, two_copy_inventory
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            data = home / "data"
            data.mkdir()
            (data / "known-sources.json").write_text(json.dumps(
                {"dupe": {"type": "github", "repo": "example/dupe",
                          "path": "skills/dupe/SKILL.md"}}), encoding="utf-8")
            sentinel = home / "staging" / "unrelated-user-data"
            sentinel.mkdir(parents=True)
            result = check(two_copy_inventory(home), data, data / "updates.json",
                           head_gh(), staging_root=home / "staging")
            self.assertTrue(result["differs"])
            self.assertTrue(sentinel.exists(), "check 的清扫不得碰不相关目录")


if __name__ == "__main__":
    unittest.main()
