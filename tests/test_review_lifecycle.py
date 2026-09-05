"""Task 5 审查历史与证据有效性(F06)。

- evaluate_review:目标内容变更 → needs-recheck(target-content-changed),历史结论保留可见;
  旧记录缺快照 → needs-recheck(可展示原结论,不许假装重审过);替代品消失/换内容 → 过期;
  无关 Skill 变化不拖累;
- record_review:safety 枚举、reviewer_model 非空、提交 hash 与队列目标一致、快照 ID;
- 台账读改写持锁;台账损坏不得默认为空再覆盖。
"""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.core.review_state import (evaluate_review, review_dependencies)
from scripts.core.reviews import record_review


def inventory_with(iid, tree_hash, logical_id="lg-1", name="demo"):
    return {"instances": [{"instance_id": iid, "tree_hash": tree_hash,
                           "logical_name": name, "directory_name": name,
                           "is_skill": True, "mutable": True}],
            "logical_skills": [{"logical_id": logical_id, "name": name,
                                "tree_hash": tree_hash, "instance_ids": [iid]}]}


def sample_record(iid="inst-1", tree_hash="a" * 64, logical_id="lg-1"):
    return {"review_id": "rv-abc123", "instance_id": iid, "logical_id": logical_id,
            "name": "demo", "verdict": "保留", "reason": "维护活跃且功能独立",
            "alternatives": [], "confidence": "high",
            "evidence": ["source: github example/demo", "coverage: 独立功能"],
            "skill_tree_hash": tree_hash,
            "inventory_fingerprint": "fp-1", "reputation_snapshot_id": "rep-1",
            "reviewed_at": "2026-09-01 00:00:00", "reviewer_model": "model-x",
            "safety": "safe", "note": "",
            "review_snapshot_id": "rs-" + "0" * 12,
            "alternatives_state": {}}


POLICY = {"review_policy_version": "p-1"}


class EvaluateReviewTests(unittest.TestCase):
    def test_content_change_expires_with_history_visible(self):
        record = sample_record()
        inv = inventory_with("inst-1", "b" * 64)  # 内容已变
        state = evaluate_review(record, inv, POLICY, {})
        self.assertEqual(state["status"], "needs-recheck")
        self.assertIn("target-content-changed", state["reason_codes"])
        self.assertEqual(state["previous_record"]["review_id"], record["review_id"],
                         "旧结论必须保留可见,不能只显示未审查")

    def test_same_content_stays_current(self):
        record = sample_record()
        inv = inventory_with("inst-1", "a" * 64)
        state = evaluate_review(record, inv, POLICY, {})
        self.assertEqual(state["status"], "current")
        self.assertEqual(state["reason_codes"], [])

    def test_legacy_record_without_snapshot_is_needs_recheck(self):
        record = sample_record()
        record.pop("review_snapshot_id")
        inv = inventory_with("inst-1", "a" * 64)
        state = evaluate_review(record, inv, POLICY, {})
        self.assertEqual(state["status"], "needs-recheck")
        self.assertIn("missing-review-snapshot", state["reason_codes"])

    def test_alternative_gone_expires_but_unrelated_change_does_not(self):
        record = sample_record()
        record["alternatives"] = ["lg-2"]
        record["alternatives_state"] = {"lg-2": {"tree_hash": "c" * 64}}
        # 目标未变,替代品消失
        inv = inventory_with("inst-1", "a" * 64)
        state = evaluate_review(record, inv, POLICY, {})
        self.assertEqual(state["status"], "needs-recheck")
        self.assertTrue(any(c.startswith("alternative-gone") for c in state["reason_codes"]),
                        state["reason_codes"])
        # 替代品内容变化 → 过期
        inv2 = {"instances": inventory_with("inst-1", "a" * 64)["instances"],
                "logical_skills": [
                    {"logical_id": "lg-1", "name": "demo", "tree_hash": "a" * 64,
                     "instance_ids": ["inst-1"]},
                    {"logical_id": "lg-2", "name": "alt", "tree_hash": "d" * 64,
                     "instance_ids": ["inst-2"]}]}
        state2 = evaluate_review(record, inv2, POLICY, {})
        self.assertTrue(any(c.startswith("alternative-changed") for c in state2["reason_codes"]),
                        state2["reason_codes"])
        # 无关 Skill(C)变化不拖累
        record_c_free = sample_record()
        inv3 = inventory_with("inst-1", "a" * 64)
        self.assertEqual(evaluate_review(record_c_free, inv3, POLICY, {})["status"],
                         "current")

    def test_review_dependencies_contract(self):
        record = sample_record()
        record["alternatives"] = ["lg-2"]
        deps = review_dependencies(record, inventory_with("inst-1", "a" * 64), POLICY, {})
        self.assertEqual(deps["review_policy_version"], "p-1")
        self.assertIn("target", deps)
        self.assertIn("lg-2", deps.get("alternatives", {}))


