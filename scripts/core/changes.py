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
import re
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
from .policy import check_action, load_policy
from . import transactions as _txn
from .transactions import read_transaction  # noqa: F401  (引擎统一入口,供 CLI/API 使用)

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


# digest 兼容归一化:值为默认值的字段在计算摘要前剔除,
# 保证旧格式计划(无 reason/recommendation_id)的 digest 校验不受新字段影响
_DIGEST_DEFAULT_FIELDS = {"reason": "", "recommendation_id": ""}


def plan_digest(row) -> str:
    """计划摘要 = 规范化 JSON(不含 digest 字段本身)的 SHA-256。"""
    body = {k: v for k, v in row.items() if k != "digest"}
    for key, default in _DIGEST_DEFAULT_FIELDS.items():
        if body.get(key, default) == default:
            body.pop(key, None)
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


def _client_managed_refusal(inst, known_sources):
    """兼容保留:直接给出客户端托管拒绝文案(策略接入前的旧入口)。"""
    if not known_sources:
        return None
    from .provenance import classify_provenance, client_managed_advice
    prov = classify_provenance(inst, {}, known_sources)
    advice = client_managed_advice(prov)
    if advice and prov.get("class") == "protected":
        return "{}(目录 {}):{}".format(
            str(inst.get("logical_name") or inst.get("directory_name")),
            str(inst.get("directory_name")), advice)
    return None


def _policy_for(plans_dir, known_sources=None):
    """加载 data_dir 权威策略;调用方传入的 known_sources 只能叠加保护,不能削弱权威配置。

    2026-09-02 复盘:外部 Agent 绕过 CLI 直调并省略 known_sources 曾绕过 builtin-app
    保护。防线必须默认在位:权威配置损坏时直接拒绝写操作,传 falsy 视同未提供。
    """
    from .policy import load_policy
    policy = load_policy(Path(plans_dir).parent)
    if known_sources:
        merged = dict(known_sources)
        merged.update(policy.get("known_sources") or {})
        policy = dict(policy, known_sources=merged)
    return policy


def _refuse_or_none(verdict):
    if verdict.get("allowed"):
        return None
    return "{}({})".format(verdict.get("message"), verdict.get("reason_code"))


def create_update_plan(instance_id, candidate_snapshot, inventory, plans_dir,
                       known_sources=None) -> ChangePlan:
    """生成固定候选更新计划:precondition 同时绑定本地 hash、来源、commit、候选 hash 与 staging 路径。"""
    policy = _policy_for(plans_dir, known_sources)
    iid = _validate_instance_id(instance_id)
    by_id = {i.get("instance_id"): i for i in inventory.get("instances", [])}
    loc_by_id = {l.get("location_id"): l for l in inventory.get("locations", [])}
    inst = by_id.get(iid)
    if not inst:
        raise ChangeError("instance_id 不在当前 inventory 中(先重跑 scan.py): " + iid)
    refusal = _refuse_or_none(check_action("update", inst, loc_by_id.get(inst.get("location_id")),
                                           policy))
    if refusal:
        raise ChangeError("拒绝生成更新计划: " + refusal)
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
        ("is_symlink:" + iid, "1" if inst.get("is_symlink") else "0"),
        ("root_real:" + iid,
         os.path.realpath(loc_by_id[inst.get("location_id")]["path"])
         if inst.get("location_id") in loc_by_id else ""),
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


