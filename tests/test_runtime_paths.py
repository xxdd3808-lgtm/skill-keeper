"""Task 8(F10):外部运行态路径——运行数据写不到 Skill 树;迁移只预演不搬家。"""
import os
import tempfile
import unittest
from pathlib import Path

from scripts.core.runtime import RuntimePaths, plan_runtime_migration, publish_snapshot
from scripts.core.fingerprint import tree_hash
from tests.helpers import write_skill


class RuntimePathIsolationTests(unittest.TestCase):
    def test_scan_report_never_change_skill_tree_hash(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            data = home / "runtime-data"  # 运行态在 Skill 树外
            data.mkdir()
            demo = write_skill(home / ".agents/skills", "demo", body="stable")
            expected = tree_hash(demo)
            paths = RuntimePaths(home=home, data_dir=data)
            for _ in range(2):
                r = publish_snapshot(paths)
                self.assertTrue(r["ok"], r)
                self.assertEqual(tree_hash(demo), expected,
                                 "连续 scan/report 不得改变 Skill 内容指纹")

    def test_engine_kwargs_and_env_pin_same_paths(self):
        with tempfile.TemporaryDirectory() as td:
            paths = RuntimePaths(home=Path(td), data_dir=Path(td) / "rt",
                                 staging_dir=Path(td) / "stage")
            kwargs = paths.engine_kwargs()
            self.assertEqual(kwargs["data_dir"], Path(td) / "rt")
            env = paths.subprocess_env()
            self.assertEqual(env["SKILL_KEEPER_DATA"], str(Path(td) / "rt"))
            self.assertEqual(env["SKILL_KEEPER_STAGING"], str(Path(td) / "stage"))


class MigrationPreviewTests(unittest.TestCase):
    def test_plan_runtime_migration_lists_without_copying(self):
        with tempfile.TemporaryDirectory() as td:
            old_dir = Path(td) / "old-data"
            new_dir = Path(td) / "new-data"
            old_dir.mkdir()
            new_dir.mkdir()
            (old_dir / "known-sources.json").write_text("{}", encoding="utf-8")
            (old_dir / "self-built.txt").write_text("demo\n", encoding="utf-8")
            before = sorted(p.name for p in old_dir.iterdir())
            result = plan_runtime_migration(old_dir, new_dir)
            self.assertTrue(result["migratable"])
            names = {f["relative"] for f in result["files"]}
            self.assertIn("known-sources.json", names)
            self.assertTrue(all(f.get("sha256") for f in result["files"]))
            self.assertEqual(sorted(p.name for p in old_dir.iterdir()), before,
                             "预演绝不移动/删除真实数据")
            self.assertEqual(list(new_dir.iterdir()), [], "预演绝不写入新目录")

    def test_conflicting_target_blocks_migration(self):
        with tempfile.TemporaryDirectory() as td:
            old_dir = Path(td) / "old"
            new_dir = Path(td) / "new"
            old_dir.mkdir()
            new_dir.mkdir()
            (old_dir / "known-sources.json").write_text('{"a": 1}', encoding="utf-8")
            (new_dir / "known-sources.json").write_text('{"b": 2}', encoding="utf-8")
            result = plan_runtime_migration(old_dir, new_dir)
            self.assertFalse(result["migratable"], "冲突必须阻止整体迁移")
            conflicts = [f for f in result["files"] if f.get("conflict")]
            self.assertEqual(len(conflicts), 1)


if __name__ == "__main__":
    unittest.main()
