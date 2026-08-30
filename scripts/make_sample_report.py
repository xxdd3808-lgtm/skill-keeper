#!/usr/bin/env python3
"""把 data/inventory.json 脱敏成可公开分享的示例报告 examples/report-sample.html。
skill 名称/功能/来源仓库/分组全部替换为演示内容,只保留报告的结构与统计形态。
用法: python3 scripts/make_sample_report.py"""
import json, os, sys

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "examples", "report-sample.html")
sys.path.insert(0, os.path.join(BASE, "scripts"))
from report import render_html

STRUCTURAL_GROUPS = {"未分组", "ZCode 插件"}

def main():
    inv = json.load(open(os.path.join(DATA, "inventory.json"), encoding="utf-8"))
    skills = sorted(inv["skills"], key=lambda s: s["name"].lower())
    group_map = {}
    for s in skills:
        g = s.get("group", "未分组")
        if g not in STRUCTURAL_GROUPS and g not in group_map:
            group_map[g] = f"示例分组 {len(group_map) + 1}"
    for i, s in enumerate(skills, 1):
        anon = f"skill-{i:02d}"
        s["name"] = anon
        s["function"] = f"演示用功能说明({anon},此报告为脱敏示例)"
        src = s.get("source", {})
        if src.get("repo"):
            src["repo"] = "owner/repo"
        if src.get("path"):
            src["path"] = "path/to/SKILL.md"
        if src.get("note"):
            src["note"] = "示例"
        s["source"] = src
        if s.get("group") not in STRUCTURAL_GROUPS:
            s["group"] = group_map[s.get("group", "未分组")]
        for inst in s["instances"]:
            inst["dir"] = anon
            inst["real_path"] = f"~/.../skills/{anon}"
    inv["junk"] = [{"dir": "not-a-skill", "location": "~/.agents/skills",
                    "issues": ["🔴 无 SKILL.md(非 skill 条目)"]} if inv.get("junk") else []]
    inv["by_source"] = {}
    for s in skills:
        t = s["source"].get("type", "unknown")
        inv["by_source"][t] = inv["by_source"].get(t, 0) + 1
    inv["scanned_at"] = "2026-08-30 09:00:00(脱敏示例)"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(render_html(inv, None))
    print(f"✅ 示例报告已生成:{OUT}")

if __name__ == "__main__":
    main()
