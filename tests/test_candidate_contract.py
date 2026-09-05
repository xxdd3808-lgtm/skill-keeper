"""Task 6 完整候选与缓存生命周期(F07)。

- truncated 上游树必须明确失败,不得产出可用候选;
- Git 100644/100755/120000 原样落地(symlink 不跟随);160000/重复路径/链接父级/
  缺 SKILL.md/无效 frontmatter 明确拒绝;
- source_dir 归一化:仓库根用空串;根目录与多层目录都有成功对照;
- 候选目录按完整哈希复核,损坏目录旁路重物化,不覆盖;
- GC 引用覆盖未过期计划与事务;本地版本更高只是 needs-review 提示。
"""
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.check_updates import stage_candidate
from scripts.core.github import fetch_skill_tree
from scripts.core.staging import collect_staging_references, cleanup_staging, record_ownership
from scripts.core.fingerprint import tree_hash
from tests.helpers import write_skill
from tests.test_check_updates import FakeGh


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


EXEC = "#!/bin/sh\necho ok\n"
LINK_TARGET = "../assets/data.bin"
TREE_ROWS = [
    {"path": "skills/demo/SKILL.md", "type": "blob", "sha": "s1", "mode": "100644"},
    {"path": "skills/demo/run.sh", "type": "blob", "sha": "s2", "mode": "100755"},
    {"path": "skills/demo/link.bin", "type": "blob", "sha": "s3", "mode": "120000"},
]
BLOBS = {
    "repos/x/y/git/blobs/s1": {"content": b64(b"---\nname: demo\ndescription: d\n---\nbody\n"),
                               "encoding": "base64", "size": len(b"---\nname: demo\ndescription: d\n---\nbody\n")},
    "repos/x/y/git/blobs/s2": {"content": b64(EXEC.encode()), "encoding": "base64",
                               "size": len(EXEC.encode())},
    "repos/x/y/git/blobs/s3": {"content": b64(LINK_TARGET.encode()), "encoding": "base64",
                               "size": len(LINK_TARGET.encode())},
}
HEAD = {"sha": "fixed-sha"}


def gh_with(extra_tree=(), tree_over=None):
    rows = list(TREE_ROWS) + list(extra_tree)
    payload = dict(BLOBS)
    payload["repos/x/y/git/trees/fixed-sha?recursive=1"] = tree_over or {"sha": "tree-sha-1", "tree": rows}
    return FakeGh(payload)


