#!/usr/bin/env python3
"""skill-keeper 报告器:读 inventory.json(+updates.json)→ Markdown + 交互式 HTML 报告。
用法: report.py [--json] [--serve [--port N] [--no-open]]
  默认:打印 Markdown 并写 data/report.md + data/report.html
  --json:只输出机器可读摘要;退出码 0=健康 1=有红色问题
  --serve:起本地交互服务,报告里的 更新/删除/忽略/恢复 按钮可直接执行
          (仅 127.0.0.1 + 随机 token;所有动作先备份、后自动重扫重报)"""
import html as _html
import json, os, subprocess, sys, time

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DATA = os.path.join(BASE, "data")
BACKUPS = os.path.join(BASE, "backups")
SERVE_HINT = "python3 ~/skill-keeper/scripts/report.py --serve"
sys.path.insert(0, os.path.join(BASE, "scripts"))
from scan import CLIENTS_OF_LOCATION

SOURCE_LABEL = {
    "github": "GitHub", "skills.sh": "skills.sh 市场", "registry-volces": "火山引擎",
    "registry-modelscope": "魔搭", "registry-openharmony": "鸿蒙", "skillhub": "SkillHub",
    "builtin-app": "随应用自带", "self-built": "自建", "plugin": "ZCode 插件", "unknown": "❓不明",
}
LEVEL_LABEL = {"update": "🟢 建议更新", "keep": "🛡️ 建议保留", "confirm": "🟡 待你确认", "info": "ℹ️ 提示", "auto": "🔵 可自动处理"}


def _load(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


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


def entity_dir(s):
    """skill 实体所在目录名(非符号链接、非旧缓存的实例);找不到就退回第一个实例"""
    for i in s["instances"]:
        if i.get("real_path") and not i.get("is_symlink") and not i.get("stale_cache"):
            return i["dir"]
    return s["instances"][0]["dir"] if s["instances"] else s["name"]


def suggestions_of(s, upd, ign):
    """把体检问题 + 上游差异翻译成处理建议。
    → [{level, text, act, payload, ignore_key}]  act∈ update|diff|None"""
    out = []
    u = (upd or {}).get(s["name"])
    if u:
        d = entity_dir(s)
        v = u.get("verdict")
        if v == "update":
            out.append({"level": "update", "text": u.get("reason") or "上游有更新,建议更新(更新前自动备份)",
                        "act": "update", "payload": {"dir": d, "confirm": True},
                        "ignore_key": f"update:{s['name']}"})
        elif v == "keep":
            out.append({"level": "keep", "text": u.get("reason") or "差异来自本地定制,建议保留、不要更新",
                        "act": "diff", "payload": {"dir": d},
                        "ignore_key": f"update:{s['name']}"})
        elif v == "manual":
            out.append({"level": "confirm", "text": u.get("reason") or "与上游有差异,机器判不了——点看差异,或让我人工看",
                        "act": "diff", "payload": {"dir": d},
                        "ignore_key": f"update:{s['name']}"})
        else:  # 旧版 updates.json 没有 verdict 字段,按 status 退化
            lv, uv = u.get("local_version") or "?", u.get("upstream_version") or "?"
            if u.get("status") == "upstream-newer":
                out.append({"level": "update", "text": f"上游有新版 v{uv}(本地 v{lv}),建议更新(更新前自动备份)",
                            "act": "update", "payload": {"dir": d, "confirm": True},
                            "ignore_key": f"update:{s['name']}"})
            elif u.get("status") == "content-diff":
                out.append({"level": "confirm", "text": f"与上游内容有差异但版本号未变(本地 v{lv}),可能是本地定制——先看差异再决定",
                            "act": "diff", "payload": {"dir": d},
                            "ignore_key": f"update:{s['name']}"})
            else:
                out.append({"level": "info", "text": f"本地版本 v{lv} 高于上游 v{uv},保留本地,不建议更新",
                            "act": None, "payload": None, "ignore_key": f"update:{s['name']}"})
    for iss in s["health"]["issues"]:
        item = {"level": "info", "text": iss, "act": None, "payload": None, "ignore_key": iss}
        if "依赖命令缺失" in iss:
            item["text"] = iss + " → 装上括号里的命令即可消除"
        elif "同名" in iss and "份" in iss:
            item.update({"level": "confirm", "text": iss + " → 只保留优先级最高的一份,其余手动移走(先备份)"})
        elif "链接漂移" in iss:
            item.update({"level": "confirm", "text": iss + " → 把链接目标内容与主库对齐,或重建符号链接"})
        elif "旧版本缓存" in iss:
            item["text"] = iss + " → 插件缓存由插件系统管理,可忽略或等插件自更新"
        elif "各副本来源不一致" in iss:
            item.update({"level": "confirm", "text": iss + " → 核对哪份是正版,删掉另一份或在 known-sources.json 补来源"})
        elif iss.startswith("🔴"):
            item.update({"level": "confirm", "text": iss + " → 建议修复或删除,详见 SKILL.md 工作流"})
        elif "来源不明" in iss:
            item["text"] = iss + " → 在 data/known-sources.json 补一条来源映射"
        out.append(item)
    rules = ign.get(s["name"], [])
    return [x for x in out if not any(r == x["ignore_key"] or r in x["text"] for r in rules)]


def backups_list():
    if not os.path.isdir(BACKUPS):
        return []
    out = []
    for f in sorted(os.listdir(BACKUPS), reverse=True):
        if f.endswith(".tar.gz"):
            p = os.path.join(BACKUPS, f)
            out.append({"name": f, "kb": round(os.path.getsize(p) / 1024, 1),
                        "ts": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))})
    return out


