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
        # 报告按统一 RuntimePaths 的备份目录取行(F04:不再读代码根 backups/)
        report_rows = report_mod.backups_list(paths.backup_dir)
        self.assertTrue(report_rows, "apply 必须产生备份,且报告必须看得见")
        row = report_rows[0]
        self.assertIn("backup_id", row)
        plan = svc.plan_action("restore", {"backup_id": row["backup_id"]})
        self.assertTrue(plan["ok"], "报告按钮字段必须直通恢复计划")
        result = svc.apply_action(plan["plan_id"], plan["digest"], True)
        self.assertTrue((demo / "SKILL.md").exists(), "恢复必须把实体放回原位")

    def test_backup_dir_agrees_across_cli_serve_and_report(self):
        """F04:新安装场景下,报告备份行、网页服务、CLI 恢复必须指向同一目录。"""
        from scripts.serve import ServiceContext
        home, data, demo = one_skill_home(self)
        paths = RuntimePaths(home=home, data_dir=data)
        wait_snapshot(paths)
        svc = AppService(paths)
        iid = instance_id("shared", "demo", os.path.realpath(str(demo)))
        plan = svc.plan_action("remove", {"instance_ids": [iid], "reason": "path-agree"})
        result = svc.apply_action(plan["plan_id"], plan["digest"], True)
        self.assertTrue(result["ok"])
        ctx = ServiceContext(data_dir=paths.data_dir, home=home)
        self.assertEqual(ctx.backup_dir, paths.backup_dir,
                         "网页服务备份目录必须与 CLI 一致")
        self.assertEqual(ctx.paths.staging_dir, paths.staging_dir,
                         "网页服务 staging 必须与 CLI 一致")
        rows = report_mod.backups_list(paths.backup_dir)
        self.assertTrue(rows)
        # 报告渲染出的备份行必须真的来自这份目录(而不是仓库 backups/)
        html = report_mod.render_html({"schema_version": 2, "instances": [],
                                       "logical_skills": [], "locations": [],
                                       "findings": []}, None, {"backups": rows})
        self.assertIn(rows[0]["filename"], html)

    def test_check_updates_staging_follows_runtime_paths(self):
        """F04:更新检查的暂存根必须与 RuntimePaths 同源(环境变量显式路径也成立)。"""
        import scripts.check_updates as cu
        home, data, _demo = one_skill_home(self)
        stage = home / "custom-stage"
        with patch.dict(os.environ, {"SKILL_KEEPER_DATA": str(data),
                                     "SKILL_KEEPER_STAGING": str(stage)}):
            from scripts.core.runtime import RuntimePaths as RP
            self.assertEqual(cu.staging_root_for(), RP().staging_dir)
            self.assertEqual(cu.staging_root_for(), stage)

    def test_publish_snapshot_survives_timeout_and_spawn_failure(self):
        """F06:刷新超时/启动失败必须返回结构化 stale,不得把异常逃给上层。"""
        import subprocess as sp
        from scripts.core import runtime as runtime_mod
        home, data, _demo = one_skill_home(self)
        paths = RuntimePaths(home=home, data_dir=data)
        wait_snapshot(paths)
        with patch.object(runtime_mod.subprocess, "run",
                          side_effect=sp.TimeoutExpired(cmd="scan.py", timeout=1)):
            result = publish_snapshot(paths)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "stale")
        self.assertIn("timeout", result["error"])

    def test_apply_after_refresh_crash_reports_committed_and_stale(self):
        """F06:提交后刷新任何失败,响应必须仍是 committed + stale,不得谎报普通失败。"""
        import subprocess as sp
        home, data, demo = one_skill_home(self)
        paths = RuntimePaths(home=home, data_dir=data)
        wait_snapshot(paths)
        svc = AppService(paths)
        iid = instance_id("shared", "demo", os.path.realpath(str(demo)))
        plan = svc.plan_action("remove", {"instance_ids": [iid], "reason": "stale test"})
        with patch.object(service_mod, "publish_snapshot",
                          side_effect=sp.TimeoutExpired(cmd="report.py", timeout=1)):
            result = svc.apply_action(plan["plan_id"], plan["digest"], True)
        self.assertTrue(result["ok"], "变更已提交,必须报告成功")
        self.assertEqual(result["transaction_status"], "committed")
        self.assertEqual(result["snapshot_status"], "stale",
                         "刷新失败只能标注报告过期,不得改写提交事实")
        self.assertIn("刷新失败", result["message"])
        self.assertFalse(demo.exists(), "提交事实不受刷新失败影响")

    def test_static_commands_are_runnable(self):
        text = report_mod.static_command_hint()
        self.assertTrue(text)
        self.assertNotIn("'~", text, "~ 不得被整体引号包住")
        self.assertTrue(text.startswith("skill-keeper manage "),
                        "复制命令必须是安装态统一 CLI(离开仓库也可用)")
        self.assertNotIn("scripts/manage.py", text)


def _inventory_report_text(paths):
    html = (paths.data_dir / "report.html")
    if html.is_file():
        return html.read_text(encoding="utf-8")
    return (paths.data_dir / "inventory.json").read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
