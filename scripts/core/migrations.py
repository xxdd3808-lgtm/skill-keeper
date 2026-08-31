"""v1 → v2 运行时数据迁移与历史备份检视(设计 §12)。

规则:
- 个人配置(groups / self-built / known-sources / ignore / workspace-locations)原样保留,
  一个字节都不动;
- 迁移前把旧运行时 JSON 原子备份到 data/migrations/<时间戳>/(gitignored);
- v1 安检台账(vetted.json)按新指纹规则一律降级 needs-recheck,历史 note/日期保留;
- v1 updates.json 失效重建,不把旧结论带进 v2 报告;
- 旧格式备份只检视(重复成员、缺位置 manifest 等限制如实列出),绝不自动恢复。
"""
import os
import shutil
import time
from pathlib import Path

from .backup import BackupError, inspect_legacy_backup
from .io import atomic_write_json, load_json_checked

PERSONAL_CONFIGS = ("groups.json", "self-built.txt", "known-sources.json",
                    "ignore.json", "workspace-locations.txt", "client-locations.json")
RUNTIME_JSONS = ("vetted.json", "updates.json", "inventory.json", "inventory-last.json")


def migrate_runtime_state(data_dir, inventory=None) -> dict:
    """把 data_dir 里的 v1 运行时产物迁到 v2;返回结构化结果供报告/汇报使用。"""
    data_dir = Path(data_dir)
    result = {"migrated": [], "backup_dir": None, "vetting": {},
              "configs_preserved": [c for c in PERSONAL_CONFIGS
                                    if (data_dir / c).exists()]}
    if not data_dir.is_dir():
        return result

    # 1) 原子备份旧运行时 JSON(绝不覆盖已存在的迁移备份)
    ts = time.strftime("%Y%m%d-%H%M%S")
    backed = []
    for name in RUNTIME_JSONS:
        src = data_dir / name
        if not src.is_file():
            continue
        mig = data_dir / "migrations" / ts
        mig.mkdir(parents=True, exist_ok=True)
        dst = mig / name
        if not dst.exists():
            tmp = mig / (name + ".tmp")
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            backed.append(name)
    if backed:
        result["backup_dir"] = str(data_dir / "migrations" / ts)
        result["migrated"] = backed

    # 2) v1 安检台账 → needs-recheck(完整树指纹规则变了,旧结论不能再当"已安检")
    vetted, _ = load_json_checked(data_dir / "vetted.json", {})
    vetting = {}
    if isinstance(vetted, dict):
        for name, rec in vetted.items():
            if name.startswith("_") or not isinstance(rec, dict):
                continue
            vetting[str(name)] = {
                "status": "needs-recheck",
                "previous_verdict": rec.get("verdict"),
                "note": rec.get("note"),
                "vetted_at": rec.get("vetted_at"),
                "legacy_hash": rec.get("sk_hash"),
            }
    if vetting:
        atomic_write_json(data_dir / "vetted-v2.json", {
            "schema_version": 2, "records": vetting,
            "note": "v1 安检结论已按 v2 完整树指纹规则降级为 needs-recheck;重新安检后以新结论为准",
        })
    result["vetting"] = vetting

    # 3) v1 updates.json 失效重建(已是 v2 则不动,保证幂等)
    upd, _ = load_json_checked(data_dir / "updates.json", {})
    if not isinstance(upd, dict) or upd.get("schema_version") != 2:
        atomic_write_json(data_dir / "updates.json", {
            "schema_version": 2,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "differs": [], "up_to_date": [],
            "skipped": [{"name": "-", "reason": "v1 更新结果已失效,请重跑 check_updates.py"}],
            "operational_ok": True,
        })
    return result


def inspect_legacy_cases(names, backup_dir):
    """只读检视历史删除案例的旧备份;restored 永远是 False。"""
    backup_dir = Path(backup_dir)
    rows = []
    for name in names:
        found = None
        if backup_dir.is_dir():
            cands = sorted(backup_dir.glob("removed-{}*.tar.gz".format(name))) + \
                    sorted(backup_dir.glob("*{}*.tar.gz".format(name)))
            found = cands[0] if cands else None
        entry = {"name": str(name), "file": found.name if found else None,
                 "found": bool(found), "restored": False, "legacy": True,
                 "limitations": ["未找到对应备份(如确实删过,请人工确认)"], "member_count": None}
        if found:
            try:
                info = inspect_legacy_backup(found)
                entry.update({"legacy": info["legacy"], "limitations": info["limitations"],
                              "member_count": info["member_count"]})
            except BackupError as e:
                entry["error"] = str(e)
        rows.append(entry)
    return rows
