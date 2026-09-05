#!/usr/bin/env python3
"""skill-keeper v2 报告器:普通人的价值审查面板(Markdown + 交互 HTML)。

顶部先给结论:受保护类、第三方待审、建议保留、重复二选一、观察、建议删除、需确认。
每个第三方卡片展示:来源置信度、GitHub/市场证据时间、仓库级热度提示、替代候选、
独特能力、删除后可能失去、置信度、过期状态;热度只是参考,绝不冒充真实使用人数。
静态模式只复制安全的 instance_id plan 命令(shlex.join 生成),绝不拼目录名进 shell。
用法: report.py [--json] [--serve [--port N] [--no-open]]
  --json:机器可读摘要;退出码 0=健康 1=有红色问题
  --serve:起本地两阶段 plan/apply 交互服务
"""
import argparse, html as _html, json, os, shlex, subprocess, sys, time
from pathlib import Path

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from scripts.core.github import flatten_repos  # noqa: E402
from scripts.core.io import load_json_checked  # noqa: E402
from scripts.core.provenance import (classify_provenance, client_managed_advice,  # noqa: E402
                                     load_user_config)
from scripts.core.reviews import inventory_fingerprint  # noqa: E402
from scripts.scan import CLIENT_LABELS  # noqa: E402

# 实例/位置展示用的客户端标签;"shared" 不是客户端,是共享库这一位置本身
CLIENT_LABELS_EXT = dict(CLIENT_LABELS, shared="共享库")

SERVE_HINT = "python3 ~/skill-keeper/scripts/report.py --serve"
VERDICT_GROUPS = ("建议保留", "优先保留另一个", "观察", "建议删除", "需要人工确认")
# 记账 verdict("保留")→ 报告分组标签("建议保留");其余两边的文字相同
VERDICT_TO_GROUP = {"保留": "建议保留", "优先保留另一个": "优先保留另一个",
                    "观察": "观察", "建议删除": "建议删除", "需要人工确认": "需要人工确认"}
VERDICT_EMOJI = {"建议保留": "💚 建议保留", "优先保留另一个": "🔁 优先保留另一个",
                 "观察": "👀 观察", "建议删除": "🗑️ 建议删除", "需要人工确认": "❓ 需要人工确认"}
VERDICT_ANCHORS = {
    "建议保留": "verdict-keep",
    "优先保留另一个": "verdict-prefer-other",
    "观察": "verdict-observe",
    "建议删除": "verdict-delete",
    "需要人工确认": "verdict-confirm",
}
UPDATE_LABEL = {
    "candidate-update": "🟢 有候选更新——先安检,再走 plan/apply(远端变化不影响已审查候选)",
    "needs-review": "🟡 与上游有差异——需要审查后再决定",
    "local-custom": "🛡️ 疑似本地定制——建议保留本地,不要覆盖",
    "unverifiable": "⏭️ 无法核实——缺来源/工具或网络失败",
}
REPO_SCOPE_NOTE = "GitHub 星数是仓库热度,不等于该 Skill 的真实使用人数"


def data_dir():
    return Path(os.environ.get("SKILL_KEEPER_DATA") or os.path.join(BASE, "data"))


def _load(path):
    value, issues = load_json_checked(path, {})
    return None if issues else value


def esc(v):
    return _html.escape(str(v if v is not None else ""), quote=True)


def fmt_source(source):
    if not isinstance(source, dict):
        return "—"
    t = str(source.get("type") or "unknown")
    conf = source.get("confidence")
    suffix = "({})".format(conf) if conf else ""
    if t == "unknown" and "self-declared-source(candidate)" in (source.get("evidence") or []):
        return "自述来源,未核实" + suffix
    label = {"github": "GitHub", "skills.sh": "skills.sh", "unknown": "来源不明"}.get(t, t)
    return label + suffix


def classify_instance(inst, self_built, known=None):
    """报告分类:客户端自带/插件 → 受保护;自建/应用内置白名单 → 受保护;其余第三方。"""
    if inst.get("kind") in ("builtin", "plugin-cache"):
        return "protected", ("客户端自带/插件管理",)
    if inst.get("directory_name") in self_built or inst.get("instance_id") in self_built:
        return "protected", ("自建白名单",)
    ks = (known or {}).get(str(inst.get("directory_name"))) or \
         (known or {}).get(str(inst.get("instance_id")))
    if isinstance(ks, dict) and ks.get("type") in ("self-built", "builtin-app"):
        return "protected", ("应用内置" if ks.get("type") == "builtin-app" else "自建白名单",)
    return "third-party", ()


def latest_reviews(value_reviews):
    """instance_id → 最近一条审查记录。"""
    out = {}
    for rec in value_reviews or []:
        if isinstance(rec, dict) and rec.get("instance_id"):
            prev = out.get(rec["instance_id"])
            if prev is None or str(rec.get("reviewed_at", "")) >= str(prev.get("reviewed_at", "")):
                out[rec["instance_id"]] = rec
    return out


