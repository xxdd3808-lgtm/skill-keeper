#!/usr/bin/env python3
"""skill-keeper v2 安全删除 CLI:只有"计划 → 确认 → 执行"两阶段,绝不接受任意目录名。

用法:
  python3 scripts/remove_skill.py plan --instance-id <instance_id> [--instance-id ...] --reason <理由>
  python3 scripts/remove_skill.py apply <plan_id> --digest <digest> --confirm

计划 30 分钟有效;执行前强制备份,验证失败自动回滚;所有结果写入 data/audit-v2.jsonl。
旧式 `remove_skill.py <目录名>` 已停用:只打印迁移说明并以退出码 2 结束,绝不删除。
"""
import argparse, json, os, shlex, sys
from pathlib import Path

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from scripts.core.audit import read_audit                    # noqa: E402
from scripts.core.changes import (ChangeContext, ChangeError,  # noqa: E402
                                  apply_plan, create_remove_plan)
from scripts.core.io import load_json_checked                 # noqa: E402


def default_context() -> ChangeContext:
    data_dir = Path(os.environ.get("SKILL_KEEPER_DATA") or os.path.join(BASE, "data"))
    return ChangeContext(
        data_dir=data_dir,
        plans_dir=data_dir / "change-plans",
        backup_dir=os.path.join(BASE, "backups"),
        audit_path=data_dir / "audit-v2.jsonl",
        lock_path=data_dir / ".change.lock",
        load_inventory=_load_inventory)


def _load_inventory():
    data_dir = Path(os.environ.get("SKILL_KEEPER_DATA") or os.path.join(BASE, "data"))
    inv, issues = load_json_checked(data_dir / "inventory.json", {})
    if issues or not isinstance(inv, dict) or not inv.get("instances"):
        raise ChangeError("inventory 缺失或为空,先跑 scan.py")
    return inv


def cmd_plan(args):
    inventory = _load_inventory()
    try:
        plan = create_remove_plan(args.instance_id, inventory, args.reason, default_context().plans_dir)
    except ChangeError as e:
        print("🛑 " + str(e))
        return 1
    print("📋 删除计划已生成(不可变,30 分钟内有效):")
    print("   plan_id: " + plan.plan_id)
    print("   digest : " + plan.digest)
    print("   摘要   : " + plan.summary)
    print("   过期   : " + plan.expires_at)
    cmd = [sys.executable, os.path.join(BASE, "scripts", "remove_skill.py"),
           "apply", plan.plan_id, "--digest", plan.digest, "--confirm"]
    print("   确认执行:")
    print("   " + shlex.join(cmd))
    return 0


def cmd_apply(args):
    ctx = default_context()
    try:
        result = apply_plan(args.plan_id, args.digest, args.confirm, ctx)
    except ChangeError as e:
        print("🛑 " + str(e))
        print("   审计详情: " + str(ctx.audit_path))
        return 1
    print("✅ 已执行。备份: {}(可随时恢复)".format(result["backup_path"]))
    print("   请重跑 scan.py 刷新盘点。最近审计:")
    for row in read_audit(ctx.audit_path)[-3:]:
        print("   - [{}] {} {} {}".format(row.get("status"), row.get("action_id"),
                                          row.get("action"), row.get("rollback_status") or ""))
    return 0


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0 if argv else 1)
    if argv[0] not in ("plan", "apply"):
        # 旧式任意目录名用法:一律停用,绝不删除
        print("🛑 v2 已停用「按目录名直接删除」的旧用法(参数 {!r})。".format(argv[0][:40]))
        print("   现在必须两步:")
        print("   1) remove_skill.py plan --instance-id <instance_id> --reason <理由>")
        print("   2) remove_skill.py apply <plan_id> --digest <digest> --confirm")
        print("   instance_id 在 data/inventory.json 的 instances 里;先跑 scan.py。")
        sys.exit(2)

    ap = argparse.ArgumentParser(prog="remove_skill.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_plan = sub.add_parser("plan", help="生成删除计划")
    p_plan.add_argument("--instance-id", action="append", required=True,
                        help="inventory 里的稳定实例 ID,可重复传多个")
    p_plan.add_argument("--reason", required=True)
    p_apply = sub.add_parser("apply", help="确认执行计划")
    p_apply.add_argument("plan_id")
    p_apply.add_argument("--digest", required=True)
    p_apply.add_argument("--confirm", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "plan":
        sys.exit(cmd_plan(args))
    if args.cmd == "apply":
        sys.exit(cmd_apply(args))


if __name__ == "__main__":
    main()
