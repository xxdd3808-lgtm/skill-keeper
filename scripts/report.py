#!/usr/bin/env python3
"""skill-keeper 报告器:读 inventory.json → Markdown + HTML 交互式报告。
用法: report.py [--json]
  默认:打印 Markdown 并写 data/report.md + data/report.html
  --json:只输出机器可读摘要;退出码 0=健康 1=有红色问题"""
import html as _html
import json, os, sys

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DATA = os.path.join(BASE, "data")
sys.path.insert(0, os.path.join(BASE, "scripts"))
from scan import CLIENTS_OF_LOCATION

SOURCE_LABEL = {
    "github": "GitHub", "skills.sh": "skills.sh 市场", "registry-volces": "火山引擎",
    "registry-modelscope": "魔搭", "registry-openharmony": "鸿蒙", "skillhub": "SkillHub",
    "builtin-app": "随应用自带", "self-built": "自建", "plugin": "ZCode 插件", "unknown": "❓不明",
}

def fmt_source(src):
    t = src.get("type", "unknown")
    parts = [SOURCE_LABEL.get(t, t)]
    if src.get("repo"):
        parts.append(f"`{src['repo']}`")
    note = src.get("note") or src.get("path")
    if note:
        parts.append(str(note))
    return " · ".join(parts)

def issues_of(s, level):
    return [i for i in s["health"]["issues"] if i.startswith(level)]

def client_stats(inv):
    stats = {}
    for c in ("zcode", "claude-code", "codex", "ego"):
        rows = []
        for s in inv["skills"]:
            for i in s["instances"]:
                if c in CLIENTS_OF_LOCATION.get(i["client"], [i["client"]]) and not i.get("stale_cache"):
                    rows.append(i.get("context_bytes", 0))
                    break
        stats[c] = {"skills": len(rows), "kb": round(sum(rows) / 1024, 1)}
    return stats

def build(inv, last):
    red = [{"name": s["name"], "issues": issues_of(s, "🔴")} for s in inv["skills"] if issues_of(s, "🔴")]
    yellow = [{"name": s["name"], "issues": issues_of(s, "🟡")} for s in inv["skills"] if issues_of(s, "🟡")]
    dup = [s["name"] for s in inv["skills"] if s["duplicated"]]
    groups = {}
    for s in inv["skills"]:
        groups.setdefault(s.get("group", "未分组"), []).append(s)
    stale = [(s["name"], i) for s in inv["skills"] for i in s["instances"] if i.get("stale_cache")]
    diff = None
    if last:
        old = {s["name"]: s for s in last["skills"]}
        new = {s["name"]: s for s in inv["skills"]}
        diff = {
            "added": sorted(n for n in new if n not in old),
            "removed": sorted(n for n in old if n not in new),
            "changed": sorted(n for n in new if n in old and
                              json.dumps(new[n]["source"], sort_keys=True) != json.dumps(old[n]["source"], sort_keys=True)),
        }
    return red, yellow, dup, groups, stale, diff

