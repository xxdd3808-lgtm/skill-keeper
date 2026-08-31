#!/usr/bin/env python3
"""用固定虚构 fixture 生成公开示例报告 examples/report-sample.html。

v2 起不再从个人 inventory 脱敏生成:只读 examples/fixtures/inventory-v2.json
(名称/仓库/路径/日期/数字全部虚构),输出逐字节确定,避免间接泄露与不稳定提交。
用法: python3 scripts/make_sample_report.py"""
import json, os, sys
from pathlib import Path

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, BASE)
FIXTURE = Path(BASE) / "examples/fixtures/inventory-v2.json"
OUT = Path(BASE) / "examples/report-sample.html"


def main():
    from scripts.report import render_html
    inv = json.loads(FIXTURE.read_text(encoding="utf-8"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render_html(inv, None, None), encoding="utf-8")
    print("✅ 示例报告已生成(固定虚构 fixture):{}".format(OUT))


if __name__ == "__main__":
    main()