def build_view(inv, last, ctx):
    ctx = ctx or {}
    self_built = set(ctx.get("self_built") or [])
    known = ctx.get("known") or inv.get("known_sources") or {}
    reviews = latest_reviews(ctx.get("value_reviews") or inv.get("value_reviews"))
    reputation = ctx.get("reputation") or inv.get("reputation") or {}
    updates = {u.get("instance_id"): u for u in
               (ctx.get("updates") or inv.get("updates") or []) if isinstance(u, dict)}
    insts = inv.get("instances", [])
    inst_by_id = {i.get("instance_id"): i for i in insts}
    logical_by_id = {l.get("logical_id"): l for l in inv.get("logical_skills", [])}

    # 逐实例来源证据(白名单/回执/自述候选);热度卡片按"该 Skill 自己的仓库"取数
    receipts = {}
    for inst in insts:
        if inst.get("kind") in ("builtin", "plugin-cache"):
            receipts[str(inst.get("instance_id"))] = {
                "type": inst["kind"], "repo": inst.get("plugin_name"),
                "client": inst.get("client")}
    prov_by_iid = {str(i.get("instance_id")): classify_provenance(i, receipts, known)
                   for i in insts}
    repos_flat = flatten_repos(reputation if isinstance(reputation, dict) else {})

    # 审查队列的替代候选(只在队列与当前 inventory 指纹一致时展示,防止拿旧候选误导)
    queue_items = {}
    queue = ctx.get("queue")
    if isinstance(queue, dict) and queue.get("inventory_fingerprint") and \
            queue.get("inventory_fingerprint") == inventory_fingerprint(inv):
        queue_items = {x.get("logical_id"): x for x in queue.get("items", [])
                       if isinstance(x, dict)}

    protected, third_party = [], []
    for inst in insts:
        cls, why = classify_instance(inst, self_built, known)
        (protected if cls == "protected" else third_party).append((inst, why))
    # 逻辑 skill 去重计数(报告按逻辑 skill 展示,实例明细在表里)
    seen_lg, protected_names, third_names, tp_ids = set(), [], [], []
    for inst, why in protected:
        lg = _logical_of(inv, inst)
        if lg and lg.get("logical_id") not in seen_lg:
            seen_lg.add(lg.get("logical_id"))
            protected_names.append(lg["name"])
    for inst, why in third_party:
        if inst.get("is_skill", True):
            lg = _logical_of(inv, inst)
            if lg and lg.get("logical_id") not in seen_lg:
                seen_lg.add(lg.get("logical_id"))
                third_names.append(lg["name"])
            tp_ids.append(inst)

    verdict_rows = {g: [] for g in VERDICT_GROUPS}
    unreviewed = []
    # 审查记录按"逻辑 ID"归并(报告以逻辑 skill 展示);同名不同内容的逻辑各有各的结论
    reviews_by_lg = {}
    for rec in (ctx.get("value_reviews") or inv.get("value_reviews") or []):
        if not isinstance(rec, dict) or not rec.get("logical_id"):
            continue
        prev = reviews_by_lg.get(rec["logical_id"])
        if prev is None or str(rec.get("reviewed_at", "")) >= str(prev.get("reviewed_at", "")):
            reviews_by_lg[rec["logical_id"]] = rec
    seen_lg = set()
    for inst, _why in third_party:
        lg = _logical_of(inv, inst)
        if not lg or lg.get("logical_id") in seen_lg:
            continue
        seen_lg.add(lg.get("logical_id"))
        rec = reviews_by_lg.get(lg.get("logical_id"))
        stale = bool(rec and rec.get("skill_tree_hash") not in (None, lg.get("tree_hash")))
        group = VERDICT_TO_GROUP.get((rec or {}).get("verdict") or "")
        if rec and group:
            verdict_rows[group].append({"inst": inst, "rec": rec, "stale": stale})
        else:
            unreviewed.append({"inst": inst, "rec": None, "stale": False})

    findings_by_skill = {}
    findings_by_instance = {}
    for f in inv.get("findings", []):
        if f.get("ignored"):
            continue
        findings_by_skill.setdefault(f.get("skill"), []).append(f)
        if f.get("instance_id"):
            findings_by_instance.setdefault(f.get("instance_id"), []).append(f)

    counts = {"total": inv.get("total", len(inv.get("logical_skills", []))),
              "protected": len(protected_names), "third_party": len(third_names),
              "unreviewed": len(unreviewed)}
    for g in VERDICT_GROUPS:
        counts[g] = len(verdict_rows[g])
    live = [f for f in inv.get("findings", []) if not f.get("ignored")]
    counts["red"] = sum(1 for f in live if f.get("severity") == "red")
    counts["yellow"] = sum(1 for f in live if f.get("severity") == "yellow")
    counts["updates"] = len(updates)
    attention = [f for f in live if f.get("severity") in ("red", "yellow")]

    diff = None
    if last:
        old = {i["instance_id"] for i in last.get("instances", [])}
        new = {i["instance_id"] for i in insts}
        diff = {"added": sorted(new - old), "removed": sorted(old - new)}
    backups = ctx.get("backups") or []
    return {"inv": inv, "counts": counts, "protected_names": protected_names,
            "verdict_rows": verdict_rows, "unreviewed": unreviewed,
            "findings_by_skill": findings_by_skill, "findings_by_instance": findings_by_instance,
            "attention_findings": attention, "updates": updates,
            "updates_checked_at": ctx.get("updates_checked_at"),
            "reputation": reputation, "reviews": reviews, "backups": backups, "diff": diff,
            "logical_by_id": logical_by_id, "inst_by_id": inst_by_id,
            "prov": prov_by_iid, "repos_flat": repos_flat, "queue_items": queue_items,
            "known": known}


def _logical_of(inv, inst):
    for lg in inv.get("logical_skills", []):
        if inst.get("instance_id") in lg.get("instance_ids", []):
            return lg
    return None


def _name_of_id(view, iid):
    inst = view["inst_by_id"].get(iid)
    if inst:
        return inst.get("logical_name") or iid
    lg = view["logical_by_id"].get(iid)
    return lg.get("name") if lg else iid


def _repo_card(view, inst):
    """来源 + 热度证据行:只显示该 Skill 自己核实仓库的热度;热度口径必须解释。"""
    prov = view["prov"].get(str(inst.get("instance_id"))) or {}
    repo = prov.get("repo")
    parts = ["来源:{}".format(fmt_source(prov))]
    snap = view["repos_flat"].get(repo) if repo else None
    if repo:
        parts.append("仓库 {}".format(repo))
    if snap and snap.get("stale"):
        parts.append("热度数据已过期/本次未能刷新({})".format(snap.get("error") or "stale"))
    elif snap:
        parts.append("stars {} · fork {}{}".format(
            snap.get("stars", "?"), snap.get("forks", "?"),
            " · 已归档" if snap.get("archived") else ""))
        if snap.get("fetched_at"):
            parts.append("数据时间 {}".format(str(snap["fetched_at"])[:16]))
    parts.append(REPO_SCOPE_NOTE)
    return "；".join(parts)


def _repo_root():
    """运行时解析的真实项目根(F08:不再输出被引号包死、无法展开的 ~ 路径)。"""
    return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def _manage_entry():
    """仓库相对入口(F08):相对路径在仓库根可直接运行,示例报告也不泄漏个人路径。"""
    return os.path.join("scripts", "manage.py")


def _safe_plan_cmd(iid):
    """静态报告里可复制的安全命令:在仓库根运行;只含 instance_id,绝不含目录名。

    F08 修复:旧版 shlex.join 里写死 "~/skill-keeper/...",~ 被整体引号包住
    无法展开,复制出来的命令不可运行。"""
    return shlex.join(["python3", _manage_entry(),
                       "plan", "remove", "--instance-id", str(iid),
                       "--reason", "报告建议,请补充或修改理由"])


def static_restore_cmd(backup_id):
    """恢复按钮在静态模式的等价命令(F08:此前复制到剪贴板却没有任何命令)。"""
    return shlex.join(["python3", _manage_entry(),
                       "plan", "restore", "--backup-id", str(backup_id)])


def static_command_hint():
    """自检用:静态模式必须能给出可运行的命令示例(在仓库根运行)。"""
    return _safe_plan_cmd("0" * 20)


# ────────────────────────── 卡片/按钮(HTML) ──────────────────────────
def btn(label, act, data, cls="", confirm=None):
    attrs = "".join(' data-{}="{}"'.format(k, esc(v)) for k, v in data.items())
    if confirm:
        attrs += ' data-confirm="{}"'.format(esc(confirm))
    return '<button class="btn {}" data-act="{}"{}>{}</button>'.format(cls, esc(act), attrs, esc(label))