def _restore_targets_document(manifest_entries):
    """manifest 目标集合的规范化 JSON(恢复计划与执行期共用同一序列化,防止口径漂移)。"""
    rows = [{"instance_id": str(e.get("instance_id")),
             "location_id": str(e.get("location_id")),
             "original_relative_path": str(e.get("original_relative_path")),
             "type": str(e.get("type"))}
            for e in manifest_entries or []]
    rows.sort(key=lambda r: (r["instance_id"], r["original_relative_path"]))
    return json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def create_restore_plan(backup_id, backup_dir, plans_dir) -> ChangePlan:
    """生成恢复计划:先校验备份可用,precondition 绑定 backup_id、归档路径、归档内容摘要
    (archive_sha256)与目标集合快照;执行期任何一项不符都拒绝。冲突不覆盖。"""
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
                       ("backup_path", os.path.abspath(str(path))),
                       ("archive_sha256", str(info.get("archive_sha256"))),
                       ("restore_targets", _restore_targets_document(
                           manifest.get("entries", [])))),
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
    evidence = [str(e).strip() for e in (evidence or [])]
    if not any(evidence):
        raise ChangeError("安检证据不能为空:至少给出一条可核查依据(来源/阅读记录/对比结论)")
    record = {"plan_id": str(plan_id), "candidate_hash": str(candidate_hash),
              "verdict": verdict, "evidence": evidence,
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
    from .policy import PolicyError, validate_candidate_vet
    try:
        return validate_candidate_vet(vet, plan_id, candidate_hash)
    except PolicyError as e:
        raise ChangeError(str(e))


def create_remove_plan(instance_ids, inventory, reason, plans_dir,
                       known_sources=None, recommendation_id=None) -> ChangePlan:
    """为可变实例生成不可变删除计划;目标不存在/不可变/路径越界/保护身份都直接拒绝。

    用户理由与(可选)价值审查推荐记录 ID 正式保存在计划中,digest 覆盖它们。
    """
    policy = _policy_for(plans_dir, known_sources)
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
        refusal = _refuse_or_none(check_action("remove", inst,
                                               loc_by_id.get(inst.get("location_id")), policy))
        if refusal:
            raise ChangeError("拒绝生成删除计划: " + refusal)
        loc = loc_by_id.get(inst.get("location_id"))
        if not loc:
            raise ChangeError("实例位置不在 inventory 中,拒绝生成计划: " + iid)
        root = os.path.realpath(loc["path"])
        parent = os.path.realpath(os.path.dirname(inst["path"]))
        if parent != root and not parent.startswith(root + os.sep):
            raise ChangeError("实例路径越出位置根目录,拒绝生成计划: " + iid)
        if not inst.get("tree_hash"):
            raise ChangeError("实例缺少完整内容指纹,先重跑 scan.py: " + iid)
        targets.append((inst, loc))

    if not targets:
        raise ChangeError("计划没有目标")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    expires = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + PLAN_TTL_SECONDS))
    plan_id = "plan-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
    preconditions = []
    for inst, loc in targets:
        preconditions.append(("tree_hash:" + inst["instance_id"], inst["tree_hash"]))
        preconditions.append(("path:" + inst["instance_id"], os.path.abspath(inst["path"])))
        preconditions.append(("is_symlink:" + inst["instance_id"],
                              "1" if inst.get("is_symlink") else "0"))
        preconditions.append(("root_real:" + inst["instance_id"], os.path.realpath(loc["path"])))
    names = ", ".join(str(i.get("logical_name") or i.get("directory_name")) for i, _ in targets)
    plan = ChangePlan(
        plan_id=plan_id, action="remove",
        target_ids=tuple(str(i["instance_id"]) for i, _ in targets),
        preconditions=tuple(preconditions),
        summary="删除 {} 个 skill 实例({});先备份,失败自动回滚".format(len(targets), names),
        digest="", created_at=now, expires_at=expires,
        reason=reason.strip(), recommendation_id=str(recommendation_id or ""))
    row = plan.to_dict()
    row["digest"] = plan_digest(row)
    plan = ChangePlan.from_dict(row)
    write_plan(plan, plans_dir)
    return plan


_PLAN_PRECONDITION_KEY_RE = re.compile(
    r"^(tree_hash:[0-9a-f]{20}|path:[0-9a-f]{20}|is_symlink:[0-9a-f]{20}"
    r"|root_real:[0-9a-f]{20}|candidate_hash|staging_path"
    r"|repo|commit_sha|backup_id|backup_path|archive_sha256|restore_targets)$")


def _validate_plan_row(row) -> dict:
    """计划文件的结构合同(F04):任何字段错位、目标夹带路径、未知前置键都直接拒绝。"""
    if not isinstance(row, dict):
        raise ChangeError("计划文件不是 JSON 对象")
    pid = row.get("plan_id")
    if not isinstance(pid, str) or not pid.startswith("plan-"):
        raise ChangeError("计划 plan_id 非法")
    action = row.get("action")
    if action not in ("remove", "update", "restore"):
        raise ChangeError("未知动作: " + str(action))
    target_ids = row.get("target_ids")
    if not isinstance(target_ids, list) or not target_ids:
        raise ChangeError("计划没有目标")
    seen = set()
    for raw in target_ids:
        text = str(raw)
        if len(text) != 20 or any(c not in "0123456789abcdef" for c in text):
            raise ChangeError("计划目标必须是稳定 instance ID,拒绝路径或目录名: " + text[:40])
        if text in seen:
            raise ChangeError("计划目标重复: " + text)
        seen.add(text)
    preconditions = row.get("preconditions")
    if not isinstance(preconditions, list):
        raise ChangeError("计划缺少前置条件")
    keys = set()
    for pair in preconditions:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not all(isinstance(x, (str, int, float)) for x in pair)):
            raise ChangeError("计划前置条件格式非法")
        key = str(pair[0])
        if not _PLAN_PRECONDITION_KEY_RE.match(key):
            raise ChangeError("计划前置键未知或非法: " + key[:40])
        if key in keys:
            raise ChangeError("计划前置键重复: " + key)
        keys.add(key)
    for field in ("created_at", "expires_at", "summary", "digest"):
        if not isinstance(row.get(field), str) or not row[field]:
            raise ChangeError("计划缺少字段: " + field)
    return row


