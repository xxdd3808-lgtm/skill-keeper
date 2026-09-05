"""持久事务状态(Task 3,F03):删除/更新/恢复的阶段记录与断点恢复依据。

铁律:
- 不可变计划不改写;事务状态另存 data/transactions/<plan_id>.json;
- phase ∈ prepared / mutating / committed / rolling-back / rolled-back / recovery-required;
- 每次关键 rename 前后都落盘(fsync),崩溃后凭"保管路径 + 实体哈希"判定,不猜目录名;
- 状态文件的读改写全程持变更互斥锁(由调用方保证)。
"""
import os
import time
from pathlib import Path

from .io import atomic_write_json

SCHEMA_VERSION = 1
PHASES = ("prepared", "mutating", "committed", "rolling-back",
          "rolled-back", "recovery-required")
HOLDING_PREFIX = ".sk-txn-"


class TransactionError(Exception):
    """事务状态缺失/损坏;相关写操作必须拒绝,不能当作无事务继续。"""


def transactions_dir(context) -> Path:
    return Path(context.data_dir) / "transactions"


def state_path(plan_id, context) -> Path:
    return transactions_dir(context) / (str(plan_id) + ".json")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def fsync_dir(path):
    """按平台能力同步父目录;不支持时跳过(rename 本身已原子)。"""
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def new_state(plan_row, action, targets, backup_id=None,
              candidate_hash=None, candidate_holding=None) -> dict:
    """创建 prepared 状态;targets 行结构由引擎决定(见 changes.py)。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": str(plan_row.get("plan_id")),
        "action": action,
        "phase": "prepared",
        "created_at": _now(),
        "updated_at": _now(),
        "backup_id": backup_id,
        "reason": str(plan_row.get("reason") or plan_row.get("summary") or ""),
        "recommendation_id": str(plan_row.get("recommendation_id") or ""),
        "candidate_hash": candidate_hash,
        "candidate_holding": candidate_holding,
        "targets": targets,
        "result": None,
        "cleanup_pending": [],
        "audit_pending": False,
    }


def write_transaction(context, state) -> None:
    """原子落盘 + 同步目录;调用方持有变更互斥锁。"""
    state = dict(state)
    state["updated_at"] = _now()
    if state.get("phase") not in PHASES:
        raise TransactionError("未知事务阶段: " + str(state.get("phase")))
    path = state_path(state["plan_id"], context)
    atomic_write_json(path, state)
    fsync_dir(path.parent)


def read_transaction(plan_id, context):
    """读取事务状态;不存在返回 None,损坏抛 TransactionError(调用方必须拒绝写操作)。"""
    path = state_path(plan_id, context)
    if not path.is_file():
        return None
    try:
        import json
        state = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise TransactionError("事务状态文件损坏({}): {}".format(type(e).__name__, path.name))
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION \
            or state.get("phase") not in PHASES:
        raise TransactionError("事务状态结构不受支持: " + path.name)
    return state


def holding_path(parent, plan_id, tag):
    """实体保管路径:与目标同目录(同文件系统,rename 原子);名字绑定计划 ID。"""
    return str(Path(parent) / (HOLDING_PREFIX + str(plan_id)[-8:] + "-" + tag))
