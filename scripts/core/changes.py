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
from .fingerprint import tree_hash
from .io import FileLock, atomic_write_json
from .models import ChangePlan

PLAN_TTL_SECONDS = 30 * 60
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))


class ChangeError(Exception):
    """变更流程被拒绝或失败;消息面向普通人。"""


class LockBusy(ChangeError):
    """互斥锁被占用:另一个变更正在进行。"""


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


def create_update_plan(instance_id, candidate_snapshot, inventory, plans_dir) -> ChangePlan:
    """生成固定候选更新计划:precondition 同时绑定本地 hash、来源、commit、候选 hash 与 staging 路径。"""
    iid = _validate_instance_id(instance_id)
    by_id = {i.get("instance_id"): i for i in inventory.get("instances", [])}
    inst = by_id.get(iid)
    if not inst:
        raise ChangeError("instance_id 不在当前 inventory 中(先重跑 scan.py): " + iid)
    if not inst.get("mutable"):
        raise ChangeError("实例不可变(客户端自带/插件缓存),拒绝更新: " + iid)
    snap = candidate_snapshot or {}
    if str(snap.get("instance_id")) != iid:
        raise ChangeError("候选快照与目标实例不一致")
    staging = Path(str(snap.get("staging_path") or ""))
    if not staging.is_dir():
        raise ChangeError("候选 staging 目录不存在: " + staging.name)
    candidate_hash = str(snap.get("candidate_hash") or "")
    try:
        staged_hash = tree_hash(staging)
    except (NotADirectoryError, OSError):
        raise ChangeError("候选 staging 不可读")
    if staged_hash != candidate_hash:
        raise ChangeError("候选 staging 内容与声称的候选 hash 不一致,拒绝生成计划")
    try:
        local_hash = tree_hash(os.path.realpath(inst["path"]) if inst.get("is_symlink")
                               else inst["path"])
    except (NotADirectoryError, OSError):
        raise ChangeError("本地内容缺失,请先重跑 scan.py")
    repo = str(snap.get("repo") or "")
    if not repo:
        raise ChangeError("候选快照缺少来源仓库")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    expires = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + PLAN_TTL_SECONDS))
    plan_id = "plan-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
    preconditions = [
        ("tree_hash:" + iid, local_hash),
        ("path:" + iid, os.path.abspath(inst["path"])),
        ("candidate_hash", candidate_hash),
        ("staging_path", os.path.abspath(staging)),
        ("repo", repo),
        ("commit_sha", str(snap.get("commit_sha") or "fixed-candidate")),
    ]
    plan = ChangePlan(
        plan_id=plan_id, action="update", target_ids=(iid,),
        preconditions=tuple(preconditions),
        summary="更新 {} 到固定候选 {}(来源 {}@{});旧版本自动保留回滚,应用前必须安检".format(
            str(inst.get("logical_name") or inst.get("directory_name")),
            candidate_hash[:12], repo, str(snap.get("commit_sha") or "fixed-candidate")),
        digest="", created_at=now, expires_at=expires)
    row = plan.to_dict()
    row["digest"] = plan_digest(row)
    plan = ChangePlan.from_dict(row)
    write_plan(plan, plans_dir)
    return plan


def create_restore_plan(backup_id, backup_dir, plans_dir) -> ChangePlan:
    """生成恢复计划:先校验备份可用,precondition 绑定 backup_id 与归档绝对路径;冲突不覆盖。"""
    from .backup import BackupError, verify_backup
    path = _find_backup(backup_dir, backup_id)
    try:
        info = verify_backup(path)
    except BackupError as e:
        raise ChangeError("备份校验失败,拒绝生成恢复计划: " + str(e))
    manifest = info.get("manifest") or {}
    iids = sorted(str(e.get("instance_id")) for e in manifest.get("entries", []))
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    expires = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + PLAN_TTL_SECONDS))
    plan_id = "plan-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
    plan = ChangePlan(
        plan_id=plan_id, action="restore", target_ids=tuple(iids),
        preconditions=(("backup_id", str(backup_id)),
                       ("backup_path", os.path.abspath(str(path)))),
        summary="从备份 {} 恢复 {} 个实体(目标已存在则冲突失败,不覆盖)".format(
            str(backup_id), len(iids)),
        digest="", created_at=now, expires_at=expires)
    row = plan.to_dict()
    row["digest"] = plan_digest(row)
    plan = ChangePlan.from_dict(row)
    write_plan(plan, plans_dir)
    return plan