def _load_plan(plan_id, context) -> dict:
    path = Path(context.plans_dir) / (str(plan_id) + ".json")
    if not path.is_file():
        raise ChangeError("计划不存在: " + str(plan_id))
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise ChangeError("计划文件损坏")
    row = _validate_plan_row(row)
    if row.get("digest") != plan_digest(row):
        raise ChangeError("计划文件被改动过,digest 不符,拒绝执行")
    return row


def _check_preconditions(row, inventory, policy):
    from .fingerprint import tree_hash
    from .paths import PathScopeError, confined_destination
    by_id = {i.get("instance_id"): i for i in inventory.get("instances", [])}
    loc_by_id = {l.get("location_id"): l for l in inventory.get("locations", [])}
    pre = dict(row.get("preconditions", []))
    action = row.get("action")
    for iid in row.get("target_ids", []):
        inst = by_id.get(iid)
        if not inst:
            raise ChangeError("目标实例已不在当前 inventory(先重跑 scan.py 再重新计划): " + str(iid))
        verdict = check_action(action, inst, loc_by_id.get(inst.get("location_id")), policy)
        if not verdict.get("allowed"):
            raise ChangeError("执行期策略复核未通过({}): {}".format(
                verdict.get("reason_code"), verdict.get("message")))
        if pre.get("path:" + iid) and os.path.abspath(pre["path:" + iid]) != os.path.abspath(inst["path"]):
            raise ChangeError("目标路径与计划不一致: " + str(iid))
        # 实体形态核对:同内容但目录被换成符号链接(或反之)时,确认对象已与计划不同
        want_link = pre.get("is_symlink:" + iid)
        if want_link is not None and bool(int(want_link)) != os.path.islink(inst["path"]):
            raise ChangeError("实体形态与计划不一致(目录/符号链接变化),拒绝执行,请重新计划: "
                              + str(iid))
        if bool(inst.get("is_symlink")) != os.path.islink(inst["path"]):
            raise ChangeError("实体形态与 inventory 记录不一致,拒绝执行,请重新盘点: " + str(iid))
        # 位置根核对:计划后根目录被换成指向别处的符号链接必须重新计划
        root_real = pre.get("root_real:" + iid)
        loc = loc_by_id.get(inst.get("location_id"))
        if root_real and loc and os.path.realpath(loc["path"]) != root_real:
            raise ChangeError("位置根目录与计划不一致(可能被换成符号链接),拒绝执行,请重新计划: "
                              + str(iid))
        # 父目录归属核对:位置根到目标的父链不得穿出根或经过符号链接
        if loc:
            try:
                confined_destination(loc["path"],
                                     os.path.relpath(inst["path"], loc["path"]))
            except (PathScopeError, ValueError) as e:
                raise ChangeError("目标父目录归属核验失败,拒绝执行,请重新计划: " + str(e))
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


def _hash_entity_path(path, kind):
    """按实体类型计算落盘摘要;与备份合同一致(dir/symlink→树摘要,file→内容摘要)。"""
    from .backup import _chunked_sha256_file
    path = str(path)
    if kind == "file":
        return _chunked_sha256_file(path)
    if kind == "symlink":
        return tree_hash(os.path.realpath(path))
    return tree_hash(path)


def _entity_kind(inst):
    if inst.get("is_symlink"):
        return "symlink"
    if os.path.isfile(inst["path"]) and not os.path.isdir(inst["path"]):
        return "file"
    return "dir"


def _commit_state(context, state, result_hashes):
    """提交点:此后文件事务成功,附属状态(审计/报告)失败不得盲目回滚。"""
    state["phase"] = "committed"
    state["result"] = {"status": "committed", "result_hashes": result_hashes}
    _txn.write_transaction(context, state)


def _record_problems(context, state, problems, phase_rolled="rolled-back"):
    state["phase"] = phase_rolled if not problems else "recovery-required"
    state["cleanup_pending"] = sorted(set(state.get("cleanup_pending", []) + problems))
    _txn.write_transaction(context, state)


