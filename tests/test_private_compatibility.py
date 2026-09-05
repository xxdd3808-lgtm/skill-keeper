"""Task 0(v4 泛化):private-v311 零退化合同 —— 冻结 v3.1.1 私人版语义。

fixture 完全虚构(tests/fixtures/private-v311/README.md),不含任何真实技能、
个人路径或秘密。v4 泛化(Task 1–5)不得改变本文件断言的任何行为:

- 适配器发现与 inventory 形状(位置/实例/逻辑身份/健康问题);
- 客户端加载上下文(eligible ≠ confirmed);
- 报告共享库区块;
- builtin-app owner 政策(正本拒绝、散布收回)与审查队列/台账;
- 计划 → 确认 → 备份 → 事务 → 恢复的完整闭环;
- 仓库内 data/backups 兼容布局;
- 旧式 `python3 scripts/*.py` 入口(scan/report/manage/remove_skill/value_review)。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "private-v311"

from scripts.core.audit import read_audit                                   # noqa: E402
from scripts.core.backup import verify_backup                               # noqa: E402
from scripts.core.changes import (ChangeContext, apply_plan,                # noqa: E402
                                  create_remove_plan, create_restore_plan)
from scripts.core.clients import discover_locations                         # noqa: E402
from scripts.core.fingerprint import tree_hash                              # noqa: E402
from scripts.core.io import atomic_write_json                               # noqa: E402
from scripts.core.policy import check_action, load_policy                   # noqa: E402
from scripts.core.provenance import load_user_config                        # noqa: E402
from scripts.core.reviews import build_review_queue, record_review          # noqa: E402
from scripts.core.runtime import BASE, RuntimePaths                         # noqa: E402
from scripts.scan import build_inventory                                    # noqa: E402
from tests.helpers import copy_private_v311_fixture                         # noqa: E402


def _change_context(home, data):
    return ChangeContext(
        data_dir=data, plans_dir=data / "change-plans", backup_dir=home / "backups",
        audit_path=data / "audit-v2.jsonl", lock_path=data / ".change.lock",
        load_inventory=lambda: build_inventory(home, data))


def _inst_by_dir(inv, directory_name, client=None):
    rows = [i for i in inv["instances"] if i["directory_name"] == directory_name
            and (client is None or i["client"] == client)]
    assert rows, "fixture 缺少实例: {}({})".format(directory_name, client)
    return rows[0]


class FixturePrivacyTests(unittest.TestCase):
    def test_fixture_is_fully_fictional(self):
        """fixture 不得携带真实绝对路径,符号链接必须是相对链接。"""
        for path in sorted(FIXTURE.rglob("*")):
            if path.is_symlink():
                self.assertFalse(os.path.isabs(os.readlink(path)),
                                 "符号链接必须是相对链接: {}".format(path))
                continue
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("/Users/", text, "fixture 泄漏真实路径: {}".format(path))


class PrivateCompatibilityTests(unittest.TestCase):
    """每条用例拿独立 fixture 副本;全部断言=当前 v3.1.1 已验证行为。"""

    def test_adapter_discovery_frozen(self):
        home, data = copy_private_v311_fixture(self)
        locs = {l.location_id: l for l in discover_locations(home, data)}
        self.assertEqual(set(locs), {"shared", "codex-user", "workbuddy-user", "ego-user"})
        expected = {
            "shared": ("shared", "user", True, ("default-shared-dir",)),
            "codex-user": ("codex", "user", True, ("default-codex-dir",)),
            "workbuddy-user": ("workbuddy", "user", True, ("default-workbuddy-dir",)),
            "ego-user": ("ego", "user", True, ("default-ego-dir",)),
        }
        for loc_id, (client, kind, mutable, evidence) in expected.items():
            loc = locs[loc_id]
            self.assertEqual(loc.client, client, loc_id)
            self.assertEqual(loc.kind, kind, loc_id)
            self.assertEqual(loc.mutable, mutable, loc_id)
            self.assertEqual(tuple(loc.evidence), evidence, loc_id)

    def test_inventory_shape_frozen(self):
        home, data = copy_private_v311_fixture(self)
        inv = build_inventory(home, data)
        self.assertTrue(inv["operational_ok"])
        self.assertEqual(len(inv["locations"]), 4)
        self.assertEqual(len(inv["instances"]), 9)
        self.assertTrue(all(i["is_skill"] for i in inv["instances"]))
        self.assertEqual(inv["total"], 7)
        self.assertEqual(inv["health_status"], "yellow")
        self.assertEqual(inv["config_issues"], [])
        self.assertTrue(inv["observation"]["complete"])

        # shared-alpha:同指纹 3 实例(共享正本 + Codex 副本 + WorkBuddy 符号链接别名)
        alpha = [i for i in inv["instances"] if i["logical_name"] == "shared-alpha"]
        self.assertEqual(len(alpha), 3)
        self.assertEqual(len({i["tree_hash"] for i in alpha}), 1)
        link = _inst_by_dir(inv, "wb-link")
        self.assertTrue(link["is_symlink"])
        self.assertEqual(link["tree_hash"], _inst_by_dir(inv, "shared-alpha", "shared")["tree_hash"])
        # builtin-widget:共享散布副本与 ego 正本内容不同 → 两个逻辑身份
        widgets = [i for i in inv["instances"] if i["directory_name"] == "builtin-widget"]
        self.assertEqual(len(widgets), 2)
        self.assertNotEqual(widgets[0]["tree_hash"], widgets[1]["tree_hash"])

        # findings 恰好 3 条黄灯,顺序固定
        self.assertEqual([f["code"] for f in inv["findings"]],
                         ["duplicate-load", "builtin-app-spread", "link-drift"])
        self.assertTrue(all(f["severity"] == "yellow" for f in inv["findings"]))

        # 启动上下文口径:只有 Codex 重复(shared 自动导入)
        cl = inv["client_load"]
        self.assertEqual(cl["zcode"]["entries"], 3)
        self.assertEqual((cl["codex"]["entries"], cl["codex"]["duplicates"]),
                         (5, ["shared-alpha"]))
        self.assertEqual(cl["codex"]["dup_entries"], 1)
        self.assertEqual(cl["workbuddy"]["entries"], 3)
        self.assertEqual(cl["ego"]["entries"], 1)
        for empty in ("claude-code", "haha", "cindy", "accio"):
            self.assertEqual(cl[empty]["entries"], 0, empty)

    def test_load_context_frozen(self):
        home, data = copy_private_v311_fixture(self)
        inv = build_inventory(home, data)
        lc = inv["observation"]["load_contexts"]
        self.assertEqual(set(lc), {"zcode", "codex", "claude-code", "haha",
                                   "cindy", "accio", "workbuddy", "ego"})

        def counts(client):
            row = lc[client]
            return (row["discovered"], row["eligible"], row["confirmed"],
                    row["unknown"], row["duplicates"])

        # eligible 只是"位置在读取集合内"的推断;confirmed 必须保持 0。
        # discovered = 全局范围内全部 skill 实例(9);eligible 才按客户端读取集合过滤。
        self.assertEqual(counts("zcode"), (9, 3, 0, 3, []))
        self.assertEqual(counts("codex"), (9, 5, 0, 5, [
            {"name": "shared-alpha",
             "instance_ids": sorted(i["instance_id"] for i in inv["instances"]
                                    if i["logical_name"] == "shared-alpha"
                                    and i["location_id"] in ("shared", "codex-user")),
             "contexts": ["global"]}]))
        self.assertEqual(counts("workbuddy"), (9, 3, 0, 3, []))
        self.assertEqual(counts("ego"), (9, 1, 0, 1, []))
        for client in ("claude-code", "haha", "cindy", "accio"):
            self.assertEqual(counts(client), (9, 0, 0, 0, []), client)

    def test_review_queue_and_ledger_frozen(self):
        home, data = copy_private_v311_fixture(self)
        inv = build_inventory(home, data)
        queue = build_review_queue(inv, {}, {}, known_sources=load_user_config(data))
        names = {x["name"] for x in queue["items"]}
        # 自建 shared-beta 正本与 builtin-widget(×2)受保护不入队;
        # wb-drift 目录名继承不到白名单,同名 "shared-beta" 漂移副本照进队列
        self.assertEqual(names, {"shared-alpha", "shared-beta", "codex-extra", "wb-only"})
        extra = next(x for x in queue["items"] if x["name"] == "codex-extra")
        self.assertEqual(extra["provenance"]["type"], "github")
        self.assertTrue(extra["provenance"]["review_required"])
        self.assertTrue(extra["content_untrusted"])

        record = record_review(queue, {
            "instance_id": extra["instance_id"],
            "verdict": "保留",
            "reason": "虚构来源已核实,功能独立(冻结测试)",
            "evidence": ["fixture:来源台账已登记", "fixture:无本机替代需求"],
            "confidence": "高",
        }, "private-v311-freeze")
        self.assertTrue(record["review_id"].startswith("rv-"))
        self.assertEqual(record["verdict"], "保留")
        atomic_write_json(data / "value-reviews.json",
                          {"schema_version": 2, "reviews": [record]})

        # 记账后重跑队列:同一 tree_hash 下结论 current,不再 unvetted
        queue2 = build_review_queue(inv, {}, [record], known_sources=load_user_config(data))
        extra2 = next(x for x in queue2["items"] if x["name"] == "codex-extra")
        self.assertEqual(extra2["previous_review_status"], "current")

    def test_owner_policy_and_change_loop_frozen(self):
        home, data = copy_private_v311_fixture(self)
        inv = build_inventory(home, data)
        ctx = _change_context(home, data)
        policy = load_policy(data)

        widget_shared = _inst_by_dir(inv, "builtin-widget", "shared")
        widget_ego = _inst_by_dir(inv, "builtin-widget", "ego")
        loc_shared = next(l for l in inv["locations"] if l["location_id"] == "shared")

        # owner 语义:散布副本(共享库)允许正规收回;正本(ego)与 update 照旧拒绝
        spread = check_action("remove", widget_shared, loc_shared, policy)
        self.assertTrue(spread["allowed"])
        entity = check_action("remove", widget_ego, dict(loc_shared, client="ego"), policy)
        self.assertFalse(entity["allowed"])
        upd = check_action("update", widget_shared, loc_shared, policy)
        self.assertFalse(upd["allowed"])

        # 散布副本走完整闭环:计划 → apply → 备份可用;ego 正本不受影响
        plan = create_remove_plan([widget_shared["instance_id"]], inv,
                                  "冻结测试:收回共享库散布快捷方式",
                                  ctx.plans_dir, known_sources=load_user_config(data))
        result = apply_plan(plan.plan_id, plan.digest, True, ctx)
        self.assertEqual(result["transaction_status"], "committed")
        self.assertFalse(Path(widget_shared["path"]).exists())
        self.assertTrue(Path(widget_ego["path"]).exists())
        self.assertTrue(verify_backup(Path(result["backup_path"]))["ok"])

        # 重放已提交计划:返回已知结果,不再物理变更
        replay = apply_plan(plan.plan_id, plan.digest, True, ctx)
        self.assertTrue(replay.get("already_applied"))
        self.assertFalse(Path(widget_shared["path"]).exists())

        # 普通第三方删除 + 恢复往返:逐字节一致,审计两条 success
        wb = _inst_by_dir(inv, "wb-only")
        original_hash = tree_hash(Path(wb["real_path"]))
        plan2 = create_remove_plan([wb["instance_id"]], inv, "冻结测试:普通删除",
                                   ctx.plans_dir, known_sources=load_user_config(data))
        removed = apply_plan(plan2.plan_id, plan2.digest, True, ctx)
        self.assertFalse(Path(wb["path"]).exists())
        restore = create_restore_plan(removed["backup_id"], ctx.backup_dir, ctx.plans_dir)
        apply_plan(restore.plan_id, restore.digest, True, ctx)
        self.assertTrue(Path(wb["path"]).exists())
        self.assertEqual(tree_hash(Path(wb["real_path"])), original_hash)
        audit = [x for x in read_audit(ctx.audit_path) if x.get("status") in ("success", "failed")]
        self.assertEqual([x["status"] for x in audit], ["success", "success", "success"])
        self.assertEqual([x["rollback_status"] for x in audit], [None, None, None])

    def test_repo_runtime_layout_frozen(self):
        """data/backups 兼容布局:真实 v2/v3 运行态 → 仓库布局;全新 checkout → 新默认。

        断言随本仓库实际状态分支(私人部署=old-repo 时冻结仓库布局;
        CI 新 clone=new 时冻结 ~/.skill-keeper),两种分支都不允许回归。
        """
        from scripts.scan import data_dir as scan_data_dir
        from scripts.core.runtime import detect_repo_layout
        layout = detect_repo_layout(BASE)
        with mock.patch.dict(os.environ, {}, clear=True):
            paths = RuntimePaths()
            self.assertEqual(paths.layout, layout)
            if layout == "old-repo":
                self.assertEqual(paths.data_dir, BASE / "data")
                self.assertEqual(paths.backup_dir, BASE / "backups")
                self.assertEqual(scan_data_dir(), Path(BASE) / "data")
            else:
                self.assertEqual(paths.data_dir, paths.home / ".skill-keeper" / "data")
                self.assertEqual(paths.backup_dir,
                                 paths.home / ".skill-keeper" / "backups")
                self.assertEqual(scan_data_dir(),
                                 paths.home / ".skill-keeper" / "data")
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SKILL_KEEPER_DATA": td}, clear=True):
                paths = RuntimePaths()
                self.assertEqual(paths.data_dir, Path(td))
                self.assertEqual(paths.backup_dir, Path(td) / "backups")
                self.assertEqual(scan_data_dir(), Path(td))
            # 显式参数优先于环境变量缺失时的默认
            paths = RuntimePaths(data_dir=Path(td) / "d", staging_dir=Path(td) / "s",
                                 backup_dir=Path(td) / "b")
            self.assertEqual(paths.backup_dir, Path(td) / "b")


class LegacyCliFrozenTests(unittest.TestCase):
    """旧式 `python3 scripts/*.py` 入口合同(真实子进程,不走包装层)。"""

    def _env(self, home, data):
        return dict(os.environ, HOME=str(home), SKILL_KEEPER_DATA=str(data))

    def _run(self, env, *args):
        return subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / args[0]), *args[1:]],
                              capture_output=True, text=True, env=env,
                              cwd=str(REPO_ROOT), timeout=180)

    def test_scan_report_value_review_legacy_entries(self):
        home, data = copy_private_v311_fixture(self)
        env = self._env(home, data)

        r = self._run(env, "scan.py", "--json")
        self.assertEqual(r.returncode, 0, r.stdout[-500:] + r.stderr[-300:])
        summary = json.loads(r.stdout[r.stdout.index("{"):])
        self.assertEqual((summary["total"], summary["instances"], summary["locations"]),
                         (7, 9, 4))
        self.assertTrue(summary["observation_complete"])
        self.assertNotIn("/Users/", r.stdout)

        self.assertTrue((data / "inventory.json").is_file(), "scan 必须把 inventory 写进 data")

        r = self._run(env, "value_review.py", "queue", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        queue = json.loads(r.stdout[r.stdout.index("{"):])
        self.assertEqual({x["name"] for x in queue["items"]},
                         {"shared-alpha", "shared-beta", "codex-extra", "wb-only"})
        self.assertTrue((data / "review-queue.json").is_file())

        r = self._run(env, "report.py")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        md = (data / "report.md").read_text(encoding="utf-8")
        html = (data / "report.html").read_text(encoding="utf-8")
        self.assertIn("## 共享库(3 个)", md)
        self.assertIn('id="shared-library"', html)
        self.assertNotIn("/Users/", md)
        self.assertNotIn("/Users/", html)

        # 旧式「按目录名直接删除」必须停用:退出码 2 + 迁移说明
        r = self._run(env, "remove_skill.py", "wb-only")
        self.assertEqual(r.returncode, 2)
        self.assertIn("停用", r.stdout)

    def test_manage_cli_full_loop(self):
        home, data = copy_private_v311_fixture(self)
        env = self._env(home, data)

        r = self._run(env, "manage.py", "rescan", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        inv = json.loads((data / "inventory.json").read_text(encoding="utf-8"))
        wb = _inst_by_dir(inv, "wb-only")
        wb_path = Path(wb["path"])

        r = self._run(env, "manage.py", "plan", "remove", "--instance-id", wb["instance_id"],
                      "--reason", "冻结测试:旧 CLI 删除闭环", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        plan = json.loads(r.stdout[r.stdout.index("{"):])
        self.assertTrue(plan["ok"])

        r = self._run(env, "manage.py", "apply", plan["plan_id"],
                      "--digest", "0" * 64, "--confirm", "--json")
        self.assertNotEqual(r.returncode, 0, "digest 错误必须拒绝执行")
        self.assertTrue(wb_path.exists())

        r = self._run(env, "manage.py", "apply", plan["plan_id"],
                      "--digest", plan["digest"], "--confirm", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertFalse(wb_path.exists(), "apply 后目标必须消失")
        result = json.loads(r.stdout[r.stdout.index("{"):])
        self.assertEqual(result["transaction_status"], "committed")

        r = self._run(env, "manage.py", "status", plan["plan_id"], "--json")
        self.assertEqual(r.returncode, 0)
        status = json.loads(r.stdout[r.stdout.index("{"):])
        self.assertEqual(status["transaction"]["phase"], "committed")


if __name__ == "__main__":
    unittest.main()
