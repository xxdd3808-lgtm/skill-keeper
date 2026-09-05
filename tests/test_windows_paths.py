"""Task 2(v4):本机路径 vs 归档路径合同。

- 本机绝对路径判断用运行系统的原生规则(os.path.isabs);Windows 语义(drive/UNC)
  在真实 Windows runner 上断言,POSIX runner 上断言 posixpath 规则,并用 stdlib
  自带的 ntpath 验证 Windows 语义本身(真实代码,不是 mock);
- 归档成员路径校验(validate_archive_member_path)是纯字符串合同,POSIX `/`、
  拒绝反斜杠/绝对路径/`.`/`..`,在所有平台完整执行;
- 备份/恢复/事务模块绝不允许调用本机路径辅助函数(跨平台不得放松归档边界)。
"""
import ast
import os
import ntpath  # noqa: F401  (全平台可用;跨平台语义断言用)
import posixpath  # noqa: F401
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

from scripts.core.paths import (PathScopeError, validate_relative_path,  # noqa: E402
                                validate_archive_member_path)
from scripts.core.platform import is_absolute_path                       # noqa: E402


class NativeAbsolutePathTests(unittest.TestCase):
    def test_running_platform_rules(self):
        if os.name == "nt":
            self.assertTrue(is_absolute_path("C:\\skills"))
            self.assertTrue(is_absolute_path("C:/skills"))
            self.assertTrue(is_absolute_path("\\\\server\\share\\skills"), "UNC 必须接受")
            self.assertFalse(is_absolute_path("relative\\skills"))
        else:
            self.assertTrue(is_absolute_path("/home/user/.agents/skills"))
            self.assertFalse(is_absolute_path("relative/skills"))
            self.assertFalse(is_absolute_path("C:/skills"), "POSIX 上 drive 路径不是绝对路径")

    def test_cross_platform_semantics_via_real_stdlib(self):
        if os.name == "nt":
            self.assertTrue(posixpath.isabs("/home/user"))
            self.assertFalse(posixpath.isabs("C:/skills"))
        else:
            self.assertTrue(ntpath.isabs("C:/skills"))
            self.assertTrue(ntpath.isabs("\\\\server\\share\\skills"))
            self.assertFalse(ntpath.isabs("relative/skills"))

    def test_is_absolute_path_agrees_with_running_os_path(self):
        samples = ["plain", "with/slash", "", ".", ".."]
        if os.name == "nt":
            samples += ["C:\\x", "C:/x", "\\\\srv\\share", "\\x"]
        else:
            samples += ["/x", "/a/b", "C:\\x"]
        for sample in samples:
            self.assertEqual(is_absolute_path(sample), os.path.isabs(sample),
                             "必须与运行系统 os.path.isabs 一致: {!r}".format(sample))


class ArchiveMemberPathTests(unittest.TestCase):
    """归档成员路径是纯字符串合同;validate_archive_member_path 与
    validate_relative_path 同一实现,备份/恢复继续走它。"""

    def test_alias_same_validation(self):
        self.assertIs(validate_archive_member_path, validate_relative_path)

    def test_rejects_absolute_drive_unc_backslash_dotdot(self):
        for bad in ("/etc/passwd", "C:\\windows\\system32", "C:/windows",
                    "\\\\server\\share\\x", "a\\b", "..", "../x", "a/../b",
                    "./a", "a/./b", "a/", "", ".", "a\x01b", None, 123):
            with self.assertRaises(PathScopeError, msg=repr(bad)):
                validate_archive_member_path(bad)

    def test_accepts_clean_relative_posix_paths(self):
        for good in ("skills/SKILL.md", "a/b/c.txt", "single"):
            self.assertEqual(validate_archive_member_path(good),
                             tuple(good.split("/")))

    def test_confined_destination_rejects_symlink_escape(self):
        from scripts.core.paths import confined_destination
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            root.mkdir()
            outside = Path(td) / "outside"
            outside.mkdir()
            dest = confined_destination(root, "normal/file.txt")
            self.assertEqual(dest, root / "normal" / "file.txt")
            try:
                (root / "link").symlink_to(outside)
            except OSError:
                # 无权创建符号链接的平台(如 Windows 严格模式):验证中间父目录
                # 不存在时仍按白名单落地,不抛链接越界;链接逃逸由真实平台 CI 覆盖
                dest2 = confined_destination(root, "fresh/child/file.txt")
                self.assertEqual(dest2, root / "fresh" / "child" / "file.txt")
                return
            with self.assertRaises(PathScopeError):
                confined_destination(root, "link/file")


class BackupNeverUsesNativePathHelpersTests(unittest.TestCase):
    def test_backup_restore_transactions_do_not_import_platform_helpers(self):
        """跨平台辅助(is_absolute_path)只能服务本机位置登记;归档边界不得借道。"""
        banned = ("backup.py", "transactions.py", "changes.py", "staging.py")
        for name in banned:
            source = (REPO_ROOT / "scripts" / "core" / name).read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "scripts.core.platform",
                                        "{} 不得导入 platform 辅助".format(name))
                    self.assertNotEqual(node.module, ".platform",
                                        "{} 不得导入 platform 辅助".format(name))
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(alias.name.startswith("scripts.core.platform"),
                                         "{} 不得导入 platform 辅助".format(name))
                if isinstance(node, ast.Name):
                    self.assertNotEqual(node.id, "is_absolute_path",
                                        "{} 不得调用本机路径判断".format(name))
                if isinstance(node, ast.Attribute):
                    self.assertNotEqual(node.attr, "is_absolute_path",
                                        "{} 不得调用本机路径判断".format(name))


if __name__ == "__main__":
    unittest.main()