def _cleanup_holdings(context, state):
    """清理本事务的保管目录;失败进入 cleanup_pending,不影响"已提交"事实。"""
    import shutil as _shutil
    pending = list(state.get("cleanup_pending", []))
    paths = [t["holding_path"] for t in state.get("targets", []) if t.get("holding_path")]
    if state.get("candidate_holding"):
        paths.append(state["candidate_holding"])
    for p in paths:
        if not os.path.lexists(p):
            continue
        try:
            if os.path.islink(p) or os.path.isfile(p):
                os.remove(p)
            else:
                _shutil.rmtree(p)
        except OSError:
            if p not in pending:
                pending.append(p)
    state["cleanup_pending"] = pending
    _txn.write_transaction(context, state)


def _undo_remove(state):
    """删除回滚:把保管中的原实体移回原位并核对哈希;返回问题清单(空=完全恢复)。

    两遍处理:先全部移回(链接目标的校验依赖其正本回位),再统一校验哈希。
    """
    problems = []
    for t in reversed(state["targets"]):
        src, holding = t["path"], t.get("holding_path")
        if holding and os.path.lexists(holding):
            if os.path.lexists(src):
                problems.append(src + "(原位已被占用,保管内容保留)")
                continue
            os.rename(holding, src)
            _txn.fsync_dir(os.path.dirname(src))
            t["moved"] = False
    for t in state["targets"]:
        src = t["path"]
        if not os.path.lexists(src):
            problems.append(src + "(原位与保管处都不存在,需从备份恢复)")
            continue
        try:
            if _hash_entity_path(src, t["kind"]) != t["original_hash"]:
                problems.append(src + "(回滚后哈希不一致)")
        except (NotADirectoryError, OSError):
            problems.append(src + "(回滚后不可读)")
    return problems


def _undo_update(state):
    """更新回滚:新候选挪开(绝不激活),旧版本回原位并核对哈希。"""
    import shutil as _shutil
    t = state["targets"][0]
    target, old_holding = t["path"], t.get("holding_path")
    new_holding = state.get("candidate_holding")
    problems = []
    if t.get("published") or (old_holding and not os.path.lexists(old_holding)
                              and os.path.lexists(target)):
        # 新内容在位:挪到候选保管位(工具暂存内容,可直接清理)
        if os.path.lexists(target) and new_holding:
            try:
                if os.path.islink(new_holding) or os.path.isfile(new_holding):
                    os.remove(new_holding)
                elif os.path.lexists(new_holding):
                    _shutil.rmtree(new_holding)
                os.rename(target, new_holding)
                t["published"] = False
            except OSError:
                problems.append(target + "(新内容挪让失败)")
    if old_holding and os.path.lexists(old_holding):
        if os.path.lexists(target):
            problems.append(target + "(原位被占用,旧版本保留在保管处)")
        else:
            os.rename(old_holding, target)
            _txn.fsync_dir(os.path.dirname(target))
            t["moved"] = False
            try:
                if _hash_entity_path(target, t["kind"]) != t["original_hash"]:
                    problems.append(target + "(回滚后哈希不一致)")
            except (NotADirectoryError, OSError):
                problems.append(target + "(回滚后不可读)")
    elif not os.path.lexists(target):
        problems.append(target + "(原位与保管处都不存在,需从备份恢复)")
    if new_holding and os.path.lexists(new_holding):
        try:
            if os.path.islink(new_holding) or os.path.isfile(new_holding):
                os.remove(new_holding)
            else:
                _shutil.rmtree(new_holding)
        except OSError:
            problems.append(new_holding + "(候选残留清理失败)")
    return problems


def _undo_restore(state):
    """恢复回滚/中断恢复:只撤销本事务发布的实体(哈希与备份一致才动,保留陌生内容)。"""
    import shutil as _shutil
    problems = []
    for t in reversed(state["targets"]):
        p = t["path"]
        if not os.path.lexists(p):
            continue
        try:
            actual = _hash_entity_path(p, t["kind"])
        except (NotADirectoryError, OSError):
            problems.append(p + "(不可读)")
            continue
        if actual != t.get("expected_hash"):
            problems.append(p + "(内容与备份不符,疑似非本事务产物,保留)")
            continue
        try:
            if os.path.islink(p) or os.path.isfile(p):
                os.remove(p)
            else:
                _shutil.rmtree(p)
            _txn.fsync_dir(os.path.dirname(p))
            t["published"] = False
        except OSError:
            problems.append(p + "(撤销失败)")
    return problems


def _settle(context, state, undo_fn):
    problems = undo_fn(state)
    _record_problems(context, state, problems)


