"""应用服务层(Task 7,F08):CLI 与网页共用的 plan/apply 入口。

引擎是唯一的资产写入口;动作后发布新快照;文件事务成功与报告刷新失败
用 snapshot_status 区分,绝不把"提交成功报告过期"谎报成普通失败。
"""
import re
import threading

from .changes import (ChangeContext, ChangeError, apply_plan, create_remove_plan,
                      create_restore_plan, create_update_plan)
from .io import load_json_checked
from .provenance import load_user_config
from .runtime import publish_snapshot


class AppService:
    """一个 data 目录一个服务实例;进程内锁 + 引擎文件锁双重防并发。"""

    def __init__(self, paths):
        self.paths = paths
        self._process_lock = threading.Lock()

    def _context(self):
        kwargs = self.paths.engine_kwargs()
        data_dir = kwargs["data_dir"]

        def load_inventory():
            inv, issues = load_json_checked(data_dir / "inventory.json", {})
            if issues or not isinstance(inv, dict):
                raise ChangeError("inventory 缺失或损坏,先重跑扫描")
            # instances 为空是合法状态(例如唯一 skill 已删除后要恢复):不视为错误
            return inv

        return ChangeContext(load_inventory=load_inventory, **kwargs)

    # ---------- plan ----------
    def plan_action(self, action, payload) -> dict:
        action = str(action or "").strip()
        payload = dict(payload or {})
        known = load_user_config(self.paths.data_dir)
        ctx = self._context()
        with self._process_lock:
            if action == "remove":
                ids = payload.get("instance_ids") or payload.get("instance_id") or []
                if isinstance(ids, str):
                    ids = [ids]
                plan = create_remove_plan(ids, ctx.load_inventory(),
                                          str(payload.get("reason") or ""),
                                          ctx.plans_dir, known_sources=known)
            elif action == "update":
                plan = self._plan_update(payload, known, ctx)
            elif action == "restore":
                backup_id = str(payload.get("backup_id") or "")
                if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", backup_id):
                    raise ChangeError("backup_id 格式不合法")
                plan = create_restore_plan(backup_id, self.paths.backup_dir, ctx.plans_dir)
            else:
                raise ChangeError("action 必须是 remove|restore|update")
        row = plan.to_dict()
        row["ok"] = True
        return row

    def _plan_update(self, payload, known, ctx):
        iid = str(payload.get("instance_id") or "")
        updates, _ = load_json_checked(self.paths.data_dir / "updates.json", {})
        hit = None
        for d in (updates or {}).get("differs", []) if isinstance(updates, dict) else []:
            if d.get("instance_id") == iid and d.get("staging_path"):
                hit = d
                break
        if not hit:
            raise ChangeError("该实例没有已暂存的候选更新(先跑 check_updates)")
        snapshot = {"instance_id": iid, "staging_path": hit["staging_path"],
                    "candidate_hash": hit.get("candidate_hash"), "repo": hit.get("repo"),
                    "source": "github", "source_dir": hit.get("source_dir") or "",
                    "commit_sha": hit.get("commit_sha")}
        return create_update_plan(iid, snapshot, ctx.load_inventory(), ctx.plans_dir,
                                  known_sources=known)

    # ---------- apply ----------
    def apply_action(self, plan_id, digest, confirm, accept_warning=False) -> dict:
        ctx = self._context()
        with self._process_lock:
            result = apply_plan(str(plan_id), str(digest), confirm, ctx,
                                accept_warning=accept_warning is True)
        # 提交后刷新快照;刷新失败不改变事务事实,只标注报告过期
        snap = publish_snapshot(self.paths)
        result["snapshot_status"] = snap.get("status")
        result["snapshot_id"] = snap.get("snapshot_id")
        if not snap.get("ok"):
            result["message"] = "变更已完成,报告刷新失败(附属状态待修复): " + str(snap.get("error"))
        else:
            result["message"] = "已执行: " + str(result.get("action", ""))
        return result
