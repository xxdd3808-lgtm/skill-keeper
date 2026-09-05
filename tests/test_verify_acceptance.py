"""Task 9:verify.py 自身必须可信——故意失败/仅跳过/空目录都返回非 0。"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY = REPO_ROOT / "scripts" / "verify.py"


class VerifySelfValidationTests(unittest.TestCase):
    def _run(self, test_dir):
        return subprocess.run([sys.executable, str(VERIFY), "--test-dir", str(test_dir)],
                              capture_output=True, text=True, timeout=120)

    def test_deliberate_failure_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "test_bad.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_fails(self):\n"
                "        self.fail('must be seen')\n", encoding="utf-8")
            self.assertNotEqual(self._run(td).returncode, 0)

    def test_skip_only_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "test_skip.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    @unittest.skip('s')\n"
                "    def test_skipped(self):\n"
                "        pass\n", encoding="utf-8")
            self.assertNotEqual(self._run(td).returncode, 0)

    def test_empty_dir_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertNotEqual(self._run(td).returncode, 0)

    def test_passing_smoke_dir_returns_zero(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "test_ok.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n", encoding="utf-8")
            self.assertEqual(self._run(td).returncode, 0)


if __name__ == "__main__":
    unittest.main()