def _rollback_status(plan_id, context, state=None):
    try:
        s = state if state is not None else _txn.read_transaction(plan_id, context)
    except _txn.TransactionError:
        return None
    if not s:
        return None
    return {"rolled-back": "restored", "recovery-required": "recovery-required"}.get(
        s.get("phase"))


def _append_audit_safe(event, context, state):
    try:
        append_audit(event, context.audit_path)
    except OSError:
        # 审计写不出:事务文件保留事实与待补写状态,不谎报失败也不丢事件
        if state is not None:
            state["audit_pending"] = True
            _txn.write_transaction(context, state)


def recover_transaction(plan_id, context) -> dict:
    """把已授权事务恢复到原状态:只撤销本事务落地的对象,绝不激活新候选。

    对象冲突时保留实体并标 recovery-required(阻止后续相关写操作);审计记录恢复结果。
    """
    row = _load_plan(plan_id, context)  # 授权依据;过期不影响恢复(恢复只回退,不前进)
    try:
        state = _txn.read_transaction(plan_id, context)
    except _txn.TransactionError as e:
        raise ChangeError(str(e))
    if state is None:
        raise ChangeError("该计划没有事务记录,无需恢复")
    if state.get("phase") in ("prepared", "mutating", "rolling-back", "recovery-required"):
        undo = {"remove": _undo_remove, "update": _undo_update,
                "restore": _undo_restore}[state.get("action")]
        problems = undo(state)
        _record_problems(context, state, problems)
        append_audit({"action": str(state.get("action")), "plan_id": str(plan_id),
                      "status": "recovered" if not problems else "recovery-required",
                      "rollback_status": "restored" if not problems else "recovery-required",
                      "backup_id": state.get("backup_id"),
                      "reason": "recover_transaction: " + str(state.get("reason") or "")},
                     context.audit_path)
    elif state.get("phase") == "committed":
        _cleanup_holdings(context, state)  # 已提交:只清理保管残留,不做物理恢复
    return state


def _preflight_parents(target_paths, plan_id):
    """Task 4:对每个目标实体的同目录做真实预检(同目录只检一次)。

    放在锁后、备份与任何移动之前;失败转换为 ChangeError,目标保持原状。
    """
    from .preflight import PreflightError, preflight_target_directory
    seen = set()
    for path in target_paths or []:
        parent = os.path.dirname(os.path.abspath(os.fspath(path)))
        if parent in seen:
            continue
        seen.add(parent)
        try:
            preflight_target_directory(parent, plan_id)
        except PreflightError as e:
            raise ChangeError("目标目录预检失败,已中止(未改动任何内容): " + str(e)) from e


