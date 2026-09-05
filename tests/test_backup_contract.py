"""Task 1 备份/恢复合同(F01/F02)。

- manifest 严格校验:路径边界、类型、权限、摘要、唯一性、整树哈希、资源上限;
- 完整往返:内部符号链接、目录权限(含根目录与 0700/0555);
- 原子发布:写入失败不留可选中的"成功备份";
- 顶层 symlink:目标必须已存在且匹配,或同事务先恢复;
- 失败清理:只撤销本次实际落地的对象;校验异常与返回 False 同责;
- 恢复计划绑定 archive_sha256 与目标集合,归档被替换/旧计划缺绑定一律拒绝。
"""
import hashlib
import io
import json
import os
import shutil
import stat
import tarfile
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.core import backup as backup_mod
from scripts.core.backup import (BackupError, PAYLOAD_PREFIX, create_backup,
                                 manifest_hash, restore_backup, validate_backup_manifest,
                                 verify_backup)
from scripts.core.changes import (ChangeContext, ChangeError, apply_plan,
                                  create_restore_plan, plan_digest, write_plan)
from scripts.core.fingerprint import tree_hash, tree_manifest
from scripts.core.paths import PathScopeError, confined_destination, validate_relative_path
from tests.helpers import temp_home
from tests.test_backup_restore import two_location_skill_fixture
from tests.test_change_remove import change_env


def snapshot(root):
    """临时根的落地快照:相对路径 → (类型, 权限, 链接目标或内容摘要)。"""
    rows = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            st = os.lstat(full)
            if os.path.islink(full):
                rows[rel] = ("symlink", None, os.readlink(full))
            elif os.path.isdir(full):
                rows[rel] = ("dir", oct(stat.S_IMODE(st.st_mode)), None)
            else:
                digest = hashlib.sha256()
                with open(full, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 16), b""):
                        digest.update(chunk)
                rows[rel] = ("file", oct(stat.S_IMODE(st.st_mode)), digest.hexdigest())
    return rows


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def valid_manifest_env(testcase):
    """单实体 demo 环境里生成一份通过校验的备份;返回 (env, saved, manifest)。"""
    env = change_env(testcase)
    saved = create_backup(env.remove_plan(), env.inventory, env.context.backup_dir)
    info = verify_backup(saved["path"])
    return env, saved, info["manifest"]


def consistent_payload(env, manifest):
    """按 manifest 从磁盘读 payload(用于重组"内部一致"的归档做变异测试)。"""
    payload = {}
    by_iid = {i["instance_id"]: i for i in env.inventory["instances"]}
    for entry in manifest["entries"]:
        root = Path(by_iid[entry["instance_id"]]["path"])
        for row in entry["tree_manifest"]:
            if row["type"] == "file":
                arc = PAYLOAD_PREFIX + entry["instance_id"] + "/" + row["path"]
                payload[arc] = (root / row["path"]).read_bytes()
    return payload


def write_archive(testcase, manifest, payload, extra_members=(), compressed=True):
    fd, path = tempfile.mkstemp(suffix=".tar.gz" if compressed else ".tar")
    os.close(fd)
    testcase.addCleanup(os.unlink, path)
    with tarfile.open(path, "w:gz" if compressed else "w") as t:
        mb = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(mb)
        t.addfile(info, io.BytesIO(mb))
        for arc, data in payload.items():
            info = tarfile.TarInfo(arc)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        for member in extra_members:
            t.addfile(member)
    return Path(path)


