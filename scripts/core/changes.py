"""变更引擎:不可变 ChangePlan → 确认(digest)→ 互斥锁 → 备份 → 执行 → 验证 → 审计。

铁律(设计 §9):
- 操作目标只能是当前 inventory 的稳定 instance ID;任意目录名/路径一律拒绝;
- 计划落盘后不可变(只读文件 + digest 校验),默认 30 分钟过期;
- apply 需要 confirm is True + digest 完全一致 + 未过期 + 目标指纹未变;
- 全程持有 data/.change.lock 互斥锁,第二个并发申请安全失败;
- 删除前强制创建并验证备份;验证失败立即从备份恢复;成功失败都写审计;
- 不调用 rm -rf,不修改插件缓存(不可变位置的计划直接拒绝)。
"""
import hashlib
import hmac
import json
import os
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .audit import append_audit
from .backup import create_backup, restore_backup, verify_backup
from .io import FileLock, atomic_write_json
from .models import ChangePlan

PLAN_TTL_SECONDS = 30 * 60


class ChangeError(Exception):
    """变更流程被拒绝或失败;消息面向普通人。"""


@dataclass
class ChangeContext:
    """一次变更的环境:目录、锁、审计与 inventory 提供者(全部可注入,测试友好)。"""
    data_dir: Path
    plans_dir: Path
    backup_dir: Path
    audit_path: Path
    lock_path: Path
    load_inventory: Optional[Callable] = None
    verify_after_apply: Optional[Callable] = None


def plan_digest(row) -> str:
    """计划摘要 = 规范化 JSON(不含 digest 字段本身)的 SHA-256。"""
    body = {k: v for k, v in row.items() if k != "digest"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def write_plan(plan: ChangePlan, plans_dir) -> Path:
    """计划原子落盘并设为只读(不可变)。"""
    plans_dir = Path(plans_dir)
    plans_dir.mkdir(parents=True, exist_ok=True)
    path = plans_dir / (plan.plan_id + ".json")
    atomic_write_json(path, plan.to_dict())
    os.chmod(path, 0o444)
    return path


def _validate_instance_id(raw) -> str:
    """instance ID 必须是引擎生成的稳定 ID:纯十六进制 20 位。其他形态一律拒绝。"""
    text = str(raw or "").strip()
    if not text or len(text) != 20 or any(c not in "0123456789abcdef" for c in text):
        raise ChangeError(
            "目标必须是 inventory 里的稳定 instance ID(20 位十六进制),"
            "不接受目录名或路径: {!r}".format(text[:40]))
    return text


def create_remove_plan(instance_ids, inventory, reason, plans_dir) -> ChangePlan:
    """为可变实例生成不可变删除计划;目标不存在/不可变/路径越界都直接拒绝。"""
    if not isinstance(reason, str) or not reason.strip():
        raise ChangeError("必须给出删除理由(写给自己和审计看的)")
    by_id = {i.get("instance_id"): i for i in inventory.get("instances", [])}
    loc_by_id = {l.get("location_id"): l for l in inventory.get("locations", [])}
    targets = []
    for raw in instance_ids or []:
        iid = _validate_instance_id(raw)
        inst = by_id.get(iid)
        if not inst:
            raise ChangeError("instance_id 不在当前 inventory 中(先重跑 scan.py): " + iid)
        if not inst.get("mutable"):
            raise ChangeError("实例不可变(客户端自带/插件缓存),拒绝删除: " + iid)
        loc = loc_by_id.get(inst.get("location_id"))
        if not loc or not loc.get("mutable"):
            raise ChangeError("实例所属位置不可变,拒绝删除: " + iid)
        root = os.path.realpath(loc["path"])
        parent = os.path.realpath(os.path.dirname(inst["path"]))
        if parent != root and not parent.startswith(root + os.sep):
            raise ChangeError("实例路径越出位置根目录,拒绝生成计划: " + iid)
        if not inst.get("tree_hash"):
            raise ChangeError("实例缺少完整内容指纹,先重跑 scan.py: " + iid)
        targets.append(inst)

    if not targets:
        raise ChangeError("计划没有目标")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    expires = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + PLAN_TTL_SECONDS))
    plan_id = "plan-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
    preconditions = []
    for inst in targets:
        preconditions.append(("tree_hash:" + inst["instance_id"], inst["tree_hash"]))
        preconditions.append(("path:" + inst["instance_id"], os.path.abspath(inst["path"])))
    names = ", ".join(str(i.get("logical_name") or i.get("directory_name")) for i in targets)
    plan = ChangePlan(
        plan_id=plan_id, action="remove",
        target_ids=tuple(str(i["instance_id"]) for i in targets),
        preconditions=tuple(preconditions),
        summary="删除 {} 个 skill 实例({});先备份,失败自动回滚".format(len(targets), names),
        digest="", created_at=now, expires_at=expires)
    row = plan.to_dict()
    row["digest"] = plan_digest(row)
    plan = ChangePlan.from_dict(row)
    write_plan(plan, plans_dir)
    return plan


def _load_plan(plan_id, context) -> dict:
    path = Path(context.plans_dir) / (str(plan_id) + ".json")
    if not path.is_file():
        raise ChangeError("计划不存在: " + str(plan_id))
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise ChangeError("计划文件损坏")
    if row.get("digest") != plan_digest(row):
        raise ChangeError("计划文件被改动过,digest 不符,拒绝执行")
    return row