VET_VERDICTS = ("safe", "warning")


def vet_path(plan_id, plans_dir) -> Path:
    return Path(plans_dir) / (str(plan_id) + ".vet.json")


def record_candidate_vet(plan_id, candidate_hash, verdict, evidence, plans_dir=None) -> dict:
    """记录候选安检结论;只接受与计划绑定的当前候选 hash;danger 判危候选直接废弃,不进应用流程。"""
    if plans_dir is None:
        plans_dir = Path(os.environ.get("SKILL_KEEPER_DATA") or
                         os.path.join(BASE_DIR, "data")) / "change-plans"
    plan_file = Path(plans_dir) / (str(plan_id) + ".json")
    if not plan_file.is_file():
        raise ChangeError("计划不存在: " + str(plan_id))
    try:
        row = json.loads(plan_file.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise ChangeError("计划文件损坏")
    pre = dict(row.get("preconditions", []))
    if str(candidate_hash) != str(pre.get("candidate_hash") or ""):
        raise ChangeError("安检对象必须是计划绑定的当前候选 hash")
    if verdict not in VET_VERDICTS:
        raise ChangeError("verdict 必须是 safe|warning;判危(danger)候选直接废弃,重新生成候选后再安检")
    record = {"plan_id": str(plan_id), "candidate_hash": str(candidate_hash),
              "verdict": verdict, "evidence": [str(e) for e in (evidence or [])],
              "vetted_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    atomic_write_json(vet_path(plan_id, plans_dir), record)
    return record


def _load_vet(plan_id, context, candidate_hash):
    path = vet_path(plan_id, context.plans_dir)
    if not path.is_file():
        raise ChangeError("候选尚未安检:先完成安全复核并 record_candidate_vet,再应用")
    try:
        vet = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise ChangeError("安检记录损坏")
    if str(vet.get("candidate_hash")) != str(candidate_hash):
        raise ChangeError("安检记录与候选不匹配,重新安检")
    return vet


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


def apply_plan(plan_id, digest, confirm, context, accept_warning=False) -> dict:
    """执行不可变计划:锁 → 前置校验 → 备份 → 执行 → 验证(失败自动回滚)→ 审计。

    update 动作要求候选已安检(verdict=safe;warning 需要第二次明确确认)。
    """
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
        raise LockBusy("另一个 skill-keeper 变更正在进行,请稍后再试")
    audit_event = {
        "action": str(row.get("action")), "target_ids": list(row.get("target_ids", [])),
        "plan_id": str(row.get("plan_id")), "reason": str(row.get("summary", "")),
        "started_at": started, "status": "failed", "error": None, "rollback_status": None,
        "backup_id": None,
    }
    try:
        if row.get("action") == "restore":
            # 恢复的目标通常不存在,不做存在性前置校验;备份本身就是 precondition
            plan = ChangePlan.from_dict(row)
            _execute_restore(row, inventory, context)
            audit_event["status"] = "success"
            audit_event["resulting_hash"] = str(dict(row.get("preconditions", [])).get("backup_id"))
            append_audit(audit_event, context.audit_path)
            return {"ok": True, "action": "restore", "backup_id": audit_event["resulting_hash"],
                    "plan_id": row.get("plan_id")}
        _check_preconditions(row, inventory)
        by_id = {i.get("instance_id"): i for i in inventory.get("instances", [])}
        targets = [by_id[i] for i in row.get("target_ids", [])]
        candidate_hash = None
        if row.get("action") == "update":
            pre = dict(row.get("preconditions", []))
            candidate_hash = str(pre.get("candidate_hash") or "")
            staging = Path(str(pre.get("staging_path") or ""))
            if not staging.is_dir():
                raise ChangeError("候选 staging 目录不存在,候选已失效,请重新检查更新")
            try:
                staged_now = tree_hash(staging)
            except (NotADirectoryError, OSError):
                raise ChangeError("候选 staging 不可读,请重新检查更新")
            if staged_now != candidate_hash:
                raise ChangeError("候选 staging 在安检后被改动过,拒绝应用,请重新生成计划")
            vet = _load_vet(row.get("plan_id"), context, candidate_hash)
            if vet.get("verdict") == "danger":
                raise ChangeError("候选安检结论为 danger,禁止应用")
            if vet.get("verdict") == "warning" and accept_warning is not True:
                raise ChangeError("候选安检为 warning,需要对风险做第二次明确确认才能应用")
        plan = ChangePlan.from_dict(row)
        backup = create_backup(plan, inventory, context.backup_dir)
        if not verify_backup(backup["path"]).get("ok"):
            raise ChangeError("备份自检失败,已中止(未改动任何内容)")
        audit_event["backup_id"] = backup["backup_id"]
        if row.get("action") == "update":
            _execute_update(row, targets, backup, context, candidate_hash)
        else:
            _execute_remove(row, targets, context)
        audit_event["status"] = "success"
        audit_event["resulting_hash"] = backup["backup_id"]
        append_audit(audit_event, context.audit_path)
        return {"ok": True, "action": audit_event["action"], "backup_id": backup["backup_id"],
                "backup_path": backup["path"], "removed": [i["path"] for i in targets],
                "plan_id": row.get("plan_id")}
    except _RollbackDone as e:
        audit_event["error"] = str(e)
        audit_event["rollback_status"] = "restored"
        append_audit(audit_event, context.audit_path)
        raise ChangeError(str(e))
    except ChangeError as e:
        audit_event["error"] = str(e)
        rollback_status = None
        if audit_event["backup_id"] and row.get("action") == "remove":
            try:
                restore_backup(_find_backup(context.backup_dir, audit_event["backup_id"]),
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


class _RollbackDone(Exception):
    """update 交换已发生但验证失败;旧版本已换回,审计记 restored。"""


def _execute_remove(row, targets, context):
    for inst in targets:
        _remove_entity(inst["path"])
    if context.verify_after_apply is not None:
        ok = context.verify_after_apply()
    else:
        ok = all(not os.path.lexists(i["path"]) for i in targets)
    if not ok:
        raise ChangeError("删除后验证失败,自动回滚")


def _execute_restore(row, inventory, context):
    """按计划恢复备份:目标已存在则冲突失败;恢复后逐实体校验摘要。"""
    from .backup import restore_backup
    pre = dict(row.get("preconditions", []))
    backup_path = str(pre.get("backup_path") or "")
    if not os.path.isfile(backup_path):
        raise ChangeError("备份归档不存在,恢复中止")
    result = restore_backup(backup_path, inventory.get("locations", []), conflict="fail")
    if context.verify_after_apply is not None and not context.verify_after_apply():
        raise ChangeError("恢复后验证失败")
    return result


def _execute_update(row, targets, backup, context, candidate_hash):
    import shutil as _shutil
    pre = dict(row.get("preconditions", []))
    staging = Path(str(pre.get("staging_path")))
    inst = targets[0]
    target = Path(inst["path"])
    parent = target.parent
    backup_id = str(backup["backup_id"])
    tmp = parent / (target.name + ".staging-tmp-" + backup_id)
    rollback = parent / (target.name + ".rollback-" + backup_id)
    for p in (tmp, rollback):
        if p.is_symlink() or p.is_file():
            p.unlink()
        elif p.exists():
            _shutil.rmtree(p)
    try:
        # 跨文件系统安全:先把候选物化到目标同目录(同一文件系统),再原子切换
        _shutil.copytree(staging, tmp, symlinks=True)
        if tree_hash(tmp) != candidate_hash:
            raise ChangeError("候选物化后摘要不符,已中止")
        swapped = False
        try:
            os.rename(target, rollback)
            swapped = True
            os.rename(tmp, target)
        except OSError as e:
            if swapped:  # 换回旧版本
                _shutil.rmtree(target, ignore_errors=True)
                os.rename(rollback, target)
            raise _RollbackDone("更新交换失败({}),保留原版本".format(type(e).__name__))
        try:
            ok = (context.verify_after_apply() if context.verify_after_apply is not None
                  else tree_hash(target) == candidate_hash)
        except Exception:
            # 验证器自身崩溃与"返回 False"同责:换回旧版本,不留新内容在位
            ok = False
        if not ok:
            _shutil.rmtree(target, ignore_errors=True)
            os.rename(rollback, target)
            raise _RollbackDone("更新后验证失败,已回滚到旧版本")
        _shutil.rmtree(rollback, ignore_errors=True)
    except _RollbackDone:
        raise
    except ChangeError:
        _shutil.rmtree(tmp, ignore_errors=True)
        raise
    except OSError as e:
        _shutil.rmtree(tmp, ignore_errors=True)
        raise ChangeError("更新失败已中止: " + type(e).__name__)


def _find_backup(backup_dir, backup_id):
    path = Path(backup_dir) / ("backup-" + str(backup_id) + ".tar.gz")
    if not path.is_file():
        raise ChangeError("找不到备份归档: " + str(backup_id))
    return path
