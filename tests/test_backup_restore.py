import hashlib, io, os, shutil, tarfile, tempfile, time, unittest
from pathlib import Path

from scripts.core.backup import BackupError, create_backup, inspect_legacy_backup, restore_backup, verify_backup
from scripts.core.fingerprint import instance_id, tree_hash
from scripts.core.models import ChangePlan, Location
from tests.helpers import temp_home, write_skill


def two_location_skill_fixture(testcase):
    """shared 放 demo 正本(含子目录与二进制),claude 放指向它的符号链接。"""
    home = temp_home(testcase)
    shared_root = home / ".agents/skills"
    claude_root = home / ".claude/skills"
    demo = write_skill(shared_root, "demo", body="back me up")
    (demo / "assets").mkdir(parents=True, exist_ok=True)
    (demo / "assets" / "a.bin").write_bytes(b"\x00\x01\x02")
    claude_root.mkdir(parents=True, exist_ok=True)
    os.symlink(demo, claude_root / "demo")

    shared_loc = Location("shared", "shared", str(shared_root), "user", True, ("t",))
    claude_loc = Location("claude-user", "claude-code", str(claude_root), "user", True, ("t",))
    iid1 = instance_id("shared", "demo", str(demo))
    iid2 = instance_id("claude-user", "demo", str(claude_root / "demo"))
    th = tree_hash(demo)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    plan = ChangePlan("pl-test", "remove", (iid1, iid2), (), "test backup", "d", now, now)
    inventory = {
        "locations": [shared_loc.to_dict(), claude_loc.to_dict()],
        "instances": [
            {"instance_id": iid1, "location_id": "shared", "directory_name": "demo",
             "path": str(demo), "real_path": str(demo), "tree_hash": th,
             "is_symlink": False, "mutable": True, "is_skill": True},
            {"instance_id": iid2, "location_id": "claude-user", "directory_name": "demo",
             "path": str(claude_root / "demo"), "real_path": str(demo), "tree_hash": th,
             "is_symlink": True, "mutable": True, "is_skill": True},
        ],
    }
    env = type("Env", (), {})()
    env.plan, env.inventory, env.backup_dir = plan, inventory, home / "backups"
    env.locations = [shared_loc, claude_loc]
    env.claude_root, env.shared_root, env.demo = claude_root, shared_root, demo
    env.original_hashes = {iid1: th, iid2: th}
    env.remove_targets = lambda: (shutil.rmtree(demo), os.remove(claude_root / "demo"))
    return env


def tar_member_names(path):
    with tarfile.open(path) as t:
        return t.getnames()


def make_tar_with_member(testcase, member_name, content=b"evil"):
    """构造含指定危险成员的伪备份(manifest 合法、payload 逃逸)。"""
    manifest = json.dumps({"schema": 2, "backup_id": "x", "plan_id": "p", "action": "remove",
                           "reason": "t", "created_at": "now", "entries": []}, ensure_ascii=False).encode()
    fd, path = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    testcase.addCleanup(os.unlink, path)
    info = tarfile.TarInfo(member_name)
    info.size = len(content)
    minfo = tarfile.TarInfo("manifest.json")
    minfo.size = len(manifest)
    with tarfile.open(path, "w:gz") as t:
        t.addfile(minfo, io.BytesIO(manifest))
        t.addfile(info, io.BytesIO(content))
    return Path(path)


import json  # noqa: E402