class PathContractTests(unittest.TestCase):
    def test_validate_relative_path_rejects_bad_inputs(self):
        for raw in ("", None, 42, "/abs", "../escape", "a/../b", "./x", "a/./b",
                    "a//b", "a/", "a\\b", "a\x01b", "a\x7fb"):
            with self.assertRaises(PathScopeError, msg=repr(raw)):
                validate_relative_path(raw)
        self.assertEqual(validate_relative_path("a/b/c.txt"), ("a", "b", "c.txt"))

    def test_confined_destination_rejects_escape_and_link_parents(self):
        home = temp_home(self)
        root = home / "skills"
        (root / "real/sub").mkdir(parents=True)
        self.assertEqual(confined_destination(root, "real/sub/x"),
                         root / "real/sub/x")
        with self.assertRaises(PathScopeError):
            confined_destination(root, "/abs")
        with self.assertRaises(PathScopeError):
            confined_destination(root, "../escape")
        # 中间父目录是符号链接:拒绝
        outside = home / "outside"
        outside.mkdir()
        os.symlink(outside, root / "linkdir")
        with self.assertRaises(PathScopeError):
            confined_destination(root, "linkdir/x")
        # 中间父路径被文件占用:拒绝
        (root / "afile").write_text("x", encoding="utf-8")
        with self.assertRaises(PathScopeError):
            confined_destination(root, "afile/x")
        # 根目录本身经 symlink 指向外部:实际父目录归属核对仍然拒绝越界组件
        link_root = home / "rootlink"
        os.symlink(root, link_root)
        with self.assertRaises(PathScopeError):
            confined_destination(link_root, "../escape")

    def test_manifest_hash_matches_tree_hash(self):
        home = temp_home(self)
        demo = home / "demo"
        demo.mkdir()
        (demo / "SKILL.md").write_text("x", encoding="utf-8")
        (demo / "sub").mkdir()
        (demo / "sub" / "a.bin").write_bytes(b"\x00\x01")
        self.assertEqual(manifest_hash(tree_manifest(demo)), tree_hash(demo))


class ManifestContractTests(unittest.TestCase):
    def test_valid_control_and_archive_hash(self):
        env, saved, manifest = valid_manifest_env(self)
        before = snapshot(env.home)
        info = verify_backup(saved["path"])
        self.assertTrue(info["ok"])
        self.assertEqual(info["archive_sha256"], _sha256_file(saved["path"]))
        self.assertEqual(snapshot(env.home), before, "verify 必须只读")

    def test_malicious_manifest_variants_are_rejected(self):
        env, saved, manifest = valid_manifest_env(self)
        payload = consistent_payload(env, manifest)
        entry = manifest["entries"][0]

        def mutated(change, keyword):
            doc = deepcopy(manifest)
            change(doc)
            before = snapshot(env.home)
            archive = write_archive(self, doc, payload)
            with self.assertRaises(BackupError, msg=keyword):
                verify_backup(archive)
            self.assertEqual(snapshot(env.home), before, "verify 不得落地任何文件")

        def set_rel(doc, value):
            doc["entries"][0]["original_relative_path"] = value

        mutated(lambda d: set_rel(d, "../escape"), "路径")
        mutated(lambda d: set_rel(d, "/absolute-fixture"), "路径")
        mutated(lambda d: set_rel(d, "a/../../escape"), "路径")
        mutated(lambda d: set_rel(d, "a//b"), "路径")

        def dir_entry_escape(doc):
            doc["entries"][0]["type"] = "dir"
            doc["entries"][0]["original_relative_path"] = "../escape"
        mutated(dir_entry_escape, "路径")

        def duplicate_target(doc):
            clone = deepcopy(doc["entries"][0])
            doc["entries"].append(clone)
        mutated(duplicate_target, "重复")

        def overlapping_targets(doc):
            clone = deepcopy(doc["entries"][0])
            clone["instance_id"] = "f" * 20
            clone["original_relative_path"] = "demo/sub"
            doc["entries"].append(clone)
        mutated(overlapping_targets, "重叠")

        def bad_tree_hash(doc):
            doc["entries"][0]["tree_hash"] = "0" * 64
        mutated(bad_tree_hash, "摘要")

        def bad_sha_format(doc):
            doc["entries"][0]["tree_hash"] = "zz"
        mutated(bad_sha_format, "摘要")

        def bad_mode(doc):
            doc["entries"][0]["mode"] = "0o999"
        mutated(bad_mode, "权限")

        def file_parent_is_symlink(doc):
            doc["entries"][0]["tree_manifest"] = [
                {"path": "link", "type": "symlink", "mode": "0o755", "target": "run.py"},
                {"path": "link/inner", "type": "file", "mode": "0o644",
                 "sha256": hashlib.sha256(b"fixture").hexdigest()},
            ]
            doc["entries"][0]["tree_hash"] = manifest_hash(doc["entries"][0]["tree_manifest"])
        mutated(file_parent_is_symlink, "父目录")

        def duplicate_tree_path(doc):
            rows = doc["entries"][0]["tree_manifest"]
            rows.append(deepcopy(rows[0]))
            doc["entries"][0]["tree_hash"] = manifest_hash(rows)
        mutated(duplicate_tree_path, "重复")

        def special_member(doc):
            rows = doc["entries"][0]["tree_manifest"]
            rows.append({"path": "fifo", "type": "special", "mode": "0o644"})
            doc["entries"][0]["tree_hash"] = manifest_hash(rows)
        mutated(special_member, "特殊文件")

        def missing_entries(doc):
            del doc["entries"]
        mutated(missing_entries, "entries")

        def entries_not_list(doc):
            doc["entries"] = {"a": "b"}
        mutated(entries_not_list, "entries")

        def bad_schema(doc):
            doc["schema"] = 99
        mutated(bad_schema, "schema")

        def empty_entries(doc):
            doc["entries"] = []
        mutated(empty_entries, "entries")

    def test_validate_backup_manifest_direct_contract(self):
        env, saved, manifest = valid_manifest_env(self)
        validated = validate_backup_manifest(deepcopy(manifest))
        self.assertEqual(validated["backup_id"], manifest["backup_id"])

    def test_resource_limits_are_enforced(self):
        env, saved, manifest = valid_manifest_env(self)
        payload = consistent_payload(env, manifest)
        archive = write_archive(self, manifest, payload)
        self.assertTrue(verify_backup(archive)["ok"], "对照组必须通过")
        for constant, value in (("MAX_ENTRIES", 0), ("MAX_FILE_BYTES", 4),
                                ("MAX_MANIFEST_BYTES", 4), ("MAX_TOTAL_BYTES", 4)):
            with patch.object(backup_mod, constant, value):
                with self.assertRaises(BackupError, msg=constant):
                    verify_backup(archive)


