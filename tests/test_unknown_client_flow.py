"""Task 3(v4)端到端:虚构未知客户端不改适配器,经模型位置声明完成盘点。

覆盖:--root / --locations-json FILE / --locations-json -(stdin)三种入口;
真实路径去重(本机事实优先);重复检测;报告"客户端自报"标注;
临时声明零持久化、零变更入口;恶意声明拒绝且不落盘。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

from scripts.core.changes import ChangeError, create_remove_plan   # noqa: E402
from scripts.core.policy import check_action, load_policy          # noqa: E402
from scripts.core.provenance import load_user_config               # noqa: E402
from scripts.scan import InventoryError, build_inventory           # noqa: E402
from tests.helpers import temp_home, write_skill                   # noqa: E402

CLIENT = "fictitious-agent"


def _unknown_client_env(testcase):
    """虚构客户端两个根:根 A 有 fa-alpha(与根 B 同内容)+ fa-beta;根 B 有 fa-alpha 副本。"""
    home = temp_home(testcase)
    data = home / "data"
    data.mkdir()
    root_a = home / ".fictitious-agent" / "skills"
    root_b = home / ".fictitious-agent" / "projects" / "skills"
    alpha = write_skill(root_a, "fa-alpha", description="虚构技能 A", version="1.0.0",
                        body="same-body")
    write_skill(root_b, "fa-alpha", description="虚构技能 A", version="1.0.0",
                body="same-body")
    write_skill(root_a, "fa-beta", description="虚构技能 B", version="2.0.0")
    write_skill(home / ".agents" / "skills", "shared-real", description="共享库真实技能")
    return home, data, alpha


def _declaration(home, complete=False):
    return {
        "schema_version": 1, "client": CLIENT, "observed_by": "model",
        "complete": complete,
        "roots": [{"path": str(home / ".fictitious-agent" / "skills"),
                   "scope": "user", "load_state": "reported"},
                  {"path": str(home / ".fictitious-agent" / "projects" / "skills"),
                   "scope": "user", "load_state": "reported"}],
    }


class InProcessFlowTests(unittest.TestCase):
    def test_inventory_includes_unknown_client_with_duplicates(self):
        home, data, _ = _unknown_client_env(self)
        model_roots = [{"client": CLIENT, "path": str(home / ".fictitious-agent" / "skills"),
                        "scope": "user", "load_state": "reported", "complete": False},
                       {"client": CLIENT, "path": str(home / ".fictitious-agent" / "projects" / "skills"),
                        "scope": "user", "load_state": "reported", "complete": False}]
        inv = build_inventory(home, data, model_roots=model_roots)
        clients = {i["client"] for i in inv["instances"]}
        self.assertIn(CLIENT, clients)
        self.assertIn("shared", clients)
        fa = [i for i in inv["instances"] if i["directory_name"] == "fa-alpha"]
        self.assertEqual(len(fa), 2, "两个根各一份 fa-alpha")
        self.assertTrue(all(not i["mutable"] for i in fa), "临时声明实例永远不可变")
        self.assertTrue(all("model-declaration" in i["evidence"] for i in fa))
        dups = [f for f in inv["findings"] if f["code"] == "duplicate-load"
                and f["skill"] == "fa-alpha"]
        self.assertEqual(len(dups), 1, "同客户端两个根的同名技能必须报重复")
        self.assertIn("自报", dups[0]["message"])
        self.assertTrue(inv["observation"]["complete"])
        self.assertEqual(inv["observation"]["observed_scope"].get("model_roots"), 2)
        # 已知客户端的精确口径不受模型声明污染
        self.assertNotIn("model-declaration",
                         [i for i in inv["instances"] if i["client"] == "shared"][0]["evidence"])

    def test_declared_missing_root_recorded_but_never_scanned(self):
        home, data, _ = _unknown_client_env(self)
        inv = build_inventory(home, data, model_roots=[
            {"client": CLIENT, "path": str(home / "no-such-dir"),
             "scope": "user", "load_state": "reported", "complete": False}])
        self.assertTrue(any(f["code"] == "model-root-missing" for f in inv["findings"]))
        self.assertFalse(any(i["client"] == CLIENT for i in inv["instances"]))

    def test_model_root_outside_declared_home_is_rejected(self):
        home, data, _ = _unknown_client_env(self)
        with self.assertRaises(InventoryError):
            build_inventory(home, data, model_roots=[
                {"client": CLIENT, "path": str(home.parent), "scope": "user",
                 "load_state": "reported", "complete": False}])

    def test_realpath_dedup_local_facts_win(self):
        home, data, _ = _unknown_client_env(self)
        shared = home / ".agents" / "skills"
        inv = build_inventory(home, data, model_roots=[
            {"client": CLIENT, "path": str(shared),
             "scope": "user", "load_state": "reported", "complete": False}])
        self.assertEqual(len(inv["instances"]),
                         len(build_inventory(home, data)["instances"]),
                         "与共享库同真实路径的声明必须被丢弃,不得双份扫描")
        self.assertFalse(any(i["client"] == CLIENT for i in inv["instances"]))
        claims = inv["observation"]["reported_roots"]
        self.assertEqual([(r["client"], r["location_id"]) for r in claims],
                         [(CLIENT, "shared")],
                         "物理目录只扫一次，但客户端读取共享库的关系不能丢")
        self.assertEqual(inv["client_load"][CLIENT]["entries"], 1)
        self.assertTrue(inv["client_load"][CLIENT]["reported"])

    def test_two_unknown_clients_can_report_same_shared_root(self):
        home, data, _ = _unknown_client_env(self)
        shared = home / ".agents" / "skills"
        inv = build_inventory(home, data, model_roots=[
            {"client": "agent-one", "path": str(shared), "scope": "user",
             "load_state": "reported", "complete": False},
            {"client": "agent-two", "path": str(shared), "scope": "user",
             "load_state": "reported", "complete": False},
        ])
        claims = inv["observation"]["reported_roots"]
        self.assertEqual({(r["client"], r["location_id"]) for r in claims},
                         {("agent-one", "shared"), ("agent-two", "shared")})
        self.assertEqual(inv["client_load"]["agent-one"]["entries"], 1)
        self.assertEqual(inv["client_load"]["agent-two"]["entries"], 1)

    def test_model_instances_have_no_change_entry(self):
        home, data, alpha = _unknown_client_env(self)
        inv = build_inventory(home, data, model_roots=[
            {"client": CLIENT, "path": str(home / ".fictitious-agent" / "skills"),
             "scope": "user", "load_state": "reported", "complete": False}])
        inst = next(i for i in inv["instances"] if i["directory_name"] == "fa-alpha")
        policy = load_policy(data)
        allowed = check_action("remove", inst,
                               next(l for l in inv["locations"] if l["client"] == CLIENT),
                               policy)
        self.assertFalse(allowed["allowed"], "模型临时位置绝不能有变更入口")
        with self.assertRaises(ChangeError):
            create_remove_plan([inst["instance_id"]], inv, "测试:模型位置必须被拒",
                               data / "change-plans",
                               known_sources=load_user_config(data))
        self.assertTrue(alpha.exists())


class CliFlowTests(unittest.TestCase):
    def _run_scan(self, env, *args, stdin_text=None):
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "scan.py"), *args],
            capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
            input=stdin_text, timeout=180)

    def _env(self, home, data):
        return dict(os.environ, HOME=str(home), SKILL_KEEPER_DATA=str(data))

    def test_locations_json_file_and_stdin_and_root_flag(self):
        for variant in ("file", "stdin", "root"):
            with self.subTest(variant=variant):
                with tempfile.TemporaryDirectory() as td:
                    home = Path(td) / "home"
                    home.mkdir()
                    data = home / "data"
                    data.mkdir()
                    write_skill(home / ".fictitious-agent" / "skills", "fa-alpha",
                                description="虚构技能", version="1.0.0")
                    env = self._env(home, data)
                    decl = json.dumps(_declaration(home), ensure_ascii=False)
                    if variant == "file":
                        decl_path = Path(td) / "decl.json"
                        decl_path.write_text(decl, encoding="utf-8")
                        r = self._run_scan(env, "--locations-json", str(decl_path), "--json")
                    elif variant == "stdin":
                        r = self._run_scan(env, "--locations-json", "-", "--json",
                                           stdin_text=decl)
                    else:
                        r = self._run_scan(
                            env, "--root",
                            "{}={}".format(CLIENT, home / ".fictitious-agent" / "skills"),
                            "--json")
                    self.assertEqual(r.returncode, 0, r.stderr[-400:] + r.stdout[-400:])
                    summary = json.loads(r.stdout[r.stdout.index("{"):])
                    self.assertEqual(summary["observation_complete"], True)
                    inv = json.loads((data / "inventory.json").read_text(encoding="utf-8"))
                    self.assertTrue(any(i["client"] == CLIENT for i in inv["instances"]))

    def test_declaration_is_never_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            data = home / "data"
            data.mkdir()
            write_skill(home / ".fictitious-agent" / "skills", "fa-alpha",
                        description="虚构技能")
            env = self._env(home, data)
            decl_path = Path(td) / "decl.json"
            decl_path.write_text(json.dumps(_declaration(home)), encoding="utf-8")
            r = self._run_scan(env, "--locations-json", str(decl_path))
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            self.assertFalse((data / "client-locations.json").exists(),
                             "临时声明不得写入本地配置")
            persisted = json.loads((data / "inventory.json").read_text(encoding="utf-8"))
            self.assertNotIn("observed_by", persisted, "原始声明不得整体落盘")
            self.assertFalse(any(i["mutable"] for i in persisted["instances"]),
                             "模型临时实例落盘时也必须保持不可变")

    def test_malicious_declaration_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            data = home / "data"
            data.mkdir()
            write_skill(home / ".fictitious-agent" / "skills", "fa-alpha",
                        description="虚构技能")
            env = self._env(home, data)
            malicious = json.dumps({"schema_version": 1, "client": CLIENT,
                                    "mutable": True,
                                    "roots": [{"path": str(home / ".fictitious-agent" / "skills")}]})
            decl_path = Path(td) / "evil.json"
            decl_path.write_text(malicious, encoding="utf-8")
            r = self._run_scan(env, "--locations-json", str(decl_path), "--json")
            self.assertEqual(r.returncode, 2, "恶意声明必须整体拒绝")
            self.assertNotIn("FAKE", r.stdout)
            self.assertFalse((data / "inventory.json").exists(),
                             "拒绝发生在扫描之前,不得产生任何落盘")

    def test_root_outside_home_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            data = home / "data"
            data.mkdir()
            env = self._env(home, data)
            r = self._run_scan(env, "--root", "{}={}".format(CLIENT, home.parent),
                               "--json")
            self.assertEqual(r.returncode, 2)
            self.assertFalse((data / "inventory.json").exists())

    def test_report_marks_model_reported_clients(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            data = home / "data"
            data.mkdir()
            write_skill(home / ".fictitious-agent" / "skills", "fa-alpha",
                        description="虚构技能")
            env = self._env(home, data)
            decl_path = Path(td) / "decl.json"
            decl_path.write_text(json.dumps(_declaration(home)), encoding="utf-8")
            r = self._run_scan(env, "--locations-json", str(decl_path))
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            r = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "report.py")],
                               capture_output=True, text=True, env=env,
                               cwd=str(REPO_ROOT), timeout=180)
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            md = (data / "report.md").read_text(encoding="utf-8")
            self.assertIn("fictitious-agent", md)
            self.assertIn("客户端自报", md)

    def test_report_keeps_unknown_client_relationship_to_shared_root(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            data = home / "data"
            data.mkdir()
            write_skill(home / ".agents" / "skills", "shared-real",
                        description="共享库真实技能")
            env = self._env(home, data)
            r = self._run_scan(env, "--root",
                               "{}={}".format(CLIENT, home / ".agents" / "skills"))
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            r = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "report.py")],
                               capture_output=True, text=True, env=env,
                               cwd=str(REPO_ROOT), timeout=180)
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            md = (data / "report.md").read_text(encoding="utf-8")
            self.assertIn("fictitious-agent(自报)", md)


if __name__ == "__main__":
    unittest.main()
