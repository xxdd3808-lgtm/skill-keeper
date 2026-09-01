"""替代候选语义验收(用户定义):

    「替代 Skill」= 本机已经安装、能覆盖被审查 Skill 主要用途、综合表现更好的另一个 Skill。

硬约束:
- 候选只能来自当前 inventory 已安装的本地逻辑 Skill(GitHub 上有但没装的不算);
- marketplace 商品目录、staging 更新候选、alias/投影/符号链接副本都不是替代品;
- 同一 Skill 的多客户端加载实例合并为同一逻辑身份,不互为替代;
- 仅名称相似或几个共同关键词不能成为候选;宁缺毋滥,允许一个候选都没有;
- 「建议删除」记账必须指名本机已安装替代品的逻辑 ID;
- 没有实测 benchmark,不得生成「性能更好」类结论。
"""
import unittest

from scripts.core.overlap import alternative_candidates
from scripts.core.reviews import build_review_queue, record_review
from scripts.scan import build_inventory
from tests.helpers import build_multi_client_home, write_skill

KNOWN = {
    "pdf-report": {"type": "github", "repo": "example/pdf-report",
                   "path": "skills/pdf-report/SKILL.md"},
    "toolkit": {"type": "github", "repo": "example/toolkit",
                "path": "skills/toolkit/SKILL.md"},
}
INSTALLED_LOGICALS = {"lg-pdf-report", "lg-docx-suite", "lg-tea", "lg-alpha-helper",
                      "lg-alpha-checker", "lg-notes-real", "lg-notes-link",
                      "lg-toolkit-v1", "lg-toolkit-v2"}


def _inst(iid, logical_id, dir_name, name, desc, tree, client="shared", loc="shared",
          kind="user", mutable=True, symlink=False, real_path=None):
    path = real_path or "/fixture/home/.agents/skills/" + dir_name
    return {"instance_id": iid, "location_id": loc, "client": client, "kind": kind,
            "directory_name": dir_name, "path": path, "display_path": path,
            "real_path": real_path or path, "is_symlink": symlink, "is_skill": True,
            "mutable": mutable, "evidence": ["fixture"], "logical_name": name,
            "tree_hash": tree, "description": desc, "version": "1.0",
            "function": desc[:20], "trigger": "auto", "context_bytes": 100,
            "requires_bins": []}


def alt_inventory():
    """虚构 inventory:真替代、受保护内置、无关、弱词元重名、孪生与版本差副本。"""
    instances = [
        _inst("inst-pdf-report", "lg-pdf-report", "pdf-report", "pdf-report",
              "解析 PDF 文档,提取表格与文本,生成 Word 文档版报告", "a" * 64),
        _inst("inst-docx-suite", "lg-docx-suite", "docx-suite", "docx-suite",
              "客户端自带的 PDF 解析、表格提取与 Word 文档报告生成能力",
              "b" * 64, client="codex", loc="codex-system", kind="builtin", mutable=False),
        _inst("inst-tea", "lg-tea", "tea-timer", "tea-timer",
              "泡茶计时提醒器", "c" * 64),
        _inst("inst-alpha-helper", "lg-alpha-helper", "alpha-helper", "alpha-helper",
              "辅助工具集合", "d" * 64),
        _inst("inst-alpha-checker", "lg-alpha-checker", "alpha-checker", "alpha-checker",
              "检查结果校验工具", "e" * 64),
        # 同名同目录的符号链接孪生(内容已漂移,健康问题由 link-drift 负责)
        _inst("inst-notes-real", "lg-notes-real", "notes-helper", "notes-helper",
              "个人笔记速记助手", "f" * 64),
        _inst("inst-notes-link", "lg-notes-link", "notes-helper", "notes-helper",
              "个人笔记速记助手", "0" * 64, symlink=True,
              real_path="/fixture/home/elsewhere/notes-helper"),
        # 同名同目录、同一已核实仓库的版本差副本
        _inst("inst-toolkit-v1", "lg-toolkit-v1", "toolkit", "toolkit",
              "通用工具箱:格式转换与批处理", "1" * 64),
        _inst("inst-toolkit-v2", "lg-toolkit-v2", "toolkit", "toolkit",
              "通用工具箱:格式转换与批处理", "2" * 64),
    ]
    logicals = []
    for lg_id, name, tree, iids in (
            ("lg-pdf-report", "pdf-report", "a" * 64, ["inst-pdf-report"]),
            ("lg-docx-suite", "docx-suite", "b" * 64, ["inst-docx-suite"]),
            ("lg-tea", "tea-timer", "c" * 64, ["inst-tea"]),
            ("lg-alpha-helper", "alpha-helper", "d" * 64, ["inst-alpha-helper"]),
            ("lg-alpha-checker", "alpha-checker", "e" * 64, ["inst-alpha-checker"]),
            ("lg-notes-real", "notes-helper", "f" * 64, ["inst-notes-real"]),
            ("lg-notes-link", "notes-helper", "0" * 64, ["inst-notes-link"]),
            ("lg-toolkit-v1", "toolkit", "1" * 64, ["inst-toolkit-v1"]),
            ("lg-toolkit-v2", "toolkit", "2" * 64, ["inst-toolkit-v2"])):
        logicals.append({"logical_id": lg_id, "name": name, "tree_hash": tree,
                         "instance_ids": iids, "clients": ["shared"], "function": "",
                         "trigger": "auto", "version": "1.0", "context_bytes": 100})
    return {"schema_version": 2, "locations": [], "instances": instances,
            "logical_skills": logicals, "findings": [], "config_issues": [],
            "total": len(logicals), "operational_ok": True, "health_status": "ok"}