class RoundtripContractTests(unittest.TestCase):
    def test_roundtrip_nested_link_and_directory_modes(self):
        env = change_env(self)
        private = env.skill_path / "private"
        private.mkdir(mode=0o700)
        (private / "note").write_bytes(b"fixture")
        (env.skill_path / "alias.py").symlink_to("run.py")
        expected = tree_hash(env.skill_path)
        env.inventory["instances"][0]["tree_hash"] = expected
        saved = create_backup(env.remove_plan(), env.inventory,
                              env.context.backup_dir)
        self.assertTrue(verify_backup(saved["path"])["ok"])
        shutil.rmtree(env.skill_path)
        restore_backup(saved["path"], env.inventory["locations"])
        self.assertEqual(tree_hash(env.skill_path), expected)
        self.assertEqual(os.readlink(env.skill_path / "alias.py"), "run.py")
        self.assertEqual(private.stat().st_mode & 0o777, 0o700)

    def test_roundtrip_root_and_readonly_dir_modes(self):
        for mode in (0o750, 0o555):
            with self.subTest(mode=oct(mode)):
                env = change_env(self)
                (env.skill_path / "sub").mkdir()
                os.chmod(env.skill_path, mode)
                expected = tree_hash(env.skill_path)
                env.inventory["instances"][0]["tree_hash"] = expected
                saved = create_backup(env.remove_plan(), env.inventory,
                                      env.context.backup_dir)
                os.chmod(env.skill_path, 0o755)  # 只读目录自身先放开才能删除
                shutil.rmtree(env.skill_path)
                restore_backup(saved["path"], env.inventory["locations"])
                self.assertEqual(tree_hash(env.skill_path), expected)
                self.assertEqual(env.skill_path.stat().st_mode & 0o777, mode)
                os.chmod(env.skill_path, 0o755)  # 让临时目录可清理

    def test_create_backup_failure_leaves_no_partial_archive(self):
        env = change_env(self)
        real_addfile = tarfile.TarFile.addfile
        calls = {"n": 0}

        def flaky_addfile(inner_self, tarinfo, fileobj=None):
            calls["n"] += 1
            if calls["n"] == 2:  # manifest 之后第一个 payload 成员
                raise OSError("fixture disk full")
            return real_addfile(inner_self, tarinfo, fileobj)

        backup_dir = Path(env.context.backup_dir)
        with patch.object(tarfile.TarFile, "addfile", flaky_addfile):
            with self.assertRaises(BackupError):
                create_backup(env.remove_plan(), env.inventory, backup_dir)
        self.assertEqual(list(backup_dir.glob("backup-*")), [],
                         "写入失败不得留下可选中的成功备份")
        self.assertEqual(list(backup_dir.glob(".tmp-backup-*")), [])


