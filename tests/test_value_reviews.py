import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests/fixtures/inventory-value.json"


def review_inventory_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def reputation_fixture():
    return {"schema_version": 2, "repos": {
        "example/word-skills": {"ok": True, "repo": "example/word-skills", "stars": 42,
                                "forks": 3, "archived": False, "pushed_at": "2026-01-01T00:00:00Z",
                                "popularity_scope": "repository", "fetched_at": "2026-08-31T09:00:00",
                                "commit_sha": "cafe123"}}}


def one_item_queue():
    from scripts.core.reviews import build_review_queue
    inv = review_inventory_fixture()
    queue = build_review_queue(inv, reputation_fixture(), {})
    items = [x for x in queue["items"] if x["instance_id"] == "third-party-word"]
    return {**queue, "items": items}


DELETE_PAYLOAD = {
    "instance_id": "third-party-word", "verdict": "建议删除",
    "reason": "功能已被客户端文档工具覆盖,且该 Skill 没有额外能力",
    "alternatives": ["codex-builtin-docx"], "unique_capabilities": [],
    "loss_if_removed": "失去旧触发描述,不影响文档处理",
    "confidence": "高", "evidence": ["overlap:0.91", "repo:archived"],
}


class ValueReviewTests(unittest.TestCase):
    def test_protected_skills_are_alternatives_but_not_review_targets(self):
        from scripts.core.reviews import build_review_queue
        inv = review_inventory_fixture()
        queue = build_review_queue(inv, reputation_fixture(), {})
        target_ids = {x["instance_id"] for x in queue["items"]}
        self.assertNotIn("codex-builtin-docx", target_ids, "受保护 skill 不进入审查队列")
        self.assertIn("third-party-word", target_ids)
        self.assertIn("unknown-tool", target_ids, "来源未知同样要审查")
        docx_item = next(x for x in queue["items"] if x["instance_id"] == "third-party-word")
        self.assertIn("codex-builtin-docx", docx_item["alternative_candidates"],
                      "受保护 skill 可以作为替代候选")

    def test_delete_recommendation_requires_explanation_not_fixed_score(self):
        from scripts.core.reviews import record_review
        saved = record_review(one_item_queue(), dict(DELETE_PAYLOAD), "test-model")
        self.assertEqual(saved["verdict"], "建议删除")
        self.assertEqual(len(saved["evidence"]), 2)
        self.assertEqual(saved["reviewer_model"], "test-model")
        self.assertEqual(saved["skill_tree_hash"], "2" * 64, "结论必须绑定当前内容指纹")
        self.assertTrue(saved["reviewed_at"])
        self.assertTrue(saved["inventory_fingerprint"])
        self.assertTrue(saved["reputation_snapshot_id"])

    def test_stars_alone_cannot_produce_delete(self):
        from scripts.core.reviews import record_review
        payload = dict(DELETE_PAYLOAD, evidence=["stars: 3"])
        with self.assertRaises(ValueError):
            record_review(one_item_queue(), payload, "test-model")

    def test_delete_requires_reason_loss_and_confidence(self):
        from scripts.core.reviews import record_review
        for field, value in (("reason", "  "), ("loss_if_removed", ""), ("confidence", "")):
            payload = dict(DELETE_PAYLOAD, **{field: value})
            with self.assertRaises(ValueError):
                record_review(one_item_queue(), payload, "test-model")

    def test_single_evidence_cannot_support_keep_or_delete(self):
        from scripts.core.reviews import record_review
        with self.assertRaises(ValueError):
            record_review(one_item_queue(), dict(DELETE_PAYLOAD, evidence=["overlap:0.91"]), "m")
        keep = dict(DELETE_PAYLOAD, verdict="保留", loss_if_removed="",
                    evidence=["unique:has-templates"])
        with self.assertRaises(ValueError):
            record_review(one_item_queue(), keep, "m")

    def test_unknown_instance_or_bad_verdict_rejected(self):
        from scripts.core.reviews import record_review
        with self.assertRaises(ValueError):
            record_review(one_item_queue(), dict(DELETE_PAYLOAD, instance_id="nope"), "m")
        with self.assertRaises(ValueError):
            record_review(one_item_queue(), dict(DELETE_PAYLOAD, verdict="自动删除"), "m")

    def test_observe_allows_single_evidence_and_stamps_binding(self):
        from scripts.core.reviews import record_review
        saved = record_review(one_item_queue(), dict(
            DELETE_PAYLOAD, verdict="观察", reason="信息不足", loss_if_removed="",
            alternatives=[], confidence="低", evidence=["source:unknown"]), "test-model")
        self.assertEqual(saved["verdict"], "观察")

    def test_exact_duplicates_and_candidate_pairs(self):
        from scripts.core.overlap import candidate_pairs, exact_duplicate_groups
        inv = review_inventory_fixture()
        self.assertEqual(exact_duplicate_groups(inv), [], "fixture 无精确副本")
        pairs = candidate_pairs(inv, min_similarity=0.32)
        self.assertTrue(all(p["score"] >= 0.32 for p in pairs))
        for p in pairs:
            self.assertIn("breakdown", p, "相似度必须分项可解释")

    def test_queue_cli_with_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "review-queue.json")
            env = dict(os.environ, HOME=td)
            r = subprocess.run(
                [sys.executable, "scripts/value_review.py", "queue",
                 "--inventory", str(FIXTURE), "--output", out, "--json"],
                capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            data = json.loads(r.stdout)
            ids = {x["instance_id"] for x in data["items"]}
            self.assertIn("third-party-word", ids)
            self.assertNotIn("codex-builtin-docx", ids)


class SourceWiringTests(unittest.TestCase):
    """来源数据流:known-sources/self-built 白名单必须真正参与队列分类(设计 §3.1/§6)。"""

    def test_self_built_is_excluded_via_known_sources_param(self):
        from scripts.core.reviews import build_review_queue
        inv = review_inventory_fixture()
        queue = build_review_queue(inv, {}, {}, known_sources={"word": {"type": "self-built"}})
        ids = {x["instance_id"] for x in queue["items"]}
        self.assertNotIn("third-party-word", ids, "自建白名单必须受保护,不进第三方队列")
        self.assertIn("unknown-tool", ids, "没登记的仍然要审")

    def test_known_sources_in_inventory_are_honored(self):
        from scripts.core.reviews import build_review_queue
        inv = review_inventory_fixture()
        inv["known_sources"] = {"word": {"type": "github", "repo": "example/word-skills",
                                         "path": "skills/word/SKILL.md"}}
        queue = build_review_queue(inv, reputation_fixture(), {})
        item = next(x for x in queue["items"] if x["instance_id"] == "third-party-word")
        self.assertEqual(item["provenance"]["type"], "github")
        self.assertEqual(item["provenance"]["repo"], "example/word-skills")
        self.assertEqual(item["provenance"]["confidence"], "high")

    def test_queue_cli_loads_personal_config_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            data.mkdir()
            (data / "known-sources.json").write_text(
                json.dumps({"word": {"type": "self-built"}}), encoding="utf-8")
            (data / "vetted.json").write_text(json.dumps(
                {"mystery": {"verdict": "safe", "vetted_at": "2026-01-01 00:00:00",
                             "note": "v1 旧安检"}}), encoding="utf-8")
            out = Path(td) / "queue.json"
            env = dict(os.environ, HOME=td)
            r = subprocess.run(
                [sys.executable, "scripts/value_review.py", "queue",
                 "--inventory", str(FIXTURE), "--output", str(out),
                 "--data-dir", str(data), "--json"],
                capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            payload = json.loads(r.stdout)
            ids = {x["instance_id"] for x in payload["items"]}
            self.assertNotIn("third-party-word", ids, "CLI 必须读取 known-sources/self-built 白名单")
            self.assertIn("unknown-tool", ids)
            item = next(x for x in payload["items"] if x["instance_id"] == "unknown-tool")
            self.assertIsNotNone(item.get("legacy_vetting"), "v1 安检历史按 needs-recheck 展示")
            self.assertEqual(item["legacy_vetting"]["previous_verdict"], "safe")
            self.assertFalse((data / "vetted-v2.json").exists(),
                             "queue 是只读命令,不得写任何迁移/缓存文件")


if __name__ == "__main__":
    unittest.main()