def review_card_html(view, row, group):
    inst, rec, stale = row["inst"], row["rec"], row["stale"]
    iid = inst.get("instance_id")
    name = inst.get("logical_name") or iid
    h = ['<div class="card review-card">']
    head = '<div class="card-t"><b>{}</b> — {}</div>'.format(esc(name), esc(inst.get("function") or ""))
    badges = []
    if stale:
        badges.append('<span class="badge badge-red">⚠️ 结论已过期:内容变化后需重新审查</span>')
    safety = (rec or {}).get("safety")
    if safety == "safe":
        badges.append('<span class="badge badge-green">🛡️ 安检 safe</span>')
    elif safety == "warning":
        badges.append('<span class="badge badge-yellow">🛡️ 安检 warning</span>')
    elif safety == "danger":
        badges.append('<span class="badge badge-red">🛡️ 安检 danger</span>')
    else:
        badges.append('<span class="badge">🛡️ 未安检</span>')
    upd = view["updates"].get(iid)
    if upd:
        badges.append('<span class="badge badge-yellow">{}</span>'.format(
            esc(UPDATE_LABEL.get(upd.get("status"), upd.get("status")))))
    h.append(head + "".join(badges))
    if rec:
        h.append('<div class="card-n">{}</div>'.format(esc(VERDICT_EMOJI.get(group, group))))
        h.append('<p>结论:{}</p>'.format(esc(rec.get("reason") or "")))
        ev = rec.get("evidence") or []
        if ev:
            h.append('<p>主要依据:' + "；".join(esc(x) for x in ev) + '</p>')
        alts = [_name_of_id(view, a) for a in (rec.get("alternatives") or [])]
        if alts:
            h.append('<p>更值得保留的替代:{}(详见受保护/第三方卡片)</p>'.format(esc("、".join(alts))))
        uniq = rec.get("unique_capabilities") or []
        if uniq:
            h.append('<p>独特能力:' + "；".join(esc(x) for x in uniq) + '</p>')
        loss = rec.get("loss_if_removed")
        h.append('<p>删除后可能失去:{}</p>'.format(esc(loss) if loss else "—(请大模型补充后再确认)"))
        h.append('<p class="mut">置信度:{} · 审查时间:{} · 审查模型:{}</p>'.format(
            esc(rec.get("confidence") or "?"), esc(rec.get("reviewed_at") or "?"),
            esc(rec.get("reviewer_model") or "?")))
    else:
        h.append('<p class="mut">尚未审查:加入大模型审查队列(value_review.py queue)后逐项审。</p>')
        h.append('<p>删除后可能失去:—(尚未审查,先审查再决定)</p>')
    h.append('<p class="mut">{}</p>'.format(esc(_repo_card(view, inst))))
    lg = _logical_of(view["inv"], inst)
    qitem = view["queue_items"].get(lg.get("logical_id")) if lg else None
    cands = (qitem or {}).get("alternative_candidates") or []
    if cands:
        names = "、".join(esc(c.get("name") or c.get("logical_id")) for c in cands[:3])
        h.append('<p class="mut">替代候选(未确认,供审查,共 {} 个):{}</p>'.format(
            len(cands), names))
    h.append('<div>{}</div>'.format(btn("🗑️ 删除(两阶段)", "remove",
                                        {"id": iid, "name": name, "cmd": _safe_plan_cmd(iid)},
                                        "btn-danger")))
    h.append('</div>')
    return "".join(h)


def findings_badges(view, inst):
    rows = view["findings_by_instance"].get(inst.get("instance_id"), [])
    out = []
    for f in rows:
        cls = "badge-red" if f.get("severity") == "red" else ("badge-yellow" if f.get("severity") == "yellow" else "")
        out.append('<span class="badge {}">{}</span>'.format(cls, esc(f.get("message"))))
    if rows:
        # 客户端托管身份(应用内置/插件/缓存)的问题不单独删 Skill,处置走所属客户端
        prov = view["prov"].get(str(inst.get("instance_id"))) or {}
        advice = client_managed_advice(prov)
        if advice:
            out.append('<span class="badge badge-yellow">💡{}</span>'.format(esc(advice)))
    return "".join(out) or '<span class="badge badge-green">✅</span>'


def _jump_metric(label, count, target, cls="", title=None):
    """顶部导航指标:有内容才可点击,零值明确呈现但不制造空跳转。"""
    classes = "metric " + cls + (" metric-zero" if not count else "")
    text = '<span class="metric-label">{}</span><strong>{}</strong>'.format(
        esc(label), esc(count))
    if count:
        return '<a class="{}" href="#{}" data-jump aria-label="{}">{}</a>'.format(
            classes, esc(target), esc(title or "定位" + label), text)
    return '<span class="{}" aria-disabled="true" title="暂无项目">{}</span>'.format(
        classes, text)


def _back_top():
    return '<a class="back-top" href="#report-top" data-jump>回到顶部 ↑</a>'


def _instance_context(inst):
    """给问题/更新条目一个不暴露绝对路径的定位说明。"""
    if not inst:
        return "安装实例"
    client = CLIENT_LABELS.get(inst.get("client"), inst.get("client") or "未知客户端")
    if inst.get("plugin_name"):
        version = inst.get("plugin_version")
        plugin = "插件 {}{}".format(inst.get("plugin_name"),
                                    " " + str(version) if version else "")
        return "{} · {}".format(client, plugin)
    return "{} · {}".format(client, inst.get("kind") or "用户目录")


def _finding_row(view, finding):
    iid = finding.get("instance_id")
    inst = view["inst_by_id"].get(iid) or {}
    name = finding.get("skill") or inst.get("logical_name") or iid or "未命名 Skill"
    severity = finding.get("severity") or "info"
    badge_cls = "badge-red" if severity == "red" else ("badge-yellow" if severity == "yellow" else "")
    label = "🔴 红灯" if severity == "red" else ("🟡 黄灯" if severity == "yellow" else "提示")
    target = "#instance-{}".format(iid) if iid else "#instance-details"
    return ('<div class="attention-row {}">'
            '<div class="attention-head"><b>{}</b><span class="badge {}">{}</span></div>'
            '<p>{}</p><p class="mut">{} · <a href="{}" data-jump>定位安装实例</a></p>'
            '</div>').format(
                "attention-" + esc(severity), esc(name), badge_cls, esc(label),
                esc(finding.get("message") or "未提供问题说明"),
                esc(_instance_context(inst)), esc(target))


def _update_row(view, update):
    iid = update.get("instance_id")
    inst = view["inst_by_id"].get(iid) or {}
    name = update.get("name") or inst.get("logical_name") or iid or "未命名 Skill"
    status = update.get("status") or "unknown"
    short = {
        "candidate-update": "🟢 有候选更新",
        "needs-review": "🟡 需要审查",
        "local-custom": "🛡️ 本地定制",
        "unverifiable": "⏭️ 无法核实",
    }.get(status, status)
    diff = update.get("full_diff_summary") or {}
    changed = []
    for key, label in (("modified", "修改"), ("added", "新增"), ("removed", "删除")):
        if diff.get(key):
            changed.append("{} {} 项".format(label, len(diff[key])))
    detail = "；".join(changed) or "检测到完整目录树差异"
    target = "#instance-{}".format(iid) if iid else "#instance-details"
    btns = ""
    if iid and update.get("staging_path"):
        # F08:候选已暂存时提供更新动作;安检 gate 在引擎侧,warning 需二次确认(JS)
        btns = ' <button class="btn" data-act="update" data-id="{}" data-name="{}">🔄 生成更新计划(先安检)</button>'.format(
            esc(iid), esc(name))
    return ('<div class="attention-row attention-update">'
            '<div class="attention-head"><b>{}</b><span class="badge badge-yellow">{}</span></div>'
            '<p>{}</p><p class="mut">{} · {} · <a href="{}" data-jump>定位安装实例</a>{}</p>'
            '</div>').format(esc(name), esc(short), esc(detail),
                               esc(_instance_context(inst)), esc(update.get("repo") or "来源待核实"),
                               esc(target), btns)


