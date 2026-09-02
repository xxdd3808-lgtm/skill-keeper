import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

from tests.helpers import build_multi_client_paths, temp_home, write_skill

REPO_ROOT = Path(__file__).resolve().parents[1]


def build_one_skill_home(testcase):
    """一个最小可扫描环境:shared 位置放一个 demo skill(带辅助脚本 run.py)。"""
    home = temp_home(testcase)
    data = home / "data"
    data.mkdir(parents=True, exist_ok=True)
    d = write_skill(home / ".agents/skills", "demo", description="demo skill")
    (d / "run.py").write_text("safe", encoding="utf-8")
    return home, data


def build_duplicate_home(testcase):
    """shared 放 demo 正本,.zcode/skills 放同名符号链接但指向内容漂移且缺 description(红)的副本。"""
    home, data = build_one_skill_home(testcase)
    alt = write_skill(home / ".other", "demo-alt", description="", fm_name="demo")
    (home / ".zcode/skills").mkdir(parents=True, exist_ok=True)
    os.symlink(alt, home / ".zcode/skills/demo")
    return home, data


class ScanV2Tests(unittest.TestCase):
    def test_duplicate_and_drift_are_health_findings(self):
        from scripts.scan import build_inventory
        home, data = build_duplicate_home(self)
        inv = build_inventory(home, data)
        findings = [x["code"] for x in inv["findings"]]
        self.assertIn("duplicate-load", findings)
        self.assertIn("link-drift", findings)

    def test_auxiliary_change_invalidates_instance_hash(self):
        from scripts.scan import build_inventory
        home, data = build_one_skill_home(self)
        before = build_inventory(home, data)["instances"][0]["tree_hash"]
        (home / ".agents/skills/demo/run.py").write_text("changed", encoding="utf-8")
        after = build_inventory(home, data)["instances"][0]["tree_hash"]
        self.assertNotEqual(before, after)

    def test_client_cache_instances_are_not_mutable(self):
        from scripts.scan import build_inventory
        inv = build_inventory(*build_multi_client_paths(self))
        self.assertTrue(all(not x["mutable"] for x in inv["instances"]
                            if x["kind"] in {"builtin", "plugin-cache"}))

    def test_inventory_schema_unique_ids_and_keys(self):
        from scripts.scan import build_inventory
        inv = build_inventory(*build_multi_client_paths(self))
        self.assertEqual(inv["schema_version"], 2)
        ids = [x["instance_id"] for x in inv["instances"]]
        self.assertTrue(ids)
        self.assertEqual(len(ids), len(set(ids)), "instance_id 必须唯一")
        for key in ("locations", "instances", "logical_skills", "findings", "config_issues"):
            self.assertIn(key, inv)

    def test_ignore_flags_but_never_drops_findings(self):
        from scripts.scan import build_inventory
        home, data = build_duplicate_home(self)
        (data / "ignore.json").write_text(json.dumps({"demo": ["duplicate-load"]}), encoding="utf-8")
        inv = build_inventory(home, data)
        dup = next(x for x in inv["findings"] if x["code"] == "duplicate-load")
        drift = next(x for x in inv["findings"] if x["code"] == "link-drift")
        self.assertTrue(dup["ignored"], "命中的 ignore 规则只翻 ignored 标记")
        self.assertFalse(drift["ignored"])
        self.assertIn("duplicate-load", [x["code"] for x in inv["findings"]],
                      "ignore 后 finding 不得消失")

    def test_extra_client_locations_config(self):
        from scripts.scan import build_inventory
        home, data = build_one_skill_home(self)
        write_skill(home / ".myclient/skills", "custom-tool")
        (data / "client-locations.json").write_text(json.dumps({
            "locations": [{"location_id": "myclient", "client": "myclient",
                           "path": str(home / ".myclient/skills"),
                           "kind": "user", "mutable": True}]}), encoding="utf-8")
        inv = build_inventory(home, data)
        self.assertIn("myclient", {x["location_id"] for x in inv["locations"]})
        self.assertIn("custom-tool", {x["directory_name"] for x in inv["instances"]})

    def test_bad_extra_config_becomes_config_issue_not_crash(self):
        from scripts.scan import build_inventory
        home, data = build_one_skill_home(self)
        (data / "client-locations.json").write_text(json.dumps({
            "locations": [{"path": "relative/path", "kind": "nonsense"}]}), encoding="utf-8")
        inv = build_inventory(home, data)
        self.assertTrue(inv["config_issues"], "非法配置必须进 config_issues")
        self.assertTrue(inv["operational_ok"])

    def test_cli_empty_home(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as data:
            env = dict(os.environ, HOME=home, SKILL_KEEPER_DATA=data)
            r = subprocess.run([sys.executable, "scripts/scan.py", "--json"],
                               capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["total"], 0)
            self.assertTrue(out["operational_ok"])
            self.assertEqual(out["health_status"], "ok")
            self.assertTrue(Path(data, "inventory.json").exists())

    def test_cli_red_findings_exit_code(self):
        home, data = build_duplicate_home(self)
        env = dict(os.environ, HOME=str(home), SKILL_KEEPER_DATA=str(data))
        r = subprocess.run([sys.executable, "scripts/scan.py", "--json"],
                           capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))
        self.assertEqual(r.returncode, 1, "有红色问题时退出码必须是 1")


