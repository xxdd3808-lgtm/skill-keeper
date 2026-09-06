"""任务4:简化使用——CLI 可发现性、增量体检口径、审查记录消费。

- 统一 CLI 必须能发现并完成:scan/report/updates(更新检查)/review(价值审查)/
  manage(plan/apply/status/recover/rescan/vet)/doctor,全部支持 --json;
- scan 的 need_vet 消费已生效的审查记录:结论有效(内容未变、替代关系完好、
  政策版本一致)的第三方不再要求重复安检;默认体检 = 新增/变化/依赖失效项;
- value_review queue 默认只输出待办(unvetted/needs-recheck),--all 才是完整模式。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(args, env=None):
    e = dict(os.environ)
    e.setdefault("SKILL_KEEPER_DATA", str(Path(tempfile.mkdtemp(prefix="sk-cli-")) / "data"))
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "cli.py"), *args],
                          capture_output=True, text=True, env=e, cwd=str(REPO_ROOT),
                          timeout=120)


class CliDiscoverabilityTests(unittest.TestCase):
    def test_help_lists_all_capabilities(self):
        r = _run_cli(["--help"])
        self.assertEqual(r.returncode, 0, r.stderr[-200:])
        for cmd in ("scan", "report", "updates", "review", "manage", "doctor"):
            self.assertIn(cmd, r.stdout, "CLI --help 必须能发现 " + cmd)

    def test_updates_and_review_subcommands_dispatch(self):
        for cmd in ("updates", "review"):
            r = _run_cli([cmd, "--help"])
            self.assertEqual(r.returncode, 0, cmd + " --help 必须可用: " + r.stderr[-200:])

    def test_updates_json_stable_exit_codes(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            data.mkdir()
            r = _run_cli(["updates", "--json"],
                         env={"SKILL_KEEPER_DATA": str(data)})
            self.assertEqual(r.returncode, 2, "无 inventory 是运行失败(退出 2)")
            payload = json.loads(r.stdout[r.stdout.index("{"):])
            self.assertIn("differs", payload)


class IncrementalReviewTests(unittest.TestCase):
    """默认体检只复核新增、内容或依赖已变化的项。"""

    @staticmethod
    def _third_party_inventory(home_data_th):
        return {"instances": [{"instance_id": "inst-1", "tree_hash": "a" * 64,
                               "logical_name": "demo", "directory_name": "demo",
                               "is_skill": True, "mutable": True,
                               "real_path": "/x/demo", "path": "/x/demo",
                               "kind": "user", "client": "shared"}],
                "logical_skills": [{"logical_id": "lg-1", "name": "demo",
                                    "tree_hash": "a" * 64,
                                    "instance_ids": ["inst-1"]}]}

    def _write_reviews(self, data_dir, records):
        (Path(data_dir) / "value-reviews.json").write_text(
            json.dumps({"schema_version": 2, "reviews": records}, ensure_ascii=False),
            encoding="utf-8")

    def test_need_vet_ignores_currently_valid_reviews(self):
        from scripts.core.review_state import REVIEW_POLICY_VERSION
        from scripts.scan import _need_vet
        inv = self._third_party_inventory(None)
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            (data / "known-sources.json").write_text(json.dumps(
                {"demo": {"type": "github", "repo": "example/demo"}}), encoding="utf-8")
            # 无记录:必须进入待安检
            self.assertEqual(_need_vet(inv, data), ["inst-1"])
            # 有效结论(内容/政策/替代关系都未变):不再要求重复安检
            self._write_reviews(data, [{
                "review_id": "rv-1", "instance_id": "inst-1", "logical_id": "lg-1",
                "verdict": "保留", "reason": "维护活跃", "alternatives": [],
                "alternatives_state": {}, "confidence": "高",
                "evidence": ["source: example/demo", "coverage: 独立"],
                "skill_tree_hash": "a" * 64, "review_snapshot_id": "rs-" + "1" * 12,
                "review_policy_version": REVIEW_POLICY_VERSION,
                "reviewed_at": "2026-09-06 00:00:00", "reviewer_model": "m",
                "safety": "safe"}])
            self.assertEqual(_need_vet(inv, data), [],
                             "已有生效审查结论的第三方不得再进 need_vet")
            # 内容变化 → 结论过期 → 回到待安检
            inv["instances"][0]["tree_hash"] = "b" * 64
            inv["logical_skills"][0]["tree_hash"] = "b" * 64
            self.assertEqual(_need_vet(inv, data), ["inst-1"])

    def test_queue_default_incremental_all_for_full_review(self):
        from scripts.value_review import cmd_queue
        import argparse
        from scripts.core.review_state import REVIEW_POLICY_VERSION
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "known-sources.json").write_text(json.dumps(
                {"demo": {"type": "github", "repo": "example/demo"}}), encoding="utf-8")
            inv = self._third_party_inventory(None)
            (td / "inventory.json").write_text(json.dumps(inv), encoding="utf-8")
            # demo 已有有效结论;另一条 fresh 无结论
            self._write_reviews(td, [{
                "review_id": "rv-1", "instance_id": "inst-1", "logical_id": "lg-1",
                "verdict": "保留", "reason": "维护活跃", "alternatives": [],
                "alternatives_state": {}, "confidence": "高",
                "evidence": ["source: example/demo", "coverage: 独立"],
                "skill_tree_hash": "a" * 64, "review_snapshot_id": "rs-" + "1" * 12,
                "review_policy_version": REVIEW_POLICY_VERSION,
                "reviewed_at": "2026-09-06 00:00:00", "reviewer_model": "m",
                "safety": "safe"}])
            args = argparse.Namespace(inventory=str(td / "inventory.json"),
                                      output=str(td / "review-queue.json"),
                                      data_dir=str(td), json=False, all=False)
            self.assertEqual(cmd_queue(args), 0)
            incremental = json.loads((td / "review-queue.json").read_text())
            self.assertEqual([x["name"] for x in incremental["items"]], [],
                             "默认体检:已有生效结论的项不进待办队列")
            self.assertTrue(incremental.get("incremental"))
            args = argparse.Namespace(inventory=str(td / "inventory.json"),
                                      output=str(td / "review-queue-all.json"),
                                      data_dir=str(td), json=False, all=True)
            self.assertEqual(cmd_queue(args), 0)
            full = json.loads((td / "review-queue-all.json").read_text())
            self.assertEqual([x["name"] for x in full["items"]], ["demo"],
                             "--all 完整模式必须包含全部第三方项")
            self.assertFalse(full.get("incremental", False))


if __name__ == "__main__":
    unittest.main()