def _attention_section(view):
    """把顶部的红/黄/更新数字对应到可见的、可定位的处理清单。"""
    c = view["counts"]
    red = [f for f in view["attention_findings"] if f.get("severity") == "red"]
    yellow = [f for f in view["attention_findings"] if f.get("severity") == "yellow"]
    updates = list(view["updates"].values())
    parts = []
    if red:
        parts.append('<details id="health-red" open><summary><b>🔴 红灯问题</b>'
                     '<span class="cnt">{} 个</span></summary><div class="attention-list">{}</div>{}</details>'.format(
                         len(red), "".join(_finding_row(view, f) for f in red), _back_top()))
    if yellow:
        parts.append('<details id="health-yellow" open><summary><b>🟡 黄灯问题</b>'
                     '<span class="cnt">{} 个安装实例</span></summary><div class="attention-list">{}</div>{}</details>'.format(
                         len(yellow), "".join(_finding_row(view, f) for f in yellow), _back_top()))
    updates_checked = view.get("updates_checked_at") or ""
    update_note = "上次检查:{}".format(updates_checked) if updates_checked else "状态来自最近一次更新检查"
    update_body = ("".join(_update_row(view, u) for u in updates)
                   if updates else '<p class="mut">当前没有发现上游差异。</p>')
    parts.append('<details id="update-review"{}><summary><b>🔄 待更新/复核</b>'
                 '<span class="cnt">{} 个</span></summary><p class="mut">{}。候选更新仍需先安检，再走计划→确认→备份→执行。</p>'
                 '<div class="attention-list">{}</div>{}</details>'.format(
                     " open" if updates else "", len(updates), esc(update_note), update_body, _back_top()))
    if not red and not yellow and not updates:
        intro = '<p class="all-clear">✅ 当前没有红灯、黄灯或待更新项目。</p>'
    else:
        intro = '<p class="mut">这里只列需要处理的事项；完整实例信息在下方明细中。</p>'
    return '<section id="attention"><h2>需要关注 <span class="cnt">{} 项</span></h2>{}{}</section>'.format(
        c["red"] + c["yellow"] + c["updates"], intro, "".join(parts))


JS_BLOB = """
function token(){return new URLSearchParams(location.search).get('t');}
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML;}
function toast(m){let t=document.getElementById('toast');t.textContent=m;t.className='show';clearTimeout(t._h);t._h=setTimeout(()=>t.className='',4000);}
function copyText(s){(navigator.clipboard?navigator.clipboard.writeText(s):Promise.reject()).then(()=>toast('已复制命令')).catch(()=>{const ta=document.createElement('textarea');ta.value=s;document.body.appendChild(ta);ta.select();try{document.execCommand('copy');}catch(e){}ta.remove();toast('已复制命令');});}
function openJump(hash){
  if(!hash||hash.charAt(0)!=='#')return;
  const target=document.getElementById(hash.slice(1));if(!target)return;
  const reveal=()=>{
    for(let p=target;p;p=p.parentElement){
      if((p.tagName||'').toLowerCase()==='details'){
        p.open=true;p.setAttribute('open','');
      }
    }
  };
  reveal();setTimeout(reveal,0);
  history.replaceState(null,'',hash);
  target.classList.remove('target-flash');void target.offsetWidth;target.classList.add('target-flash');
  target.scrollIntoView({behavior:'smooth',block:'start'});
}
window.addEventListener('hashchange',()=>openJump(location.hash));
window.addEventListener('DOMContentLoaded',()=>{if(location.hash)openJump(location.hash);});
if(location.hash)openJump(location.hash);
async function post(path,body){const t=token();const r=await fetch(path+(path.includes('?')?'&':'?')+'t='+encodeURIComponent(t),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let j=null;try{j=await r.json();}catch(e){}return {status:r.status,j:j};}
document.addEventListener('click',async e=>{
  const jump=e.target.closest('a[data-jump]');
  if(jump){e.preventDefault();openJump(jump.getAttribute('href'));return;}
  const b=e.target.closest('button[data-act]');if(!b)return;
  const act=b.dataset.act,id=b.dataset.id;
  if(act==='copy'){copyText(b.dataset.cmd||'');toast('已复制命令');return;}
  if(!token()){copyText(b.dataset.cmd||'');toast('静态模式:已复制等价命令');return;}
  if(act==='remove'){
    if(!confirm('为「'+b.dataset.name+'」生成删除计划?'))return;
    b.disabled=true;
    const pr=await post('/api/plan',{action:'remove',instance_ids:[id],reason:'报告建议(网页一键)'});
    if(!pr.j||!pr.j.ok){toast('❌ 生成计划失败:'+(pr.j&&pr.j.error||'请求失败'));b.disabled=false;return;}
    const p=pr.j;
    if(!confirm('计划摘要:'+p.summary+'\\n确认执行 digest: '+p.digest+'\\n(先自动备份;失败自动回滚)')){b.disabled=false;return;}
    const ar=await post('/api/apply',{plan_id:p.plan_id,digest:p.digest,confirm:true});
    toast(ar.j&&ar.j.ok?'✅ 已执行,稍后自动刷新':'❌ '+(ar.j&&ar.j.error||'执行失败'));
    if(ar.j&&ar.j.ok)setTimeout(()=>location.reload(),1500);else b.disabled=false;
    return;
  }
  if(act==='restore'){
    if(!confirm('为备份 '+b.dataset.backup+' 生成恢复计划?'))return;
    b.disabled=true;
    const pr=await post('/api/restore-plan',{backup_id:b.dataset.backup});
    if(!pr.j||!pr.j.ok){toast('❌ '+(pr.j&&pr.j.error||'请求失败'));b.disabled=false;return;}
    const p=pr.j;
    if(!confirm('计划摘要:'+p.summary+'\\n确认恢复 digest: '+p.digest+'\\n(目标已存在则冲突失败,不覆盖)')){b.disabled=false;return;}
    const ar=await post('/api/apply',{plan_id:p.plan_id,digest:p.digest,confirm:true});
    toast(ar.j&&ar.j.ok?'✅ 已恢复,稍后自动刷新':'❌ '+(ar.j&&ar.j.error||'执行失败'));
    if(ar.j&&ar.j.ok)setTimeout(()=>location.reload(),1500);else b.disabled=false;
    return;
  }
  if(act==='update'){
    if(!confirm('为「'+b.dataset.name+'」生成更新计划?(候选须已安检;计划确认后才会执行)'))return;
    b.disabled=true;
    const pr=await post('/api/plan',{action:'update',instance_id:id});
    if(!pr.j||!pr.j.ok){toast('❌ 生成更新计划失败:'+(pr.j&&pr.j.error||'请求失败'));b.disabled=false;return;}
    const p=pr.j;
    if(!confirm('更新计划摘要:'+p.summary+'\n确认执行 digest: '+p.digest+'\n(旧版本自动备份;失败自动回滚)')){b.disabled=false;return;}
    let ar=await post('/api/apply',{plan_id:p.plan_id,digest:p.digest,confirm:true});
    if(ar.j&&ar.j.error&&String(ar.j.error).indexOf('warning')>=0){
      if(!confirm('候选安检为 warning: '+ar.j.error+'\n确认接受风险并继续?')){b.disabled=false;return;}
      ar=await post('/api/apply',{plan_id:p.plan_id,digest:p.digest,confirm:true,accept_warning:true});
    }
    toast(ar.j&&ar.j.ok?'✅ 已更新,稍后自动刷新':'❌ '+(ar.j&&ar.j.error||'执行失败'));
    if(ar.j&&ar.j.ok)setTimeout(()=>location.reload(),1500);else b.disabled=false;
    return;
  }
  if(act==='ignore'){
    if(!confirm('忽略这个问题?'))return;
    const r=await post('/api/ignore',{name:b.dataset.name,match:b.dataset.match,confirm:true});
    toast(r.j&&r.j.ok?'✅ 已忽略':'❌ '+(r.j&&r.j.error||'失败'));
    if(r.j&&r.j.ok)setTimeout(()=>location.reload(),1200);
  }
});
"""


