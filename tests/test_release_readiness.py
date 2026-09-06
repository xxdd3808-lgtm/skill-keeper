"""发布就绪回归(独立复核 2026-09-06 提出的 6 个发布阻断项)。

1. SKILL.md 必须有合法 frontmatter(缺开头 --- 会被判 frontmatter missing,
   Skill 无法被发现/触发);
2. 面向用户复制的命令必须是安装态统一 CLI(skill-keeper manage ...),
   不得再给离开仓库就失败的 python3 scripts/manage.py ...;
3. 网页展示的安检命令里的占位证据必须被后端拒绝——照抄占位文本不能形成
   "安检通过"的假记录;
4. SKILL.md 更新流程顺序必须可执行:固定候选 → 创建 update plan → 安检该
   plan → apply(安检绑定 plan_id,先安检后建计划是不可执行的);
5. AGENTS.md 状态段必须与分支实际状态一致(不得仍写"仅第一阶段完成/338 项/
   二至四阶段未执行");
6. 发布对象:scripts/__init__.py 仍是 4.0.0 而 tag v4.0.0 已存在——版本号在
   "合并 main 后、打 tag 前"按授权流程单独确定(建议 4.1.0),此处只锁定
   "SKILL.md/doctor 输出的版本与 scripts.__version__ 一致",防止漂移。
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / "SKILL.md"
AGENTS = REPO_ROOT / "AGENTS.md"


class FrontmatterTests(unittest.TestCase):
    def test_skill_md_has_parseable_frontmatter(self):
        from scripts.scan import parse_frontmatter_detailed
        text = SKILL.read_text(encoding="utf-8")
        fields, ok, warnings = parse_frontmatter_detailed(text)
        self.assertTrue(ok, "SKILL.md 缺少开头 --- 或 frontmatter 无法解析,Skill 无法被发现")
        self.assertEqual(str(fields.get("name") or ""), "skill-keeper")
        self.assertTrue(str(fields.get("description") or "").strip())
        self.assertFalse(any(w.get("code") == "truncated" for w in warnings))

    def test_skill_md_version_matches_package(self):
        from scripts import __version__
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("version: {}".format(__version__), text,
                      "SKILL.md 版本字段必须与 scripts.__version__ 一致")


class CopyCommandTests(unittest.TestCase):
    """面向用户复制的命令:安装态统一 CLI + 占位证据必须被后端拒绝。"""

    def test_report_js_commands_use_installed_cli(self):
        js = (REPO_ROOT / "scripts" / "assets" / "report.js").read_text(encoding="utf-8")
        self.assertNotIn("python3 scripts/manage.py", js,
                         "离开仓库就失败的路径不得出现在复制命令里")
        self.assertIn("skill-keeper manage vet", js)
        self.assertIn("skill-keeper manage apply", js)

    def test_report_static_commands_use_installed_cli(self):
        import scripts.report as report_mod
        for cmd in (report_mod.static_command_hint(), report_mod.static_restore_cmd("x")):
            self.assertTrue(cmd.startswith("skill-keeper manage "), cmd)
            self.assertNotIn("scripts/manage.py", cmd)

    def test_serve_vet_hint_uses_installed_cli(self):
        import scripts.serve as serve_mod
        src = Path(serve_mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn("python3 scripts/manage.py", src)

    def test_placeholder_evidence_is_rejected_by_backend(self):
        """照抄网页展示的占位文本当证据,必须被拒绝(否则形成假 safe 记录)。"""
        from scripts.core.changes import ChangeError, record_candidate_vet
        js = (REPO_ROOT / "scripts" / "assets" / "report.js").read_text(encoding="utf-8")
        m = re.search(r'--evidence \\"([^"\\]+)\\"', js) or \
            re.search(r'--evidence \\?"([^"\\]+)\\?"', js)
        self.assertIsNotNone(m, "网页安检命令必须带明确的必填占位标记")
        placeholder = m.group(1)
        # 占位文本必须是"明显待替换"的标记,而不是一句像依据的说明
        self.assertIn("必填", placeholder, "占位证据必须带【必填】类标记: " + placeholder)
        # 与计划绑定无关的直接校验:占位文本在任何其他校验之前被拒,
        # 错误消息必须点名"占位"(防止"计划不存在"等无关原因造成假绿)
        with self.assertRaises(ChangeError) as cm:
            record_candidate_vet("plan-x", "0" * 64, "safe", [placeholder],
                                 plans_dir=REPO_ROOT / "data" / "no-such-plans")
        self.assertIn("占位", str(cm.exception), str(cm.exception))

    def test_no_placeholder_in_skilled_example(self):
        """SKILL.md/手册里的 vet 示例不得把占位文本写成可直接回车的 safe 命令。"""
        for doc in (SKILL, REPO_ROOT / "docs" / "skill-manual.md", AGENTS):
            text = doc.read_text(encoding="utf-8")
            self.assertNotIn('--evidence "填写可核查依据', text,
                             doc.name + " 的示例证据仍是可照抄的占位文本")


class DocContractTests(unittest.TestCase):
    def test_skill_md_update_order_is_executable(self):
        """更新顺序:updates 暂存 → 创建 update plan → 对该 plan 安检(vet)→ apply。

        安检绑定 plan_id,"先 vet 后建计划"不可执行;断言限定在「标准工作流」章节,
        避免被顶部命令表里的静态罗列干扰。"""
        text = SKILL.read_text(encoding="utf-8")
        start = text.find("## 标准工作流")
        end = text.find("## 失败恢复")
        self.assertGreater(start, 0)
        workflow = text[start:end]
        i_updates = workflow.find("`skill-keeper updates`")
        i_plan = workflow.find("plan update")
        i_vet = workflow.find("manage vet")
        i_apply = workflow.find("manage apply")
        for name, idx in (("updates", i_updates), ("plan update", i_plan),
                          ("vet", i_vet), ("apply", i_apply)):
            self.assertGreaterEqual(idx, 0, "工作流缺少更新步骤: " + name)
        self.assertLess(i_updates, i_plan, "先建计划再安检:updates 必须在 plan update 之前")
        self.assertLess(i_plan, i_vet, "安检绑定 plan_id:vet 必须在 plan update 之后")
        self.assertLess(i_vet, i_apply, "apply 必须在 vet 之后")

    def test_agents_md_matches_branch_state(self):
        text = AGENTS.read_text(encoding="utf-8")
        self.assertNotIn("第二/三/四阶段,均未执行", text,
                         "AGENTS.md 不得仍声称第二至第四阶段未执行")
        self.assertIn("收尾轮", text)
        self.assertIn("373", text, "AGENTS.md 状态段必须反映当前验收数字")


if __name__ == "__main__":
    unittest.main()