def apply_plan(plan_id, digest, confirm, context, accept_warning=False) -> dict:
    """执行不可变计划:锁 → 事务状态 → 备份 → 受控移动 → 验证(失败恢复原状)→ 提交 → 审计。

    - 已提交计划重放返回已知结果(already_applied),不再发生物理变更;
    - 中断的未完成事务拒绝执行,先 recover_transaction 恢复原状;
    - update 要求候选已安检(safe;warning 需第二次明确确认);
    - 提交点之后的审计失败标注 audit_pending,不谎报失败。
    """
    if confirm is not True:
        raise ChangeError("缺少明确确认(confirm 必须是布尔 true)")
    row = _load_plan(plan_id, context)
    if not isinstance(digest, str) or not hmac.compare_digest(digest, str(row.get("digest"))):
        raise ChangeError("digest 不一致:必须使用生成计划时输出的摘要,逐字匹配")
    if row.get("expires_at", "") < time.strftime("%Y-%m-%d %H:%M:%S"):
        raise ChangeError("计划已过期(30 分钟有效),请重新生成")
    action = row.get("action")

    started = time.strftime("%Y-%m-%d %H:%M:%S")
    if context.load_inventory is None:
        raise ChangeError("变更环境缺少 inventory 提供者")

    lock = FileLock(context.lock_path)
    try:
        lock.acquire()
    except (BlockingIOError, OSError):
        raise LockBusy("另一个 skill-keeper 变更正在进行,请稍后再试")
    audit_event = {
        "action": str(action), "target_ids": list(row.get("target_ids", [])),
        "plan_id": str(row.get("plan_id")),
        "reason": str(row.get("reason") or row.get("summary", "")),
        "recommendation_id": str(row.get("recommendation_id") or ""),
        "started_at": started, "status": "failed", "error": None, "rollback_status": None,
        "backup_id": None,
    }
    state = None
    try:
        # 互斥范围内重读 inventory 与权威策略(F04):执行期与计划共用同一策略
        try:
            inventory = context.load_inventory()
        except Exception as e:
            raise ChangeError("读取 inventory 失败: " + type(e).__name__)
        policy = load_policy(context.data_dir)
        if not policy.get("healthy"):
            reason_text = ";".join(str(i.get("source")) + ":" + str(i.get("code"))
                                   for i in (policy.get("issues") or []))
            raise ChangeError("保护策略配置损坏,拒绝执行写操作,请先修复 data 目录配置: "
                              + reason_text)
        # 事务断点检查(F03):同一计划不得发生第二次物理变更
        try:
            prior = _txn.read_transaction(plan_id, context)
        except _txn.TransactionError as e:
            raise ChangeError("事务状态损坏,拒绝执行,请先人工检查 data/transactions: " + str(e))
        if prior is not None:
            phase = prior.get("phase")
            if phase == "committed":
                result = dict(prior.get("result") or {})
                result.update({"already_applied": True, "transaction_status": "committed",
                               "plan_id": str(plan_id)})
                return result
            if phase == "rolled-back":
                raise ChangeError("该计划已回滚(结果已知),不能重复执行;如需重做请重新生成计划")
            raise ChangeError("检测到未完成事务(phase={}),请先运行恢复接口 recover_transaction('{}')"
                              .format(phase, plan_id))

        if action == "restore":
            # 恢复的目标通常不存在,不做存在性前置校验;备份绑定本身就是 precondition
            _policy_check_restore_targets(row, inventory, policy)
            state = _prepare_restore_state(row, inventory, context)
            _preflight_parents([t["path"] for t in state["targets"]], plan_id)
            audit_event["backup_id"] = state["backup_id"]
            audit_event["expected_hash"] = json.dumps(
                {t["instance_id"]: "" for t in state["targets"]},
                ensure_ascii=False, sort_keys=True)
            result_hashes = _execute_restore(row, inventory, context, state)
            audit_event["status"] = "success"
            audit_event["resulting_hash"] = json.dumps(result_hashes,
                                                       ensure_ascii=False, sort_keys=True)
            result = {"ok": True, "action": "restore", "backup_id": state["backup_id"],
                      "transaction_status": "committed", "result_hashes": result_hashes,
                      "plan_id": str(plan_id),
                      "restored": [t["path"] for t in state["targets"]]}
            _append_audit_safe(audit_event, context, state)
            if state.get("audit_pending"):
                result["audit_pending"] = True
            return result

        _check_preconditions(row, inventory, policy)
        by_id = {i.get("instance_id"): i for i in inventory.get("instances", [])}
        targets = [by_id[i] for i in row.get("target_ids", [])]
        # Task 4:真实目标预检 —— 在每个目标同目录实际验证创建/rename/fsync,
        # 失败在备份与任何移动发生之前中止(此时目标从未被动过)。
        _preflight_parents([inst["path"] for inst in targets], plan_id)
        candidate_hash = None
        if action == "update":
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
            if vet.get("verdict") == "warning" and accept_warning is not True:
                raise ChangeError("候选安检为 warning,需要对风险做第二次明确确认才能应用")
        plan = ChangePlan.from_dict(row)
        backup = create_backup(plan, inventory, context.backup_dir)
        if not verify_backup(backup["path"]).get("ok"):
            raise ChangeError("备份自检失败,已中止(未改动任何内容)")
        audit_event["backup_id"] = backup["backup_id"]

        # 事务状态(prepared):崩溃后凭保管路径 + 实体哈希恢复,不猜目录名
        txn_targets = []
        for inst in targets:
            txn_targets.append({
                "instance_id": str(inst["instance_id"]), "path": str(inst["path"]),
                "kind": _entity_kind(inst),
                "original_hash": str(inst.get("tree_hash") or ""),
                "holding_path": _txn.holding_path(os.path.dirname(inst["path"]), plan_id,
                                                  str(inst["instance_id"])[:8]),
                "moved": False, "published": False})
        candidate_holding = None
        if action == "update":
            candidate_holding = _txn.holding_path(os.path.dirname(targets[0]["path"]),
                                                  plan_id, "cand")
        state = _txn.new_state(row, action, txn_targets, backup_id=backup["backup_id"],
                               candidate_hash=candidate_hash,
                               candidate_holding=candidate_holding)
        _txn.write_transaction(context, state)
        expected = {t["instance_id"]: t["original_hash"] for t in txn_targets}
        if action == "update":
            result_hashes = _execute_update(row, targets, context, state)
        else:
            result_hashes = _execute_remove(row, context, state)
        # 提交点之后:附属状态失败只标注,不盲目回滚
        audit_event["status"] = "success"
        audit_event["expected_hash"] = json.dumps(expected, ensure_ascii=False, sort_keys=True)
        audit_event["resulting_hash"] = json.dumps(result_hashes,
                                                   ensure_ascii=False, sort_keys=True)
        result = {"ok": True, "action": action, "backup_id": backup["backup_id"],
                  "backup_path": backup["path"], "transaction_status": "committed",
                  "result_hashes": result_hashes, "plan_id": str(plan_id),
                  "removed": [i["path"] for i in targets] if action == "remove" else []}
        _append_audit_safe(audit_event, context, state)
        if state.get("audit_pending"):
            result["audit_pending"] = True
        return result
    except ChangeError as e:
        audit_event["error"] = str(e)
        audit_event["rollback_status"] = _rollback_status(plan_id, context, state)
        _append_audit_safe(audit_event, context, state)
        raise
    except _txn.TransactionError as e:
        audit_event["error"] = "TransactionError: " + str(e)
        audit_event["rollback_status"] = None
        append_audit(audit_event, context.audit_path)
        raise ChangeError("事务状态损坏,拒绝执行,请先人工检查 data/transactions: " + str(e))
    except Exception as e:
        audit_event["error"] = type(e).__name__ + ": " + str(e)[:200]
        audit_event["rollback_status"] = _rollback_status(plan_id, context, state)
        _append_audit_safe(audit_event, context, state)
        raise ChangeError("变更失败已中止: " + type(e).__name__)
    finally:
        lock.release()