def _client_load_rows(inv):
    """各客户端加载总览行:客户端 / 加载条目 / 实际技能 / 重复条目 / 备注。"""
    cl = inv.get("client_load") or {}
    notes = {
        "codex": "2026-08-25 起自动导入共享库 ~/.agents/skills",
        "haha": "复用 ~/.claude/skills 镜像(Claude Code 卸载后由 Haha 独用)" if cl.get("haha") else "",
        "cindy": "只读投影(共享库 + Codex 目录)",
    }
    if cl.get("claude-code") and cl.get("haha") and not _claude_app_present():
        notes["claude-code"] = "应用已卸载,目录实际由 Haha 读取"
    rows = []
    for client in ("zcode", "codex", "claude-code", "haha", "cindy", "accio", "workbuddy", "ego"):
        s = cl.get(client)
        if not s or not s.get("entries"):
            continue
        dup = "⚠️ {} 个重复:{}".format(len(s["duplicates"]), "、".join(s["duplicates"][:8]) +
                                       ("…" if len(s["duplicates"]) > 8 else "")) if s["duplicates"] else "✅"
        rows.append((client, s["entries"], s["skills"], dup, notes.get(client, "")))
    return rows


def _claude_app_present():
    """只查应用是否存在(不读内容):Claude Code 应用卸载后,claude 目录实际只有 Haha 在用。"""
    for base in ("/Applications", os.path.expanduser("~/Applications")):
        for name in ("Claude.app", "Claude Code.app"):
            if os.path.isdir(os.path.join(base, name)):
                return True
    return False


def _shared_library_rows(view):
    """共享库视图行:哪些逻辑 Skill 放在 ~/.agents/skills(用户 2026-09-05 反馈:
    该事实只藏在明细表 client 列里,159 行中等于看不见)。

    每行带价值结论与其他占用客户端,让用户能就地判断去留;只做展示,
    变更仍走 计划→确认→备份→执行 流程。
    """
    insts = view["inv"].get("instances", [])
    verdict_of = {}
    for g in VERDICT_GROUPS:
        for row in view["verdict_rows"][g]:
            verdict_of[row["inst"].get("logical_name")] = (g, row["stale"])
    for row in view["unreviewed"]:
        verdict_of.setdefault(row["inst"].get("logical_name"), (None, False))
    loc_client = {l.get("location_id"): l.get("client")
                  for l in view["inv"].get("locations", [])}
    lg_insts = {}
    for i in insts:
        lg_insts.setdefault(i.get("logical_name"), []).append(i)
    rows = []
    for inst in insts:
        if inst.get("location_id") != "shared":
            continue
        nm = inst.get("logical_name")
        cls, _why = classify_instance(inst, set(), view.get("known"))
        g, stale = verdict_of.get(nm, (None, False))
        others = sorted({CLIENT_LABELS_EXT.get(loc_client.get(x.get("location_id")),
                                                x.get("client") or "") or "?"
                         for x in lg_insts.get(nm, [])
                         if x.get("location_id") != "shared"})
        rows.append({"name": nm, "iid": inst.get("instance_id"),
                     "protected": cls == "protected",
                     "verdict": g, "stale": stale,
                     "function": inst.get("function") or "",
                     "directory_name": inst.get("directory_name"),
                     "others": "、".join(others) or "仅共享库"})
    return rows


