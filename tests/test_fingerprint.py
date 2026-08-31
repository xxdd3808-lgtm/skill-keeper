import os, tempfile, unittest
from pathlib import Path
from scripts.core.fingerprint import instance_id, tree_hash, tree_manifest

class FingerprintTests(unittest.TestCase):
    def test_auxiliary_content_changes_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "SKILL.md").write_text("---\nname: x\ndescription: x\n---\n", encoding="utf-8")
            (root / "run.py").write_text("safe", encoding="utf-8")
            before = tree_hash(root)
            (root / "run.py").write_text("changed", encoding="utf-8")
            self.assertNotEqual(before, tree_hash(root))

    def test_symlink_is_hashed_without_following_target(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "skill"; outside = Path(td) / "outside"
            root.mkdir(); outside.write_text("secret", encoding="utf-8")
            os.symlink(outside, root / "link")
            rows = tree_manifest(root)
            self.assertEqual(rows[0]["type"], "symlink")
            self.assertNotIn("secret", str(rows))

    def test_root_must_be_directory(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "plain.txt"
            p.write_text("x", encoding="utf-8")
            with self.assertRaises(NotADirectoryError):
                tree_manifest(p)

    def test_permission_and_path_changes_alter_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a").mkdir(); (root / "a" / "SKILL.md").write_text("v1", encoding="utf-8")
            base = tree_hash(root)
            os.chmod(root / "a" / "SKILL.md", 0o600)
            self.assertNotEqual(base, tree_hash(root), "权限变化必须改变摘要")
            (root / "a" / "SKILL.md").rename(root / "a" / "renamed.md")
            self.assertNotEqual(base, tree_hash(root), "相对路径变化必须改变摘要")

    def test_runtime_junk_is_excluded_but_list_is_stable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "SKILL.md").write_text("same", encoding="utf-8")
            (root / ".DS_Store").write_text("junk", encoding="utf-8")
            cache = root / "__pycache__"; cache.mkdir(); (cache / "x.pyc").write_bytes(b"junk")
            with_junk = tree_hash(root)
            (root / ".DS_Store").write_text("different junk", encoding="utf-8")
            (cache / "x.pyc").write_bytes(b"other")
            self.assertEqual(with_junk, tree_hash(root), "运行时垃圾文件不应影响指纹")

    def test_instance_id_is_stable_and_input_sensitive(self):
        a = instance_id("shared", "demo", "/tmp/home/.agents/skills/demo")
        b = instance_id("shared", "demo", "/tmp/home/.agents/skills/demo")
        c = instance_id("claude-user", "demo", "/tmp/home/.agents/skills/demo")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(len(a), 20)


if __name__ == "__main__":
    unittest.main()
