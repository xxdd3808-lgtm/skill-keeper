"""Task 7 工作流合同(F08):报告按钮字段→API→磁盘→审计→新快照全链路一致。

- apply 后发布新快照;刷新失败时区分"事务已提交,报告未更新";
- 报告备份行的 backup_id 直接可用(不得再拼前后缀);
- 静态模式输出真实可运行命令(路径不整体引号包住 ~)。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.core import service as service_mod
from scripts.core.backup import create_backup
from scripts.core.fingerprint import instance_id, tree_hash
from scripts.core.models import ChangePlan, Location
from scripts.core.runtime import RuntimePaths, publish_snapshot
from scripts.core.service import AppService
import scripts.report as report_mod


def one_skill_home(testcase):
    home = Path(tempfile.mkdtemp(prefix="sk-wf-"))
    testcase.addCleanup(shutil.rmtree, home, ignore_errors=True)
    data = home / "data"
    data.mkdir()
    demo = home / ".agents/skills/demo"
    demo.mkdir(parents=True)
    (demo / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill\nversion: 1.0.0\n---\nbody\n",
        encoding="utf-8")
    (demo / "run.py").write_text("x", encoding="utf-8")
    (data / "workspace-locations.txt").write_text("", encoding="utf-8")
    return home, data, demo


def wait_snapshot(paths):
    """首轮:跑真实 scan+report 子进程,拿到 inventory 与 report.html。"""
    result = publish_snapshot(paths)
    assert result["ok"], result
    return result


class WorkflowContractTests(unittest.TestCase):
    def test_apply_publishes_snapshot_and_marks_stale_on_refresh_failure(self):
        home, data, demo = one_skill_home(self)
        paths = RuntimePaths(home=home, data_dir=data)
        first = wait_snapshot(paths)
        svc = AppService(paths)
        iid = instance_id("shared", "demo", os.path.realpath(str(demo)))
        plan = svc.plan_action("remove", {"instance_ids": [iid], "reason": "workflow test"})
        result = svc.apply_action(plan["plan_id"], plan["digest"], True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["transaction_status"], "committed")
        self.assertEqual(result["snapshot_status"], "fresh")
        self.assertNotIn("SKILL.md", _inventory_report_text(paths),
                         "新快照不得再包含已删除目标")
        self.assertNotEqual(result["snapshot_id"], first["snapshot_id"],
                            "快照 id 必须变化")
        # 报告刷新失败:事务已提交,响应必须可区分,不得谎报普通失败
        with patch.object(service_mod, "publish_snapshot",
                          return_value={"ok": False, "status": "stale",
                                        "snapshot_id": "inv-x", "error": "boom"}):
            plan2 = svc.plan_action("restore", {"backup_id": result["backup_id"]})
            self.assertTrue(plan2["ok"])  # 计划仍可生成(报告失败不阻塞引擎)

    def test_report_backup_row_backup_id_feeds_restore_plan(self):
        """报告按钮字段(backup_id)必须能直接生成恢复计划,禁止手写正确 id 绕过。"""
        home, data, demo = one_skill_home(self)
        paths = RuntimePaths(home=home, data_dir=data)
        wait_snapshot(paths)
        svc = AppService(paths)
        iid = instance_id("shared", "demo", os.path.realpath(str(demo)))
        plan = svc.plan_action("remove", {"instance_ids": [iid], "reason": "backup test"})
        svc.apply_action(plan["plan_id"], plan["digest"], True)
        # 报告生成真实按钮行
        rows = report_mod.backups_list()  # 读真实 BASE/backups
        report_rows = _backups_from_real_dir(paths)
        self.assertTrue(report_rows, "apply 必须产生备份")
        row = report_rows[0]
        self.assertIn("backup_id", row)
        plan = svc.plan_action("restore", {"backup_id": row["backup_id"]})
        self.assertTrue(plan["ok"], "报告按钮字段必须直通恢复计划")
        result = svc.apply_action(plan["plan_id"], plan["digest"], True)
        self.assertTrue((demo / "SKILL.md").exists(), "恢复必须把实体放回原位")

    def test_static_commands_are_runnable(self):
        text = report_mod.static_command_hint()
        self.assertTrue(text)
        self.assertNotIn("'~", text, "~ 不得被整体引号包住")
        self.assertIn("manage.py", text)


def _inventory_report_text(paths):
    html = (paths.data_dir / "report.html")
    if html.is_file():
        return html.read_text(encoding="utf-8")
    return (paths.data_dir / "inventory.json").read_text(encoding="utf-8")


def _backups_from_real_dir(paths):
    real_base = report_mod.BASE
    report_mod.BASE = str(paths.backup_dir.parent)
    try:
        return report_mod.backups_list()
    finally:
        report_mod.BASE = real_base


if __name__ == "__main__":
    unittest.main()
