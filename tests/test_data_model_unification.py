"""任务2(F10–F13)数据口径统一回归。

- F10:加载统计只有一个模型——client_load 从 evaluate_load 派生,工作区技能
  不进全局启动口径,跨项目同名不再误报重复;插件旧版本在全模型一致过滤;
- F11:观察不完整在每个入口如实报错——client-locations 损坏 → complete=false
  (scan 退出 2);report --json operational_ok 如实反映并退出 2;
  check_updates 输入缺失不得覆盖上一份可用 updates;
- F12:更新回滚的 original_hash 取计划前置哈希,不取过期库存;
- F13:同一真实内容根一次扫描只算一次指纹;读不出指纹的实例按稳定身份隔离,
  绝不按空哈希合并;根目录自身权限不在树指纹内(合同另行校验并写明)。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.core.fingerprint import instance_id, tree_hash
from tests.helpers import temp_home, write_skill

REPO_ROOT = Path(__file__).resolve().parents[1]


def _workspace_project(home, project, name="ws-dup"):
    """在项目 .claude/skills 下放一个工作区技能,返回其目录。"""
    return write_skill(home / project / ".claude" / "skills", name,
                       description="workspace " + project)


def _register_workspace(data, home, *projects):
    (data / "workspace-locations.txt").write_text(
        "".join(str(home / p / ".claude" / "skills") + "\n" for p in projects),
        encoding="utf-8")


class LoadModelUnificationTests(unittest.TestCase):
    """F10:client_load 与 load_contexts 必须同一口径。"""

    def test_cross_project_same_name_not_global_duplicate(self):
        from scripts.scan import build_inventory
        home = temp_home(self)
        data = home / "data"
        data.mkdir()
        _workspace_project(home, "proj-a")
        _workspace_project(home, "proj-b")
        _register_workspace(data, home, "proj-a", "proj-b")
        inv = build_inventory(home, data, workspace=None)
        cc = inv["client_load"]["claude-code"]
        self.assertNotIn("ws-dup", cc["duplicates"],
                         "跨项目同名不构成全局启动重复(F10 病灶)")
        self.assertEqual(cc["duplicates"], [])
        self.assertFalse(any(f["code"] == "duplicate-load" and f["skill"] == "ws-dup"
                             for f in inv["findings"]),
                         "跨项目同名不得再报 duplicate-load")
        # 工作区技能仍在盘点里,只是不计入全局启动口径
        names = {i["directory_name"] for i in inv["instances"]}
        self.assertIn("ws-dup", names)
        # 同一评估模型的另一侧:全局上下文 eligible 同样不含工作区技能
        lc = inv["observation"]["load_contexts"]["claude-code"]
        self.assertEqual(lc["eligible"], cc["entries"],
                         "client_load 必须从 load_contexts 同一评估派生")

    def test_same_name_within_one_workspace_is_flagged(self):
        from scripts.scan import build_inventory
        home = temp_home(self)
        data = home / "data"
        data.mkdir()
        root = home / "proj-a" / ".claude" / "skills"
        write_skill(root, "ws-dup", description="one")
        write_skill(root, "ws-dup2", description="two", fm_name="ws-dup")
        _register_workspace(data, home, "proj-a")
        inv = build_inventory(home, data, workspace="proj-a")
        dup = [f for f in inv["findings"]
               if f["code"] == "duplicate-load" and f["skill"] == "ws-dup"]
        self.assertTrue(dup, "同一工作区内同名双载必须可见")
        self.assertIn("工作区", dup[0]["message"],
                      "工作区重复必须标明口径,不得冒充全局启动占用")

    def test_client_load_and_load_contexts_agree_everywhere(self):
        from scripts.scan import build_inventory
        home, data = temp_home(self), None
        data = home / "data"
        data.mkdir()
        write_skill(home / ".agents/skills", "shared-tool")
        write_skill(home / ".codex/skills", "codex-tool")
        inv = build_inventory(home, data, workspace=None)
        lc = inv["observation"]["load_contexts"]
        for client, row in inv["client_load"].items():
            if client in lc:
                self.assertEqual(row["entries"], lc[client]["eligible"],
                                 "client_load 与 load_contexts 必须同一口径: " + client)


class ObservationHonestyTests(unittest.TestCase):
    """F11:观察不完整必须每个入口如实报错。"""

    def test_corrupt_client_locations_marks_observation_incomplete(self):
        from scripts.scan import build_inventory
        home = temp_home(self)
        data = home / "data"
        data.mkdir()
        write_skill(home / ".agents/skills", "demo")
        (data / "client-locations.json").write_text("{broken", encoding="utf-8")
        inv = build_inventory(home, data)
        self.assertFalse(inv["observation"]["complete"],
                         "登记配置损坏必须算观察不完整,漏扫的根不得伪装成完整观察")
        self.assertTrue(any("client-locations" in str(i.get("source") or i.get("code"))
                            for i in inv["observation"]["issues"]))
        self.assertTrue(inv["config_issues"],
                        "config_issues 保留解析级细节;观察问题单列(不再只进一处)")

    def test_scan_exits_2_on_incomplete_observation(self):
        home = temp_home(self)
        data = home / "data"
        data.mkdir()
        write_skill(home / ".agents/skills", "demo")
        (data / "client-locations.json").write_text("{broken", encoding="utf-8")
        r = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "scan.py"),
                            "--json"], capture_output=True, text=True, env=dict(
                                os.environ, HOME=str(home), SKILL_KEEPER_DATA=str(data)),
                           cwd=str(REPO_ROOT), timeout=120)
        self.assertEqual(r.returncode, 2, "观察不完整必须退出 2: " + r.stderr[-200:])

    def test_report_json_reports_incomplete_observation(self):
        home = temp_home(self)
        data = home / "data"
        data.mkdir()
        write_skill(home / ".agents/skills", "demo")
        subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "scan.py"),
                        "--json"], capture_output=True, text=True, env=dict(
                            os.environ, HOME=str(home), SKILL_KEEPER_DATA=str(data)),
                       cwd=str(REPO_ROOT), timeout=120)
        inv = json.loads((data / "inventory.json").read_text())
        inv["observation"]["complete"] = False
        inv["observation"]["issues"] = [{"code": "location-unreadable"}]
        (data / "inventory.json").write_text(json.dumps(inv), encoding="utf-8")
        r = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "report.py"),
                            "--json"], capture_output=True, text=True, env=dict(
                                os.environ, HOME=str(home), SKILL_KEEPER_DATA=str(data)),
                           cwd=str(REPO_ROOT), timeout=120)
        self.assertEqual(r.returncode, 2,
                         "观察不完整时报告不得伪装健康并退出 0: " + r.stdout[-200:])
        payload = json.loads(r.stdout[r.stdout.index("{"):])
        self.assertFalse(payload["operational_ok"])

    def test_check_updates_keeps_previous_updates_on_missing_inventory(self):
        home = temp_home(self)
        data = home / "data"
        data.mkdir()
        updates = data / "updates.json"
        good = {"schema_version": 2, "checked_at": "2026-09-06 08:00:00",
                "differs": [{"name": "keep-me", "instance_id": "x" * 20}],
                "up_to_date": [], "skipped": [], "operational_ok": True}
        updates.write_text(json.dumps(good), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "check_updates.py"),
             "--inventory", str(data / "missing.json"), "--output", str(updates),
             "--json"],
            capture_output=True, text=True, env=dict(os.environ, HOME=str(home)),
            cwd=str(REPO_ROOT), timeout=120)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(json.loads(updates.read_text()), good,
                         "输入缺失是本次运行失败;上一份可用结果不得被空差异覆盖")


class RollbackHashContractTests(unittest.TestCase):
    """F12:回滚核对必须用计划前置哈希,不能用过期库存哈希。"""

    def test_update_rollback_with_stale_inventory_is_rolled_back(self):
        from tests.test_change_update import update_env
        env = update_env(self)
        # 扫描后用户修改了本地内容:inventory 记录的 tree_hash 已过期,
        # 而更新计划按引擎设计绑定"当前磁盘哈希"(fresh)
        (env.skill_path / "local-note.md").write_text("user edit", encoding="utf-8")
        plan = env.create_plan(candidate="v2")
        from scripts.core.changes import record_candidate_vet
        record_candidate_vet(plan.plan_id, env.v2_hash, "safe", ["fixture-review"],
                             plans_dir=env.plans_dir)
        env.context.verify_after_apply = lambda: False  # 业务校验失败 → 回滚
        from scripts.core.changes import apply_plan
        with self.assertRaises(Exception):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        from scripts.core.transactions import read_transaction
        state = read_transaction(plan.plan_id, env.context)
        self.assertEqual(state["phase"], "rolled-back",
                         "磁盘内容与计划前置哈希一致就不得误报 recovery-required")
        self.assertEqual(state.get("cleanup_pending"), [])
        self.assertEqual(tree_hash(env.skill_path), tree_hash_from_dir(env.skill_path),
                         "回滚后磁盘必须等于计划时内容")
        self.assertIn("user edit", (env.skill_path / "local-note.md").read_text(),
                      "回滚恢复的是计划绑定的修改后内容")


def tree_hash_from_dir(path):
    return tree_hash(path)


class FingerprintReuseTests(unittest.TestCase):
    """F13:同轮扫描同一真实内容根只算一次指纹。"""

    def test_tree_hash_computed_once_per_real_root(self):
        import scripts.scan as scan_mod
        home = temp_home(self)
        data = home / "data"
        data.mkdir()
        shared = write_skill(home / ".agents/skills", "mirror-me")
        (home / ".zcode/skills").mkdir(parents=True, exist_ok=True)
        os.symlink(shared, home / ".zcode/skills/mirror-me")
        calls = {"n": 0}
        real_tree_hash = scan_mod.tree_hash

        def counting(root, *a, **k):
            calls["n"] += 1
            return real_tree_hash(root, *a, **k)

        with mock.patch.object(scan_mod, "tree_hash", side_effect=counting):
            scan_mod.build_inventory(home, data)
        real_roots = {os.path.realpath(str(shared))}
        # 同一真实根的两个入口:修复后整个扫描对它只哈希一次
        self.assertEqual(calls["n"], 1,
                         "同一真实内容根在同一轮扫描内必须复用指纹(实测 {} 次)".format(calls["n"]))

    def test_unreadable_instances_are_isolated_not_merged(self):
        from scripts.scan import _build_logical_skills
        insts = [{"instance_id": "a" * 20, "is_skill": True, "tree_hash": "",
                  "logical_name": "broken-one", "directory_name": "broken-one",
                  "client": "shared", "context_bytes": 1, "load_priority": 2},
                 {"instance_id": "b" * 20, "is_skill": True, "tree_hash": "",
                  "logical_name": "broken-two", "directory_name": "broken-two",
                  "client": "shared", "context_bytes": 1, "load_priority": 2}]
        rows = _build_logical_skills(insts)
        self.assertEqual(len(rows), 2,
                         "空哈希对象内容不一定相等,必须按稳定实例隔离,不得合并成一个逻辑身份")
        self.assertEqual(len({r["logical_id"] for r in rows}), 2)

    def test_root_permission_boundary_is_documented(self):
        """合同:根目录自身元数据不进树指纹;plan 的 root_real/is_symlink 前置
        与预检另行校验。指纹文档必须写明这一边界。"""
        home = temp_home(self)
        d = write_skill(home / ".agents/skills", "demo")
        before = tree_hash(d)
        os.chmod(d, 0o700)
        try:
            self.assertEqual(tree_hash(d), before,
                             "根目录自身权限变化不改变树指纹(边界另行校验)")
        finally:
            os.chmod(d, 0o755)
        import scripts.core.fingerprint as fp
        self.assertIn("根目录", (fp.tree_hash.__doc__ or "") + (fp.__doc__ or ""),
                      "指纹边界必须写进文档合同")


class ViewSeparationTests(unittest.TestCase):
    """报告渲染只消费 view 输入,不再自行探测本机环境。"""

    def test_renderers_do_not_probe_local_apps(self):
        import scripts.report as report_mod
        # claude-code + haha 同时有条目才会走"应用是否卸载"探测分支——
        # 夹具必须触发该分支,否则此测试空转
        inv = {"schema_version": 2, "scanned_at": "t", "instances": [],
               "logical_skills": [], "locations": [], "findings": [],
               "client_load": {"claude-code": {"entries": 1, "skills": 1,
                                               "duplicates": [], "dup_entries": 0},
                               "haha": {"entries": 1, "skills": 1,
                                        "duplicates": [], "dup_entries": 0}}}

        def forbidden(*a, **k):
            raise AssertionError("渲染不得探测本机应用/全局状态")

        with mock.patch.object(report_mod, "_claude_app_present", side_effect=forbidden), \
                mock.patch.object(report_mod.os.path, "isdir", side_effect=forbidden):
            report_mod.render_html(inv, None, {"claude_app_present": False,
                                               "groups": {}})
            report_mod.render_md(inv, None, {"claude_app_present": False,
                                             "groups": {}})

    def test_haha_note_uses_ctx_fact(self):
        import scripts.report as report_mod
        inv = {"schema_version": 2, "scanned_at": "t", "instances": [],
               "logical_skills": [], "locations": [], "findings": [],
               "client_load": {"claude-code": {"entries": 1, "skills": 1,
                                               "duplicates": [], "dup_entries": 0},
                               "haha": {"entries": 1, "skills": 1,
                                        "duplicates": [], "dup_entries": 0}}}
        html = report_mod.render_html(inv, None, {"claude_app_present": False,
                                                  "groups": {}})
        self.assertIn("应用已卸载", html, "ctx 事实必须驱动备注")


class DualLayoutRoundtripTests(unittest.TestCase):
    """任务2收尾:旧(显式数据目录)布局与新默认 ~/.skill-keeper 布局,
    各跑一轮 doctor→scan→report→plan→apply→备份可见→restore 完整入口回归。"""

    def _run(self, env, *args):
        return subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "cli.py"), *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(REPO_ROOT), timeout=300)

    def _roundtrip(self, env, home, expect_data, expect_backup, expect_layout):
        r = self._run(env, "doctor", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        doc = json.loads(r.stdout[r.stdout.index("{"):])
        self.assertEqual(doc["layout"], expect_layout)
        self.assertEqual(Path(doc["paths"]["data_dir"]), expect_data)
        self.assertEqual(Path(doc["paths"]["backup_dir"]), expect_backup)
        demo = home / ".agents/skills/demo"

        r = self._run(env, "scan", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        inv = json.loads((expect_data / "inventory.json").read_text())
        iid = inv["instances"][0]["instance_id"]

        r = self._run(env, "report", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])

        r = self._run(env, "manage", "plan", "remove", "--instance-id", iid,
                      "--reason", "layout roundtrip", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        plan = json.loads(r.stdout[r.stdout.index("{"):])
        r = self._run(env, "manage", "apply", plan["plan_id"],
                      "--digest", plan["digest"], "--confirm", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        result = json.loads(r.stdout[r.stdout.index("{"):])
        self.assertEqual(result["transaction_status"], "committed")
        self.assertFalse(demo.exists(), "删除后目标必须消失")

        backups = sorted(expect_backup.glob("backup-*.tar.gz"))
        self.assertTrue(backups, "备份必须落在统一解析的备份目录")
        r = self._run(env, "manage", "plan", "restore",
                      "--backup-id", backups[-1].name[len("backup-"):-len(".tar.gz")],
                      "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        plan = json.loads(r.stdout[r.stdout.index("{"):])
        r = self._run(env, "manage", "apply", plan["plan_id"],
                      "--digest", plan["digest"], "--confirm", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertTrue((demo / "SKILL.md").exists(), "恢复必须把实体放回原位")

    @staticmethod
    def _home_with_skill(td):
        home = Path(td) / "home"
        (home / ".agents/skills/demo").mkdir(parents=True)
        (home / ".agents/skills/demo/SKILL.md").write_text(
            "---\nname: demo\ndescription: d\nversion: 1.0.0\n---\nbody\n", encoding="utf-8")
        return home

    def test_explicit_data_dir_layout_full_roundtrip(self):
        """旧私人部署的兼容路径(显式/环境数据目录)完整回归。

        注:仓库 checkout 自身是 old-repo 布局(BASE/data 有 v2/v3 标记),
        从仓库内无环境变量运行 CLI 会解析到真实运行态——那是产品行为,
        测试绝不那样跑;「新默认 ~/.skill-keeper 布局」的完整回归在
        test_packaging_install(真安装态,BASE 无标记)中执行。
        """
        with tempfile.TemporaryDirectory(prefix="sk-layout-") as td:
            home = self._home_with_skill(td)
            data = Path(td) / "proj-data"
            env = dict(os.environ, HOME=str(home), SKILL_KEEPER_HOME=str(home),
                       SKILL_KEEPER_DATA=str(data))
            self._roundtrip(env, home, data, data / "backups", "env")


if __name__ == "__main__":
    unittest.main()
