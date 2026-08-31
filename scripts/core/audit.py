"""统一审计:成功、失败与回滚都追加为 JSONL 事件(设计 §11)。

每条记录:action_id/action/target_ids/plan_id/reason/recommendation_id/backup_id/
expected_hash/resulting_hash/started_at/finished_at/status/error/rollback_status。
审计文件本身只追加;写入后 flush + fsync,断电也不丢事件。
"""
import json
import os
import secrets
import time
from pathlib import Path

EVENT_FIELDS = ("action_id", "action", "target_ids", "plan_id", "reason", "recommendation_id",
                "backup_id", "expected_hash", "resulting_hash", "started_at", "finished_at",
                "status", "error", "rollback_status")


def new_action_id():
    return "act-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)


def append_audit(event, audit_path):
    """追加一条审计事件;自动补 action_id 与时间戳缺省。返回完整事件。"""
    row = {k: event.get(k) for k in EVENT_FIELDS}
    if not row.get("action_id"):
        row["action_id"] = new_action_id()
    if not row.get("finished_at"):
        row["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path = Path(audit_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return row


def read_audit(audit_path):
    """读取全部审计事件(损坏行跳过并返回 issue 计数放在最后的 _issues 键)。"""
    path = Path(audit_path)
    if not path.is_file():
        return []
    rows, bad = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            bad += 1
    if bad:
        rows.append({"action_id": "-", "status": "corrupt-lines", "error": str(bad)})
    return rows