def _policy_check_restore_targets(row, inventory, policy):
    """恢复动作的执行期策略复核:目标位置存在且不可变(客户端托管)时拒绝恢复。"""
    pre = dict(row.get("preconditions", []))
    doc = pre.get("restore_targets")
    if not (isinstance(doc, str) and doc):
        return  # 绑定缺失由 _execute_restore 拒绝
    try:
        rows = json.loads(doc)
    except ValueError:
        raise ChangeError("恢复计划目标集合损坏,拒绝执行")
    loc_by_id = {str(l.get("location_id")): l for l in inventory.get("locations", [])}
    for t in rows:
        loc = loc_by_id.get(str(t.get("location_id")))
        verdict = check_action("restore", {"instance_id": t.get("instance_id")}, loc, policy)
        if not verdict.get("allowed"):
            raise ChangeError("恢复执行期策略复核未通过: " + str(verdict.get("message")))


def _prepare_restore_state(row, inventory, context) -> dict:
    """恢复动作的事务准备:重新核对归档绑定,把目标实体清单写入事务状态。"""
    from .backup import BackupError, verify_backup
    from .paths import PathScopeError, confined_destination
    pre = dict(row.get("preconditions", []))
    backup_path = str(pre.get("backup_path") or "")
    if not os.path.isfile(backup_path):
        raise ChangeError("备份归档不存在,恢复中止")
    if not pre.get("archive_sha256") or not pre.get("restore_targets"):
        # F01/F02:旧恢复计划没有归档摘要与目标集合绑定,无法证明确认对象就是执行对象
        raise ChangeError("旧恢复计划缺少归档摘要/目标集合绑定,请重新生成恢复计划(原备份保留)")
    try:
        info = verify_backup(backup_path)
    except BackupError as e:
        raise ChangeError("备份当前校验失败,恢复中止: " + str(e))
    if str(info.get("archive_sha256")) != str(pre.get("archive_sha256")):
        raise ChangeError("备份归档与计划确认时的内容不一致(archive_sha256 不符),"
                          "拒绝执行,请重新生成恢复计划")
    entries = (info.get("manifest") or {}).get("entries", [])
    if _restore_targets_document(entries) != str(pre.get("restore_targets")):
        raise ChangeError("备份目标集合与计划确认时的不一致,拒绝执行,请重新生成恢复计划")
    loc_map = {str(l.get("location_id")): l.get("path")
               for l in inventory.get("locations", [])}
    txn_targets = []
    for e in entries:
        root = loc_map.get(str(e.get("location_id")))
        if not root:
            raise ChangeError("备份位置未登记,无法恢复: " + str(e.get("location_id")))
        try:
            dest = confined_destination(root, str(e["original_relative_path"]))
        except PathScopeError as exc:
            raise ChangeError("恢复目标越界,拒绝落地: " + str(exc))
        txn_targets.append({"instance_id": str(e["instance_id"]), "path": str(dest),
                            "kind": str(e.get("type")),
                            "expected_hash": str(e.get("tree_hash")),
                            "published": False})
    state = _txn.new_state(row, "restore", txn_targets, backup_id=str(pre.get("backup_id")))
    _txn.write_transaction(context, state)
    return state