DELETE_PAYLOAD = {
    "instance_id": "inst-pdf-report", "verdict": "建议删除",
    "reason": "docx-suite 完整覆盖 PDF 报告生成与表格提取;本机保留一个即可",
    "alternatives": ["lg-docx-suite"], "unique_capabilities": [],
    "loss_if_removed": "失去旧的触发描述;文档处理不受影响",
    "confidence": "高", "evidence": ["overlap:0.51", "coverage:核心功能一致"],
}
KEEP_PAYLOAD = {
    "instance_id": "inst-pdf-report", "verdict": "保留",
    "reason": "功能与内置工具互补,维护活跃", "alternatives": [],
    "unique_capabilities": ["水印模板"], "loss_if_removed": "",
    "confidence": "中", "evidence": ["unique:水印模板", "repo:active"],
}


class AlternativeSemanticTests(unittest.TestCase):
    def test_installed_full_coverage_is_alternative_and_builtin_stays_protected(self):
        cands = alternative_candidates(alt_inventory(), "lg-pdf-report")
        by_id = {c["logical_id"]: c for c in cands}
        self.assertIn("lg-docx-suite", by_id, "已安装且功能覆盖的内置 skill 应成为替代候选")
        self.assertTrue(by_id["lg-docx-suite"]["protected"])
        queue = build_review_queue(alt_inventory(), {}, {}, known_sources=KNOWN)
        ids = {x["logical_id"] for x in queue["items"]}
        self.assertIn("lg-pdf-report", ids)
        self.assertNotIn("lg-docx-suite", ids, "受保护的替代品自身不进入审查队列")

    def test_github_only_or_uninstalled_project_cannot_be_alternative(self):
        cands = alternative_candidates(alt_inventory(), "lg-pdf-report")
        self.assertTrue(all(c["logical_id"] in INSTALLED_LOGICALS for c in cands),
                        "候选只能来自本机已安装的逻辑 Skill")
        queue = build_review_queue(alt_inventory(), {}, {}, known_sources=KNOWN)
        with self.assertRaises(ValueError):
            record_review(queue, dict(DELETE_PAYLOAD, alternatives=["remote-only-logical"]),
                          "m")

    def test_marketplace_and_staging_never_become_alternatives(self):
        home = build_multi_client_home(self)
        staging_skill = home / "project-data/staging/cand-xyz"
        staging_skill.mkdir(parents=True)
        write_skill(staging_skill, "staging-copy")
        inv = build_inventory(home, home / "project-data")
        names = {lg["name"] for lg in inv["logical_skills"]}
        self.assertNotIn("marketplace-copy", names, "marketplace 商品目录不算已安装")
        self.assertNotIn("catalog-entry-x", names)
        self.assertNotIn("staging-copy", names, "staging 更新候选不算已安装")
        queue = build_review_queue(inv, {}, {}, known_sources={})
        cand_names = {c["name"] for x in queue["items"] for c in x["alternative_candidates"]}
        self.assertNotIn("marketplace-copy", cand_names)
        self.assertNotIn("staging-copy", cand_names)

    def test_symlink_twin_and_same_repo_skew_do_not_substitute_each_other(self):
        inv = alt_inventory()
        real_view = [c["logical_id"] for c in alternative_candidates(inv, "lg-notes-real")]
        self.assertNotIn("lg-notes-link", real_view, "符号链接孪生不互为替代")
        link_view = [c["logical_id"] for c in alternative_candidates(inv, "lg-notes-link")]
        self.assertNotIn("lg-notes-real", link_view)
        v1_view = [c["logical_id"] for c in alternative_candidates(inv, "lg-toolkit-v1")]
        self.assertNotIn("lg-toolkit-v2", v1_view, "同仓库同路径的版本差不互为替代")

    def test_name_or_common_keyword_alone_is_not_alternative(self):
        inv = alt_inventory()
        self.assertEqual([c["logical_id"] for c in alternative_candidates(inv, "lg-alpha-helper")],
                         [], "仅名称相似、功能不同不得成为候选")
        self.assertEqual([c["logical_id"] for c in alternative_candidates(inv, "lg-tea")],
                         [], "无关 skill 必须返回空列表")

    def test_candidate_shape_is_explainable_and_capped(self):
        cands = alternative_candidates(alt_inventory(), "lg-pdf-report")
        self.assertLessEqual(len(cands), 8, "宁缺毋滥:候选必须有上限")
        scores = [c["score"] for c in cands]
        self.assertEqual(scores, sorted(scores, reverse=True), "按相似度降序")
        for c in cands:
            self.assertTrue({"logical_id", "instance_id", "name", "protected",
                             "score", "reasons"} <= set(c), "候选必须可解释")

    def test_delete_verdict_requires_named_installed_alternative(self):
        queue = build_review_queue(alt_inventory(), {}, {}, known_sources=KNOWN)
        with self.assertRaises(ValueError):
            record_review(queue, dict(DELETE_PAYLOAD, alternatives=[]), "m")
        with self.assertRaises(ValueError):
            record_review(queue, dict(DELETE_PAYLOAD, alternatives=["not-installed"]), "m")
        saved = record_review(queue, dict(DELETE_PAYLOAD), "m")
        self.assertEqual(saved["alternatives"], ["lg-docx-suite"])
        normalized = record_review(queue, dict(DELETE_PAYLOAD, alternatives=["inst-docx-suite"]),
                                   "m")
        self.assertEqual(normalized["alternatives"], ["lg-docx-suite"],
                         "实例 ID 自动归一到逻辑 ID")

    def test_partial_coverage_confidence_flows_through(self):
        queue = build_review_queue(alt_inventory(), {}, {}, known_sources=KNOWN)
        saved = record_review(queue, dict(DELETE_PAYLOAD, verdict="观察", confidence="低",
                                          reason="只覆盖部分场景,信息不足", alternatives=[],
                                          evidence=["overlap:0.4"]), "m")
        self.assertEqual(saved["verdict"], "观察", "部分覆盖只能进观察/需确认")

    def test_performance_claims_require_benchmark_evidence(self):
        queue = build_review_queue(alt_inventory(), {}, {}, known_sources=KNOWN)
        with self.assertRaises(ValueError):
            record_review(queue, dict(KEEP_PAYLOAD, reason="替代方案性能更好",
                                      evidence=["overlap:0.9", "repo:active"]), "m")
        with self.assertRaises(ValueError):
            record_review(queue, dict(KEEP_PAYLOAD, reason="老 skill 速度太慢",
                                      evidence=["overlap:0.9", "repo:active"]), "m")
        saved = record_review(queue, dict(KEEP_PAYLOAD, reason="替代方案性能更好",
                                          evidence=["benchmark:本机 10 次计时对比",
                                                    "overlap:0.9"]), "m")
        self.assertEqual(saved["verdict"], "保留", "有实测 benchmark 证据才允许性能结论")

    def test_queue_similar_candidates_use_same_threshold(self):
        queue = build_review_queue(alt_inventory(), {}, {}, known_sources=KNOWN)
        for item in queue["items"]:
            for row in item["similar_candidates"]:
                self.assertGreaterEqual(row["score"], 0.32)

    def test_report_shows_candidates_as_unconfirmed_for_review(self):
        from scripts.report import render_html
        inv = alt_inventory()
        queue = build_review_queue(inv, {}, {}, known_sources=KNOWN)
        html = render_html(inv, None, {"known": KNOWN, "queue": queue})
        self.assertIn("替代候选", html)
        self.assertIn("未确认", html, "报告必须明示候选未经确认,不能冒充已核实替代")


if __name__ == "__main__":
    unittest.main()
