"""候选暂存的所有权与清理边界(Task 2,F07 提前阻断)。

铁律:
- 暂存根是本工具的私有区域;根不能落在任何技能树/数据目录/安装位置内外链里;
- 清理只删除"本工具登记了所有权、且当前无有效引用"的 cand-/tmp- 目录;
- 不相关目录、无所有权记录的历史目录一律保留(列入 unowned 待人工处置),
  绝不因为"看起来像候选"就直接 rmtree。
"""
import os
import time
from pathlib import Path

from .io import atomic_write_json, load_json_checked

OWNERSHIP_DIR = "ownership"
CAND_PREFIX = "cand-"
TMP_PREFIX = "tmp-"
OWNERSHIP_SCHEMA = 1


class StagingBoundaryError(ValueError):
    """暂存根位置不合法(进入受保护目录、互为嵌套、符号链接根等)。"""


def _real(path):
    return os.path.realpath(os.fspath(path))


def validate_staging_root(root, protected_paths) -> Path:
    """校验暂存根位置;返回绝对化路径。不合法抛 StagingBoundaryError。"""
    root = Path(os.path.abspath(os.fspath(root)))
    if root == root.parent:
        raise StagingBoundaryError("暂存根不能是文件系统根")
    if os.path.islink(root):
        raise StagingBoundaryError("暂存根不能是符号链接: " + root.name)
    if root == Path.home().resolve():
        raise StagingBoundaryError("暂存根不能是用户 HOME 本身")
    real_root = _real(root)
    for protected in protected_paths or []:
        real_protected = _real(protected)
        if not real_protected:
            continue
        if real_root == real_protected:
            raise StagingBoundaryError("暂存根不能与受保护目录重合: " + os.path.basename(real_protected))
        if real_root.startswith(real_protected + os.sep):
            raise StagingBoundaryError("暂存根不能在技能树/数据目录内: " + os.path.basename(real_protected))
        if real_protected.startswith(real_root + os.sep):
            raise StagingBoundaryError("受保护目录在暂存根内,清理会波及技能: "
                                       + os.path.basename(real_protected))
    return root


def record_ownership(staging_root, name, meta=None):
    """给本工具创建的候选目录写所有权记录(ownership/<name>.json)。"""
    root = Path(staging_root)
    record = {"schema": OWNERSHIP_SCHEMA, "tool": "skill-keeper", "name": str(name),
              "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if meta:
        record.update({k: v for k, v in meta.items() if k != "schema"})
    atomic_write_json(root / OWNERSHIP_DIR / (str(name) + ".json"), record)
    return record


def read_ownership(staging_root) -> dict:
    """读取所有权记录;目录缺失返回 {}。损坏的单条记录跳过(视作无所有权,保留待处置)。"""
    ownership_dir = Path(staging_root) / OWNERSHIP_DIR
    if not ownership_dir.is_dir():
        return {}
    records = {}
    for path in sorted(ownership_dir.glob("*.json")):
        value, _ = load_json_checked(path, None)
        if isinstance(value, dict) and value.get("tool") == "skill-keeper" and value.get("name"):
            records[str(value["name"])] = value
    return records


def cleanup_staging(root, references) -> dict:
    """清理无有效引用且本工具登记所有的 cand-/tmp- 目录;返回 {removed, kept, unowned, errors}。

    references 是仍被引用的目录名或绝对路径集合;其余一切对象(含不相关文件、
    无所有权记录的历史候选)只保留并报告,绝不动手。
    """
    root = Path(root)
    ownership = read_ownership(root)
    refs = set()
    for r in references or []:
        refs.add(str(r))
        refs.add(Path(str(r)).name)
    removed, kept, unowned, errors = [], [], [], []
    if not root.is_dir():
        return {"removed": removed, "kept": kept, "unowned": unowned, "errors": errors}
    for child in sorted(root.iterdir()):
        name = child.name
        if name == OWNERSHIP_DIR or name in refs or str(child) in refs:
            kept.append(name)
            continue
        owned = name in ownership
        is_candidate = child.is_dir() and not child.is_symlink() and (
            name.startswith(CAND_PREFIX) or name.startswith(TMP_PREFIX))
        if owned and is_candidate:
            import shutil
            shutil.rmtree(child, ignore_errors=True)
            if child.exists():
                errors.append(name)
            else:
                removed.append(name)
        else:
            unowned.append(name)
    return {"removed": removed, "kept": kept, "unowned": unowned, "errors": errors}
