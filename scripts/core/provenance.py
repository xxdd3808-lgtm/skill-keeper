"""来源证据合并、置信度与第三方/受保护分类(设计 §6)。

优先级:自建白名单(精确目录名/稳定 ID)> 客户端回执/manifest > known-sources 已核实
> 实例自述来源(仅候选,置信度低)。名称前缀(如 autoglm-)和 frontmatter name
永远不能换取受保护身份。
"""
import json

# 客户端回执可接受的类型;其余一律不算受保护证据
RECEIPT_TYPES = ("builtin", "plugin", "plugin-cache", "client-managed", "adapter-receipt")

# known-sources.json 里可作为"已核实来源"的类型
VERIFIED_SOURCE_TYPES = ("github", "skills.sh", "registry-volces", "registry-modelscope",
                         "registry-openharmony", "skillhub")


def classify_provenance(instance, receipts=None, known_sources=None):
    """返回 {class, type, repo, path, confidence, evidence, review_required, candidate_source?}。

    class ∈ protected | third-party;protected 不参与第三方价值审查,
    但安检(内容审查)仍然适用于所有实体。
    """
    instance = instance or {}
    receipts = receipts or {}
    known_sources = known_sources or {}
    directory = str(instance.get("directory_name") or "")
    iid = str(instance.get("instance_id") or "")

    ks = known_sources.get(directory) or known_sources.get(iid)
    if isinstance(ks, dict) and ks.get("type") == "self-built":
        return {"class": "protected", "type": "self-built", "repo": None, "path": None,
                "confidence": "high", "evidence": ["self-built-whitelist"], "review_required": False}

    receipt = receipts.get(iid) or receipts.get(directory)
    if isinstance(receipt, dict) and receipt.get("type") in RECEIPT_TYPES:
        return {"class": "protected", "type": receipt["type"],
                "repo": receipt.get("repo"), "path": receipt.get("path"),
                "confidence": "high",
                "evidence": ["adapter-receipt:" + str(receipt.get("type"))],
                "review_required": False}

    if isinstance(ks, dict) and ks.get("type") in VERIFIED_SOURCE_TYPES:
        ev = ["known-sources"]
        if ks.get("_confirmed_at"):
            ev.append("confirmed:" + str(ks["_confirmed_at"]))
        return {"class": "third-party", "type": ks["type"], "repo": ks.get("repo"),
                "path": ks.get("path"), "confidence": "high", "evidence": ev,
                "review_required": True}

    # 实例/扫描器自带的来源声明只是候选,不得自动确认为来源
    claim = instance.get("source")
    if isinstance(claim, dict) and claim.get("type") and claim.get("type") != "unknown":
        return {"class": "third-party", "type": "unknown", "repo": claim.get("repo"),
                "path": claim.get("path"), "confidence": "low",
                "evidence": ["self-declared-source(candidate)"], "review_required": True,
                "candidate_source": claim}

    return {"class": "third-party", "type": "unknown", "repo": None, "path": None,
            "confidence": "low", "evidence": ["no-source-evidence"], "review_required": True}


def search_source_candidates(skill, gh_runner):
    """GitHub 仓库搜索,只产生候选(confirmed=False, confidence=low);绝不自动确认来源。"""
    if gh_runner is None:
        return []
    name = str((skill or {}).get("logical_name") or (skill or {}).get("directory_name") or "").strip()
    if not name:
        return []
    from urllib.parse import quote
    query = "{} skill in:name skill".format(name)
    code, out = gh_runner(["search/repositories?q={}&per_page=10".format(quote(query))])
    if code != 0:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    directory = str((skill or {}).get("directory_name") or "").lower()
    cands = []
    for item in (data.get("items") or [])[:10]:
        full_name = str(item.get("full_name") or "")
        if not full_name:
            continue
        reasons = []
        score = 0.0
        repo_name = full_name.rsplit("/", 1)[-1].lower()
        if repo_name == directory or repo_name.replace("-", "") == directory.replace("-", ""):
            score += 0.5
            reasons.append("repo-name-match")
        if (item.get("description") or "") and name.lower() in str(item.get("description")).lower():
            score += 0.2
            reasons.append("description-mention")
        if item.get("pushed_at"):
            reasons.append("active:" + str(item.get("pushed_at"))[:10])
        cands.append({"repo": full_name, "score": round(score, 2), "confidence": "low",
                      "confirmed": False, "reasons": reasons})
    cands.sort(key=lambda c: -c["score"])
    return cands