def render_html(inv, last=None, ctx=None):
    view = build_view(inv, last, ctx)
    c = view["counts"]
    overview_metrics = [
        _jump_metric("逻辑 Skill", c["total"], "instance-details"),
        _jump_metric("🛡️ 受保护", c["protected"], "protected-skills", "metric-green"),
        _jump_metric("第三方", c["third_party"], "third-party-review"),
        _jump_metric("📂 共享库", len(_shared_library_rows(view)),
                     "shared-library", "metric-blue"),
    ]
    attention_metrics = [
        _jump_metric("🔴 红灯", c["red"], "health-red", "metric-red"),
        _jump_metric("🟡 黄灯", c["yellow"], "health-yellow", "metric-yellow"),
        _jump_metric("🔄 待更新/复核", c["updates"], "update-review", "metric-blue"),
        _jump_metric("🔍 未审查", c["unreviewed"], "verdict-unreviewed", "metric-blue"),
    ]
    verdict_metrics = [
        _jump_metric(VERDICT_EMOJI[g], c[g], VERDICT_ANCHORS[g]) for g in VERDICT_GROUPS
    ]
    dashboard = (
        '<section id="report-top" class="dashboard">'
        '<div class="metric-group"><div class="metric-title">资产概况</div><div class="metrics">{}</div></div>'
        '<div class="metric-group"><div class="metric-title">需要关注</div><div class="metrics">{}</div></div>'
        '<div class="metric-group"><div class="metric-title">价值结论</div><div class="metrics">{}</div></div>'
        '</section>').format("".join(overview_metrics), "".join(attention_metrics),
                              "".join(verdict_metrics))

    # 各客户端加载上下文总览(用户最关心的口径:每个客户端启动时占用多少条)
    load_rows = _client_load_rows(inv)
    load_cells = "".join(
        '<tr><td><b>{}</b></td><td>{}</td><td>{}</td><td>{}</td><td class="mut">{}</td></tr>'.format(
            esc(CLIENT_LABELS.get(c, c)), e, s, d, esc(n))
        for c, e, s, d, n in load_rows) or '<tr><td colspan="5">无</td></tr>'
    load_sec = (
        '<details id="client-load"><summary><b>📱 各客户端加载上下文</b>'
        '<span class="cnt">启动即占用 name+description;同名多份=重复占用</span></summary>'
        '<table><tr><th>客户端</th><th>加载条目</th><th>实际技能</th><th>重复条目</th><th>备注</th></tr>'
        '{}</table></details>').format(load_cells)

    # 受保护区
    prot_rows = []
    for name in view["protected_names"]:
        lg = next((l for l in view["inv"].get("logical_skills", []) if l["name"] == name), {})
        prot_rows.append('<tr><td><b>{}</b></td><td>{}</td><td>{}</td></tr>'.format(
            esc(name), esc(lg.get("function") or ""),
            esc("、".join(lg.get("clients") or []))))
    protected_sec = (
        '<details id="protected-skills"><summary><b>🛡️ 受保护类(客户端自带 / 用户自建)</b>'
        '<span class="cnt">{n} 个 · 不进入清理建议,内容安检仍适用</span></summary>'
        '<table><tr><th>Skill</th><th>功能</th><th>客户端</th></tr>{rows}</table></details>'
    ).format(n=c["protected"], rows="".join(prot_rows) or '<tr><td colspan="3">无</td></tr>')

    # 价值审查区(五个结论组永远渲染,哪怕为空)
    sections = []
    for group in VERDICT_GROUPS:
        rows = view["verdict_rows"][group]
        cards = "".join(review_card_html(view, row, group) for row in rows)
        open_attr = " open" if rows and group in ("建议删除", "需要人工确认") else ""
        sections.append(
            '<details id="{}"{}><summary><b>{}</b><span class="cnt">{} 个</span></summary>'
            '<div class="cards">{}</div>{}</details>'.format(
                VERDICT_ANCHORS[group], open_attr, esc(VERDICT_EMOJI[group]), len(rows),
                cards or '<p class="mut">无</p>', _back_top()))
    un_rows = "".join(review_card_html(view, row, "") for row in view["unreviewed"])
    sections.append(
        '<details id="verdict-unreviewed"{}><summary><b>🔍 待审查(第三方)</b><span class="cnt">{n} 个</span></summary>'
        '<div class="cards">{cards}</div>{back}</details>'.format(
            " open" if view["unreviewed"] else "", n=len(view["unreviewed"]),
            cards=un_rows or '<p class="mut">无</p>', back=_back_top()))
    value_sec = (
        '<section id="third-party-review"><h2>第三方 Skill 价值审查</h2>'
        '<p class="mut">口径:{note}。结论只有五种;「建议删除」必有理由、替代、损失与置信度,'
        '且永不自动执行。结论过期会显著标注。</p>{sections}{back}</section>').format(
        note=esc(REPO_SCOPE_NOTE), sections="".join(sections), back=_back_top())

    # 全量明细表;groups.json 作为分类筛选维度(F08:此前有文件无消费者),
    # 未登记目录名归入默认组「未分组」;分组不改价值结论与安全状态
    groups_cfg, _ = load_json_checked(Path(data_dir()) / "groups.json", {})
    group_of = {}
    if isinstance(groups_cfg, dict):
        for grp, names in groups_cfg.items():
            for n in names if isinstance(names, list) else []:
                group_of[str(n)] = str(grp)
    rows = []
    for inst in view["inv"].get("instances", []):
        cls, _why = classify_instance(inst, set(), view.get("known"))
        if cls == "protected":
            action = '<span class="mut">受保护</span>'
        else:
            action = btn("🗑️ 删", "remove", {"id": inst.get("instance_id"),
                                             "name": inst.get("logical_name"),
                                             "cmd": _safe_plan_cmd(inst.get("instance_id"))},
                         "btn-danger")
        grp = group_of.get(str(inst.get("directory_name")), "未分组")
        rows.append(
            '<tr id="instance-{}"><td><b>{}</b>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
                esc(inst.get("instance_id")),
                esc(inst.get("logical_name")),
                ' <span class="badge">受保护</span>' if cls == "protected" else "",
                esc(grp),
                esc(inst.get("function") or ""),
                esc(CLIENT_LABELS_EXT.get(inst.get("client"), inst.get("client")) or ""),
                esc(inst.get("kind") or ""), findings_badges(view, inst), action))
    table_sec = (
        '<details id="instance-details"><summary><b>📋 安装实例明细</b>'
        '<span class="cnt">{} 个实例 / {} 个逻辑 Skill</span></summary>'
        '<table><tr><th>Skill</th><th>分组</th><th>功能</th><th>客户端</th><th>位置类型</th><th>健康</th><th>操作</th></tr>'
        '{}</table>{}</details>').format(len(view["inv"].get("instances", [])), c["total"],
                                         "".join(rows), _back_top())

    # 共享库视图:哪些 Skill 放在 ~/.agents/skills(ZCode/Codex 都会整体加载)
    shared_rows = _shared_library_rows(view)
    shared_cells = []
    for r in shared_rows:
        if r["protected"]:
            verdict_cell = '<span class="mut">受保护</span>'
            action = '<span class="mut">受保护</span>'
        else:
            if r["verdict"]:
                verdict_cell = esc(VERDICT_EMOJI[r["verdict"]])
                if r["stale"]:
                    verdict_cell += ' <span class="badge badge-yellow">已过期</span>'
            else:
                verdict_cell = '<span class="badge badge-yellow">🔍 待审查</span>'
            action = btn("🗑️ 删", "remove", {"id": r["iid"], "name": r["name"],
                                             "cmd": _safe_plan_cmd(r["iid"])}, "btn-danger")
        shared_cells.append(
            '<tr><td><b>{}</b></td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
                esc(r["name"]), esc(group_of.get(str(r["directory_name"]), "未分组")),
                verdict_cell, esc(r["function"]), esc(r["others"]), action))
    shared_sec = (
        '<details id="shared-library"><summary><b>📂 共享库(~/.agents/skills)</b>'
        '<span class="cnt">{n} 个 · ZCode 与 Codex 都会整体加载(Codex 2026-08-25 起);'
        '放一个 = 多个客户端同时占</span></summary>'
        '<table><tr><th>Skill</th><th>分组</th><th>价值结论</th><th>功能</th><th>还占用哪些客户端</th><th>操作</th></tr>'
        '{rows}</table>{back}</details>').format(
        n=len(shared_rows), rows="".join(shared_cells) or '<tr><td colspan="6" class="mut">共享库当前为空</td></tr>',
        back=_back_top())

    extras = []
    if view["backups"]:
        bk_rows = "".join(
            '<p>• <code>{}</code>({} KB · {} · {}){} {}</p>'.format(
                esc(b["filename"]), b.get("kb", "?"), esc(b.get("ts", "")),
                esc(b.get("verification_status") or "未校验"),
                ' <span style="color:#b45309">⚠损坏</span>' if b.get("verification_status") == "failed" else "",
                btn("♻️ 恢复", "restore", {"backup": b["backup_id"]}, "btn-ghost"),
                btn("📋 命令", "copy", {"cmd": static_restore_cmd(b["backup_id"])},
                    "btn-ghost"))
            for b in view["backups"])
        extras.append('<details><summary><b>♻️ 备份(恢复走两阶段计划,冲突不覆盖)</b></summary>'
                      '<div class="body">{}</div></details>'.format(bk_rows))
    if view["diff"]:
        d = view["diff"]
        body = '<p>• 新增:{}</p><p>• 移除:{}</p>'.format(
            esc("、".join(d["added"]) or "无"), esc("、".join(d["removed"]) or "无"))
        extras.append('<details><summary><b>🔄 与上次盘点差异</b></summary>'
                      '<div class="body">{}</div></details>'.format(body))

    head = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Skill 管家报告 v2</title><style>
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;max-width:1120px;margin:24px auto;padding:0 16px;color:#1f2937;background:#f8fafc;scroll-behavior:smooth}
h1{font-size:22px;margin-bottom:6px} h2{font-size:19px;margin:24px 0 8px} h3{margin-top:22px}
.dashboard{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:16px 0}
.metric-group{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:10px 12px}
.metric-title{font-size:12px;font-weight:700;color:#6b7280;margin-bottom:6px}
.metrics{display:flex;flex-wrap:wrap;gap:5px}
.metric{display:flex;align-items:center;gap:7px;background:#f1f5f9;border:1px solid transparent;border-radius:9px;padding:7px 9px;color:#334155;text-decoration:none;font-size:12px;line-height:1.2}
.metric:hover{border-color:#94a3b8;background:#e2e8f0}.metric:focus-visible{outline:3px solid #93c5fd;outline-offset:2px}
.metric strong{font-size:15px}.metric-green{background:#ecfdf5;color:#15803d}.metric-red{background:#fef2f2;color:#b91c1c}.metric-yellow{background:#fefce8;color:#a16207}.metric-blue{background:#eff6ff;color:#1d4ed8}
.metric-zero{opacity:.55;cursor:default}.metric-label{white-space:nowrap}
details{background:#fff;border:1px solid #e5e7eb;border-radius:12px;margin:10px 0;padding:10px 16px;scroll-margin-top:14px}
/* 锚点跳转的无脚本兜底:--serve 的安全策略即使暂时阻止内联脚本,目标内容仍可见。 */
details:target > .cards,details:target > .attention-list{display:grid!important}
details:target > table{display:table!important}
details:target > .body,details:target > p,details:target > a{display:block!important}
summary{cursor:pointer;font-size:16px;padding:4px 0} .cnt{color:#6b7280;font-size:13px;margin-left:10px}
section{scroll-margin-top:14px}.back-top{display:inline-block;margin-top:8px;color:#64748b;font-size:12px;text-decoration:none}.back-top:hover{text-decoration:underline}
details.target-flash,section.target-flash,tr.target-flash{animation:target-flash 1.8s ease-out}
@keyframes target-flash{0%{box-shadow:0 0 0 3px #93c5fd}100%{box-shadow:0 0 0 0 transparent}}
table{width:100%;border-collapse:collapse;margin:10px 0;font-size:13.5px}
th{text-align:left;color:#6b7280;border-bottom:2px solid #e5e7eb;padding:6px 8px}
td{border-bottom:1px solid #f1f5f9;padding:7px 8px;vertical-align:top}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:10px;margin:10px 0}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:12px 14px}
.card-t{color:#374151;font-size:14px} .card-n{font-size:15px;font-weight:700;margin:6px 0}
.card p{margin:4px 0;font-size:13px;color:#374151}
.badge{display:inline-block;font-size:12px;background:#f1f5f9;border-radius:6px;padding:2px 8px;margin:1px}
.badge-green{background:#dcfce7;color:#15803d} .badge-yellow{background:#fef9c3;color:#a16207} .badge-red{background:#fee2e2;color:#b91c1c}
.mut{color:#9ca3af;font-size:12px} code{background:#f1f5f9;padding:1px 6px;border-radius:6px;word-break:break-all}
.btn{display:inline-block;border:1px solid #d1d5db;background:#fff;border-radius:8px;padding:3px 10px;margin:1px 2px;font-size:12.5px;cursor:pointer;white-space:nowrap}
.btn:hover{background:#f3f4f6} .btn:disabled{opacity:.5}
.btn-danger{background:#fef2f2;border-color:#fecaca;color:#b91c1c} .btn-ghost{color:#6b7280}
.attention-list{display:grid;gap:7px;margin:8px 0}.attention-row{border-left:4px solid #cbd5e1;background:#f8fafc;border-radius:8px;padding:8px 10px}.attention-red{border-left-color:#ef4444}.attention-yellow{border-left-color:#eab308}.attention-update{border-left-color:#3b82f6}.attention-head{display:flex;align-items:center;gap:6px}.attention-row p{margin:4px 0;font-size:13px}.all-clear{color:#15803d;margin:8px 0}
#toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:#1f2937;color:#fff;border-radius:99px;padding:8px 18px;font-size:13px;opacity:0;pointer-events:none;transition:opacity .25s;max-width:80%}
#toast.show{opacity:.95}
@media(max-width:760px){body{margin:14px auto}.dashboard{grid-template-columns:1fr}.cards{grid-template-columns:1fr}table{display:block;overflow-x:auto;white-space:nowrap}}
</style></head><body>
<h1>📋 Skill 管家报告 <span class="mut">v2 · 价值审查面板</span></h1>
<div>生成时间:__TS__</div>
__DASHBOARD__
__ATTENTION__
__LOAD__
__SHARED__
__VALUE__
__PROTECTED__
__TABLE__
<h3>其他</h3>__EXTRAS__
<p style="color:#9ca3af;font-size:12px">由 skill-keeper v2 生成 · 所有删除/恢复都走 计划→确认→备份→执行→验证 两阶段流程,永不自动执行 · 一键操作需 <code>report.py --serve</code>(静态打开时按钮复制等价命令)</p>
<div id="toast"></div>
<script>__JS__</script>
</body></html>"""
    return (head
            .replace("__TS__", esc(inv.get("scanned_at") or ""))
            .replace("__DASHBOARD__", dashboard)
            .replace("__ATTENTION__", _attention_section(view))
            .replace("__LOAD__", load_sec)
            .replace("__SHARED__", shared_sec)
            .replace("__PROTECTED__", protected_sec)
            .replace("__VALUE__", value_sec)
            .replace("__TABLE__", table_sec)
            .replace("__EXTRAS__", "".join(extras))
            .replace("__JS__", JS_BLOB))


def render_md(inv, last=None, ctx=None):
    view = build_view(inv, last, ctx)
    c = view["counts"]
    L = ["# Skill 管家报告(v2)",
         "",
         "> 生成时间:{} · 逻辑 skill **{}** 个(受保护 {} / 第三方 {});红 {} 黄 {} · 待更新/复核 {}".format(
             inv.get("scanned_at"), c["total"], c["protected"], c["third_party"], c["red"], c["yellow"], c["updates"]),
         "",
         "口径:{}。".format(REPO_SCOPE_NOTE),
         "",
         "## 〇、各客户端加载上下文(启动即占用)",
         "",
         "| 客户端 | 加载条目 | 实际技能 | 重复条目 | 备注 |",
         "|---|---|---|---|---|"]
    for _cl, _e, _s, _d, _n in _client_load_rows(inv):
        L.append("| {} | {} | {} | {} | {} |".format(
            CLIENT_LABELS.get(_cl, _cl), _e, _s, _d, _n))
    shared_rows = _shared_library_rows(view)
    L += ["", "## 共享库({} 个)".format(len(shared_rows)), "",
          "> 共享库 = `~/.agents/skills`:ZCode 与 Codex 都会整体加载(Codex 2026-08-25 起);"
          "Haha/Claude Code 走 `~/.claude/skills` 镜像。放一个 = 多个客户端同时占。", "",
          "| Skill | 价值结论 | 功能 | 还占用哪些客户端 |", "|---|---|---|---|"]
    for r in shared_rows:
        if r["protected"]:
            verdict = "受保护"
        elif r["verdict"]:
            verdict = VERDICT_EMOJI[r["verdict"]] + ("(已过期)" if r["stale"] else "")
        else:
            verdict = "🔍 待审查"
        L.append("| {} | {} | {} | {} |".format(r["name"], verdict, r["function"], r["others"]))
    L += ["", "## 一、需要关注", ""]
    if view["attention_findings"]:
        for finding in view["attention_findings"]:
            inst = view["inst_by_id"].get(finding.get("instance_id"), {})
            level = "🔴" if finding.get("severity") == "red" else "🟡"
            L.append("- {} **{}** — {}（{}）".format(
                level, finding.get("skill") or inst.get("logical_name") or "未命名 Skill",
                finding.get("message") or "未提供问题说明", _instance_context(inst)))
    if view["updates"]:
        for update in view["updates"].values():
            L.append("- 🔄 **{}** — {}（{}）".format(
                update.get("name") or "未命名 Skill",
                UPDATE_LABEL.get(update.get("status"), update.get("status") or "未知状态"),
                update.get("repo") or "来源待核实"))
    if not view["attention_findings"] and not view["updates"]:
        L.append("- ✅ 当前没有红灯、黄灯或待更新项目。")
    L += ["",
          "## 二、价值审查(第三方 Skill)",
          ""]
    for group in VERDICT_GROUPS:
        L.append("**{}**({} 个)".format(VERDICT_EMOJI[group], c[group]))
        rows = view["verdict_rows"][group]
        if not rows:
            L.append("- 无")
        for row in rows:
            inst, rec = row["inst"], row["rec"]
            stale = "(⚠️ 结论已过期,需重新审查)" if row["stale"] else ""
            alts = "、".join(_name_of_id(view, a) for a in (rec.get("alternatives") or []))
            L.append("- **{}**({}){} — {} 依据:{};替代:{};删除后可能失去:{};置信度:{}".format(
                inst.get("logical_name"), inst.get("function") or "", stale,
                rec.get("reason") or "", "；".join(rec.get("evidence") or []),
                alts or "—", rec.get("loss_if_removed") or "—",
                rec.get("confidence") or "?"))
        L.append("")
    L.append("**🔍 待审查**({} 个)".format(c["unreviewed"]))
    if not view["unreviewed"]:
        L.append("- 无")
    for row in view["unreviewed"]:
        L.append("- **{}**({})尚未审查".format(row["inst"].get("logical_name"),
                                               row["inst"].get("function") or ""))
    L += ["", "## 三、受保护类(客户端自带 / 用户自建,{} 个)".format(c["protected"])]
    for name in view["protected_names"] or ["(无)"]:
        L.append("- {}".format(name))
    L += ["", "## 四、安装实例明细", "",
          "| Skill | 客户端 | 位置类型 | 健康 |", "|---|---|---|---|"]
    for inst in view["inv"].get("instances", []):
        rows = view["findings_by_instance"].get(inst.get("instance_id"), [])
        health = "；".join(f.get("message") for f in rows) or "✅"
        cls, _ = classify_instance(inst, set(), view.get("known"))
        L.append("| {}{} | {} | {} | {} |".format(
            inst.get("logical_name"),
            "(受保护)" if cls == "protected" else "",
            CLIENT_LABELS_EXT.get(inst.get("client"), inst.get("client")),
            inst.get("kind"), health))
    if view["backups"]:
        L += ["", "## 四、备份(恢复走两阶段计划,冲突不覆盖)"]
        for b in view["backups"]:
            L.append("- {}({} KB · {} · {})".format(
            b["filename"], b.get("kb", "?"), b.get("ts", ""),
            b.get("verification_status") or "未校验"))
    L += ["", "> 一键操作:`{}`;所有变更走 计划→确认→备份→执行→验证,永不自动执行。".format(SERVE_HINT)]
    return "\n".join(L), view


def backups_list():
    """备份行统一 {backup_id, filename, path, kb, ts, verification_status}。

    API/命令一律使用 backup_id(不含前后缀);F08 修复:此前把整文件名当 id
    传给 API,底层再拼 backup-<id>.tar.gz 造成双重前后缀,恢复按钮必失败。"""
    backups = Path(BASE) / "backups"
    if not backups.is_dir():
        return []
    from scripts.core.backup import BackupError, verify_backup
    out = []
    for f in sorted(backups.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.name.startswith("backup-") and f.name.endswith(".tar.gz"):
            backup_id = f.name[len("backup-"):-len(".tar.gz")]
            try:
                info = verify_backup(f)
                status = "ok" if info.get("ok") else "failed"
            except BackupError:
                status = "failed"
            out.append({"backup_id": backup_id, "filename": f.name, "path": str(f),
                        "kb": round(f.stat().st_size / 1024, 1),
                        "ts": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
                        "verification_status": status})
    return out[:30]


def main():
    ap = argparse.ArgumentParser(description="skill-keeper v2 价值审查报告")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--serve", action="store_true")
    args, rest = ap.parse_known_args()
    if args.serve:
        subprocess.run([sys.executable, os.path.join(BASE, "scripts", "serve.py")] + rest)
        return
    ddir = data_dir()
    inv = _load(ddir / "inventory.json")
    if not isinstance(inv, dict) or inv.get("schema_version") != 2:
        print("🛑 inventory 不是 v2(先跑 scan.py 重新扫描)")
        sys.exit(2)
    last = _load(ddir / "inventory-last.json")
    update_store = _load(ddir / "updates.json") or {}
    reviews_store = _load(ddir / "value-reviews.json") or {}
    ctx = {
        "updates": update_store.get("differs", []) if isinstance(update_store, dict) else [],
        "updates_checked_at": update_store.get("checked_at") if isinstance(update_store, dict) else "",
        "ignore": _load(ddir / "ignore.json") or {},
        "backups": backups_list(),
        "value_reviews": reviews_store.get("reviews", []) if isinstance(reviews_store, dict) else [],
        "reputation": _load(ddir / "reputation.json") or {},
        "self_built": _self_built(ddir),
        "known": load_user_config(ddir),
        "queue": _load(ddir / "review-queue.json"),
    }
    md, view = render_md(inv, last, ctx)
    if args.json:
        c = view["counts"]
        print(json.dumps({
            "generated_at": inv.get("scanned_at"), "schema_version": 2,
            "total": c["total"], "protected": c["protected"], "third_party": c["third_party"],
            "verdicts": {g: c[g] for g in VERDICT_GROUPS},
            "unreviewed": c["unreviewed"], "red": c["red"], "yellow": c["yellow"],
            "updates": c["updates"],
            "operational_ok": True, "health_status": inv.get("health_status", "ok"),
        }, ensure_ascii=False, indent=1))
        sys.exit(1 if c["red"] else 0)
    print(md)
    (ddir / "report.md").write_text(md + "\n", encoding="utf-8")
    (ddir / "report.html").write_text(render_html(inv, last, ctx), encoding="utf-8")
    print("\n💾 已存:data/report.md + data/report.html(双击浏览器打开;一键操作用 --serve)",
          file=sys.stderr)
    sys.exit(1 if view["counts"]["red"] else 0)


def _self_built(ddir):
    try:
        text = (ddir / "self-built.txt").read_text(encoding="utf-8")
    except OSError:
        return []
    return [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]


if __name__ == "__main__":
    main()
