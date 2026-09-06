"""Task 5 审查历史与证据有效性(F06)。

- evaluate_review:目标内容变更 → needs-recheck(target-content-changed),历史结论保留可见;
  旧记录缺快照 → needs-recheck(可展示原结论,不许假装重审过);替代品消失/换内容 → 过期;
  无关 Skill 变化不拖累;
- record_review:safety 枚举、reviewer_model 非空、提交 hash 与队列目标一致、快照 ID;
- 台账读改写持锁;台账损坏不得默认为空再覆盖。
"""
import contextlib
import io
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
    from scripts.core.review_state import REVIEW_POLICY_VERSION
    return {"review_id": "rv-abc123", "instance_id": iid, "logical_id": logical_id,
            "name": "demo", "verdict": "保留", "reason": "维护活跃且功能独立",
            "alternatives": [], "confidence": "high",
            "evidence": ["source: github example/demo", "coverage: 独立功能"],
            "skill_tree_hash": tree_hash,
            "inventory_fingerprint": "fp-1", "reputation_snapshot_id": "rep-1",
            "reviewed_at": "2026-09-01 00:00:00", "reviewer_model": "model-x",
            "safety": "safe", "note": "",
            "review_snapshot_id": "rs-" + "0" * 12,
            "review_policy_version": REVIEW_POLICY_VERSION,
            "alternatives_state": {}}


POLICY = {"review_policy_version": "p-1"}  # 依赖提取测试用:显式自定义版本
# 评估类测试的"当前政策":与记录版本一致才可能判 current(任务1缺口3语义)
from scripts.core.review_state import REVIEW_POLICY_VERSION as _RPV
POLICY_CURRENT = {"review_policy_version": _RPV}


