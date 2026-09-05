"""统一执行策略(Task 2,F04):动作许可、来源保护与候选安检合同的唯一权威。

铁律:
- 计划(plan)与执行(apply)共用 load_policy + check_action;
- 保护白名单以 data_dir 权威配置(known-sources.json + self-built.txt)为准;
  调用方传入的 known_sources 只能叠加保护,不能覆盖/削弱权威配置;
- 配置损坏时拒绝一切写操作,绝不 fallback 成空保护表;
- 自建 skill 默认免于删除/更新;客户端托管(builtin-app 等)只能走所属客户端。
"""
import hashlib
import json
from pathlib import Path

from .io import ISSUE_CORRUPT, ISSUE_NOT_FOUND, load_json_checked
from .provenance import classify_provenance, client_managed_advice

ACTIONS = ("remove", "update", "restore")


class PolicyError(ValueError):
    """策略/安检输入不合法;changes 层会转换为 ChangeError 面向用户。"""


def _denied(code, message, policy_hash):
    return {"allowed": False, "reason_code": code, "message": message,
            "policy_hash": policy_hash}


def _allowed(policy_hash):
    return {"allowed": True, "reason_code": "ok", "message": "", "policy_hash": policy_hash}


def load_policy(data_dir) -> dict:
    """加载权威保护策略;返回 {known_sources, issues, healthy, policy_hash}。

    known-sources.json / self-built.txt 都是可选文件:不存在 ≠ 损坏;
    已存在但读不出合法结构 = 损坏,healthy=False,写操作必须拒绝。
    """
    data_dir = Path(data_dir)
    issues = []
    known, ks_issues = load_json_checked(data_dir / "known-sources.json", None)
    known_missing = any(i.get("code") == ISSUE_NOT_FOUND for i in ks_issues)
    if not known_missing:
        for issue in ks_issues:
            issues.append({"source": "known-sources.json", **issue})
    if known is None:
        known = {}
    if not isinstance(known, dict):
        if not known_missing:
            issues.append({"source": "known-sources.json", "code": ISSUE_CORRUPT,
                           "reason": "not-an-object"})
        known = {}
    merged = {}
    for key, value in known.items():
        if key == "_comment":
            continue
        merged[key] = value if isinstance(value, dict) else {"type": "unknown"}
    try:
        text = (data_dir / "self-built.txt").read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    except (OSError, UnicodeDecodeError) as e:
        issues.append({"source": "self-built.txt", "code": ISSUE_CORRUPT,
                       "reason": type(e).__name__})
    else:
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                merged[line] = {"type": "self-built"}
    canonical = json.dumps(merged, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    policy_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {"known_sources": merged, "issues": issues, "healthy": not issues,
            "policy_hash": policy_hash}


def check_action(action, target, location, policy) -> dict:
    """plan 与 apply 共用的动作许可判定。target=实例 dict,location=位置 dict(可为 None)。"""
    policy = policy or {}
    policy_hash = str(policy.get("policy_hash") or "")
    if action not in ACTIONS:
        return _denied("unknown-action", "未知动作: " + str(action), policy_hash)
    if not policy.get("healthy", False):
        reason = ";".join(str(i.get("source")) + ":" + str(i.get("code"))
                          for i in (policy.get("issues") or []))
        return _denied("policy-corrupt", "保护策略配置损坏,拒绝写操作,请先修复 data 目录配置: "
                       + reason, policy_hash)
    if not isinstance(target, dict) or not target.get("instance_id"):
        return _denied("missing-target", "目标缺少 instance_id,拒绝", policy_hash)
    if action == "restore":
        if location is not None and not location.get("mutable"):
            return _denied("location-immutable",
                           "恢复目标位置不可变(客户端托管),拒绝: " + str(location.get("location_id")),
                           policy_hash)
        return _allowed(policy_hash)
    if not target.get("mutable"):
        return _denied("instance-immutable",
                       "实例不可变(客户端自带/插件缓存),拒绝{}: {}".format(
                           action, target.get("instance_id")), policy_hash)
    if location is not None and not location.get("mutable"):
        return _denied("location-immutable",
                       "实例所属位置不可变,拒绝{}: {}".format(action, target.get("instance_id")),
                       policy_hash)
    known_sources = policy.get("known_sources") or {}
    prov = classify_provenance(target, {}, known_sources)
    if prov.get("class") == "protected":
        if prov.get("type") == "self-built":
            return _denied("self-built",
                           "自建 skill 默认免于删除/更新;如确要处置,请先从 self-built.txt "
                           "移除登记并重新盘点,再生成计划", policy_hash)
        advice = client_managed_advice(prov)
        if advice:
            return _denied("client-managed:" + str(prov.get("type")),
                           "该 Skill 由所属客户端托管: " + advice, policy_hash)
        return _denied("protected:" + str(prov.get("type")),
                       "目标属于受保护身份({}),拒绝{}".format(prov.get("type"), action),
                       policy_hash)
    return _allowed(policy_hash)


def validate_candidate_vet(record, plan_id, candidate_hash) -> dict:
    """候选安检记录的载入校验:枚举、证据、plan_id/candidate_hash 绑定全部核对。

    只接受 safe / warning;danger、缺值、非法值、空证据一律拒绝(抛 PolicyError)。
    返回规范化后的记录。
    """
    if not isinstance(record, dict):
        raise PolicyError("安检记录不是对象")
    if str(record.get("plan_id") or "") != str(plan_id):
        raise PolicyError("安检记录与计划不匹配(plan_id 不符),重新安检")
    if str(record.get("candidate_hash") or "") != str(candidate_hash):
        raise PolicyError("安检记录与候选不匹配(candidate_hash 不符),重新安检")
    verdict = record.get("verdict")
    if verdict not in ("safe", "warning"):
        raise PolicyError("verdict 必须是 safe|warning,拒绝可疑值: {!r}".format(verdict))
    evidence = record.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise PolicyError("安检证据必须是非空列表")
    normalized = [str(e).strip() for e in evidence]
    if any(not e for e in normalized):
        raise PolicyError("安检证据含空白条目")
    out = dict(record)
    out["evidence"] = normalized
    return out
