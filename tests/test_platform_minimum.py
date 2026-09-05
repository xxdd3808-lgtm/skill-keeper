"""Task 2(v4):最小跨平台底座合同 —— 锁后端、原生绝对路径、import 冒烟。

锁测试用真实双进程,不 mock;平台规则断言在对应真实平台执行,其余平台断言
stdlib 真实语义(ntpath/posixpath 全平台可用),每个用例在任何平台都有真实
断言,0 skipped。
"""
import ast
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"

from scripts.core import platform as platform_tools   # noqa: E402
from scripts.core.io import FileLock                  # noqa: E402

PLATFORM_MODULE_NAMES = ("fcntl", "msvcrt")


def _all_script_files():
    return sorted(SCRIPTS_DIR.rglob("*.py"))


def _direct_module_platform_imports(tree):
    """模块体第一层(无条件执行路径)上的 fcntl/msvcrt 导入行号。

    函数体、try/except、if 分支里的导入都是合法的"延迟选择",不算。
    """
    flagged = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "").split(".")[0]]
        else:
            continue
        if any(n in PLATFORM_MODULE_NAMES for n in names):
            flagged.append(node.lineno)
    return flagged


class LockBackendContractTests(unittest.TestCase):
    def test_backend_name_matches_running_platform(self):
        name = platform_tools.lock_backend_name()
        if os.name == "posix":
            self.assertEqual(name, "fcntl", "POSIX 必须选 fcntl 后端")
        elif os.name == "nt":
            self.assertEqual(name, "msvcrt", "Windows 必须选 msvcrt 后端")
        else:
            self.assertIn(name, ("fcntl", "msvcrt", "none"))

    def _holder_script(self, td, mode):
        script = Path(td) / "holder_{}.py".format(mode)
        script.write_text(textwrap.dedent("""
            import os, sys
            sys.path.insert(0, {repo!r})
            from scripts.core.io import FileLock
            lock = FileLock({lock!r}).acquire()
            print("LOCKED", flush=True)
            if {mode!r} == "clean":
                sys.stdin.readline()  # 等父进程验证完互斥再放行
                lock.release()
                print("RELEASED", flush=True)
            else:
                os._exit(3)
        """).format(repo=str(REPO_ROOT), lock=str(Path(td) / ".change.lock"), mode=mode),
            encoding="utf-8")
        return script

    def test_double_process_mutex_and_clean_release(self):
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / ".change.lock"
            proc = subprocess.Popen([sys.executable, str(self._holder_script(td, "clean"))],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(proc.stdout.readline().strip(), "LOCKED",
                                 "持有进程必须先拿到锁")
                with self.assertRaises(BlockingIOError,
                                       msg="已持锁时第二个进程必须立刻失败"):
                    FileLock(lock_path).acquire()
                proc.stdin.write("go\n")
                proc.stdin.flush()
                self.assertEqual(proc.stdout.readline().strip(), "RELEASED")
                self.assertEqual(proc.wait(timeout=30), 0)
                lock = FileLock(lock_path).acquire()
                lock.release()
            finally:
                if proc.poll() is None:
                    proc.kill()

    def test_abnormal_exit_releases_fd_but_never_wipes_lock_file(self):
        """异常退出:OS 层锁随 fd 消失,锁文件必须原样保留(破锁恢复归事务状态管)。"""
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / ".change.lock"
            proc = subprocess.Popen([sys.executable, str(self._holder_script(td, "crash"))],
                                    stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(proc.stdout.readline().strip(), "LOCKED")
                self.assertEqual(proc.wait(timeout=30), 3)
                self.assertTrue(lock_path.is_file(), "异常退出不得清掉锁文件")
                lock = FileLock(lock_path).acquire()
                lock.release()
            finally:
                if proc.poll() is None:
                    proc.kill()

    def test_release_without_acquire_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            FileLock(Path(td) / "unused.lock").release()


class NativePathContractTests(unittest.TestCase):
    def test_scan_accepts_native_absolute_and_rejects_relative(self):
        """_extra_locations 必须按当前系统原生规则判断绝对路径。"""
        from tests.helpers import temp_home, write_skill
        from scripts.scan import build_inventory
        home = temp_home(self)
        data = home / "data"
        data.mkdir()
        legacy = write_skill(home / "custom-client" / "skills", "legacy-skill")
        (data / "client-locations.json").write_text(json.dumps({
            "locations": [{"location_id": "legacy-user", "client": "legacy-tool",
                           "path": str(legacy.parent), "kind": "user",
                           "mutable": False}]}), encoding="utf-8")
        inv = build_inventory(home, data)
        self.assertIn("legacy-user", {l["location_id"] for l in inv["locations"]})
        self.assertEqual(inv["config_issues"], [])
        self.assertTrue(any(i["directory_name"] == "legacy-skill" for i in inv["instances"]))

        (data / "client-locations.json").write_text(json.dumps({
            "locations": [{"location_id": "bad-user", "client": "legacy-tool",
                           "path": "relative/path", "kind": "user",
                           "mutable": False}]}), encoding="utf-8")
        inv = build_inventory(home, data)
        self.assertTrue(any(i.get("code") == "bad-client-locations"
                            for i in inv["config_issues"]), "相对路径必须被拒绝")


class ImportSmokeTests(unittest.TestCase):
    def test_every_scripts_module_imports(self):
        """全部 scripts 模块(含 core/clients)在当前平台必须可导入。"""
        import importlib
        for path in _all_script_files():
            rel = path.relative_to(REPO_ROOT).with_suffix("")
            module = ".".join(rel.parts)
            if module.endswith(".__init__"):
                module = module[: -len(".__init__")] or "scripts"
            try:
                importlib.import_module(module)
            except Exception as e:  # noqa: BLE001
                self.fail("导入 {} 失败: {}: {}".format(module, type(e).__name__, e))

    def test_no_unconditional_platform_imports_anywhere(self):
        """scripts/ 任何模块都不得在模块顶层无条件导入 fcntl/msvcrt。"""
        for path in _all_script_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            flagged = _direct_module_platform_imports(tree)
            self.assertEqual(
                flagged, [],
                "{} 顶层存在无条件平台导入(行 {}),Windows 导入会崩".format(path, flagged))


if __name__ == "__main__":
    unittest.main()
