import base64, json, tempfile, unittest
from pathlib import Path
from urllib.parse import quote

from scripts.core.github import fetch_skill_tree, repo_snapshot
from scripts.core.provenance import classify_provenance, search_source_candidates


def fake_instance(directory="demo", logical_name=None, kind="user", source=None):
    return {"instance_id": "inst-" + directory, "location_id": "shared",
            "directory_name": directory, "logical_name": logical_name or directory,
            "kind": kind, "client": "shared", "tree_hash": "a" * 64,
            "source": source or {}}


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class FakeGh:
    """可注入的 gh api 替身:按端点精确匹配返回 (code, body)。"""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, args):
        key = " ".join(args)
        self.calls.append(key)
        if key not in self.responses:
            return 1, '{"message": "Not Found"}'
        val = self.responses[key]
        return 0, val if isinstance(val, str) else json.dumps(val)

    @staticmethod
    def multi_skill_repo():
        return FakeGh({
            "repos/owner/multi-skills": {
                "stargazers_count": 1200, "forks_count": 45, "archived": False,
                "pushed_at": "2026-08-01T00:00:00Z", "created_at": "2024-01-01T00:00:00Z",
                "open_issues_count": 3, "license": {"spdx_id": "MIT"},
                "default_branch": "main"},
            "repos/owner/multi-skills/commits/main": {"sha": "head123"},
            "repos/owner/multi-skills/contributors?per_page=100&anon=true": [{"login": "a"}, {"login": "b"}],
            "repos/owner/multi-skills/releases/latest": {"tag_name": "v1.2.0"},
        })

    @staticmethod
    def tree_with_binary():
        return FakeGh({
            "repos/o/r/git/trees/abc123?recursive=1": {"tree": [
                {"path": "skills/demo", "type": "tree", "sha": "d"},
                {"path": "skills/demo/SKILL.md", "type": "blob", "sha": "b3"},
                {"path": "skills/demo/scripts/run.py", "type": "blob", "sha": "b1"},
                {"path": "skills/demo/assets/icon.bin", "type": "blob", "sha": "b2"},
            ]},
            "repos/o/r/git/blobs/b1": {"content": b64(b"print('ok')\n"), "encoding": "base64"},
            "repos/o/r/git/blobs/b2": {"content": b64(b"\x00\xff"), "encoding": "base64"},
            "repos/o/r/git/blobs/b3": {"content": b64(b"hello\n"), "encoding": "base64"},
        })


class ProvenanceGithubTests(unittest.TestCase):
    def test_prefix_and_frontmatter_name_do_not_grant_builtin_or_self_built(self):
        row = fake_instance(directory="autoglm-untrusted", logical_name="trusted self skill")
        result = classify_provenance(row, receipts={},
                                     known_sources={"trusted-dir": {"type": "self-built"}})
        self.assertEqual(result["class"], "third-party")

    def test_repo_snapshot_labels_repo_level_popularity(self):
        snap = repo_snapshot("owner/multi-skills", FakeGh.multi_skill_repo())
        self.assertEqual(snap["stars"], 1200)
        self.assertEqual(snap["popularity_scope"], "repository")
        self.assertNotIn("users", snap)

    def test_binary_and_nested_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            result = fetch_skill_tree("o/r", "skills/demo", "abc123", Path(td), FakeGh.tree_with_binary())
            self.assertEqual((Path(td) / "scripts/run.py").read_bytes(), b"print('ok')\n")
            self.assertEqual((Path(td) / "assets/icon.bin").read_bytes(), b"\x00\xff")
            self.assertEqual(result["commit_sha"], "abc123")
            self.assertTrue((Path(td) / "SKILL.md").exists())

    def test_self_built_and_receipt_evidence_protect(self):
        row = fake_instance(directory="my-tool")
        r1 = classify_provenance(row, {}, {"my-tool": {"type": "self-built"}})
        self.assertEqual(r1["class"], "protected")
        self.assertEqual(r1["type"], "self-built")
        r2 = classify_provenance(row, {"inst-my-tool": {"type": "plugin"}}, {})
        self.assertEqual(r2["class"], "protected")
        r3 = classify_provenance(row, {}, {})  # 无任何证据 → 第三方
        self.assertEqual(r3["class"], "third-party")

    def test_declared_source_is_only_a_candidate(self):
        row = fake_instance(directory="word", source={"type": "github", "repo": "example/word"})
        r = classify_provenance(row, {}, {})
        self.assertEqual(r["class"], "third-party")
        self.assertEqual(r["confidence"], "low")
        self.assertTrue(r.get("review_required"))

    def test_known_sources_github_is_verified_third_party(self):
        row = fake_instance(directory="word")
        r = classify_provenance(row, {}, {"word": {"type": "github", "repo": "example/word",
                                                    "path": "skills/word/SKILL.md"}})
        self.assertEqual(r["class"], "third-party")
        self.assertEqual(r["repo"], "example/word")
        self.assertEqual(r["confidence"], "high")
        self.assertTrue(r.get("review_required"))

    def test_search_candidates_never_autoconfirm(self):
        q = "search/repositories?q={}&per_page=10".format(quote("word skill in:name skill"))
        gh = FakeGh({q: {"items": [{"full_name": "someone/word-skill"}]}})
        cands = search_source_candidates({"logical_name": "word", "directory_name": "word"}, gh)
        self.assertTrue(cands)
        self.assertTrue(all(c["confirmed"] is False and c["confidence"] == "low" for c in cands),
                        "搜索候选永远不能自动确认为来源")

    def test_fetch_rejects_unsafe_and_missing_paths(self):
        base = {
            "repos/x/y/git/trees/c1?recursive=1": {"tree": [
                {"path": "skills/demo/sub/../../../evil.txt", "type": "blob", "sha": "be"},
            ]},
        }
        with tempfile.TemporaryDirectory() as td:
            result = fetch_skill_tree("x/y", "skills/demo", "c1", Path(td), FakeGh(base))
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "unsafe-path")
        with tempfile.TemporaryDirectory() as td:
            result = fetch_skill_tree("x/y", "skills/nope", "c1", Path(td), FakeGh(base))
            self.assertFalse(result["ok"])

    def test_snapshot_failure_is_structured_stale(self):
        snap = repo_snapshot("owner/none", FakeGh({}))
        self.assertFalse(snap.get("ok", True))
        self.assertTrue(snap.get("stale"))

    def test_cli_fixture_and_empty_home(self):
        import os, subprocess, sys
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "updates.json")
            env = dict(os.environ, HOME=td)
            r = subprocess.run(
                [sys.executable, "scripts/check_updates.py",
                 "--inventory", "tests/fixtures/inventory-value.json",
                 "--output", out, "--json"],
                capture_output=True, text=True, env=env,
                cwd=str(Path(__file__).resolve().parents[1]))
            self.assertEqual(r.returncode, 0, "本地内容缺失 → 无法核实,不算差异:" + r.stderr[-300:])
            data = json.loads(r.stdout)
            self.assertTrue(data["operational_ok"])
            self.assertTrue(data["skipped"], "fixture 本地路径不存在,必须以 skipped 呈现")


if __name__ == "__main__":
    unittest.main()
