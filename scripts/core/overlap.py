"""重复与替代候选生成(设计 §7.1 确定性层)。

只负责缩小范围:精确副本分组、可解释的分项相似度、关键词重叠的替代候选;
"是否真的可以互相替代"由大模型综合判断,本模块不下结论。
"""
import hashlib
import json
import re

from .provenance import classify_provenance

# 分项相似度权重(可解释;不给总分冒充判断)
WEIGHTS = {"name": 0.4, "description": 0.3, "body": 0.15, "trigger": 0.05, "bins": 0.05, "source_path": 0.05}

# 关键词重叠判定用的停用词(候选去噪;不影响相似度计算)
STOPWORDS = {
    "skill", "skills", "demo", "sample", "test", "testing", "tool", "tools",
    "the", "and", "for", "with", "tool", "from", "into", "this", "that", "your",
    "you", "are", "not", "but", "its", "it's", "can", "will", "should", "must",
    "all", "any", "each", "use", "used", "uses", "using", "user", "users",
    "when", "then", "than", "else", "also", "only", "more", "one", "two",
    "before", "after", "between", "within", "without", "over", "under",
    "their", "there", "here", "what", "which", "who", "how", "why", "was",
    "were", "has", "have", "had", "does", "does", "done", "make", "made",
    "name", "description", "version", "metadata", "frontmatter", "license",
    "file", "files", "data", "value", "values", "text", "list", "lists",
    "return", "returns", "new", "add", "added", "set", "get", "run", "runs",
    "based", "via", "per", "out", "non", "may", "must", "need", "needs",
    "into", "onto", "such", "same", "other", "others", "some", "both",
}
CJK_STOP = {"演示", "示例", "来源", "声称", "未知", "工具", "能力", "自带", "完全", "第三方", "一个", "使用", "可以"}

_LATIN = re.compile(r"[a-z0-9]{2,}")
_CJK = re.compile(r"[\u4e00-\u9fff]+")


def tokens(text):
    """拉丁词(≥2)+ 中文双字词元,全部小写。"""
    text = str(text or "").lower()
    out = set(_LATIN.findall(text))
    for run in _CJK.findall(text):
        if len(run) == 1:
            out.add(run)
        else:
            out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def significant_overlap(a_tokens, b_tokens):
    """双方共享的"有意义"词元:拉丁 ≥3 且不在停用表,或中文双字词不在停用表。"""
    shared = a_tokens & b_tokens
    out = []
    for t in sorted(shared):
        if _CJK.match(t) and len(t) >= 2:
            if t not in CJK_STOP:
                out.append(t)
        elif len(t) >= 3 and t not in STOPWORDS:
            out.append(t)
    return out


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def receipts_from_inventory(inventory):
    """从 inventory 位置/实例的 builtin、plugin-cache 标记生成客户端回执证据。"""
    receipts = {}
    for inst in inventory.get("instances", []):
        if inst.get("kind") in ("builtin", "plugin-cache"):
            receipts[str(inst.get("instance_id"))] = {"type": inst["kind"],
                                                      "repo": inst.get("plugin_name"),
                                                      "client": inst.get("client")}
    return receipts


def logical_status_map(inventory):
    """logical_id → {protected, source, representative(instance dict)}。"""
    receipts = receipts_from_inventory(inventory)
    inst_by_id = {i["instance_id"]: i for i in inventory.get("instances", []) if i.get("instance_id")}
    out = {}
    known_sources = {}
    claim = inventory.get("known_sources")
    if isinstance(claim, dict):
        known_sources = claim
    for logical in inventory.get("logical_skills", []):
        insts = [inst_by_id[i] for i in logical.get("instance_ids", []) if i in inst_by_id]
        if not insts:
            continue
        rep = sorted(insts, key=lambda x: (not x.get("mutable"), x.get("is_symlink", False),
                                           x.get("load_priority", 9), x["instance_id"]))[0]
        source = classify_provenance(rep, receipts, known_sources)
        out[logical.get("logical_id")] = {
            "protected": source.get("class") == "protected",
            "source": source,
            "representative": rep,
            "instances": insts,
        }
    return out


def exact_duplicate_groups(inventory):
    """完整指纹一致的精确副本组(同一内容装了多份,占上下文)。"""
    groups = {}
    for inst in inventory.get("instances", []):
        if inst.get("is_skill") and inst.get("tree_hash"):
            groups.setdefault(inst["tree_hash"], []).append(inst)
    rows = []
    for tree_hash, insts in sorted(groups.items()):
        if len(insts) < 2:
            continue
        rows.append({"tree_hash": tree_hash, "name": insts[0].get("logical_name", ""),
                     "instance_ids": sorted(i["instance_id"] for i in insts),
                     "locations": sorted({i.get("display_path", "") for i in insts})})
    return rows


def _content_tokens(logical, inst_by_id):
    """name/description 词元(含实例描述)+ SKILL.md 正文词元(正文只在本地可读时参与)。"""
    name_t = tokens(logical.get("name"))
    desc_t = tokens(logical.get("description"))
    body_t = set()
    for inst_id in logical.get("instance_ids", []):
        inst = inst_by_id.get(inst_id, {})
        desc_t |= tokens(inst.get("description"))
        sk = os_path_join(inst.get("real_path"), "SKILL.md")
        if sk:
            try:
                body_t |= tokens(strip_frontmatter(read_head(sk)))
            except OSError:
                continue
    return name_t, desc_t, body_t


