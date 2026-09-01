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

    def test_sample_report_is_deterministic(self):
        out1 = REPO_ROOT / "examples/report-sample.html"
        h1 = hashlib.sha256(out1.read_bytes()).hexdigest() if out1.exists() else ""
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