def build(inv, last, ctx):
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
    sugg = []
    ctx = ctx or {}
    for s in inv["skills"]:
        meta = {"func": s.get("function") or "—", "src": fmt_source(s["source"]),
                "clients": "、".join(s["clients"])}
        for x in suggestions_of(s, ctx.get("updates"), ctx.get("ignore") or {}):
            x["name"] = s["name"]
            x.update(meta)
            sugg.append(x)
    order = {"update": 0, "keep": 1, "auto": 2, "confirm": 3, "info": 4}
    sugg.sort(key=lambda x: (order.get(x["level"], 9), x["name"].lower()))
    ignored_n = sum(len(s["health"].get("ignored", [])) for s in inv["skills"])
    return red, yellow, dup, groups, stale, diff, sugg, ignored_n


def top_context(inv, n=10):
    rows = sorted(inv["skills"], key=lambda s: -s.get("context_bytes", 0))[:n]
    return [(s["name"], round(s.get("context_bytes", 0) / 1024, 1)) for s in rows]


# ────────────────────────── 按钮生成(HTML 专用) ──────────────────────────
def esc_attr(v):
    return _html.escape(str(v), quote=True)


def btn_html(label, act, payload, cmd, cls="", confirm_msg=None):
    h = (f'<button class="btn {cls}" data-act="{esc_attr(act)}" '
         f'data-payload="{esc_attr(json.dumps(payload, ensure_ascii=False))}" data-cmd="{esc_attr(cmd)}"')
    if confirm_msg:
        h += f' data-confirm="{esc_attr(confirm_msg)}"'
    return h + f'>{label}</button>'


def sugg_actions_html(x):
    btns = []
    if x["act"] == "update":
        btns.append(btn_html("🔄 更新", "update", x["payload"], SERVE_HINT, "btn-go",
                             f"更新「{x['name']}」?将用上游内容覆盖本地(先自动备份,再重扫)。"))
    if x["act"] in ("update", "diff"):
        btns.append(btn_html("🔍 看差异", "diff", x["payload"], SERVE_HINT))
    if x["ignore_key"]:
        btns.append(btn_html("✕ 忽略", "ignore", {"name": x["name"], "match": x["ignore_key"], "confirm": True},
                             SERVE_HINT, "btn-ghost"))
    return "".join(btns) or '<span class="mut">仅提示</span>'


def row_actions_html(s):
    if s["source"].get("type") == "plugin":
        return '<span class="mut">插件管理</span>'
    d = entity_dir(s)
    return btn_html("🗑️ 删", "remove", {"dir": d, "confirm": True},
                    f"python3 ~/skill-keeper/scripts/remove_skill.py {d}", "btn-danger",
                    f"删除 skill「{s['name']}」?将先打包备份到 backups/,再从所有客户端位置移除。")