def os_path_join(base, name):
    if not base or not name:
        return None
    import os
    return os.path.join(base, name)


def read_head(path, limit=8000):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read(limit)


def strip_frontmatter(text):
    """去掉 SKILL.md 的 frontmatter 块,正文词元不把 name/description 等键名算进去。"""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    rest = text[end + 4:]
    return rest.lstrip("\n")


def pair_breakdown(a, b, tok_a, tok_b):
    (an, ad, ab), (bn, bd, bb) = tok_a, tok_b
    return {
        "name": round(_jaccard(an, bn), 3),
        "description": round(_jaccard(ad, bd), 3),
        "body": round(_jaccard(ab, bb), 3),
        "trigger": 1.0 if a.get("trigger") == b.get("trigger") else 0.0,
        "bins": round(_jaccard(set(a.get("bins", [])), set(b.get("bins", []))), 3),
        "source_path": 1.0 if (a.get("repo") and a.get("repo") == b.get("repo")) else 0.0,
    }


def pair_score(breakdown):
    return round(sum(WEIGHTS[k] * v for k, v in breakdown.items()), 3)


def candidate_pairs(inventory, min_similarity=0.32):
    """第三方↔第三方(含一方受保护)的相似候选对;只生成候选,不下结论。"""
    logicals = inventory.get("logical_skills", [])
    inst_by_id = {i["instance_id"]: i for i in inventory.get("instances", [])}
    status = logical_status_map(inventory)
    toks = {}
    for lg in logicals:
        insts = {"instance_ids": lg.get("instance_ids", [])}
        toks[lg.get("logical_id")] = _content_tokens(
            {**lg, "instance_ids": insts["instance_ids"]}, inst_by_id)
    rows = []
    for i in range(len(logicals)):
        for j in range(i + 1, len(logicals)):
            a, b = logicals[i], logicals[j]
            breakdown = pair_breakdown(a, b, toks[a.get("logical_id")], toks[b.get("logical_id")])
            score = pair_score(breakdown)
            if score < min_similarity:
                continue
            rows.append({
                "a": a.get("logical_id"), "a_name": a.get("name"),
                "b": b.get("logical_id"), "b_name": b.get("name"),
                "a_protected": status.get(a.get("logical_id"), {}).get("protected", False),
                "b_protected": status.get(b.get("logical_id"), {}).get("protected", False),
                "score": score, "breakdown": breakdown,
            })
    rows.sort(key=lambda x: -x["score"])
    return rows


def alternative_candidates(inventory, target_logical_id, min_similarity=0.32):
    """可能与目标重叠的其他逻辑 skill(含受保护类):有区分度的关键词重叠或相似度达标。

    区分度 = 共享词元的文档频率 ≤ 35%(常见的通用词如"支持/自动/api"不算重叠),
    保证这一层真的在缩小范围。
    """
    logicals = inventory.get("logical_skills", [])
    if not logicals:
        return []
    inst_by_id = {i["instance_id"]: i for i in inventory.get("instances", [])}
    status = logical_status_map(inventory)
    tok_map = {}
    for lg in logicals:
        tok_map[lg.get("logical_id")] = _content_tokens(
            {**lg, "instance_ids": lg.get("instance_ids", [])}, inst_by_id)
    total = len(logicals)
    # 文档频率只统计 name/description 词元:正文词元天然嘈杂,只参与相似度打分
    df = {}
    for name_t, desc_t, _body_t in tok_map.values():
        for tok in set().union(name_t, desc_t):
            df[tok] = df.get(tok, 0) + 1
    max_df = max(2, int(total * 0.15))
    rare_df = max(2, int(total * 0.05))

    target = next((lg for lg in logicals if lg.get("logical_id") == target_logical_id), None)
    if target is None:
        return []
    t_name, t_desc, t_body = tok_map[target.get("logical_id")]
    t_all = t_name | t_desc
    t_body = t_body
    out = []
    for lg in logicals:
        if lg.get("logical_id") == target_logical_id:
            continue
        l_name, l_desc, l_body = tok_map[lg.get("logical_id")]
        l_all = l_name | l_desc
        breakdown = pair_breakdown(target, lg, tok_map[target.get("logical_id")], tok_map[lg.get("logical_id")])
        score = pair_score(breakdown)
        shared = [t for t in significant_overlap(t_all, l_all) if df.get(t, 0) <= max_df]
        rare = [t for t in shared if df.get(t, 0) <= rare_df]
        # 单个普通共享词不算替代信号;要:≥2 个有区分度词元,或 1 个极稀有词元,或相似度达标
        if score >= min_similarity or len(shared) >= 2 or rare:
            reasons = []
            if shared:
                detail = ",".join("{}(df{})".format(t, df.get(t, 0)) for t in (rare or shared)[:3])
                reasons.append("keyword-overlap:" + detail)
            reasons.append("similarity:" + str(score))
            st = status.get(lg.get("logical_id"), {})
            out.append({"logical_id": lg.get("logical_id"), "name": lg.get("name"),
                        "protected": st.get("protected", False),
                        "instance_id": (st.get("representative") or {}).get("instance_id"),
                        "score": score, "reasons": reasons})
    out.sort(key=lambda x: (-x["score"], x["name"]))
    return out
