"""F01 回归:报告交互脚本必须是真实、可解析的 JavaScript。

- 静态与服务模式共用 scripts/assets/report.js 同一份资源,HTML 里的
  <script> 产物逐字节来自它;
- node --check 可用时对最终产物做真实语法检查(CI 四平台都预装 node);
- 没有 node 时退回纯 Python 扫描器:字符串字面量内出现裸换行即失败
  ——F01 的根源就是 Python 三引号字符串把 \\n 变成真实换行混进 JS 单引号串;
- 每个 data-act 在 JS 里必须有对应处理分支(不再只断言按钮/fetch 字样)。
"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import scripts.report as report_mod

REPO_ROOT = Path(__file__).resolve().parents[1]


def _minimal_inventory():
    return {"schema_version": 2, "scanned_at": "2026-09-06 00:00:00",
            "instances": [], "logical_skills": [], "locations": [], "findings": []}


def _final_script_html():
    html = report_mod.render_html(_minimal_inventory())
    m = re.search(r"<script>(.*?)</script>", html, re.S)
    if not m:
        raise AssertionError("最终 HTML 缺少内联脚本")
    return m.group(1)


def _assert_no_bare_newline_in_strings(js):
    """纯 Python 兜底扫描:单/双引号字符串里出现裸换行立即失败。

    只识别注释/引号串/模板串,不解析正则字面量——本项目交互脚本不用正则
    字面量;若未来引入,需同步扩展扫描器(或依赖 node --check)。
    """
    i, n = 0, len(js)
    state = "code"
    while i < n:
        c = js[i]
        nxt = js[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nxt == "/":
                state = "line"
                i += 2
                continue
            if c == "/" and nxt == "*":
                state = "block"
                i += 2
                continue
            if c == "'":
                state = "sq"
            elif c == '"':
                state = "dq"
            elif c == "`":
                state = "tpl"
            i += 1
        elif state == "line":
            if c == "\n":
                state = "code"
            i += 1
        elif state == "block":
            if c == "*" and nxt == "/":
                state = "code"
                i += 2
            else:
                i += 1
        elif state in ("sq", "dq"):
            if c == "\\":
                i += 2
                continue
            if c in ("\n", "\r"):
                raise AssertionError(
                    "JS 字符串字面量内出现裸换行(偏移 {}):F01 回归".format(i))
            if (state == "sq" and c == "'") or (state == "dq" and c == '"'):
                state = "code"
            i += 1
        else:  # tpl
            if c == "\\":
                i += 2
                continue
            if c == "`":
                state = "code"
            i += 1


class ReportFrontendTests(unittest.TestCase):
    def test_final_script_is_valid_javascript(self):
        js = _final_script_html()
        self.assertTrue(js.strip(), "最终产物脚本不能为空")
        _assert_no_bare_newline_in_strings(js)
        node = shutil.which("node")
        if node:
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "report-final.js"
                path.write_text(js, encoding="utf-8")
                r = subprocess.run([node, "--check", str(path)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0,
                             "node --check 必须通过: " + (r.stderr or "")[-400:])

    def test_static_and_serve_share_one_asset_file(self):
        asset = REPO_ROOT / "scripts" / "assets" / "report.js"
        self.assertTrue(asset.is_file(), "scripts/assets/report.js 必须存在")
        self.assertEqual(report_mod.JS_BLOB, asset.read_text(encoding="utf-8"),
                         "HTML 内联与 /report.js 服务端必须来自同一份资源")
        self.assertEqual(_final_script_html(), report_mod.JS_BLOB)

    def test_every_html_action_has_a_js_handler(self):
        html = report_mod.render_html(_minimal_inventory())
        acts = set(re.findall(r'data-act="([^"]+)"', html))
        self.assertIn("refresh", acts, "刷新报告按钮必须随报告渲染")
        for act in sorted(acts):
            self.assertIn("act==='%s'" % act, report_mod.JS_BLOB,
                          "data-act=%s 在交互脚本里没有处理分支" % act)

    def test_update_flow_pauses_for_formal_vetting(self):
        """任务1缺口1:网页不得把 confirm 当 safe 安检。

        交互脚本必须暂停并给出可续办的正式 CLI 流程(manage.py vet,需要
        可核查证据),浏览器绝不携带 verdict 调 /api/vet 造记录。
        """
        js = report_mod.JS_BLOB
        self.assertIn("skill-keeper manage vet", js,
                      "更新流程必须给出安装态统一 CLI 的正式安检命令")
        self.assertIn("vet-continue", js, "安检后的续办执行必须有处理分支")
        self.assertNotIn("/api/vet", js,
                         "浏览器脚本不得直接记账安检:confirm 不等于 safe")
        _assert_no_bare_newline_in_strings(js)


if __name__ == "__main__":
    unittest.main()