class ClientLoadModelTests(unittest.TestCase):
    """2026-09:Codex 自动导入共享库后的按客户端加载模型与重复检测。"""

    def build_codex_dup_home(self, testcase):
        """shared 与 ~/.codex/skills 各放一份同名 skill(Codex 现在两个都读)。"""
        home = temp_home(testcase)
        data = home / "data"
        data.mkdir(parents=True, exist_ok=True)
        write_skill(home / ".agents/skills", "dup-tool", description="shared copy")
        write_skill(home / ".codex/skills", "dup-tool", description="codex copy")
        return home, data

    def test_codex_duplicate_via_shared_is_flagged(self):
        from scripts.scan import build_inventory
        home, data = self.build_codex_dup_home(self)
        inv = build_inventory(home, data)
        dups = [f for f in inv["findings"] if f["code"] == "duplicate-load"]
        self.assertTrue(any("Codex" in f["message"] and f["skill"] == "dup-tool" for f in dups),
                        "Codex 经共享库的同名双载必须报 duplicate-load")
        self.assertEqual(inv["client_load"]["codex"]["duplicates"], ["dup-tool"])
        self.assertEqual(inv["client_load"]["codex"]["entries"], 2)

    def test_stale_plugin_version_not_counted_as_loaded(self):
        from scripts.scan import build_inventory
        from tests.helpers import make_plugin_cache
        home = temp_home(self)
        data = home / "data"
        data.mkdir(parents=True, exist_ok=True)
        write_skill(home / ".agents/skills", "solo")
        make_plugin_cache(home / ".zcode/cli/plugins/cache", "demo-plugin", "0.4.1",
                          "demo-skill", nested=True)
        make_plugin_cache(home / ".zcode/cli/plugins/cache", "demo-plugin", "0.4.0",
                          "demo-skill", nested=True)
        inv = build_inventory(home, data)
        stale = [f for f in inv["findings"] if f["code"] == "stale-plugin-version"]
        self.assertEqual(len(stale), 1, "旧版本残留应记 info,不算加载")
        self.assertEqual(stale[0].get("severity"), "info")
        zc = inv["client_load"]["zcode"]
        self.assertEqual(zc["duplicates"], [], "同插件新旧版本不算同名重复加载")
        self.assertEqual(zc["skills"], 2, "solo + demo-skill(仅最高版本)")

    def test_builtin_app_skill_in_shared_is_flagged(self):
        from scripts.scan import build_inventory
        home = temp_home(self)
        data = home / "data"
        data.mkdir(parents=True, exist_ok=True)
        write_skill(home / ".agents/skills", "vendor-app-tool", description="app builtin")
        (data / "known-sources.json").write_text(json.dumps({
            "vendor-app-tool": {"type": "builtin-app", "repo": None, "path": None,
                                "note": "测试应用自带"}}, ensure_ascii=False), encoding="utf-8")
        inv = build_inventory(home, data)
        self.assertTrue(any(f["code"] == "builtin-app-spread" and f["skill"] == "vendor-app-tool"
                            for f in inv["findings"]),
                        "应用内置技能进入共享库必须报 builtin-app-spread")

    def test_haha_follows_claude_mirror_without_phantom_dups(self):
        """2026-09-02 按 Haha traces 核实:Haha 走 ~/.claude/skills 镜像,不直接读共享库——
        加载模型不得再把共享库算进 Haha(那会把镜像符号链接虚报成双份)。"""
        from scripts.scan import build_inventory
        home = temp_home(self)
        data = home / "data"
        data.mkdir(parents=True, exist_ok=True)
        write_skill(home / ".agents/skills", "mirror-tool", description="shared")
        claude = home / ".claude/skills"
        claude.mkdir(parents=True)
        os.symlink(home / ".agents/skills/mirror-tool", claude / "mirror-tool")
        (home / ".claude/cc-haha").mkdir(parents=True)
        inv = build_inventory(home, data)
        self.assertFalse([f for f in inv["findings"] if f["code"] == "wrapper-double-load"],
                         "Haha 不读共享库,镜像符号链接不算双份")
        self.assertEqual(inv["client_load"]["haha"]["duplicates"], [])
        self.assertEqual(inv["client_load"]["haha"]["entries"],
                         inv["client_load"]["claude-code"]["entries"])

    def test_haha_detected_via_installed_app_bundle(self):
        """Claude Code 卸载只留 Haha 后,~/.claude/cc-haha 可能被一并清空——
        只要 Haha 应用还在(~/Applications/Claude Code Haha.app),镜像仍要归属 Haha。"""
        from scripts.scan import build_inventory
        home = temp_home(self)
        data = home / "data"
        data.mkdir(parents=True, exist_ok=True)
        write_skill(home / ".agents/skills", "mirror-tool", description="shared")
        claude = home / ".claude/skills"
        claude.mkdir(parents=True)
        os.symlink(home / ".agents/skills/mirror-tool", claude / "mirror-tool")
        (home / "Applications/Claude Code Haha.app").mkdir(parents=True)
        inv = build_inventory(home, data)
        self.assertEqual(inv["client_load"]["haha"]["entries"], 1,
                         "无 cc-haha 配置目录时,靠已安装应用识别 Haha")

    def test_wrapper_double_load_aggregates_for_shared_alias(self):
        """真正同时读 Claude 目录与共享库的包装客户端(未来出现时):聚合为一条,不刷屏。"""
        from scripts.scan import _structural_findings
        home = temp_home(self)
        shared = write_skill(home / ".agents/skills", "mirror-tool", description="shared")
        mirror = home / ".claude/skills/mirror-tool"
        mirror.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(shared, mirror)
        inst = {"instance_id": "i1", "location_id": "shared", "client": "shared",
                "kind": "user", "logical_name": "mirror-tool", "is_skill": True,
                "is_symlink": False, "tree_hash": "t1", "display_path": "~/.agents/skills/x",
                "directory_name": "mirror-tool", "real_path": str(shared)}
        mirror_inst = dict(inst, instance_id="i2", location_id="claude-user",
                           client="claude-code", is_symlink=True,
                           display_path="~/.claude/skills/x", real_path=str(shared))
        locations = [
            {"location_id": "shared", "client": "shared", "aliases": ["haha"],
             "path": str(home / ".agents/skills"), "kind": "user", "mutable": True, "evidence": []},
            {"location_id": "claude-user", "client": "claude-code", "aliases": ["haha"],
             "path": str(home / ".claude/skills"), "kind": "user", "mutable": True, "evidence": []},
        ]
        findings = _structural_findings([inst, mirror_inst], locations, home / "data")
        wrapper = [f for f in findings if f["code"] == "wrapper-double-load"]
        self.assertEqual(len(wrapper), 1)

    def test_nested_skill_tree_inside_skill_dir_is_flagged(self):
        """技能目录内部嵌套技能树(如仓库 data/staging 候选)会被递归扫描的客户端面板
        当独立技能重复列出——必须检出(2026-09-02 ZCode 面板 aihot/brainstorming 双条)。"""
        from scripts.scan import build_inventory
        home = temp_home(self)
        data = home / "data"
        data.mkdir(parents=True, exist_ok=True)
        root = write_skill(home / ".agents/skills", "repo-tool", description="repo")
        write_skill(root / "data/staging/cand-abc123", "aihot", description="staged copy")
        inv = build_inventory(home, data)
        nested = [f for f in inv["findings"] if f["code"] == "nested-skill-tree"]
        self.assertEqual(len(nested), 1, "嵌套技能树必须报 nested-skill-tree")
        self.assertIn("staging", nested[0]["message"])


if __name__ == "__main__":
    unittest.main()
