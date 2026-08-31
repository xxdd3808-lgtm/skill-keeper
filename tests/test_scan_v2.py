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


if __name__ == "__main__":
    unittest.main()