class SymlinkRestoreContractTests(unittest.TestCase):
    def _alias_only_archive(self, testcase, env):
        saved = create_backup(env.plan, env.inventory, env.backup_dir)
        info = verify_backup(Path(saved["path"]))
        manifest = deepcopy(info["manifest"])
        manifest["entries"] = [e for e in manifest["entries"] if e["type"] == "symlink"]
        self.assertEqual(len(manifest["entries"]), 1)
        payload = consistent_payload(env, manifest)
        return write_archive(testcase, manifest, payload)

    def test_top_level_symlink_restored_when_target_matches(self):
        env = two_location_skill_fixture(self)
        expected_target = os.readlink(env.claude_root / "demo")
        archive = self._alias_only_archive(self, env)
        os.remove(env.claude_root / "demo")  # 只移除链接,正本保留且内容一致
        result = restore_backup(archive, env.locations)
        self.assertTrue((env.claude_root / "demo").is_symlink())
        self.assertEqual(os.readlink(env.claude_root / "demo"), expected_target)
        self.assertIn("demo", result["restored"][0])

    def test_symlink_restore_refuses_missing_target(self):
        env = two_location_skill_fixture(self)
        archive = self._alias_only_archive(self, env)
        shutil.rmtree(env.demo)  # 链接目标缺失
        before = snapshot(env.claude_root)
        with self.assertRaises(BackupError):
            restore_backup(archive, env.locations)
        self.assertEqual(snapshot(env.claude_root), before,
                         "目标缺失时不得凭链接 payload 写出任何东西")

    def test_symlink_restore_refuses_changed_target(self):
        env = two_location_skill_fixture(self)
        archive = self._alias_only_archive(self, env)
        (env.demo / "extra.txt").write_text("drift", encoding="utf-8")
        os.remove(env.claude_root / "demo")
        with self.assertRaises(BackupError):
            restore_backup(archive, env.locations)
        self.assertTrue((env.demo / "extra.txt").exists(), "不匹配目标不得被破坏")
        self.assertFalse(os.path.lexists(env.claude_root / "demo"))

    def test_reversed_manifest_order_still_restores_both(self):
        env = two_location_skill_fixture(self)
        saved = create_backup(env.plan, env.inventory, env.backup_dir)
        info = verify_backup(Path(saved["path"]))
        manifest = deepcopy(info["manifest"])
        payload = consistent_payload(env, manifest)
        manifest["entries"] = list(reversed(manifest["entries"]))  # 别名条目在前
        archive = write_archive(self, manifest, payload)
        env.remove_targets()
        result = restore_backup(archive, env.locations)
        self.assertEqual(result["restored_hashes"], env.original_hashes)
        self.assertTrue((env.claude_root / "demo").is_symlink())
        self.assertTrue((env.demo / "SKILL.md").exists())

    def test_relative_top_level_symlink_roundtrip(self):
        """相对目标的顶层链接:目标实体是同事务先恢复对象,顺序无关恢复成功。"""
        from scripts.core.changes import create_remove_plan
        from scripts.core.fingerprint import instance_id
        env = change_env(self)
        link = env.agents_root / "alias"
        os.symlink("demo", link)  # 相对顶层链接
        iid = instance_id("shared", "alias", str(link))
        env.inventory["instances"].append(
            {"instance_id": iid, "location_id": "shared", "directory_name": "alias",
             "path": str(link), "real_path": str(env.skill_path),
             "tree_hash": tree_hash(env.skill_path),
             "mutable": True, "is_symlink": True, "is_skill": True})
        plan = create_remove_plan([env.iid, iid], env.inventory, "test", env.plans_dir)
        saved = create_backup(plan, env.inventory, env.context.backup_dir)
        self.assertTrue(verify_backup(saved["path"])["ok"])
        os.remove(link)
        shutil.rmtree(env.skill_path)
        restore_backup(saved["path"], env.inventory["locations"])
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "demo")
        self.assertEqual((env.skill_path / "SKILL.md").exists(), True)