def _execute_restore(row, inventory, context, state):
    """恢复执行:冲突不覆盖;失败撤销本事务已发布实体,回到"目标不存在"的原状态。"""
    pre = dict(row.get("preconditions", []))
    backup_path = str(pre.get("backup_path") or "")
    state["phase"] = "mutating"
    _txn.write_transaction(context, state)
    try:
        restore_backup(backup_path, inventory.get("locations", []), conflict="fail")
        ok = True
        if context.verify_after_apply is not None:
            try:
                ok = context.verify_after_apply()
            except Exception:
                ok = False  # 验证异常与 False 同责
        if not ok:
            raise ChangeError("恢复后验证失败")
    except BaseException:
        _settle(context, state, _undo_restore)
        raise
    result_hashes = {}
    for t in state["targets"]:
        t["published"] = True
        try:
            result_hashes[t["instance_id"]] = _hash_entity_path(t["path"], t["kind"])
        except (NotADirectoryError, OSError):
            result_hashes[t["instance_id"]] = ""
    _commit_state(context, state, result_hashes)
    return result_hashes


def _execute_remove(row, context, state):
    """删除 = 原实体移入同目录事务保管(原子 rename),全部完成并验证后提交,再清理保管。"""
    state["phase"] = "mutating"
    _txn.write_transaction(context, state)
    try:
        for t in state["targets"]:
            src = t["path"]
            if os.path.lexists(src):
                os.rename(src, t["holding_path"])
                _txn.fsync_dir(os.path.dirname(src))
                t["moved"] = True
                _txn.write_transaction(context, state)
        leftovers = [t["path"] for t in state["targets"] if os.path.lexists(t["path"])]
        if leftovers:
            raise ChangeError("仍有目标未移走,回滚: " + ", ".join(leftovers))
        ok = True
        if context.verify_after_apply is not None:
            try:
                ok = context.verify_after_apply()
            except Exception:
                ok = False  # 验证异常与 False 同责
        if not ok:
            raise ChangeError("删除后验证失败,自动回滚")
    except BaseException:
        _settle(context, state, _undo_remove)
        raise
    result_hashes = {t["instance_id"]: "" for t in state["targets"]}
    _commit_state(context, state, result_hashes)
    _cleanup_holdings(context, state)
    return result_hashes


def _execute_update(row, targets, context, state):
    """更新 = 候选物化到目标同目录 → 旧版本移入保管 → 候选就位;失败旧版本回原位。"""
    import shutil as _shutil
    pre = dict(row.get("preconditions", []))
    staging = Path(str(pre.get("staging_path")))
    t = state["targets"][0]
    target = Path(t["path"])
    new_holding = Path(state["candidate_holding"])
    state["phase"] = "mutating"
    _txn.write_transaction(context, state)
    try:
        # 跨文件系统安全:先把候选物化到目标同目录(同一文件系统),再原子切换
        if new_holding.is_symlink() or new_holding.is_file():
            new_holding.unlink()
        elif new_holding.exists():
            _shutil.rmtree(new_holding)
        _shutil.copytree(staging, new_holding, symlinks=True)
        if tree_hash(new_holding) != state["candidate_hash"]:
            raise ChangeError("候选物化后摘要不符,已中止")
        os.rename(target, t["holding_path"])
        _txn.fsync_dir(target.parent)
        t["moved"] = True
        _txn.write_transaction(context, state)
        os.rename(new_holding, target)
        _txn.fsync_dir(target.parent)
        t["published"] = True
        _txn.write_transaction(context, state)
        disk_ok = tree_hash(target) == state["candidate_hash"]  # 引擎自身落盘校验,回调绕不过
        biz_ok = True
        if context.verify_after_apply is not None:
            try:
                biz_ok = context.verify_after_apply()
            except Exception:
                biz_ok = False  # 验证异常与 False 同责
        if not (disk_ok and biz_ok):
            raise ChangeError("更新后验证失败,已回滚到旧版本")
    except BaseException:
        _settle(context, state, _undo_update)
        raise
    result_hashes = {t["instance_id"]: state["candidate_hash"]}
    _commit_state(context, state, result_hashes)
    _cleanup_holdings(context, state)
    return result_hashes


def _find_backup(backup_dir, backup_id):
    path = Path(backup_dir) / ("backup-" + str(backup_id) + ".tar.gz")
    if not path.is_file():
        raise ChangeError("找不到备份归档: " + str(backup_id))
    return path
