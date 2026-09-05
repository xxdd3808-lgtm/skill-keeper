import hashlib, json, os, subprocess, sys, unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "examples/fixtures/inventory-v2.json"

MALICIOUS = "x;touch /tmp/pwn<script>"


def v2_report_fixture(name=None):
    """固定虚构报告数据;name 参数可把第三方 skill 名替换成恶意串,验证转义。"""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if name is not None:
        for inst in data["instances"]:
            if inst["instance_id"] == "aaa1aaa1aaa1aaa1aaa1":
                inst["logical_name"] = name
        for lg in data["logical_skills"]:
            if lg["name"] == "word":
                lg["name"] = name
        for rec in data.get("value_reviews", []):
            if rec["instance_id"] == "aaa1aaa1aaa1aaa1aaa1":
                rec["name"] = name
    return data


class ReportV2Tests(unittest.TestCase):
    def test_value_sections_and_repo_scope_are_explained(self):
        from scripts.report import render_html
        html = render_html(v2_report_fixture())
        for label in ("建议保留", "优先保留另一个", "观察", "建议删除", "需要人工确认"):
            self.assertIn(label, html)
        self.assertIn("仓库热度,不等于该 Skill 的真实使用人数", html)
        self.assertIn("删除后可能失去", html)
        self.assertIn("受保护", html)
        self.assertIn("过期", html, "过期结论必须明显标注")

    def test_untrusted_names_are_escaped_and_static_action_has_no_shell_command(self):
        from scripts.report import render_html
        html = render_html(v2_report_fixture(name=MALICIOUS))
        self.assertNotIn("<script>touch", html)
        self.assertNotIn("remove_skill.py " + MALICIOUS, html)
        self.assertIn("aaa1aaa1aaa1aaa1aaa1", html, "静态操作只出现安全的 instance_id")

    def test_markdown_render_works(self):
        from scripts.report import render_md
        md, *_ = render_md(v2_report_fixture(), None, None)
        self.assertIn("价值审查", md)
        self.assertIn("建议删除", md)

    def test_backup_section_renders_without_crash(self):
        """回归(2026-09-02):备份行模板误用 {kb}/{ts} 命名占位符,render 一遇到
        真实备份列表就 KeyError,网页版从 9-01 起一直渲染失败而测试全绿——
        因为测试从未传过 ctx.backups。备份区必须在 HTML 与 Markdown 双通道有覆盖。"""
        from scripts.report import render_html, render_md
        # F08:行结构改为 backup_id + filename;恢复按钮带 backup_id 而非文件名
        ctx = {"backups": [{"backup_id": "20260902-090000-abcdef",
                            "filename": "backup-20260902-090000-abcdef.tar.gz",
                            "path": "/fixture/backups/backup-20260902-090000-abcdef.tar.gz",
                            "kb": 12, "ts": "2026-09-02 09:00:00",
                            "verification_status": "ok"}]}
        html = render_html(v2_report_fixture(), None, ctx)
        self.assertIn("backup-20260902-090000-abcdef.tar.gz", html)
        self.assertIn("12 KB", html)
        md, _ = render_md(v2_report_fixture(), None, ctx)
        self.assertIn("backup-20260902-090000-abcdef.tar.gz", md)

    def test_dashboard_metrics_navigate_to_matching_sections(self):
        """顶部指标必须有稳定落点;大区块默认收起,点击后由 JS 展开祖先 details。"""
        from scripts.report import render_html
        base = v2_report_fixture()
        html = render_html(base, None, {
            "updates": base["updates"],
            "updates_checked_at": "2026-09-02 10:00:00",
        })
        for target in ("instance-details", "protected-skills", "third-party-review",
                       "health-yellow", "update-review", "verdict-delete",
                       "verdict-confirm", "verdict-keep", "verdict-prefer-other",
                       "verdict-observe"):
            self.assertIn('id="{}"'.format(target), html)
        self.assertIn('href="#health-yellow"', html)
        self.assertIn('href="#update-review"', html)
        self.assertNotIn('href="#verdict-delete"', html,
                         "零值结论只展示为不可点击指标")
        self.assertIn("待更新/复核", html)
        self.assertIn("上次检查:2026-09-02 10:00:00", html)
        self.assertIn('<details id="protected-skills">', html,
                      "大区块默认收起,避免报告打开即被长表淹没")
        self.assertIn('<details id="instance-details">', html)
        self.assertIn("function openJump(hash)", html)

    def test_findings_are_attached_to_their_instance_not_same_name_siblings(self):
        """同名不同实例的告警不能互相复制,否则顶部黄灯与明细会对不上。"""
        from scripts.report import render_html
        base = json.loads(json.dumps(v2_report_fixture()))
        base["instances"].append({
            "instance_id": "fff2fff2fff2fff2fff2", "location_id": "shared",
            "client": "shared", "kind": "user", "directory_name": "word-copy",
            "path": "/fixture/home/.agents/skills/word-copy",
            "real_path": "/fixture/home/.agents/skills/word-copy", "is_symlink": False,
            "is_skill": True, "mutable": True, "logical_name": "word",
            "tree_hash": "9" * 64, "description": "同名第二份", "function": "",
            "trigger": "auto", "context_bytes": 100, "requires_bins": [],
        })
        base["logical_skills"].append({
            "logical_id": "lg-word-2", "name": "word", "tree_hash": "9" * 64,
            "instance_ids": ["fff2fff2fff2fff2fff2"], "clients": ["shared"],
            "function": "", "trigger": "auto", "version": "", "context_bytes": 100,
        })
        base["findings"].append({
            "code": "duplicate-only", "severity": "yellow",
            "instance_id": "fff2fff2fff2fff2fff2", "skill": "word",
            "location_id": "shared", "message": "仅第二份实例的问题", "ignored": False,
        })
        html = render_html(base)
        original = html[html.index('id="instance-aaa1aaa1aaa1aaa1aaa1"'):]
        original = original[:original.index("</tr>")]
        sibling = html[html.index('id="instance-fff2fff2fff2fff2fff2"'):]
        sibling = sibling[:sibling.index("</tr>")]
        self.assertNotIn("仅第二份实例的问题", original)
        self.assertIn("仅第二份实例的问题", sibling)

    def test_same_name_logicals_show_their_own_verdicts(self):
        """两个同名逻辑 skill(不同内容/实例)必须各自显示自己的审查结论。"""
        from scripts.report import render_html
        base = json.loads(json.dumps(v2_report_fixture()))
        # 在 fixture 基础上叠加第二个同名 "word" 逻辑(不同 tree_hash/实例),各自带不同结论
        base["instances"].append({
            "instance_id": "fff2fff2fff2fff2fff2", "location_id": "shared",
            "client": "shared", "kind": "user", "directory_name": "word-accio",
            "path": "/fixture/home/.agents/skills/word-accio",
            "real_path": "/fixture/home/.agents/skills/word-accio",
            "is_symlink": False, "is_skill": True, "mutable": True,
            "logical_name": "word", "tree_hash": "9" * 64,
            "description": "同名不同内容的第二份", "function": "同名第二份",
            "trigger": "auto", "context_bytes": 100, "requires_bins": []})
        base["logical_skills"].append({
            "logical_id": "lg-word-2", "name": "word", "tree_hash": "9" * 64,
            "instance_ids": ["fff2fff2fff2fff2fff2"], "clients": ["shared"],
            "function": "", "trigger": "auto", "version": "", "context_bytes": 100})
        base["value_reviews"] = base.get("value_reviews", []) + [{
            "review_id": "rv-2", "instance_id": "fff2fff2fff2fff2fff2",
            "logical_id": "lg-word-2", "name": "word", "verdict": "观察",
            "reason": "与另一份 word 同名不同版,先观察", "alternatives": [],
            "unique_capabilities": [], "loss_if_removed": "", "confidence": "低",
            "evidence": ["dup:同名不同版", "source:unknown"],
            "skill_tree_hash": "9" * 64, "reviewed_at": "2026-09-01 10:00:00",
            "reviewer_model": "test"}]
        html = render_html(base, None, None)
        self.assertIn("同名不同版", html, "第二个同名逻辑的结论必须出现在报告里")

    def test_keep_verdict_lands_in_keep_group(self):
        """记账 verdict「保留」必须进「建议保留」分组(回归:文字不同导致全部落到未审查)。"""
        from scripts.report import render_html
        base = json.loads(json.dumps(v2_report_fixture()))
        rec = next(r for r in base["value_reviews"] if r["verdict"] == "保留")
        lg = next(l for l in base["logical_skills"] if l["name"] == "notes-pro")
        rec["logical_id"] = lg["logical_id"]
        rec["skill_tree_hash"] = lg["tree_hash"]
        html = render_html(base, None, None)
        self.assertGreater(html.count("建议保留</b>"), 0, "分组必须存在")
        self.assertIn("功能独特", html, "保留结论的理由必须渲染出来")

    def test_sample_report_is_deterministic(self):
        out1 = REPO_ROOT / "examples/report-sample.html"
        # 先生成一次消除磁盘历史状态,再连跑两次对比(同输入必须同输出)
        subprocess.run([sys.executable, "scripts/make_sample_report.py"],
                       capture_output=True, text=True, cwd=str(REPO_ROOT), check=True)
        h1 = hashlib.sha256(out1.read_bytes()).hexdigest()
        subprocess.run([sys.executable, "scripts/make_sample_report.py"],
                       capture_output=True, text=True, cwd=str(REPO_ROOT), check=True)
        h2 = hashlib.sha256(out1.read_bytes()).hexdigest()
        self.assertEqual(h1, h2, "两次生成的示例报告必须逐字节一致")
        self.assertNotIn("/Users/", out1.read_text(encoding="utf-8"),
                         "示例报告不得包含个人绝对路径")

    def test_sample_fixture_has_no_personal_data(self):
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("/Users/", text)
        self.assertNotIn("sk-", text)

    def test_builtin_app_instances_are_protected_in_report(self):
        from scripts.report import render_html
        inv = {
            "schema_version": 2, "scanned_at": "2026-09-01 10:00:00", "home_display": "~",
            "locations": [{"location_id": "shared", "client": "shared",
                           "path": "/fixture/home/.agents/skills", "kind": "user",
                           "mutable": True, "evidence": ["t"], "aliases": []}],
            "instances": [{
                "instance_id": "inst-ba-0000000000000", "location_id": "shared",
                "client": "shared", "kind": "user", "directory_name": "autoglm-websearch",
                "path": "/fixture/home/.agents/skills/autoglm-websearch",
                "real_path": "/fixture/home/.agents/skills/autoglm-websearch",
                "is_symlink": False, "is_skill": True, "mutable": True,
                "logical_name": "autoglm-websearch", "tree_hash": "a" * 64,
                "description": "应用内置搜索", "function": "应用内置搜索", "trigger": "auto",
                "context_bytes": 100, "requires_bins": [],
            }],
            "logical_skills": [{"logical_id": "lg-ba", "name": "autoglm-websearch",
                                "tree_hash": "a" * 64, "instance_ids": ["inst-ba-0000000000000"],
                                "clients": ["shared"], "function": "", "trigger": "auto",
                                "version": "", "context_bytes": 100}],
            "findings": [{"code": "missing-bins", "severity": "yellow",
                          "instance_id": "inst-ba-0000000000000",
                          "skill": "autoglm-websearch", "location_id": "shared",
                          "message": "依赖命令缺失: fake-bin", "ignored": False}],
            "config_issues": [], "total": 1, "operational_ok": True, "health_status": "yellow",
        }
        html = render_html(inv, None, {"known": {"autoglm-websearch": {"type": "builtin-app"}}})
        self.assertNotIn('data-id="inst-ba-0000000000000"', html,
                         "应用内置 skill 不得出现删除按钮")
        self.assertIn("受保护", html)
        self.assertIn("更新或卸载所属客户端", html,
                      "有问题时建议更新/卸载所属客户端,而不是单独删除")


if __name__ == "__main__":
    unittest.main()
