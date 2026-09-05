"""运行时路径与快照发布(Task 7,F08):CLI/API/报告共用同一套路径解析与刷新。

优先级:显式参数 > 环境变量(SKILL_KEEPER_DATA/SKILL_KEEPER_STAGING)> 兼容默认。
publish_snapshot 在同一状态下重跑 scan+report 子进程;失败保留旧快照并显式标旧。
"""
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))

# 仓库 data/ 里代表"真实 v2/v3 运行态"的标记:只有出现这些才启用旧仓库布局,
# 新 clone/新安装的空 data/ 目录一律走新统一默认,防止误认。
REPO_MARKER_FILES = ("inventory.json", "inventory-last.json", "audit-v2.jsonl",
                     "updates.json", "value-reviews.json")
REPO_MARKER_DIRS = ("change-plans", "transactions")


def detect_repo_layout(base=None) -> str:
    """base/data 是否为真实 v2/v3 运行态:old-repo(有标记)/ new(无)。"""
    data = Path(base) / "data" if base is not None else BASE / "data"
    if not data.is_dir():
        return "new"
    if any((data / name).is_file() for name in REPO_MARKER_FILES):
        return "old-repo"
    if any((data / name).is_dir() for name in REPO_MARKER_DIRS):
        return "old-repo"
    return "new"


def _default_staging(home=None):
    home = Path(home) if home is not None else Path(os.path.expanduser("~"))
    if sys.platform == "darwin":
        return home / "Library/Caches/skill-keeper/staging"
    return home / ".cache/skill-keeper/staging"


def default_layout_dirs(base=None, home=None) -> dict:
    """无显式参数、无环境变量时的默认路径。

    优先级:可识别旧仓库运行态(仓库 data 有 v2/v3 标记 → 沿用仓库布局与平台缓存)
    > 新统一默认 ~/.skill-keeper/{data,cache/staging,backups}。
    """
    base = Path(base) if base is not None else BASE
    home = Path(home) if home is not None else Path(os.path.expanduser("~"))
    if detect_repo_layout(base) == "old-repo":
        return {"layout": "old-repo", "data_dir": base / "data",
                "staging_dir": _default_staging(home), "backup_dir": base / "backups"}
    root = home / ".skill-keeper"
    return {"layout": "new", "data_dir": root / "data",
            "staging_dir": root / "cache" / "staging", "backup_dir": root / "backups"}


def default_data_dir() -> Path:
    """入口脚本共用的数据目录解析:环境变量 > 布局默认(旧仓库 / 新统一 ~/.skill-keeper)。"""
    if os.environ.get("SKILL_KEEPER_DATA"):
        return Path(os.environ["SKILL_KEEPER_DATA"])
    return default_layout_dirs()["data_dir"]


class RuntimePaths:
    """一次运行的全部路径;所有入口从同一解析函数获得,不从全局 BASE 偷换某一项。

    解析优先级:显式参数 > 环境变量(SKILL_KEEPER_DATA/SKILL_KEEPER_STAGING)
    > 可识别旧仓库运行态 > 新统一默认 ~/.skill-keeper。
    """

    def __init__(self, home=None, data_dir=None, staging_dir=None, backup_dir=None):
        self.home = Path(home) if home else Path(os.path.expanduser("~"))
        defaults = None
        if data_dir:
            self.data_dir = Path(data_dir)
            self.layout = "explicit"
        elif os.environ.get("SKILL_KEEPER_DATA"):
            self.data_dir = Path(os.environ["SKILL_KEEPER_DATA"])
            self.layout = "env"
        else:
            defaults = default_layout_dirs(BASE, self.home)
            self.layout = defaults["layout"]
            self.data_dir = defaults["data_dir"]

        if backup_dir:
            self.backup_dir = Path(backup_dir)
        elif self.layout == "new":
            self.backup_dir = defaults["backup_dir"]
        elif self.data_dir == BASE / "data":
            self.backup_dir = BASE / "backups"  # 兼容既有真实部署布局
        else:
            self.backup_dir = self.data_dir / "backups"

        if staging_dir:
            self.staging_dir = Path(staging_dir)
        elif os.environ.get("SKILL_KEEPER_STAGING"):
            self.staging_dir = Path(os.environ["SKILL_KEEPER_STAGING"])
        elif defaults is not None:
            self.staging_dir = defaults["staging_dir"]
        else:
            self.staging_dir = _default_staging(self.home)


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


def plan_runtime_migration(old_paths, new_paths) -> dict:
    """迁移预演(F10):只列文件清单、哈希与冲突,绝不复制/删除/移动真实数据。

    用户真实迁移仍须走新计划与确认;预演只是给决策材料。
    """
    import hashlib
    old_paths, new_paths = Path(old_paths), Path(new_paths)
    files = []
    migratable = old_paths.is_dir()
    if migratable:
        for path in sorted(old_paths.rglob("*")):
            if not path.is_file() or path.name.startswith(".tmp-"):
                continue
            rel = str(path.relative_to(old_paths))
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            target = new_paths / rel
            conflict = False
            if target.is_file():
                th = hashlib.sha256()
                with open(target, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        th.update(chunk)
                conflict = th.hexdigest() != h.hexdigest()
            if conflict:
                migratable = False
            files.append({"relative": rel, "bytes": path.stat().st_size,
                          "sha256": h.hexdigest(), "conflict": conflict})
    return {"schema_version": 1, "old": str(old_paths), "new": str(new_paths),
            "migratable": migratable, "files": files,
            "note": "预演输出;真实迁移需另行计划与确认,本函数不移动任何文件"}


def _snapshot_id(inv_path):
    try:
        st = inv_path.stat()
        return "inv-{}-{}".format(int(st.st_mtime), st.st_size)
    except OSError:
        return "inv-missing"
