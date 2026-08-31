import json, unittest
from pathlib import Path

from tests.helpers import FAKE_SECRET, build_multi_client_home, temp_home
from scripts.core.clients import client_load_aliases, discover_locations, discover_skill_roots


class ClientDiscoveryTests(unittest.TestCase):
    def test_clients_marketplaces_aliases_and_secrets(self):
        home = build_multi_client_home(self)
        rows = discover_locations(home, home / "project-data")
        ids = {x.location_id for x in rows}
        self.assertTrue(
            {"shared", "claude-user", "codex-user", "accio-account-a",
             "workbuddy-user", "cindy-codex-home"} <= ids,
            "七类客户端的加载位置必须被识别,缺:{}".format(ids))
        self.assertFalse(any(
            "skills-marketplace" in x.path or "connectors-marketplace" in x.path
            for x in rows if x.kind == "user"),
            "marketplace 商品目录不得成为已安装位置")
        self.assertFalse(any("plugins/marketplaces" in x.path for x in rows),
            "Claude marketplace checkout 不得成为加载位置")
        haha = next(x for x in rows if x.location_id == "claude-user")
        self.assertIn("haha", haha.aliases)
        rendered = json.dumps([x.to_dict() for x in rows])
        self.assertNotIn(FAKE_SECRET, rendered)

    def test_builtin_and_plugin_cache_locations_are_immutable(self):
        rows = discover_locations(build_multi_client_home(self), Path("/tmp/data"))
        self.assertTrue(all(not x.mutable for x in rows if x.kind in {"builtin", "plugin-cache"}),
                        "builtin/plugin-cache 位置一律不可变")

    def test_numeric_accio_account_is_hashed_not_revealed(self):
        home = build_multi_client_home(self)
        ids = {x.location_id for x in discover_locations(home, home / "project-data")}
        self.assertNotIn("accio-account-10086", ids, "纯数字账号目录名不得直接出现在 ID 里")

    def test_marketplace_contents_do_not_create_locations(self):
        home = build_multi_client_home(self)
        paths = {x.path for x in discover_locations(home, home / "project-data")}
        self.assertNotIn(str(home / ".workbuddy/skills-marketplace"), paths)
        self.assertNotIn(str(home / ".claude/plugins/marketplaces"), paths)

    def test_plugin_cache_skill_roots(self):
        home = build_multi_client_home(self)
        rows = discover_locations(home, home / "project-data")
        zc = next(x for x in rows if x.location_id == "zcode-plugin-cache")
        roots = discover_skill_roots(zc)
        self.assertTrue(roots, "ZCode 插件缓存必须解析出 skills 根目录")
        self.assertTrue(all(p.name == "skills" for p in roots))

    def test_haha_alias_requires_marker_and_empty_home_is_safe(self):
        home = temp_home(self)
        self.assertEqual(discover_locations(home, home / "data"), [])
        self.assertEqual(client_load_aliases(home), {})

    def test_accio_installed_manifest_is_field_whitelisted(self):
        from scripts.core.clients.accio import accio_installed_entries
        home = build_multi_client_home(self)
        entries = accio_installed_entries(home / ".accio/accounts/a")
        self.assertEqual(entries[0]["name"], "accio-skill")
        self.assertNotIn(FAKE_SECRET, json.dumps(entries), "安装清单只允许白名单字段")


if __name__ == "__main__":
    unittest.main()
