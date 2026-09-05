"""check_updates 的 staging 生命周期:候选目录被多个逻辑共享时不得误删。

回归:同一上游(同 repo/path/commit)装了两份不同版本时,"已是最新"的那份
会在检查后清掉共享的 cand-<hash> 目录,把另一个待更新项引用的 staging 一并删掉,
导致后续 create_update_plan 因"staging 目录不存在"而失败。
"""
import json, os, tempfile, unittest
from pathlib import Path

from scripts.check_updates import check
from scripts.core.fingerprint import tree_hash
from scripts.core.github import fetch_skill_tree
from tests.helpers import write_skill


def b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode()


class FakeGh:
    def __init__(self, responses):
        self.responses = responses

    def __call__(self, args):
        key = " ".join(args)
        if key not in self.responses:
            return 1, "{}"
        val = self.responses[key]
        return 0, val if isinstance(val, str) else json.dumps(val)


HEAD_SKILL = "---\nname: dupe\ndescription: upstream head version\n---\n\n# dupe v2\n"
STALE_SKILL = "---\nname: dupe\ndescription: stale local copy\n---\n\n# dupe v1\n"


def head_gh():
    return FakeGh({
        "repos/example/dupe": {"stargazers_count": 5, "forks_count": 1,
                               "archived": False, "default_branch": "main",
                               "pushed_at": "2026-08-01T00:00:00Z"},
        "repos/example/dupe/commits/main": {"sha": "headsha"},
        "repos/example/dupe/git/trees/headsha?recursive=1": {"tree": [
            {"path": "skills/dupe/SKILL.md", "type": "blob", "sha": "b1",
             "mode": "100644"},
        ]},
        "repos/example/dupe/git/blobs/b1": {"content": b64(HEAD_SKILL.encode()),
                                            "encoding": "base64"},
    })


def two_copy_inventory(home):
    stale = write_skill(home / ".agents/skills", "dupe", body="v1-local")
    # write_bytes:Windows 文本模式会把 \n 翻译成 \r\n,导致与上游候选树假性差异
    (stale / "SKILL.md").write_bytes(STALE_SKILL.encode("utf-8"))
    fresh = write_skill(home / ".accio/accounts/a/skills", "dupe", body="v2-local")
    (fresh / "SKILL.md").write_bytes(HEAD_SKILL.encode("utf-8"))
    inst = []
    for iid, path in (("inststale0000000000001", stale), ("instfresh0000000000001", fresh)):
        inst.append({"instance_id": iid, "location_id": "shared" if "agents" in str(path) else "accio-a",
                     "client": "shared", "kind": "user", "directory_name": "dupe",
                     "path": str(path), "real_path": str(path), "is_symlink": False,
                     "is_skill": True, "mutable": True, "logical_name": "dupe",
                     "tree_hash": tree_hash(path), "description": "", "version": "",
                     "function": "", "trigger": "auto", "context_bytes": 10,
                     "requires_bins": []})
    logicals = []
    for lg_id, iid, th in (("lg-stale", "inststale0000000000001", inst[0]["tree_hash"]),
                           ("lg-fresh", "instfresh0000000000001", inst[1]["tree_hash"])):
        logicals.append({"logical_id": lg_id, "name": "dupe", "tree_hash": th,
                         "instance_ids": [iid], "clients": ["shared"], "function": "",
                         "trigger": "auto", "version": "", "context_bytes": 10})
    return {"schema_version": 2, "locations": [], "instances": inst,
            "logical_skills": logicals, "findings": [], "config_issues": [],
            "total": 2, "operational_ok": True, "health_status": "ok"}


class CheckUpdatesStagingTests(unittest.TestCase):
    def test_shared_staging_dir_survives_uptodate_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            data = home / "data"
            data.mkdir()
            (data / "known-sources.json").write_text(json.dumps(
                {"dupe": {"type": "github", "repo": "example/dupe",
                          "path": "skills/dupe/SKILL.md"}}), encoding="utf-8")
            result = check(two_copy_inventory(home), data, data / "updates.json", head_gh(),
                           staging_root=home / "staging")
            names = [d["name"] for d in result["differs"]]
            self.assertEqual(names, ["dupe"], "只有过期副本进入 differs(最新副本不算差异)")
            staging = Path(result["differs"][0]["staging_path"])
            self.assertTrue(staging.is_dir(),
                            "候选 staging 目录不得被另一个逻辑的最新检查误删")
            self.assertTrue((staging / "SKILL.md").exists())

    def test_stale_leftover_candidates_are_swept(self):
        """历史残留清扫:本工具登记所有权且不再被引用的候选必须清掉;
        无所有权记录的历史目录与不相关目录只能保留(F07 边界,任务 2)。"""
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            data = home / "data"
            data.mkdir()
            (data / "known-sources.json").write_text(json.dumps(
                {"dupe": {"type": "github", "repo": "example/dupe",
                          "path": "skills/dupe/SKILL.md"}}), encoding="utf-8")
            stale = home / "staging/cand-deadbeef0000"
            write_skill(stale, "dupe", description="leftover")
            fresh2 = home / "staging/cand-0123456789ab"
            write_skill(fresh2, "dupe", description="pending")
            from scripts.core.staging import record_ownership
            record_ownership(home / "staging", stale.name, {"candidate_hash": "d" * 64})
            record_ownership(home / "staging", fresh2.name, {"candidate_hash": "0" * 64})
            sentinel = home / "staging/unrelated-user-data"
            sentinel.mkdir()
            legacy = home / "staging/cand-oldtool0000"  # 无所有权记录的历史目录
            legacy.mkdir()
            (data / "updates.json").write_text(json.dumps({"differs": [
                {"name": "dupe", "staging_path": str(fresh2)}]}), encoding="utf-8")
            result = check(two_copy_inventory(home), data, data / "updates.json", head_gh(),
                           staging_root=home / "staging")
            self.assertFalse(stale.exists(), "登记所有且无引用的历史候选必须被清扫")
            self.assertTrue(sentinel.exists(), "不相关目录绝不能被清扫")
            self.assertTrue(legacy.exists(), "无所有权记录的历史目录只能保留待清理")
            self.assertTrue((home / "staging").is_dir())
            # 被本次 differs 引用的候选仍在
            kept = Path(result["differs"][0]["staging_path"])
            self.assertTrue(kept.is_dir())


if __name__ == "__main__":
    unittest.main()
