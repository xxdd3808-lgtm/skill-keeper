"""Task 4 观察完整性与加载上下文(F05)。

- frontmatter 确定性子集:metadata.requires.bins 不再静默丢失;合法但不支持的结构给警告;
- 指纹:不可读子树/扫描中消失必须有结构化错误,部分树不得冒充完整树;
- 实例带 content_status;inventory 带 observation(complete/issues/observed_scope/rule_version);
- 加载规则独立成 load_rules:eligible ≠ confirmed,没有直接证据 confirmed 保持未知;
- 回执证据只按白名单字段读取(虚构 secret 贯穿测试);
- CLI:观察不完整/输入损坏 → 退出码 2;跨 marketplace 同名插件不算旧版本残留。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.scan import collect_bins, parse_frontmatter, parse_frontmatter_detailed

REPO_ROOT = Path(__file__).resolve().parents[1]


def write_skill(root, name, body="demo", description="demo skill"):
    d = Path(root) / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: {}\ndescription: {}\nversion: 1.0.0\n---\n\n{}\n".format(
            name, description, body), encoding="utf-8")
    return d


class FrontmatterContractTests(unittest.TestCase):
    def test_requires_bins_are_not_silently_lost(self):
        text = ("---\nname: fixture\ndescription: fixture\nmetadata:\n  requires:\n"
                "    bins: [definitely-missing-fixture-bin]\n---\nbody\n")
        fields, ok = parse_frontmatter(text)
        bins = []
        collect_bins(fields, bins)
        self.assertTrue(ok)
        self.assertEqual(bins, ["definitely-missing-fixture-bin"])

    def test_block_list_bins_and_booleans(self):
        text = ("---\nname: fix\ndescription: |\n  multi line\n  description here\n"
                "user_invocable: true\nmetadata:\n  requires:\n    bins:\n"
                "      - bin-a\n      - bin-b\n---\nbody\n")
        fields, ok = parse_frontmatter(text)
        self.assertTrue(ok)
        self.assertTrue(fields.get("user_invocable") is True)
        self.assertIn("multi line", str(fields.get("description")))
        bins = []
        collect_bins(fields, bins)
        self.assertEqual(sorted(bins), ["bin-a", "bin-b"])

    def test_unsupported_structure_reports_warning(self):
        text = ("---\nname: fix\ndescription: x\npermissions:\n  - admin\n"
                "unknown_nested:\n  deep:\n    value: 1\n---\nbody\n")
        fields, ok, warnings = parse_frontmatter_detailed(text)
        self.assertTrue(ok)
        self.assertTrue(warnings, "合法但不支持的结构必须给 unsupported 警告")
        self.assertTrue(any("permissions" in w.get("path", "") for w in warnings))
        self.assertTrue(any("unknown_nested" in w.get("path", "") for w in warnings))

    def test_truncated_frontmatter_reports_specific_issue(self):
        text = "---\nname: fix\ndescription: x\n"  # 没有闭合 ---
        fields, ok, warnings = parse_frontmatter_detailed(text)
        self.assertFalse(ok)
        self.assertTrue(any(w.get("code") == "truncated" for w in warnings))


class FingerprintObservationTests(unittest.TestCase):
    def _skill_with_locked_subtree(self, td):
        skill = write_skill(Path(td) / ".agents/skills", "demo")
        sub = skill / "secret-sub"
        sub.mkdir()
        inner = sub / "inner.txt"
        inner.write_text("x", encoding="utf-8")
        if os.name == "nt":
            # NTFS 没有 POSIX 权限位:用真实 ACL 拒绝读文件(不阻塞后续删除/清理)
            subprocess.run(["icacls", str(inner), "/deny", "*S-1-1-0:(R)"],
                           check=True, capture_output=True)
            self.addCleanup(lambda: subprocess.run(
                ["icacls", str(inner), "/reset"], capture_output=True))
        else:
            os.chmod(sub, 0o000)  # 目录不可读

            def unlock(path=str(sub)):
                try:
                    os.chmod(path, 0o755)
                except OSError:
                    pass  # 临时目录可能已先被清理

            self.addCleanup(unlock)
        return skill

    def test_unreadable_subtree_is_structured_not_silent(self):
        with tempfile.TemporaryDirectory() as td:
            skill = self._skill_with_locked_subtree(td)
            from scripts.core.fingerprint import FingerprintError, tree_hash
            with self.assertRaises(FingerprintError):
                tree_hash(skill)
            errors = []
            from scripts.core.fingerprint import tree_manifest
            tree_manifest(skill, collect_errors=errors)
            self.assertTrue(errors, "必须收集结构化错误而不是静默跳过")
            self.assertTrue(any(e.get("code") == "unreadable" for e in errors))

    def test_scan_entry_marks_unreadable_content(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            skill = self._skill_with_locked_subtree(home)
            from scripts.core.models import Location
            from scripts.scan import _scan_entry
            loc = Location("shared", "shared", str(home / ".agents/skills"),
                           "user", True, ("t",))
            inst, findings = _scan_entry(loc, loc.path and Path(loc.path),
                                         Path(skill), home)
            self.assertEqual(inst.get("content_status"), "unreadable")
            self.assertEqual(inst.get("tree_hash"), "", "不完整对象不得提供完整哈希")
            self.assertTrue(any(f["code"] == "content-unreadable" for f in findings))

    def test_build_inventory_observation_contract(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            data = home / "data"
            data.mkdir()
            write_skill(home / ".agents/skills", "good")
            self._skill_with_locked_subtree(home)
            from scripts.scan import build_inventory
            inv = build_inventory(home, data)
            obs = inv.get("observation")
            self.assertIsInstance(obs, dict)
            self.assertFalse(obs["complete"], "有不可读位置/实例时观察不完整")
            self.assertTrue(obs["issues"])
            self.assertTrue(obs.get("rule_version"))
            statuses = {i["content_status"] for i in inv["instances"]}
            self.assertIn("unreadable", statuses)
            good = [i for i in inv["instances"] if i["directory_name"] == "good"]
            self.assertTrue(good, "其余发现必须保留")
            self.assertEqual(good[0]["content_status"], "complete")
            self.assertEqual(inv.get("health_status"), "yellow")

    def test_scan_cli_exit_2_when_observation_incomplete(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            data = home / "data"
            data.mkdir()
            self._skill_with_locked_subtree(home)
            r = subprocess.run(
                [sys.executable, "scripts/scan.py", "--json"],
                capture_output=True, text=True, env=dict(
                    os.environ, HOME=str(home), SKILL_KEEPER_DATA=str(data)),
                cwd=str(REPO_ROOT))
            self.assertEqual(r.returncode, 2, "观察不完整必须退出 2: " + r.stdout[-300:])
            payload = json.loads(r.stdout)
            self.assertFalse(payload["observation_complete"])


class LoadRulesContractTests(unittest.TestCase):
    def test_evaluate_load_eligible_vs_confirmed(self):
        from scripts.core.models import Location
        from scripts.core.observations import evaluate_load
        loc = Location("shared", "shared", "/fixture/shared", "user", True, ("t",))
        inst = {"instance_id": "i1", "location_id": "shared", "is_skill": True,
                "logical_name": "demo", "client": "shared", "content_status": "complete"}
        result = evaluate_load([inst], [loc.to_dict()], "codex")
        self.assertEqual(result["discovered"], 1)
        self.assertGreaterEqual(result["eligible"], 1)
        self.assertEqual(result["confirmed"], 0, "没有直接加载证据时 confirmed 必须未知")
        self.assertTrue(result["rule_evidence"], "每条规则附来源标识")

    def test_workspace_same_name_not_global_duplicate(self):
        from scripts.core.models import Location
        from scripts.core.observations import evaluate_load
        shared = Location("shared", "shared", "/fixture/shared", "user", True, ("t",))
        ws_a = Location("workspace-aaaa", "workspace-claude", "/fixture/pa/.claude/skills",
                        "workspace", True, ("t",))
        ws_b = Location("workspace-bbbb", "workspace-claude", "/fixture/pb/.claude/skills",
                        "workspace", True, ("t",))
        insts = [
            {"instance_id": "g1", "location_id": "shared", "is_skill": True,
             "logical_name": "demo", "client": "shared", "content_status": "complete"},
            {"instance_id": "w1", "location_id": "workspace-aaaa", "is_skill": True,
             "logical_name": "demo", "client": "workspace-claude",
             "content_status": "complete"},
            {"instance_id": "w2", "location_id": "workspace-bbbb", "is_skill": True,
             "logical_name": "demo", "client": "workspace-claude",
             "content_status": "complete"},
        ]
        locs = [shared.to_dict(), ws_a.to_dict(), ws_b.to_dict()]
        result = evaluate_load(insts, locs, "claude-code")
        dup_names = {d["name"] for d in result.get("duplicates", [])}
        self.assertNotIn("demo", dup_names,
                         "两个未同时打开项目里的同名技能不是全局双载")

    def test_load_receipt_evidence_whitelist_only(self):
        from scripts.core.observations import load_receipt_evidence
        from tests.helpers import FAKE_SECRET
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            acc = home / ".accio/accounts/a"
            write_skill(acc / "skills", "accio-skill")
            (acc / "installed.json").write_text(json.dumps({
                "token": FAKE_SECRET,
                "skills": [{"name": "accio-skill", "id": "acc-1", "official": False,
                            "version": "1.2", "oss": "github", "secret_extra": FAKE_SECRET}],
            }, ensure_ascii=False), encoding="utf-8")
            evidence = load_receipt_evidence(
                home, [{"location_id": "accio-account-a", "client": "accio",
                        "path": str(acc / "skills"), "kind": "user"}])
            blob = json.dumps(evidence, ensure_ascii=False)
            self.assertNotIn(FAKE_SECRET, blob, "回执读取不得带出任何非白名单字段")
            rows = evidence.get("receipts") or {}
            matched = [r for r in rows.get("accio-skill", []) if r.get("id") == "acc-1"]
            self.assertTrue(matched, "白名单字段应保留作为证据")
            self.assertIn("inferred", blob, "无运行时启用证据时必须标注推断")

    def test_cross_marketplace_same_plugin_not_stale(self):
        with tempfile.TemporaryDirectory() as td:
            from scripts.scan import _effective_loaded
            from tests.helpers import make_plugin_cache
            base = Path(td) / "cache"
            make_plugin_cache(base, "dup", "1.0.0", "skill-x", nested=True,
                              marketplace="official")
            make_plugin_cache(base, "dup", "1.0.0", "skill-x", nested=True,
                              marketplace="community")
            insts = []
            for mp in ("official", "community"):
                skills_root = base / mp / "dup" / "1.0.0" / "skills"
                entry = skills_root / "skill-x"
                insts.append({
                    "instance_id": "i-" + mp, "location_id": "cache", "is_skill": True,
                    "logical_name": "skill-x", "directory_name": "skill-x",
                    "path": str(entry), "real_path": str(entry), "is_symlink": False,
                    "kind": "plugin-cache", "plugin_name": "dup", "plugin_version": "1.0.0",
                    "plugin_marketplace": mp, "tree_hash": "x", "mutable": False})
            loaded, stale = _effective_loaded(insts)
            self.assertEqual(len(stale), 0, "跨 marketplace 同名插件互不算旧版本残留")
            self.assertEqual(len(loaded), 2)


class CheckUpdatesInputContractTests(unittest.TestCase):
    def test_missing_inventory_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run(
                [sys.executable, "scripts/check_updates.py", "--json",
                 "--inventory", str(Path(td) / "nope.json"),
                 "--output", str(Path(td) / "updates.json")],
                capture_output=True, text=True, env=dict(
                    os.environ, HOME=td, SKILL_KEEPER_STAGING=str(Path(td) / "staging")),
                cwd=str(REPO_ROOT))
            self.assertEqual(r.returncode, 2, "输入缺失必须退出 2,不能假装无差异")


if __name__ == "__main__":
    unittest.main()
