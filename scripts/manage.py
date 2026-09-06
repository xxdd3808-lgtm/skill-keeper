#!/usr/bin/env python3
"""skill-keeper 管理入口(Task 7):plan / vet / apply / status / recover / rescan。

与网页 API 共用 scripts/core/service.py 与同一 RuntimePaths;真实资产变更仍走
不可变计划 → digest 确认 → 备份 → 执行 → 验证/回滚 → 审计的引擎闭环。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from scripts.core.changes import ChangeError, recover_transaction, read_transaction  # noqa: E402
from scripts.core.io import load_json_checked  # noqa: E402
from scripts.core.runtime import RuntimePaths, publish_snapshot  # noqa: E402
from scripts.core.service import AppService  # noqa: E402


def _paths(args):
    return RuntimePaths(
        data_dir=getattr(args, "data_dir", None) or os.environ.get("SKILL_KEEPER_DATA"),
        staging_dir=getattr(args, "staging_dir", None) or os.environ.get("SKILL_KEEPER_STAGING"),
        backup_dir=getattr(args, "backup_dir", None))


def _emit(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=1))


def cmd_plan(args):
    payload = {"instance_ids": args.instance_id, "reason": args.reason,
               "backup_id": args.backup_id, "instance_id": args.instance}
    if args.action == "update" and not args.instance:
        raise SystemExit("update 计划需要 --instance <instance_id>")
    result = AppService(_paths(args)).plan_action(args.action, payload)
    _emit(result)
    print("确认执行: manage.py apply {} --digest {} --confirm".format(
        result["plan_id"], result["digest"]), file=sys.stderr)
    return 0


def cmd_apply(args):
    if args.confirm is not True:
        raise SystemExit("confirm 必须是布尔确认(--confirm)")
    result = AppService(_paths(args)).apply_action(args.plan_id, args.digest,
                                                   True, args.accept_warning)
    _emit(result)
    return 0


def cmd_vet(args):
    """候选安检记账(F09):绑定指定计划;网页与 CLI 走同一服务入口。"""
    result = AppService(_paths(args)).vet_candidate(args.plan_id, args.verdict,
                                                    list(args.evidence or []))
    _emit({"ok": True, "vet": result})
    print("下一步: manage.py apply {} --digest <digest> --confirm".format(args.plan_id),
          file=sys.stderr)
    return 0


def cmd_status(args):
    paths = _paths(args)
    state = read_transaction(args.plan_id, _ctx(paths))
    if state is None:
        plan_path = paths.data_dir / "change-plans" / (args.plan_id + ".json")
        row, issues = load_json_checked(plan_path, {})
        if issues or not isinstance(row, dict):
            _emit({"ok": False, "error": "没有该计划的事务或计划不存在"})
            return 2
        _emit({"ok": True, "plan": row, "transaction": None})
        return 0
    _emit({"ok": True, "transaction": state})
    return 0


def cmd_recover(args):
    result = recover_transaction(args.plan_id, _ctx(_paths(args)))
    _emit({"ok": True, "phase": result.get("phase"),
           "cleanup_pending": result.get("cleanup_pending", [])})
    return 0


def cmd_rescan(args):
    result = publish_snapshot(_paths(args))
    _emit(result)
    return 0 if result.get("ok") else 2


def _ctx(paths):
    from scripts.core.changes import ChangeContext
    return ChangeContext(load_inventory=None, **paths.engine_kwargs())


def main(argv=None):
    ap = argparse.ArgumentParser(description="skill-keeper 管理 CLI(plan/apply/status/recover/rescan)")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--staging-dir", default=None)
    ap.add_argument("--backup-dir", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="生成计划")
    p_plan.add_argument("action", choices=["remove", "update", "restore"])
    p_plan.add_argument("--instance-id", dest="instance_id", action="append", default=[])
    p_plan.add_argument("--reason", default="")
    p_plan.add_argument("--backup-id", dest="backup_id", default="")
    p_plan.add_argument("--instance", default="", help="update 用单实例 ID")
    p_plan.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p_plan.set_defaults(func=cmd_plan)

    p_apply = sub.add_parser("apply", help="确认执行计划")
    p_apply.add_argument("plan_id")
    p_apply.add_argument("--digest", required=True)
    p_apply.add_argument("--confirm", action="store_true")
    p_apply.add_argument("--accept-warning", action="store_true")
    p_apply.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p_apply.set_defaults(func=cmd_apply)

    p_vet = sub.add_parser("vet", help="候选安检记账(绑定计划;update 计划应用前必做)")
    p_vet.add_argument("plan_id")
    p_vet.add_argument("--verdict", required=True, choices=["safe", "warning"],
                       help="safe=无风险;warning=有风险,应用时还需 --accept-warning")
    p_vet.add_argument("--evidence", action="append", default=[],
                       help="可核查依据(来源/阅读记录/对比结论),可重复")
    p_vet.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p_vet.set_defaults(func=cmd_vet)

    p_status = sub.add_parser("status", help="查看计划/事务状态")
    p_status.add_argument("plan_id")
    p_status.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p_status.set_defaults(func=cmd_status)

    p_rec = sub.add_parser("recover", help="恢复中断事务的原状态")
    p_rec.add_argument("plan_id")
    p_rec.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p_rec.set_defaults(func=cmd_recover)

    p_scan = sub.add_parser("rescan", help="重跑扫描并刷新报告")
    p_scan.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p_scan.set_defaults(func=cmd_rescan)

    args = ap.parse_args(list(sys.argv[1:]) if argv is None else list(argv))
    try:
        return args.func(args) or 0
    except ChangeError as e:
        _emit({"ok": False, "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
