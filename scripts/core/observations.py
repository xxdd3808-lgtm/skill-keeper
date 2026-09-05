"""观察完整性(Task 4,F05):加载上下文评估与安装回执证据。

- evaluate_load:discovered / eligible / confirmed / unknown 分层——
  eligible 只是"位置在客户端读取集合内"的推断;没有直接运行时证据时
  confirmed 保持未知,绝不把 eligible 复制过去;
- load_receipt_evidence:安装回执只按字段白名单读取;位置不明确或无法
  关联内容时保持候选,不自动升级为官方事实。
"""
import os
from pathlib import Path

from .clients import load_rules


def _workspace_of(loc):
    """工作区上下文标识:workspace 位置按路径各自成上下文;全局位置为 None。"""
    if str(loc.get("kind")) != "workspace":
        return None
    return str(loc.get("path"))


def evaluate_load(instances, locations, client, workspace=None) -> dict:
    """评估某客户端的加载上下文。

    workspace=None 表示全局上下文(不选中任何项目):workspace 位置不参与;
    workspace=<路径> 表示该项目上下文:只看属于它的 workspace 位置 + 全局位置。
    返回 discovered/eligible/confirmed/unknown 计数、duplicates 与 rule_evidence。
    """
    locs = [l for l in locations or []]
    if workspace is None:
        scoped = [l for l in locs if _workspace_of(l) is None]
    else:
        scoped = [l for l in locs
                  if _workspace_of(l) is None or os.path.abspath(_workspace_of(l))
                  == os.path.abspath(str(workspace))]
    eligible_loc_ids = {str(l.get("location_id")) for l in scoped
                        if load_rules.location_in_client(l, client)}
    discovered = [i for i in instances or []
                  if i.get("is_skill") and i.get("location_id") in {str(l.get("location_id")) for l in scoped}]
    eligible = [i for i in discovered if i.get("location_id") in eligible_loc_ids]
    # confirmed 需要直接运行时启用证据(回执/启用记录);当前数据没有 → 保持未知
    confirmed = [i for i in eligible if i.get("load_confirmed") is True]
    unknown = [i for i in eligible if i.get("load_confirmed") is not True]
    by_name = {}
    for i in eligible:
        by_name.setdefault(str(i.get("logical_name")), []).append(i)
    duplicates = [{"name": name, "instance_ids": sorted(x["instance_id"] for x in rows),
                   "contexts": sorted({_workspace_of(l) or "global" for l in scoped
                                       if l.get("location_id") in
                                       {r["location_id"] for r in rows}})}
                  for name, rows in sorted(by_name.items()) if len(rows) > 1]
    # 全局上下文里,不同工作区的同名技能不构成重复
    if workspace is None:
        duplicates = [d for d in duplicates
                      if not all(ctx != "global" for ctx in d["contexts"])]
    return {
        "client": client,
        "workspace": workspace,
        "discovered": len(discovered),
        "eligible": len(eligible),
        "confirmed": len(confirmed),
        "unknown": len(unknown),
        "duplicates": duplicates,
        "rule_evidence": load_rules.rule_evidence(client),
    }


def load_receipt_evidence(home, locations) -> dict:
    """按位置收集安装回执证据:只返回白名单字段,秘密字段永不读取或返回。

    返回 {receipts: {目录名: [白名单行]}, inferred: true, sources: [...]};
    matched=false 的行保持候选证据,不升级为官方事实。
    """
    home = Path(home)
    receipts = {}
    sources = []
    accio_accounts = home / ".accio/accounts"
    if accio_accounts.is_dir():
        from .clients.accio import accio_installed_entries
        sources.append("accio-installed-json")
        for account_dir in sorted(accio_accounts.iterdir()):
            if not account_dir.is_dir():
                continue
            skills = account_dir / "skills"
            if not skills.is_dir():
                continue
            wanted = [str(l.get("path")) for l in locations or []
                      if str(l.get("path")) == str(skills)]
            if not wanted:
                continue
            for entry in accio_installed_entries(account_dir):
                name = str(entry.get("name") or "")
                if not name:
                    continue
                row = dict(entry)
                row["matched"] = True     # 内容级匹配由 provenance 阶段复核
                row["status"] = "candidate"
                receipts.setdefault(name, []).append(row)
    return {"receipts": receipts, "sources": sources, "inferred": True}