def _check_preconditions(row, inventory):
    from .fingerprint import tree_hash
    by_id = {i.get("instance_id"): i for i in inventory.get("instances", [])}
    pre = dict(row.get("preconditions", []))
    for iid in row.get("target_ids", []):
        inst = by_id.get(iid)
        if not inst:
            raise ChangeError("目标实例已不在当前 inventory(先重跑 scan.py 再重新计划): " + str(iid))
        if pre.get("path:" + iid) and os.path.abspath(pre["path:" + iid]) != os.path.abspath(inst["path"]):
            raise ChangeError("目标路径与计划不一致: " + str(iid))
        expected = pre.get("tree_hash:" + iid)
        # TOCTOU 防护:重新计算磁盘上的真实指纹,而不是信任计划生成时的快照
        content_root = (os.path.realpath(inst["path"]) if inst.get("is_symlink")
                        else inst["path"])
        try:
            actual = tree_hash(content_root)
        except (NotADirectoryError, OSError):
            raise ChangeError("目标内容已消失或不可读,拒绝执行,请重新生成计划: " + str(iid))
        if not expected or expected != actual:
            raise ChangeError("目标内容在计划生成后发生过变化,拒绝执行,请重新生成计划: " + str(iid))


def _remove_entity(path):
    """精确删除单个实体;符号链接只删链接本身。绝不使用 rm -rf 子进程。"""
    p = Path(path)
    if p.is_symlink() or p.is_file():
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)
    else:
        raise ChangeError("目标不存在或类型未知,停止执行: " + str(path))


def apply_plan(plan_id, digest, confirm, context) -> dict:
    """执行不可变计划:锁 → 前置校验 → 备份 → 执行 → 验证(失败自动回滚)→ 审计。"""
    if confirm is not True:
        raise ChangeError("缺少明确确认(confirm 必须是布尔 true)")
    row = _load_plan(plan_id, context)
    if not isinstance(digest, str) or not hmac.compare_digest(digest, str(row.get("digest"))):
        raise ChangeError("digest 不一致:必须使用生成计划时输出的摘要,逐字匹配")
    if row.get("expires_at", "") < time.strftime("%Y-%m-%d %H:%M:%S"):
        raise ChangeError("计划已过期(30 分钟有效),请重新生成")
    if row.get("action") not in ("remove", "update", "restore"):
        raise ChangeError("未知动作: " + str(row.get("action")))

    started = time.strftime("%Y-%m-%d %H:%M:%S")
    action_id_prefix = str(row.get("plan_id"))
    if context.load_inventory is None:
        raise ChangeError("变更环境缺少 inventory 提供者")
    try:
        inventory = context.load_inventory()
    except Exception as e:
        raise ChangeError("读取 inventory 失败: " + type(e).__name__)

    lock = FileLock(context.lock_path)
    try:
        lock.acquire()
    except (BlockingIOError, OSError):
        raise ChangeError("另一个 skill-keeper 变更正在进行,请稍后再试")
    audit_event = {
        "action": str(row.get("action")), "target_ids": list(row.get("target_ids", [])),
        "plan_id": str(row.get("plan_id")), "reason": str(row.get("summary", "")),
        "started_at": started, "status": "failed", "error": None, "rollback_status": None,
        "backup_id": None,
    }
    targets = []
    try:
        _check_preconditions(row, inventory)
        by_id = {i.get("instance_id"): i for i in inventory.get("instances", [])}
        targets = [by_id[i] for i in row.get("target_ids", [])]
        plan = ChangePlan.from_dict(row)
        backup = create_backup(plan, inventory, context.backup_dir)
        if not verify_backup(backup["path"]).get("ok"):
            raise ChangeError("备份自检失败,已中止(未删除任何内容)")
        audit_event["backup_id"] = backup["backup_id"]
        for inst in targets:
            _remove_entity(inst["path"])
        if context.verify_after_apply is not None:
            ok = context.verify_after_apply()
        else:
            ok = all(not os.path.lexists(i["path"]) for i in targets)
        if not ok:
            raise ChangeError("删除后验证失败,自动回滚")
        audit_event["status"] = "success"
        audit_event["resulting_hash"] = backup["backup_id"]
        append_audit(audit_event, context.audit_path)
        return {"ok": True, "action": audit_event["action"], "backup_id": backup["backup_id"],
                "backup_path": backup["path"], "removed": [i["path"] for i in targets],
                "plan_id": row.get("plan_id")}
    except ChangeError as e:
        audit_event["error"] = str(e)
        rollback_status = None
        if audit_event["backup_id"]:
            try:
                restore_backup(audit_event["backup_id"] and _find_backup(
                    context.backup_dir, audit_event["backup_id"]),
                    inventory.get("locations", []))
                rollback_status = "restored"
            except Exception as re:
                rollback_status = "failed: " + type(re).__name__
        audit_event["rollback_status"] = rollback_status
        append_audit(audit_event, context.audit_path)
        raise
    except Exception as e:
        audit_event["error"] = type(e).__name__ + ": " + str(e)[:200]
        audit_event["rollback_status"] = "failed: " + type(e).__name__
        append_audit(audit_event, context.audit_path)
        raise ChangeError("变更失败已中止: " + type(e).__name__)
    finally:
        lock.release()


def _find_backup(backup_dir, backup_id):
    path = Path(backup_dir) / ("backup-" + str(backup_id) + ".tar.gz")
    if not path.is_file():
        raise ChangeError("找不到备份归档: " + str(backup_id))
    return path