class EvaluateReviewTests(unittest.TestCase):
    def test_content_change_expires_with_history_visible(self):
        record = sample_record()
        inv = inventory_with("inst-1", "b" * 64)  # 内容已变
        state = evaluate_review(record, inv, POLICY_CURRENT, {})
        self.assertEqual(state["status"], "needs-recheck")
        self.assertIn("target-content-changed", state["reason_codes"])
        self.assertEqual(state["previous_record"]["review_id"], record["review_id"],
                         "旧结论必须保留可见,不能只显示未审查")

    def test_same_content_stays_current(self):
        record = sample_record()
        inv = inventory_with("inst-1", "a" * 64)
        state = evaluate_review(record, inv, POLICY_CURRENT, {})
        self.assertEqual(state["status"], "current")
        self.assertEqual(state["reason_codes"], [])

    def test_legacy_record_without_snapshot_is_needs_recheck(self):
        record = sample_record()
        record.pop("review_snapshot_id")
        inv = inventory_with("inst-1", "a" * 64)
        state = evaluate_review(record, inv, POLICY_CURRENT, {})
        self.assertEqual(state["status"], "needs-recheck")
        self.assertIn("missing-review-snapshot", state["reason_codes"])

    def test_alternative_gone_expires_but_unrelated_change_does_not(self):
        record = sample_record()
        record["alternatives"] = ["lg-2"]
        record["alternatives_state"] = {"lg-2": {"tree_hash": "c" * 64}}
        # 目标未变,替代品消失
        inv = inventory_with("inst-1", "a" * 64)
        state = evaluate_review(record, inv, POLICY_CURRENT, {})
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
        state2 = evaluate_review(record, inv2, POLICY_CURRENT, {})
        self.assertTrue(any(c.startswith("alternative-changed") for c in state2["reason_codes"]),
                        state2["reason_codes"])
        # 无关 Skill(C)变化不拖累
        record_c_free = sample_record()
        inv3 = inventory_with("inst-1", "a" * 64)
        self.assertEqual(evaluate_review(record_c_free, inv3, POLICY_CURRENT, {})["status"],
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

    def test_record_saves_actual_policy_version(self):
        """任务1缺口3:record_review 落盘必须带实际 review_policy_version。"""
        from scripts.core.review_state import REVIEW_POLICY_VERSION
        record = record_review(self._queue(), {
            "instance_id": "inst-1", "verdict": "观察", "confidence": "中",
            "evidence": ["source: example"], "safety": "safe"}, "model-x")
        self.assertEqual(record.get("review_policy_version"), REVIEW_POLICY_VERSION,
                         "记账必须保存实际政策版本,评估才有比对依据")

    def test_unprovable_policy_version_expires(self):
        """任务1缺口3:政策版本缺失(旧记录不可证明)或与当前不一致都必须 needs-recheck。"""
        from scripts.core.review_state import REVIEW_POLICY_VERSION, evaluate_review
        inv = inventory_with("inst-1", "a" * 64)
        # 缺版本字段:同一内容也不再当作有效结论
        rec = sample_record()
        rec.pop("review_policy_version")
        state = evaluate_review(rec, inv, {}, {})
        self.assertEqual(state["status"], "needs-recheck")
        self.assertIn("policy-version-unproven", state["reason_codes"])
        # 版本与当前政策不一致
        rec2 = sample_record()
        rec2["review_policy_version"] = "review-policy-v0"
        state2 = evaluate_review(rec2, inv, {}, {})
        self.assertEqual(state2["status"], "needs-recheck")
        self.assertIn("policy-changed", state2["reason_codes"])
        # 当前政策显式带版本时按传入政策比对
        rec3 = sample_record()
        rec3["review_policy_version"] = "review-policy-v0"
        state3 = evaluate_review(rec3, inv, {"review_policy_version": "review-policy-v0"}, {})
        self.assertEqual(state3["status"], "current",
                         "评估按'记录版本==当前政策版本'判定,不是只对常量")


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
            # 进程内调用 cmd_record 的错误结论走 print——接住,避免污染真实验收
            # (尤其 --json)的共享 stdout;结果以返回码与台账文件为准。
            with contextlib.redirect_stdout(io.StringIO()):
                rc = cmd_record(args)
            self.assertEqual(rc, 2, "台账损坏必须拒绝记账")
            self.assertEqual((td / "value-reviews.json").read_text(encoding="utf-8"),
                             "{corrupt", "损坏台账不得被空表覆盖")


class StalenessIntegrationTests(unittest.TestCase):
    """F03 回归:报告与队列必须统一调用 evaluate_review,历史按稳定实例 ID 连接。

    病灶复现:审查时建议删除 demo、保留 alt;之后 alt 被删除——
    旧报告按 logical_id 连接且只比目标哈希,继续显示"建议删除 + 安检 safe";
    demo 内容变化时旧结论干脆消失,被当成全新未审查对象。
    """

    @staticmethod
    def _inventory(demo_hash="a" * 64, with_alt=True, alt_hash="c" * 64,
                   demo_lid="lg-demo"):
        instances = [{"instance_id": "inst-demo", "tree_hash": demo_hash,
                      "logical_name": "demo", "directory_name": "demo",
                      "is_skill": True, "mutable": True, "kind": "user",
                      "client": "shared", "location_id": "shared",
                      "path": "/x/demo", "real_path": "/x/demo"}]
        logicals = [{"logical_id": demo_lid, "name": "demo", "tree_hash": demo_hash,
                     "instance_ids": ["inst-demo"]}]
        if with_alt:
            instances.append({"instance_id": "inst-alt", "tree_hash": alt_hash,
                              "logical_name": "alt", "directory_name": "alt",
                              "is_skill": True, "mutable": True, "kind": "user",
                              "client": "shared", "location_id": "shared",
                              "path": "/x/alt", "real_path": "/x/alt"})
            logicals.append({"logical_id": "lg-alt", "name": "alt",
                             "tree_hash": alt_hash, "instance_ids": ["inst-alt"]})
        return {"schema_version": 2, "instances": instances,
                "logical_skills": logicals, "locations": [], "findings": []}

    @staticmethod
    def _delete_record(alt_hash="c" * 64, demo_hash="a" * 64):
        from scripts.core.review_state import REVIEW_POLICY_VERSION
        return {"review_id": "rv-del1", "instance_id": "inst-demo",
                "logical_id": "lg-demo", "name": "demo", "verdict": "建议删除",
                "reason": "功能被 alt 完全覆盖", "alternatives": ["lg-alt"],
                "alternatives_state": {"lg-alt": {"tree_hash": alt_hash}},
                "unique_capabilities": [], "loss_if_removed": "失去备用入口",
                "confidence": "高", "evidence": ["功能对比:并集属于 alt", "alt 维护活跃"],
                "skill_tree_hash": demo_hash, "inventory_fingerprint": "fp-1",
                "reputation_snapshot_id": "rep-1", "review_snapshot_id": "rs-" + "1" * 12,
                "review_policy_version": REVIEW_POLICY_VERSION,
                "reviewed_at": "2026-09-01 00:00:00", "reviewer_model": "model-x",
                "safety": "safe", "note": ""}

    def _demo_row(self, inv, reviews):
        import scripts.report as report_mod
        view = report_mod.build_view(inv, None, {"value_reviews": reviews})
        rows = view["verdict_rows"]["建议删除"]
        self.assertEqual(len(rows), 1, "建议删除组必须有且只有 demo")
        return rows[0]

    def test_alt_gone_marks_delete_advice_stale_but_history_visible(self):
        reviews = [self._delete_record()]
        row = self._demo_row(self._inventory(), reviews)
        self.assertFalse(row["stale"])
        self.assertFalse(row["alt_stale"])
        # 删除 alt:目标内容未变,但"建议删除"的替代依据已不成立
        row = self._demo_row(self._inventory(with_alt=False), reviews)
        self.assertTrue(row["stale"])
        self.assertTrue(row["alt_stale"], "替代品消失必须单独标注")
        self.assertFalse(row["target_stale"], "目标内容未变,内容安检仍然有效")
        import scripts.report as report_mod
        html = report_mod.render_html(self._inventory(with_alt=False), None,
                                      {"value_reviews": reviews})
        self.assertIn("删除建议已失效", html)
        self.assertIn("安检 safe", html, "内容安检有效与建议失效必须并存,不得混成单一绿标")

    def test_target_change_keeps_history_via_stable_instance_id(self):
        reviews = [self._delete_record()]
        # 内容变化 → 新 logical_id,但 instance_id 不变:历史结论必须可见并标过期
        row = self._demo_row(self._inventory(demo_hash="b" * 64, demo_lid="lg-demo-v2"),
                             reviews)
        self.assertTrue(row["target_stale"])
        self.assertEqual(row["rec"]["review_id"], "rv-del1", "旧结论保留可见")
        import scripts.report as report_mod
        view = report_mod.build_view(self._inventory(demo_hash="b" * 64,
                                                     demo_lid="lg-demo-v2"),
                                     None, {"value_reviews": reviews})
        self.assertEqual([r["inst"]["instance_id"] for r in view["unreviewed"]],
                         ["inst-alt"],
                         "demo 不得因内容变化被当成全新未审查(alt 本就未审)")

    def test_queue_uses_evaluate_review_for_staleness(self):
        from scripts.core.reviews import build_review_queue
        reviews = [self._delete_record()]
        queue = build_review_queue(self._inventory(with_alt=False),
                                   existing_reviews=reviews)
        items = {x["instance_id"]: x for x in queue["items"]}
        self.assertEqual(items["inst-demo"]["previous_review_status"], "needs-recheck",
                         "替代品消失后队列必须要求复核,不得沿用旧哈希口径判 current")
        self.assertTrue(any(c.startswith("alternative-gone") for c in
                            items["inst-demo"]["previous_review_reasons"]))

    def test_record_review_takes_alternative_state_from_full_inventory(self):
        """受保护替代品不进队列;记账时的依赖快照必须从全量安装索引取,否则永远误报。"""
        from scripts.core.reviews import build_review_queue, record_review
        inv = self._inventory()
        queue = build_review_queue(inv, known_sources={"alt": {"type": "self-built"}})
        self.assertEqual([x["instance_id"] for x in queue["items"]], ["inst-demo"],
                         "受保护替代品不进入队列")
        payload = {"instance_id": "inst-demo", "verdict": "建议删除",
                   "reason": "功能被受保护的 alt 覆盖", "confidence": "高",
                   "evidence": ["功能对比:并集属于 alt", "alt 为用户自建"],
                   "loss_if_removed": "失去备用入口", "alternatives": ["lg-alt"]}
        record = record_review(queue, payload, "model-x", inventory=inv)
        self.assertEqual(record["alternatives_state"]["lg-alt"]["tree_hash"], "c" * 64,
                         "替代品快照必须来自全量安装索引,不能记成未知")
        record_no_inv = record_review(queue, payload, "model-x")
        self.assertIsNone(record_no_inv["alternatives_state"]["lg-alt"]["tree_hash"],
                          "未提供 inventory 时保持旧的降级行为(评估按需复核)")


if __name__ == "__main__":
    unittest.main()