class RecordReviewHardeningTests(unittest.TestCase):
    def _queue(self):
        return {"items": [{"instance_id": "inst-1", "logical_id": "lg-1",
                           "name": "demo", "tree_hash": "a" * 64}],
                "inventory_fingerprint": "fp-1", "reputation_snapshot_id": "rep-1"}

    def test_bad_safety_wrong_hash_empty_model_rejected(self):
        queue = self._queue()
        for payload, model in (
                ({"instance_id": "inst-1", "verdict": "观察", "confidence": "中",
                  "evidence": ["x"], "safety": "typo"}, "model-x"),
                ({"instance_id": "inst-1", "verdict": "观察", "confidence": "中",
                  "evidence": ["x"], "skill_tree_hash": "f" * 64}, "model-x"),
                ({"instance_id": "inst-1", "verdict": "观察", "confidence": "中",
                  "evidence": ["x"]}, "  "),
        ):
            with self.assertRaises(ValueError, msg=repr(payload)[:60]):
                record_review(queue, payload, model)

    def test_record_carries_snapshot_and_alternatives_state(self):
        queue = {"items": [{"instance_id": "inst-1", "logical_id": "lg-1",
                            "name": "demo", "tree_hash": "a" * 64},
                           {"instance_id": "inst-2", "logical_id": "lg-2",
                            "name": "alt", "tree_hash": "c" * 64}],
                 "inventory_fingerprint": "fp-1", "reputation_snapshot_id": "rep-1"}
        record = record_review(queue, {
            "instance_id": "inst-1", "verdict": "观察", "confidence": "中",
            "evidence": ["source: example"], "safety": "safe",
            "alternatives": ["lg-2"]}, "model-x")
        self.assertTrue(record.get("review_snapshot_id"))
        self.assertEqual(record["skill_tree_hash"], "a" * 64)
        self.assertEqual(record["alternatives_state"]["lg-2"]["tree_hash"], "c" * 64)


class LedgerIntegrityTests(unittest.TestCase):
    def test_corrupt_ledger_is_not_overwritten(self):
        from scripts.value_review import cmd_record
        import argparse
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            queue = {"items": [{"instance_id": "inst-1", "logical_id": "lg-1",
                                "name": "demo", "tree_hash": "a" * 64}],
                     "inventory_fingerprint": "fp", "reputation_snapshot_id": "r"}
            (td / "review-queue.json").write_text(json.dumps(queue), encoding="utf-8")
            (td / "value-reviews.json").write_text("{corrupt", encoding="utf-8")
            payload = td / "review.json"
            payload.write_text(json.dumps({
                "instance_id": "inst-1", "verdict": "观察", "confidence": "中",
                "evidence": ["source: example"], "safety": "safe"}), encoding="utf-8")
            args = argparse.Namespace(file=str(payload), model="model-x",
                                      data_dir=str(td), queue=str(td / "review-queue.json"),
                                      reviews_out=str(td / "value-reviews.json"),
                                      json=True)
            rc = cmd_record(args)
            self.assertEqual(rc, 2, "台账损坏必须拒绝记账")
            self.assertEqual((td / "value-reviews.json").read_text(encoding="utf-8"),
                             "{corrupt", "损坏台账不得被空表覆盖")


if __name__ == "__main__":
    unittest.main()
