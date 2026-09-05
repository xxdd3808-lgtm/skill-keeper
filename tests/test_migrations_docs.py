import io, json, os, tarfile, tempfile, unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_INV = REPO_ROOT / "tests/fixtures/inventory-value.json"


def v2_inventory_fixture():
    return json.loads(FIXTURE_INV.read_text(encoding="utf-8"))


def v1_state_fixture(testcase):
    data = tempfile.mkdtemp(prefix="sk-v1-data-")
    testcase.addCleanup(__import__("shutil").rmtree, data, ignore_errors=True)
    data = Path(data)
    (data / "groups.json").write_text(json.dumps(
        {"文档处理": ["demo"]}, ensure_ascii=False), encoding="utf-8")
    (data / "self-built.txt").write_text("# 自建\ndemo\n", encoding="utf-8")
    (data / "known-sources.json").write_text(json.dumps(
        {"pdf": {"type": "github", "repo": "example/skills", "path": "skills/pdf/SKILL.md"}}),
        encoding="utf-8")
    (data / "vetted.json").write_text(json.dumps({
        "demo": {"verdict": "safe", "note": "首轮安检通过", "vetted_at": "2026-08-30",
                 "sk_hash": "abc123:3"}}), encoding="utf-8")
    (data / "updates.json").write_text(json.dumps(
        {"checked_at": "2026-08-30 09:00:00", "differs": [{"name": "demo"}]}), encoding="utf-8")
    (data / "inventory.json").write_text(json.dumps({"total": 1, "skills": []}), encoding="utf-8")
    return data


def fixture_backup_dir(testcase):
    d = tempfile.mkdtemp(prefix="sk-legacy-backups-")
    testcase.addCleanup(__import__("shutil").rmtree, d, ignore_errors=True)
    d = Path(d)
    for name in ("code-1.0.4", "memory", "word-docx"):
        with tarfile.open(d / ("removed-{}-20260801-000000.tar.gz".format(name)), "w:gz") as t:
            data = b"# old skill"
            info = tarfile.TarInfo("{}/SKILL.md".format(name))
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return d


class MigrationDocsTests(unittest.TestCase):
    def test_personal_config_is_preserved_and_old_vetting_needs_recheck(self):
        from scripts.core.migrations import migrate_runtime_state
        data = v1_state_fixture(self)
        before_groups = (data / "groups.json").read_bytes()
        before_selfbuilt = (data / "self-built.txt").read_bytes()
        before_known = (data / "known-sources.json").read_bytes()
        result = migrate_runtime_state(data, v2_inventory_fixture())
        self.assertEqual((data / "groups.json").read_bytes(), before_groups)
        self.assertEqual((data / "self-built.txt").read_bytes(), before_selfbuilt)
        self.assertEqual((data / "known-sources.json").read_bytes(), before_known)
        self.assertEqual(result["vetting"]["demo"]["status"], "needs-recheck",
                         "v1 安检结论按 v2 指纹规则必须降级为需复检")
        self.assertEqual(result["vetting"]["demo"]["previous_verdict"], "safe")
        self.assertTrue(list((data / "migrations").iterdir()), "旧运行时 JSON 必须先备份")
        upd = json.loads((data / "updates.json").read_text(encoding="utf-8"))
        self.assertEqual(upd["schema_version"], 2)
        self.assertEqual(upd["differs"], [], "v1 更新结果必须失效重建")
        # 幂等:再跑一遍不破坏任何个人配置
        migrate_runtime_state(data, v2_inventory_fixture())
        self.assertEqual((data / "self-built.txt").read_bytes(), before_selfbuilt)

    def test_legacy_removed_examples_are_inspected_not_restored(self):
        from scripts.core.migrations import inspect_legacy_cases
        result = inspect_legacy_cases(["code-1.0.4", "memory", "word-docx"],
                                      fixture_backup_dir(self))
        self.assertEqual({x["name"] for x in result},
                         {"code-1.0.4", "memory", "word-docx"})
        self.assertTrue(all(x["restored"] is False for x in result),
                        "旧备份只检视,绝不自动恢复")
        self.assertTrue(all(x["legacy"] for x in result))
        self.assertTrue(all(x["limitations"] for x in result))

    def test_docs_are_v2_consistent(self):
        skill = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("version: 3.0.0", skill)  # F11:版本字段必须与项目状态一致(2.1.1 已过期)
        for client in ("ZCode", "Codex", "Accio", "WorkBuddy", "Claude Code", "Haha", "Cindy"):
            self.assertIn(client, readme, "README 必须列出支持的客户端:" + client)
            self.assertIn(client, agents, "AGENTS 必须列出支持的客户端:" + client)
        for doc in (readme, skill, agents):
            self.assertNotIn("放心更新", doc, "不得保留旧版绝对化承诺")
            self.assertNotIn("天然免检", doc)
            self.assertIn("plan", doc)
            self.assertIn("永不自动删除", doc)
        self.assertIn("仓库热度", readme, "README 必须写明热度口径")


if __name__ == "__main__":
    unittest.main()