# ────────────────────────── Markdown ──────────────────────────
def render_md(inv, last):
    red, yellow, dup, groups, stale, diff = build(inv, last)
    L = ["# 本地 Skill 盘点报告",
         f"\n> 生成时间:{inv['scanned_at']} · 共 **{inv['total']}** 个 skill(另有 {len(inv.get('junk', []))} 个非 skill 条目)\n",
         "来源分布:" + "、".join(f"{k} {v}" for k, v in sorted(inv["by_source"].items(), key=lambda x: -x[1])) + "\n"]
    L.append("\n## 一、总表(分组 · 功能 · 来源 · 配套客户端)\n")
    L.append("| Skill | 分组 | 功能 | 来源 | 配套客户端 | 触发 | 健康 |")
    L.append("|---|---|---|---|---|---|---|")
    for s in inv["skills"]:
        L.append(f"| **{s['name']}** | {s.get('group','—')} | {s['function'] or '—'} | {fmt_source(s['source'])} | {'、'.join(s['clients'])} | {s['trigger']} | {'<br>'.join(s['health']['issues']) if s['health']['issues'] else '✅'} |")
    L.append("\n## 二、加载分析(谁启动时加载了什么)\n")
    L.append("> 每个 skill 常驻上下文的是「名称+描述」,SKILL.md 全文在触发时才读。")
    for c, st in client_stats(inv).items():
        L.append(f"- **{c}**:加载 {st['skills']} 个,常驻层约 **{st['kb']} KB**")
    if dup:
        L.append(f"\n**⚠️ ZCode 重复加载({len(dup)} 个)**——同名多份全部进列表,只加载第一份:\n")
        for s in inv["skills"]:
            if s["duplicated"]:
                locs = "、".join(i["location"] for i in s["instances"] if i["client"] in ("zcode", "shared", "plugin") and not i.get("stale_cache"))
                L.append(f"- {s['name']} ← {locs}")
    if stale:
        L.append(f"\n**🧹 插件旧版本缓存({len(stale)} 个)**——未被加载,可清理:\n")
        for n, i in stale:
            L.append(f"- {n} v{i.get('plugin_version')} @ {i['location']}")
    junk = inv.get("junk", [])
    if junk:
        L.append(f"\n**🗑️ 非 skill 条目({len(junk)} 个)**:\n")
        for j in junk:
            L.append(f"- `{j['dir']}` @ {j['location']}")
    L.append("\n## 三、健康体检\n")
    if not red and not yellow:
        L.append("✅ 全部健康,无问题。")
    else:
        for s in inv["skills"]:
            for i in s["health"]["issues"]:
                L.append(f"- {i}(**{s['name']}**)")
    if last and diff:
        L.append("\n## 四、与上次盘点的差异\n")
        if not (diff["added"] or diff["removed"] or diff["changed"]):
            L.append("无变化。")
        else:
            if diff["added"]:
                L.append("- 新增:" + "、".join(diff["added"]))
            if diff["removed"]:
                L.append("- 移除:" + "、".join(diff["removed"]))
            if diff["changed"]:
                L.append("- 来源变更:" + "、".join(diff["changed"]))
    return "\n".join(L), red, yellow, dup, groups, stale, diff

