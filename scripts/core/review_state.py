"""审查结论的有效性模型(Task 5,F06):历史连接、依赖与过期判定。

铁律:
- 结论按稳定 instance ID + 内容版本连接历史;当前 logical_id 只表示内容组;
- 目标内容、已采用替代品、审查政策任一变化 → 相关结论过期(needs-recheck);
- 旧记录缺快照 = needs-recheck,可展示原结论但绝不假装重新审过;
- stars/抓取时间等热度小变化不触发过期。
"""

REVIEW_POLICY_VERSION = "review-policy-v2"


def review_dependencies(record, inventory, policy, reputation) -> dict:
    """提取一条结论依赖的证据快照:目标内容 + 已采用替代品版本 + 政策版本。"""
    record = record or {}
    iid = str(record.get("instance_id") or "")
    target = None
    for inst in (inventory or {}).get("instances", []):
        if str(inst.get("instance_id")) == iid:
            target = {"instance_id": iid, "tree_hash": str(inst.get("tree_hash") or "")}
            break
    alt_state = {}
    items_by_lid = {str(l.get("logical_id")): l
                    for l in (inventory or {}).get("logical_skills", [])}
    alt_ids = [str(a) for a in (record.get("alternatives") or [])]
    for lid in (record.get("alternatives_state") or {}):
        if str(lid) not in alt_ids:
            alt_ids.append(str(lid))
    for lid in alt_ids:
        snap = (record.get("alternatives_state") or {}).get(lid) or {}
        current = items_by_lid.get(lid)
        alt_state[lid] = {"recorded": snap,
                          "current_tree_hash": (str(current.get("tree_hash"))
                                                if current else None)}
    return {
        "review_policy_version": str((policy or {}).get("review_policy_version")
                                     or REVIEW_POLICY_VERSION),
        "target": target,
        "alternatives": alt_state,
    }


def evaluate_review(record, inventory, policy, reputation) -> dict:
    """评估一条审查记录当前是否仍有效;返回 {status, reason_codes, previous_record}。

    status ∈ current / needs-recheck / unreviewed(record 为 None 时)。
    """
    if not record:
        return {"status": "unreviewed", "reason_codes": ["no-record"],
                "previous_record": None}
    reasons = []
    if not record.get("review_snapshot_id"):
        reasons.append("missing-review-snapshot")  # 旧格式记录:展示原结论,但必须复核
    deps = review_dependencies(record, inventory, policy, reputation)
    target = deps.get("target")
    if target is None:
        reasons.append("target-missing")
    elif str(record.get("skill_tree_hash") or "") != target["tree_hash"]:
        reasons.append("target-content-changed")
    for lid, alt in (deps.get("alternatives") or {}).items():
        recorded = (alt.get("recorded") or {})
        recorded_hash = recorded.get("tree_hash")
        current_hash = alt.get("current_tree_hash")
        if recorded_hash in (None, ""):
            reasons.append("alternative-unverified:" + str(lid))
        elif current_hash is None:
            reasons.append("alternative-gone:" + str(lid))
        elif current_hash != recorded_hash:
            reasons.append("alternative-changed:" + str(lid))
    if str(record.get("review_policy_version") or REVIEW_POLICY_VERSION) \
            != REVIEW_POLICY_VERSION:
        reasons.append("policy-changed")
    return {"status": "needs-recheck" if reasons else "current",
            "reason_codes": reasons, "previous_record": record}