class FetchContractTests(unittest.TestCase):
    def test_truncated_tree_fails_without_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            runner = gh_with(tree_over={"tree": TREE_ROWS, "truncated": True})
            result = fetch_skill_tree("x/y", "skills/demo", "fixed-sha", Path(td) / "d",
                                      runner)
            self.assertFalse(result["ok"])
            self.assertNotIn("candidate_hash", result)
            self.assertEqual(result["error"], "tree-truncated")

    def test_modes_materialize_and_hash_roundtrips(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "d"
            result = fetch_skill_tree("x/y", "skills/demo", "fixed-sha", dest, gh_with())
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["tree_complete"])
            self.assertEqual(result["source_dir"], "skills/demo")
            self.assertEqual(result["source_tree_sha"], "tree-sha-1")
            self.assertTrue(result["materialization_version"])
            self.assertEqual(os.stat(dest / "run.sh").st_mode & 0o777, 0o755)
            self.assertEqual(os.stat(dest / "SKILL.md").st_mode & 0o777, 0o644)
            self.assertTrue((dest / "link.bin").is_symlink())
            self.assertEqual(os.readlink(dest / "link.bin"), LINK_TARGET)
            self.assertEqual(tree_hash(dest), result["tree_hash"])

    def test_submodule_and_duplicate_and_link_parent_rejected(self):
        sub = {"path": "skills/demo/vendor", "type": "commit", "sha": "c1", "mode": "160000"}
        with tempfile.TemporaryDirectory() as td:
            r = fetch_skill_tree("x/y", "skills/demo", "fixed-sha", Path(td) / "a",
                                 gh_with(extra_tree=[sub]))
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"], "unsupported-submodule")
        dup = {"path": "skills/demo/run.sh", "type": "blob", "sha": "s2", "mode": "100755"}
        with tempfile.TemporaryDirectory() as td:
            r = fetch_skill_tree("x/y", "skills/demo", "fixed-sha", Path(td) / "b",
                                 gh_with(extra_tree=[dup]))
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"], "duplicate-path")
        link = {"path": "skills/demo/link.bin", "type": "blob", "sha": "s3", "mode": "120000"}
        under = {"path": "skills/demo/link.bin/nested", "type": "blob", "sha": "s1",
                 "mode": "100644"}
        with tempfile.TemporaryDirectory() as td:
            r = fetch_skill_tree("x/y", "skills/demo", "fixed-sha", Path(td) / "c",
                                 gh_with(extra_tree=[under]))
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"], "link-parent-conflict")

    def test_repo_root_skill_and_invalid_frontmatter(self):
        root_rows = [{"path": "SKILL.md", "type": "blob", "sha": "r1", "mode": "100644"}]
        good = dict(BLOBS)
        good["repos/x/y/git/blobs/r1"] = {
            "content": b64(b"---\nname: rooty\ndescription: root demo\n---\nbody\n"),
            "encoding": "base64"}
        with tempfile.TemporaryDirectory() as td:
            r = fetch_skill_tree("x/y", "", "fixed-sha", Path(td) / "r", FakeGh(good | {
                "repos/x/y/git/trees/fixed-sha?recursive=1": {"tree": root_rows}}))
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["source_dir"], "")
            self.assertTrue((Path(td) / "r/SKILL.md").is_file())
        bad = {"repos/x/y/git/blobs/r1": {"content": b64(b"no frontmatter here"),
                                          "encoding": "base64"}}
        with tempfile.TemporaryDirectory() as td:
            r = fetch_skill_tree("x/y", "", "fixed-sha", Path(td) / "r2", FakeGh(bad | {
                "repos/x/y/git/trees/fixed-sha?recursive=1": {"tree": root_rows}}))
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"], "invalid-frontmatter")


class StagingLifecycleTests(unittest.TestCase):
    def test_corrupt_candidate_rematerialized_not_trusted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "staging"
            first = stage_candidate("x/y", "skills/demo", "fixed-sha", root, gh_with())
            self.assertTrue(first["ok"])
            cand = Path(first["staging_path"])
            (cand / "SKILL.md").write_text("corrupted", encoding="utf-8")
            second = stage_candidate("x/y", "skills/demo", "fixed-sha", root, gh_with())
            self.assertTrue(second["ok"])
            self.assertEqual(tree_hash(Path(second["staging_path"])), first["candidate_hash"],
                             "重物化目录必须与候选哈希一致")
            self.assertNotEqual(second["staging_path"], first["staging_path"],
                                "损坏目录保留证据,旁路重物化,不覆盖")

    def test_gc_references_cover_plans_and_transactions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "staging"
            keep_plan = root / "cand-111111111111"
            keep_plan.mkdir(parents=True)
            record_ownership(root, keep_plan.name, {})
            keep_txn = root / "cand-222222222222"
            keep_txn.mkdir(parents=True)
            record_ownership(root, keep_txn.name, {})
            doomed = root / "cand-333333333333"
            doomed.mkdir()
            record_ownership(root, doomed.name, {})
            refs = collect_staging_references(
                {"differs": [{"staging_path": str(keep_plan)}]},
                [{"preconditions": [["staging_path", str(keep_plan)]],
                  "expires_at": "2099-01-01 00:00:00"}],
                [{"phase": "mutating", "candidate_holding": str(keep_txn)}])
            result = cleanup_staging(root, refs)
            self.assertIn(keep_plan.name, result["kept"])
            self.assertIn(keep_txn.name, result["kept"])
            self.assertIn(doomed.name, result["removed"])


if __name__ == "__main__":
    unittest.main()