class RestoreCleanupContractTests(unittest.TestCase):
    def test_second_entity_materialize_failure_revokes_first(self):
        env = two_location_skill_fixture(self)
        saved = create_backup(env.plan, env.inventory, env.backup_dir)
        env.remove_targets()
        os.chmod(env.claude_root, 0o555)  # 第二个实体所在根不可写
        self.addCleanup(os.chmod, env.claude_root, 0o755)
        with self.assertRaises(BackupError):
            restore_backup(Path(saved["path"]), env.locations)
        self.assertFalse(env.demo.exists(), "已落地的第一个实体必须撤销")
        self.assertEqual(os.listdir(env.claude_root), [], "失败根目录不留半成品")
        home = env.backup_dir.parent
        for dirpath, dirnames, _ in os.walk(home):
            for name in dirnames:
                self.assertFalse(name.startswith(".restore-"),
                                 "残留恢复临时目录: " + name)

    def test_post_verify_exception_revokes_published_entity(self):
        env = change_env(self)
        saved = create_backup(env.remove_plan(), env.inventory, env.context.backup_dir)
        shutil.rmtree(env.skill_path)
        real_tree_hash = backup_mod.tree_hash
        calls = {"n": 0}

        def flaky_tree_hash(path):
            calls["n"] += 1
            if calls["n"] >= 2:  # 第 1 次是物化校验,第 2 次是发布后验
                raise RuntimeError("fixture post-verify crash")
            return real_tree_hash(path)

        with patch.object(backup_mod, "tree_hash", flaky_tree_hash):
            with self.assertRaises(BackupError):
                restore_backup(Path(saved["path"]), env.inventory["locations"])
        self.assertFalse(os.path.lexists(env.skill_path),
                         "校验异常与返回 False 同责:已发布对象必须撤销")


class RestorePlanBindingTests(unittest.TestCase):
    def _context(self, env):
        data = env.backup_dir.parent / "data"
        return ChangeContext(
            data_dir=data, plans_dir=data / "change-plans",
            backup_dir=env.backup_dir, audit_path=data / "audit-v2.jsonl",
            lock_path=data / ".change.lock", load_inventory=lambda: env.inventory)

    def test_restore_plan_binds_archive_content(self):
        env = two_location_skill_fixture(self)
        context = self._context(env)
        backup_a = create_backup(env.plan, env.inventory, env.backup_dir)
        (env.demo / "v2.txt").write_text("changed", encoding="utf-8")
        backup_b = create_backup(env.plan, env.inventory, env.backup_dir)
        plan = create_restore_plan(backup_a["backup_id"], env.backup_dir,
                                   context.plans_dir)
        # 用户确认后、执行前,同一备份路径被替换成另一份合法归档
        shutil.copy2(backup_b["path"], backup_a["path"])
        env.remove_targets()
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, context)
        self.assertFalse(env.demo.exists(), "归档被替换后旧计划不得执行任何恢复")

    def test_old_restore_plan_without_binding_is_rejected(self):
        env = two_location_skill_fixture(self)
        context = self._context(env)
        context.plans_dir.mkdir(parents=True, exist_ok=True)
        saved = create_backup(env.plan, env.inventory, env.backup_dir)
        row = {"plan_id": "plan-old-restore", "action": "restore",
               "target_ids": list(env.plan.target_ids),
               "preconditions": [["backup_id", saved["backup_id"]],
                                 ["backup_path", str(Path(saved["path"]).resolve())]],
               "summary": "旧格式恢复计划", "created_at": "2026-09-05 00:00:00",
               "expires_at": "2099-01-01 00:00:00"}
        row["digest"] = plan_digest(row)
        from scripts.core.models import ChangePlan as CP
        write_plan(CP.from_dict(row), context.plans_dir)
        env.remove_targets()
        with self.assertRaises(ChangeError):
            apply_plan("plan-old-restore", row["digest"], True, context)
        self.assertTrue(Path(saved["path"]).is_file(), "原备份必须保留")
        self.assertFalse(env.demo.exists(), "未绑定的旧计划不得落地")

    def test_restore_plan_happy_path_still_restores(self):
        env = two_location_skill_fixture(self)
        context = self._context(env)
        saved = create_backup(env.plan, env.inventory, env.backup_dir)
        plan = create_restore_plan(saved["backup_id"], env.backup_dir,
                                   context.plans_dir)
        env.remove_targets()
        result = apply_plan(plan.plan_id, plan.digest, True, context)
        self.assertTrue(result["ok"])
        self.assertTrue((env.demo / "SKILL.md").exists())
        self.assertTrue((env.claude_root / "demo").is_symlink())


if __name__ == "__main__":
    unittest.main()