# ────────────────────────── Markdown ──────────────────────────
def render_md(inv, last, ctx=None):
    red, yellow, dup, groups, stale, diff, sugg, ignored_n = build(inv, last, ctx)
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
    L.append("\n常驻上下文占用 Top 10:" + "、".join(f"{n}({kb}KB)" for n, kb in top_context(inv)))
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

    L.append("\n## 三、处理建议(体检 + 上游更新)\n")
    if not sugg:
        L.append("✅ 全部健康,无待处理事项。")
    else:
        cur = None
        for x in sugg:
            if x["level"] != cur:
                cur = x["level"]
                L.append(f"\n**{LEVEL_LABEL[cur]}**\n")
            L.append(f"- **{x['name']}**({x['func']} · {x['src']} · {x['clients']}) — {x['text']}")
    if ignored_n:
        L.append(f"\n(另有 {ignored_n} 条问题已按 `data/ignore.json` 规则忽略,不再列出)")
    L.append(f"\n> 一键执行:`{SERVE_HINT}`,浏览器里直接点按钮;所有动作先备份、后自动重扫重报。")

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
    return "\n".join(L), red, yellow, dup, groups, stale, diff, sugg


# ────────────────────────── HTML ──────────────────────────
JS_BLOB = """
const SERVE_HINT = "python3 ~/skill-keeper/scripts/report.py --serve";
function token(){return new URLSearchParams(location.search).get('t');}
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML;}
function toast(m){let t=document.getElementById('toast');t.textContent=m;t.className='show';clearTimeout(t._h);t._h=setTimeout(()=>t.className='',3000);}
function copyText(s){(navigator.clipboard?navigator.clipboard.writeText(s):Promise.reject()).then(()=>toast('已复制命令')).catch(()=>{const ta=document.createElement('textarea');ta.value=s;document.body.appendChild(ta);ta.select();try{document.execCommand('copy');}catch(e){}ta.remove();toast('已复制命令');});}
function renderDiff(txt){
  const lines=String(txt).replace(/\\n$/,'').split('\\n');
  if(lines.length<=1&&/一致/.test(lines[0]))return '<span class="mut">'+esc(lines[0])+'</span>';
  return '<pre class="dpre">'+lines.map(l=>{
    const e=esc(l);
    if(l.startsWith('@@'))return '<span class="dhunk">'+e+'</span>';
    if(l.startsWith('+++')||l.startsWith('---'))return '<span class="dh">'+e+'</span>';
    if(l.startsWith('+'))return '<span class="dadd">'+e+'</span>';
    if(l.startsWith('-'))return '<span class="ddel">'+e+'</span>';
    return '<span>'+e+'</span>';
  }).join('')+'</pre>';
}
document.addEventListener('click',async e=>{
  const b=e.target.closest('button[data-act]');if(!b)return;
  const act=b.dataset.act,payload=JSON.parse(b.dataset.payload||'{}'),cmd=b.dataset.cmd,t=token();
  if(act==='diff'){
    const tr=b.closest('tr');if(!tr)return;
    const open=tr.nextElementSibling;
    if(open&&open.classList.contains('diff-row')){open.remove();return;}
    document.querySelectorAll('tr.diff-row').forEach(x=>x.remove());
    if(!t){copyText(cmd||SERVE_HINT);toast('静态模式看不了差异,已复制 --serve 命令');return;}
    const cs=tr.children.length;
    tr.insertAdjacentHTML('afterend','<tr class="diff-row"><td colspan="'+cs+'"><div class="dbody"><span class="mut">加载中…</span></div></td></tr>');
    try{
      const r=await fetch('/api/diff?dir='+encodeURIComponent(payload.dir)+'&t='+encodeURIComponent(t));
      const j=await r.json();
      tr.nextElementSibling.querySelector('.dbody').innerHTML=j.ok?renderDiff(j.diff):('❌ '+esc(j.message||'拉取失败'));
    }catch(err){const d=document.querySelector('tr.diff-row .dbody');if(d)d.textContent='请求失败:'+err;}
    return;
  }
  if(!t){copyText(cmd||SERVE_HINT);return;}
  if(b.dataset.confirm&&!confirm(b.dataset.confirm))return;
  b.disabled=true;
  try{
    const r=await fetch('/api/'+act+'?t='+encodeURIComponent(t),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const j=await r.json();toast((j.ok?'✅ ':'❌ ')+(j.message||''));
    if(j.ok)setTimeout(()=>location.reload(),1400);else b.disabled=false;
  }catch(err){toast('请求失败:'+err);b.disabled=false;}
});
"""


