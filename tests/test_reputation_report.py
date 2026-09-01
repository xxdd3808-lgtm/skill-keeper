"""reputation 缓存自愈与报告热度归属:热度证据必须属于该 Skill 自己的仓库。

背景回归:cached_repo_snapshot 曾把整个旧文件当成缓存表再嵌套写回,导致
reputation.json 一层层套娃;报告则把"缓存里最后一个仓库"的热度套在所有卡片上。
"""
import json, tempfile, unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests/fixtures/inventory-value.json"


def snap(repo, stars, fetched="2026-08-01T00:00:00"):
    return {"ok": True, "repo": repo, "stars": stars, "forks": 1, "archived": False,
            "popularity_scope": "repository", "fetched_at": fetched,
            "popularity_note": "仓库热度,不等于该 Skill 的真实使用人数"}


class FakeGh:
    """成功时返回 repo payload;否则一律非零码(模拟断网/限流)。"""

    def __init__(self, ok_repo=None, payload=None):
        self.ok_repo, self.payload = ok_repo, payload

    def __call__(self, args):
        if self.ok_repo and args[0] == "repos/" + self.ok_repo:
            return 0, json.dumps(self.payload or {})
        return 1, ""


class ReputationCacheTests(unittest.TestCase):
    def test_snapshot_write_does_not_nest_previous_file(self):
        from scripts.core.github import cached_repo_snapshot
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reputation.json"
            path.write_text(json.dumps(
                {"schema_version": 2, "repos": {"example/a": snap("example/a", 7)}}),
                encoding="utf-8")
            gh = FakeGh("example/b", {"stargazers_count": 9, "forks_count": 2,
                                      "default_branch": "main"})
            result = cached_repo_snapshot("example/b", path, gh)
            self.assertTrue(result["ok"])
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(data.keys()), {"schema_version", "repos"},
                             "顶层只允许 schema_version 和 repos")
            self.assertEqual(set(data["repos"].keys()), {"example/a", "example/b"})
            self.assertEqual(data["repos"]["example/a"]["stars"], 7, "旧快照必须原样保留")

    def test_flat_legacy_cache_is_normalized(self):
        from scripts.core.github import cached_repo_snapshot
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reputation.json"
            path.write_text(json.dumps({"example/a": snap("example/a", 7)}), encoding="utf-8")
            gh = FakeGh("example/b", {"stargazers_count": 9, "forks_count": 2,
                                      "default_branch": "main"})
            cached_repo_snapshot("example/b", path, gh)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(data["repos"].keys()), {"example/a", "example/b"})

    def test_failure_keeps_old_snapshot_and_marks_stale(self):
        from scripts.core.github import cached_repo_snapshot
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reputation.json"
            original = json.dumps(
                {"schema_version": 2, "repos": {"example/a": snap("example/a", 7)}})
            path.write_text(original, encoding="utf-8")
            result = cached_repo_snapshot("example/a", path, FakeGh(None))
            self.assertTrue(result.get("stale"), "网络失败必须返回带 stale 标记的旧数据")
            self.assertEqual(result["stars"], 7)
            self.assertEqual(path.read_text(encoding="utf-8"), original, "失败时不得改写缓存")

    def test_deeply_nested_file_self_heals(self):
        from scripts.core.github import cached_repo_snapshot
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reputation.json"
            nested = {"schema_version": 2, "repos": {
                "schema_version": 2,
                "repos": {"schema_version": 2,
                          "repos": {"example/a": snap("example/a", 7, "2026-07-01T00:00:00")},
                          "example/a": snap("example/a", 5, "2026-06-01T00:00:00")},
                "example/b": snap("example/b", 8)}}
            path.write_text(json.dumps(nested), encoding="utf-8")
            gh = FakeGh("example/c", {"stargazers_count": 3, "forks_count": 1,
                                      "default_branch": "main"})
            result = cached_repo_snapshot("example/c", path, gh)
            self.assertTrue(result["ok"])
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(data["repos"].keys()), {"example/a", "example/b", "example/c"})
            self.assertEqual(data["repos"]["example/a"]["stars"], 7,
                             "同一仓库取 fetched_at 最新的快照")


class ReportRepoCardTests(unittest.TestCase):
    def test_repo_card_shows_own_repo_evidence_not_other_repos(self):
        from scripts.report import render_html
        inv = json.loads(FIXTURE.read_text(encoding="utf-8"))
        known = {"word": {"type": "github", "repo": "example/word-skills",
                          "path": "skills/word/SKILL.md"}}
        reputation = {"schema_version": 2, "repos": {
            "example/word-skills": snap("example/word-skills", 4242),
            "example/unrelated": snap("example/unrelated", 9999)}}
        html = render_html(inv, None, {"known": known, "reputation": reputation})
        self.assertIn("example/word-skills", html)
        self.assertIn("4242", html, "必须显示该 Skill 自己仓库的热度")
        self.assertNotIn("9999", html, "绝不能把缓存里别的仓库热度套到这张卡片上")

    def test_unknown_source_card_fabricates_no_stars(self):
        from scripts.report import render_html
        inv = json.loads(FIXTURE.read_text(encoding="utf-8"))
        reputation = {"schema_version": 2, "repos": {
            "example/unrelated": snap("example/unrelated", 9999)}}
        html = render_html(inv, None, {"known": {}, "reputation": reputation})
        self.assertNotIn("9999", html, "来源不明的 Skill 不得显示任何仓库热度")


if __name__ == "__main__":
    unittest.main()
