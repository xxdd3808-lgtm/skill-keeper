"""运行时路径与快照发布(Task 7,F08):CLI/API/报告共用同一套路径解析与刷新。

优先级:显式参数 > 环境变量(SKILL_KEEPER_DATA/SKILL_KEEPER_STAGING)> 兼容默认。
publish_snapshot 在同一状态下重跑 scan+report 子进程;失败保留旧快照并显式标旧。
"""
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))


class RuntimePaths:
    """一次运行的全部路径;所有入口从同一解析函数获得,不从全局 BASE 偷换某一项。"""

    def __init__(self, home=None, data_dir=None, staging_dir=None, backup_dir=None):
        self.home = Path(home) if home else Path(os.path.expanduser("~"))
        self.data_dir = Path(data_dir) if data_dir else Path(
            os.environ.get("SKILL_KEEPER_DATA") or (BASE / "data"))
        self.staging_dir = Path(staging_dir) if staging_dir else Path(
            os.environ.get("SKILL_KEEPER_STAGING") or _default_staging())
        if backup_dir:
            self.backup_dir = Path(backup_dir)
        elif self.data_dir == BASE / "data":
            self.backup_dir = BASE / "backups"  # 兼容既有真实部署布局
        else:
            self.backup_dir = self.data_dir / "backups"

    def engine_kwargs(self):
        """构造 ChangeContext 需要的全部路径(不含可注入函数)。"""
        return {"data_dir": self.data_dir, "plans_dir": self.data_dir / "change-plans",
                "backup_dir": self.backup_dir,
                "audit_path": self.data_dir / "audit-v2.jsonl",
                "lock_path": self.data_dir / ".change.lock"}

    def subprocess_env(self, home=None):
        """子进程环境:强制与当前 paths 一致,禁止子进程回到真实 HOME/默认数据目录。"""
        env = dict(os.environ)
        env["SKILL_KEEPER_DATA"] = str(self.data_dir)
        env["SKILL_KEEPER_STAGING"] = str(self.staging_dir)
        if home:
            env["HOME"] = str(home)
        return env

    def to_dict(self):
        return {"home": str(self.home), "data_dir": str(self.data_dir),
                "staging_dir": str(self.staging_dir), "backup_dir": str(self.backup_dir)}


def _default_staging():
    home = Path(os.path.expanduser("~"))
    if sys.platform == "darwin":
        return home / "Library/Caches/skill-keeper/staging"
    return home / ".cache/skill-keeper/staging"


def publish_snapshot(paths, timeout=300) -> dict:
    """重跑 scan + report,发布新快照(同一 paths,子进程不偷换目录)。

    返回 {ok, snapshot_id, status: fresh|stale, error?};snapshot_id 取
    inventory.json 的 mtime+size(同周期幂等);失败时旧快照保留并标 stale。
    """
    env = paths.subprocess_env(home=str(paths.home))
    results = []
    for script in ("scan.py", "report.py"):
        r = subprocess.run([sys.executable, str(BASE / "scripts" / script)],
                           capture_output=True, text=True, timeout=timeout, env=env)
        results.append((script, r.returncode))
    failed = [name for name, rc in results if rc not in (0, 1)]
    inv_path = paths.data_dir / "inventory.json"
    if failed or not inv_path.is_file():
        return {"ok": False, "status": "stale", "snapshot_id": _snapshot_id(inv_path),
                "error": "刷新失败: " + ",".join(failed or ["inventory-missing"])}
    return {"ok": True, "status": "fresh", "snapshot_id": _snapshot_id(inv_path)}


def _snapshot_id(inv_path):
    try:
        st = inv_path.stat()
        return "inv-{}-{}".format(int(st.st_mtime), st.st_size)
    except OSError:
        return "inv-missing"