def render_html(inv, last, ctx=None):
    red, yellow, dup, groups, stale, diff, sugg, ignored_n = build(inv, last, ctx)
    red_names = {r["name"] for r in red}
    esc = _html.escape
    n_upd = sum(1 for x in sugg if x["level"] == "update")
    n_keep = sum(1 for x in sugg if x["level"] == "keep")
    n_cfm = sum(1 for x in sugg if x["level"] == "confirm")
    chips = [f'<span class="chip">共 {inv["total"]} 个</span>']
    chips += [f'<span class="chip">{esc(k)} {v}</span>' for k, v in sorted(inv["by_source"].items(), key=lambda x: -x[1])]
    chips.append(f'<span class="chip {"chip-red" if red else "chip-green"}">🔴 红色 {len(red)}</span>')
    chips.append(f'<span class="chip {"chip-yellow" if yellow else "chip-green"}">🟡 黄色 {len(yellow)}</span>')
    chips.append(f'<span class="chip {"chip-green" if n_upd else ""}">🟢 建议更新 {n_upd}</span>')
    chips.append(f'<span class="chip">🛡️ 建议保留 {n_keep}</span>')
    chips.append(f'<span class="chip">🟡 待确认 {n_cfm}</span>')

    client_cards = "".join(
        f'<div class="card"><div class="card-t">{c}</div><div class="card-n">{st["skills"]} 个</div>'
        f'<div class="card-k">常驻 {st["kb"]} KB</div></div>'
        for c, st in client_stats(inv).items())
    top = top_context(inv, 10)
    top_line = "、".join(f'{esc(n)}({kb}KB)' for n, kb in top[:5]) + "…"

    # 处理建议表
    if sugg:
        rows = []
        cur = None
        for x in sugg:
            if x["level"] != cur:
                cur = x["level"]
                rows.append(f'<tr class="sep"><td colspan="6">{LEVEL_LABEL[cur]}</td></tr>')
            rows.append(f'<tr><td><b>{esc(x["name"])}</b></td><td>{esc(x["func"])}</td>'
                        f'<td>{esc(x["src"])}</td><td>{esc(x["clients"])}</td>'
                        f'<td>{esc(x["text"])}</td><td>{sugg_actions_html(x)}</td></tr>')
        ignored_note = (f'<p class="mut">另有 {ignored_n} 条问题已按 data/ignore.json 规则忽略。</p>' if ignored_n else "")
        sugg_sec = (f'<details open><summary><b>🎯 处理建议</b><span class="cnt">建议更新 {n_upd} · 建议保留 {n_keep} · 待确认 {n_cfm} · 提示 {len(sugg)-n_upd-n_keep-n_cfm}</span></summary>'
                    f'<table><tr><th>Skill</th><th>功能</th><th>来源</th><th>客户端</th><th>结论与理由</th><th>操作</th></tr>{"".join(rows)}</table>{ignored_note}</details>')
    else:
        sugg_sec = '<details open><summary><b>🎯 处理建议</b><span class="cnt">全部健康,无待处理事项 ✅</span></summary></details>'

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
                f'<td>{esc(s["trigger"])}</td><td>{iss}</td><td>{row_actions_html(s)}</td></tr>')
        n_red = sum(1 for s in groups[g] if s["name"] in red_names)
        n_ylw = sum(1 for s in groups[g] if any(i["name"] == s["name"] for i in yellow))
        mark = " 🔴" if n_red else (" 🟡" if n_ylw else "")
        sec.append(
            f'<details open><summary><b>{esc(g)}</b><span class="cnt">{len(groups[g])} 个{mark}</span></summary>'
            f'<table><tr><th>Skill</th><th>功能</th><th>来源</th><th>配套客户端</th><th>触发</th><th>健康</th><th>操作</th></tr>'
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
    if ctx:
        bks = ctx.get("backups") or []
        if bks:
            rows = []
            for b in bks:
                rows.append(f'<p>• <code>{esc(b["name"])}</code>({b["kb"]} KB · {b["ts"]}) '
                            + btn_html("♻️ 恢复", "restore", {"backup": b["name"], "confirm": True},
                                       SERVE_HINT, "btn-ghost",
                                       f"从 {b['name']} 恢复到 ~/.agents/skills?(若同名 skill 已存在会被覆盖)"))
            extra.append(f'<details><summary><b>♻️ 备份与恢复</b><span class="cnt">{len(bks)} 个</span><div class="body">{"".join(rows)}</div></summary></details>')

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
tr.sep td{{background:#f8fafc;font-weight:700;color:#475569;font-size:13px}}
.row-red{{background:#fef2f2}} .row-yellow{{background:#fefce8}}
.badge{{display:inline-block;font-size:12px;background:#f1f5f9;border-radius:6px;padding:2px 8px;margin:1px}}
.badge-green{{background:#dcfce7;color:#15803d}}
.body{{padding:8px 4px;color:#374151;font-size:14px}} code{{background:#f1f5f9;padding:1px 6px;border-radius:6px}}
.mut{{color:#9ca3af;font-size:12px}}
.btn{{display:inline-block;border:1px solid #d1d5db;background:#fff;border-radius:8px;padding:3px 10px;margin:1px 2px;font-size:12.5px;cursor:pointer;white-space:nowrap}}
.btn:hover{{background:#f3f4f6}} .btn:disabled{{opacity:.5;cursor:default}}
.btn-go{{background:#dcfce7;border-color:#86efac;color:#166534}} .btn-go:hover{{background:#bbf7d0}}
.btn-danger{{background:#fef2f2;border-color:#fecaca;color:#b91c1c}} .btn-danger:hover{{background:#fee2e2}}
.btn-ghost{{color:#6b7280}}
tr.diff-row>td{{background:#f8fafc;border-bottom:1px solid #e5e7eb;padding:0}}
.dpre{{margin:0;padding:10px 14px;font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-x:auto;border-top:2px solid #e2e8f0}}
.dpre span{{display:block;white-space:pre-wrap;word-break:break-all}}
.dadd{{background:#ecfdf5;color:#047857}} .ddel{{background:#fef2f2;color:#b91c1c}}
.dhunk{{background:#eef2ff;color:#4338ca;margin:2px 0}} .dh{{color:#94a3b8}}
#toast{{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:#1f2937;color:#fff;border-radius:99px;padding:8px 18px;font-size:13px;opacity:0;pointer-events:none;transition:opacity .25s;max-width:80%}}
#toast.show{{opacity:.95}}
</style></head><body>
<h1>📋 本地 Skill 盘点报告</h1><div>生成时间:{inv["scanned_at"]}</div>
<div class="chips">{''.join(chips)}</div>
<h3>各客户端加载</h3><div class="cards">{client_cards}</div>
<p class="mut">📊 常驻上下文占用 Top5:{top_line}</p>
{sugg_sec}
<h3>Skill 分组明细(点组名可折叠)</h3>{''.join(sec)}
<h3>其他</h3>{''.join(extra)}
<p style="color:#9ca3af;font-size:12px">由 skill-keeper 生成 · 数据源 data/inventory.json · 一键操作需 <code>report.py --serve</code> 模式(静态打开时点按钮会复制等价命令)</p>
<div id="toast"></div>
<script>{JS_BLOB}</script>
</body></html>"""


def main():
    argv = sys.argv[1:]
    if "--serve" in argv:
        subprocess.run([sys.executable, os.path.join(BASE, "scripts", "serve.py")] + [a for a in argv if a != "--serve"])
        return
    inv = json.load(open(os.path.join(DATA, "inventory.json"), encoding="utf-8"))
    last_path = os.path.join(DATA, "inventory-last.json")
    last = json.load(open(last_path, encoding="utf-8")) if os.path.exists(last_path) else None
    ctx = None
    if "--json" not in argv:
        # 交互信息只在文件/网页模式注入;--json 与脱敏示例保持无个人数据
        raw_upd = _load(os.path.join(DATA, "updates.json")) or {}
        ctx = {"updates": {d.get("name"): d for d in raw_upd.get("differs", [])},
               "ignore": _load(os.path.join(DATA, "ignore.json")) or {},
               "backups": backups_list()}
    md, red, yellow, dup, groups, stale, diff, sugg = render_md(inv, last, ctx)
    if "--json" in argv:
        print(json.dumps({
            "generated_at": inv["scanned_at"], "total": inv["total"], "by_source": inv["by_source"],
            "groups": {g: len(v) for g, v in sorted(groups.items())},
            "clients": client_stats(inv), "duplicated": dup, "red": red, "yellow": yellow,
            "update_suggest": [x["name"] for x in sugg if x["level"] == "update"],
            "confirm_suggest": [x["name"] for x in sugg if x["level"] == "confirm"],
        }, ensure_ascii=False, indent=1))
        sys.exit(1 if red else 0)
    print(md)
    with open(os.path.join(DATA, "report.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")
    with open(os.path.join(DATA, "report.html"), "w", encoding="utf-8") as f:
        f.write(render_html(inv, last, ctx))
    print(f"\n💾 已存:data/report.md + data/report.html(双击用浏览器打开;一键操作用 --serve)", file=sys.stderr)
    sys.exit(1 if red else 0)


if __name__ == "__main__":
    main()