class BackupRestoreTests(unittest.TestCase):
    def test_round_trip_preserves_two_instances_and_symlink(self):
        env = two_location_skill_fixture(self)
        backup = create_backup(env.plan, env.inventory, env.backup_dir)
        names = tar_member_names(backup["path"])
        self.assertEqual(len(names), len(set(names)), "tar 成员名不得重复")
        self.assertIn("manifest.json", names)
        env.remove_targets()
        result = restore_backup(Path(backup["path"]), env.locations)
        self.assertEqual(result["restored_hashes"], env.original_hashes)
        self.assertTrue((env.shared_root / "demo" / "SKILL.md").exists())
        self.assertEqual((env.shared_root / "demo" / "assets" / "a.bin").read_bytes(), b"\x00\x01\x02")
        self.assertTrue((env.claude_root / "demo").is_symlink(), "符号链接实例必须还原为符号链接")

    def test_verify_detects_payload_corruption(self):
        env = two_location_skill_fixture(self)
        backup = create_backup(env.plan, env.inventory, env.backup_dir)
        self.assertTrue(verify_backup(Path(backup["path"]))["ok"])
        data = bytearray(Path(backup["path"]).read_bytes())
        data[len(data) // 2] ^= 0xFF  # 破坏压缩流中部(要么解压失败,要么 payload 摘要不符)
        tampered = env.backup_dir / "tampered.tar.gz"
        env.backup_dir.mkdir(parents=True, exist_ok=True)
        tampered.write_bytes(bytes(data))
        with self.assertRaises(BackupError):
            verify_backup(tampered)

    def test_malicious_member_is_rejected(self):
        archive = make_tar_with_member(self, "../../escape")
        with self.assertRaises(BackupError):
            verify_backup(archive)

    def _valid_archive_parts(self):
        """一份真实备份的 manifest 与 payload,用于在合法归档上注入单个非法成员。"""
        env = two_location_skill_fixture(self)
        backup = create_backup(env.plan, env.inventory, env.backup_dir)
        info = verify_backup(Path(backup["path"]))
        with tarfile.open(backup["path"], "r:gz") as t:
            payload = {m.name: t.extractfile(m).read()
                       for m in t if m.name != "manifest.json"}
        return info["manifest"], payload

    def _write_archive(self, manifest, payload, extra_members=(), compressed=True):
        fd, path = tempfile.mkstemp(suffix=".tar.gz" if compressed else ".tar")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        with tarfile.open(path, "w:gz" if compressed else "w") as t:
            mb = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
            minfo = tarfile.TarInfo("manifest.json")
            minfo.size = len(mb)
            t.addfile(minfo, io.BytesIO(mb))
            for arc, data in payload.items():
                ainfo = tarfile.TarInfo(arc)
                ainfo.size = len(data)
                t.addfile(ainfo, io.BytesIO(data))
            for member in extra_members:
                t.addfile(member)
        return Path(path)

    def test_tar_link_and_device_members_are_rejected(self):
        """修正(任务 1):旧夹具生成未压缩 tar,verify 因 r:gz 打不开而通过,
        并不能证明命中链接/设备拒绝分支;现在用合法 gzip+完整 manifest,
        仅注入非法成员,并保留合法对照组。"""
        manifest, payload = self._valid_archive_parts()
        ok_archive = self._write_archive(manifest, payload)
        self.assertTrue(verify_backup(ok_archive)["ok"], "合法对照组必须通过打开归档步骤")

        sinfo = tarfile.TarInfo("payload/x/link")
        sinfo.type = tarfile.SYMTYPE
        sinfo.linkname = "/etc/passwd"
        with self.assertRaises(BackupError) as ctx:
            verify_backup(self._write_archive(manifest, payload, extra_members=[sinfo]))
        self.assertIn("链接", str(ctx.exception), "必须以链接成员为由拒绝")

        dinfo = tarfile.TarInfo("payload/x/dev")
        dinfo.type = tarfile.CHRTYPE
        with self.assertRaises(BackupError) as ctx:
            verify_backup(self._write_archive(manifest, payload, extra_members=[dinfo]))
        self.assertIn("设备", str(ctx.exception), "必须以设备成员为由拒绝")

    def test_uncompressed_tar_rejected_as_archive_format(self):
        manifest, payload = self._valid_archive_parts()
        archive = self._write_archive(manifest, payload, compressed=False)
        with self.assertRaises(BackupError) as ctx:
            verify_backup(archive)
        self.assertIn("无法打开备份", str(ctx.exception))

    def test_conflict_restore_fails_without_touching_target(self):
        env = two_location_skill_fixture(self)
        backup = create_backup(env.plan, env.inventory, env.backup_dir)
        # 不删除目标 → 恢复冲突 → 必须失败且原样保留
        sentinel = env.demo / "sentinel.txt"
        sentinel.write_text("keep me", encoding="utf-8")
        with self.assertRaises(BackupError):
            restore_backup(Path(backup["path"]), env.locations)
        self.assertTrue(sentinel.exists(), "冲突恢复不得破坏现有内容")

    def test_legacy_backup_is_inspected_not_restored(self):
        fd, path = tempfile.mkstemp(suffix=".tar.gz")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        with tarfile.open(path, "w:gz") as t:
            info = tarfile.TarInfo("old-demo/SKILL.md")
            data = b"old"
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        result = inspect_legacy_backup(Path(path))
        self.assertTrue(result["legacy"])
        self.assertFalse(result["restored"])
        self.assertTrue(result["limitations"])


if __name__ == "__main__":
    unittest.main()