# ────────────────────────── HTML ──────────────────────────
def render_html(inv, last):
    red, yellow, dup, groups, stale, diff = build(inv, last)
    red_names = {r["name"] for r in red}
    esc = _html.escape
    chips = [f'<span class="chip">共 {inv["total"]} 个</span>']
    chips += [f'<span class="chip">{esc(k)} {v}</span>' for k, v in sorted(inv["by_source"].items(), key=lambda x: -x[1])]
    chips.append(f'<span class="chip {"chip-red" if red else "chip-green"}">🔴 红色 {len(red)}</span>')
    chips.append(f'<span class="chip {"chip-yellow" if yellow else "chip-green"}">🟡 黄色 {len(yellow)}</span>')

    client_cards = "".join(
        f'<div class="card"><div class="card-t">{c}</div><div class="card-n">{st["skills"]} 个</div>'
        f'<div class="card-k">常驻 {st["kb"]} KB</div></div>'
        for c, st in client_stats(inv).items())

    order = sorted(groups.keys(), key=lambda g: (g == "未分组", g == "ZCode 插件", g))
    sec = []
    for g in order:
        rows = []
        for s in sorted(groups[g], key=lambda x: x["name"].lower()):
            hl = ' class="row-red"' if s["name"] in red_names else (' class="row-yellow"' if any(i["name"] == s["name"] for i in yellow) else "")
            iss = "".join(f'<span class="badge">{esc(i)}</span>' for i in s["health"]["issues"]) or '<span class="badge badge-green">✅</span>'
            rows.append(
                f'<tr{hl}><td><b>{esc(s["name"])}</b></td><td>{esc(s["function"] or "—")}</td>'
                f'<td>{esc(fmt_source(s["source"]))}</td><td>{esc("、".join(s["clients"]))}</td>'
                f'<td>{esc(s["trigger"])}</td><td>{iss}</td></tr>')
        n_red = sum(1 for s in groups[g] if s["name"] in red_names)
        n_ylw = sum(1 for s in groups[g] if any(i["name"] == s["name"] for i in yellow))
        mark = " 🔴" if n_red else (" 🟡" if n_ylw else "")
        sec.append(
            f'<details open><summary><b>{esc(g)}</b><span class="cnt">{len(groups[g])} 个{mark}</span></summary>'
            f'<table><tr><th>Skill</th><th>功能</th><th>来源</th><th>配套客户端</th><th>触发</th><th>健康</th></tr>'
            + "".join(rows) + "</table></details>")

    extra = []
    if dup:
        extra.append(f'<details><summary><b>⚠️ ZCode 重复加载</b><span class="cnt">{len(dup)} 个</span><div class="body">'
                     + "".join(f'<p>• {esc(n)}</p>' for n in dup) + "</div></summary></details>")
    if stale:
        extra.append(f'<details><summary><b>🧹 插件旧版本缓存</b><span class="cnt">{len(stale)} 个</span><div class="body">'
                     + "".join(f'<p>• {esc(n)} v{i.get("plugin_version")}</p>' for n, i in stale) + "</div></summary></details>")
    if inv.get("junk"):
        extra.append(f'<details><summary><b>🗑️ 非 skill 条目</b><span class="cnt">{len(inv["junk"])} 个</span><div class="body">'
                     + "".join(f'<p>• <code>{esc(j["dir"])}</code> @ {esc(j["location"])}</p>' for j in inv["junk"]) + "</div></summary></details>")
    if last:
        d = diff
        body = "".join(f'<p>• {k}:{esc("、".join(v)) if v else "无"}</p>' for k, v in
                       (("新增", d["added"]), ("移除", d["removed"]), ("来源变更", d["changed"])))
        extra.append(f'<details><summary><b>🔄 与上次盘点差异</b><div class="body">{body}</div></summary></details>')

    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Skill 盘点报告</title><style>
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;max-width:1080px;margin:24px auto;padding:0 16px;color:#1f2937;background:#f8fafc}}
h1{{font-size:22px}} .chips{{margin:10px 0}} .chip{{display:inline-block;background:#e2e8f0;border-radius:99px;padding:3px 12px;margin:2px;font-size:13px}}
.chip-red{{background:#fee2e2;color:#b91c1c}} .chip-yellow{{background:#fef9c3;color:#a16207}} .chip-green{{background:#dcfce7;color:#15803d}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}} .card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:12px 18px;min-width:150px}}
.card-t{{font-size:13px;color:#6b7280}} .card-n{{font-size:22px;font-weight:700}} .card-k{{font-size:12px;color:#9ca3af}}
details{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;margin:10px 0;padding:10px 16px}}
summary{{cursor:pointer;font-size:16px;padding:4px 0}} .cnt{{color:#6b7280;font-size:13px;margin-left:10px}}
table{{width:100%;border-collapse:collapse;margin:10px 0;font-size:13.5px}}
th{{text-align:left;color:#6b7280;border-bottom:2px solid #e5e7eb;padding:6px 8px}}
td{{border-bottom:1px solid #f1f5f9;padding:7px 8px;vertical-align:top}}
.row-red{{background:#fef2f2}} .row-yellow{{background:#fefce8}}
.badge{{display:inline-block;font-size:12px;background:#f1f5f9;border-radius:6px;padding:2px 8px;margin:1px}}
.badge-green{{background:#dcfce7;color:#15803d}}
.body{{padding:8px 4px;color:#374151;font-size:14px}} code{{background:#f1f5f9;padding:1px 6px;border-radius:6px}}
</style></head><body>
<h1>📋 本地 Skill 盘点报告</h1><div>生成时间:{inv["scanned_at"]}</div>
<div class="chips">{''.join(chips)}</div>
<h3>各客户端加载</h3><div class="cards">{client_cards}</div>
<h3>Skill 分组明细(点组名可折叠)</h3>{''.join(sec)}
<h3>其他</h3>{''.join(extra)}
<p style="color:#9ca3af;font-size:12px">由 skill-keeper 生成 · 数据源 data/inventory.json</p>
</body></html>"""

def main():
    inv = json.load(open(os.path.join(DATA, "inventory.json"), encoding="utf-8"))
    last_path = os.path.join(DATA, "inventory-last.json")
    last = json.load(open(last_path, encoding="utf-8")) if os.path.exists(last_path) else None
    md, red, yellow, dup, groups, stale, diff = render_md(inv, last)
    if "--json" in sys.argv:
        print(json.dumps({
            "generated_at": inv["scanned_at"], "total": inv["total"], "by_source": inv["by_source"],
            "groups": {g: len(v) for g, v in sorted(groups.items())},
            "clients": client_stats(inv), "duplicated": dup, "red": red, "yellow": yellow,
        }, ensure_ascii=False, indent=1))
        sys.exit(1 if red else 0)
    print(md)
    with open(os.path.join(DATA, "report.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")
    with open(os.path.join(DATA, "report.html"), "w", encoding="utf-8") as f:
        f.write(render_html(inv, last))
    print(f"\n💾 已存:data/report.md + data/report.html(双击用浏览器打开)", file=sys.stderr)
    sys.exit(1 if red else 0)

if __name__ == "__main__":
    main()
