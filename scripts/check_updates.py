#!/usr/bin/env python3
"""skill-keeper 更新检查(只读):对比本地与上游 SKILL.md,不做任何修改。
结果(含本地/上游版本对比与建议状态)缓存到 data/updates.json,供 report.py 生成「处理建议」。
status: upstream-newer=上游有新版,建议更新 | content-diff=版本未变但内容有差异,可能本地定制,待确认
        | local-ahead=本地版本更高,保留本地。"""
import difflib, json, os, re, subprocess, sys, time, urllib.parse, urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DATA = os.path.join(BASE, "data")
HOME = os.path.expanduser("~")
sys.path.insert(0, os.path.join(BASE, "scripts"))
from scan import parse_frontmatter

STATUS_LABEL = {
    "upstream-newer": "上游有新版,建议更新",
    "content-diff": "版本未变但内容有差异,可能本地定制,待确认",
    "local-ahead": "本地版本更高,保留本地",
}
VERDICT_LABEL = {"update": "🟢 建议更新", "keep": "🛡️ 建议保留", "manual": "🟡 需人工研判"}


def gh_raw(repo, path):
    """经 gh api 取上游文件原文;失败返回 None"""
    r = subprocess.run(["gh", "api", f"repos/{repo}/contents/{urllib.parse.quote(path)}",
                        "--header", "Accept: application/vnd.github.raw"],
                       capture_output=True, text=True, timeout=30)
    return r.stdout if r.returncode == 0 else None


def skills_sh_skillmd(repo, slug):
    """经 skills.sh download API 取上游 SKILL.md"""
    try:
        owner, r = repo.split("/")
        url = f"https://skills.sh/api/download/{owner}/{r}/{slug}"
        req = urllib.request.Request(url, headers={"User-Agent": "skill-keeper"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            for f in json.loads(resp.read().decode()).get("files", []):
                if f["path"] == "SKILL.md":
                    return f.get("contents") or ""
    except Exception:
        return None
    return None


def fm_version(text):
    fm, ok = parse_frontmatter(text or "")
    return str(fm.get("version")) if ok and fm.get("version") else ""


def ver_tuple(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.]", str(v).lstrip("vV")))


def gh_last_commit_date(repo, path):
    """上游文件最后一次被改动的时间(YYYY-MM-DD);取不到返回 None"""
    r = subprocess.run(["gh", "api", f"repos/{repo}/commits?path={urllib.parse.quote(path)}&per_page=1"],
                       capture_output=True, text=True, timeout=30)
    try:
        return json.loads(r.stdout)[0]["commit"]["committer"]["date"][:10]
    except Exception:
        return None


def diff_anatomy(local, upstream):
    """统计差异规模,并判断改动是否只落在 frontmatter 说明区(没碰正文)。
    → (总变更行数, 是否仅说明区, 改到的键名列表)"""
    a, b = local.splitlines(), upstream.splitlines()
    m = re.match(r"^---\s*\n(.*?)\n---", local, re.S)
    fm_end = len(m.group(0).splitlines()) if m else 0
    changed, keys, meta_only = 0, set(), True
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        lines = a[i1:i2] + b[j1:j2]
        changed += len(lines)
        if i1 >= fm_end:
            meta_only = False
        for l in lines:
            km = re.match(r"^([A-Za-z_-]{2,})\s*:", l.strip())
            if km:
                keys.add(km.group(1))
    return changed, meta_only, sorted(keys)


def judge(status, lv, uv, meta_only, keys, changed, repo, path, src_type, local_sk):
    """把差异翻译成给非程序员的结论:update=建议更新 keep=建议保留 manual=机器判不了。
    依据:版本号 → 是否只碰说明区 → 上游最后改动时间 vs 本地文件改动时间 → 改动规模。"""
    if status == "local-ahead":
        return "keep", "本地版本比上游还新,保留本地即可"
    if status == "upstream-newer":
        return "update", f"上游发布了新版 v{uv}(本地 v{lv}),建议更新"
    if meta_only:
        ks = "、".join(keys[:4]) if keys else "说明信息"
        return "update", f"上游只改了{ks}这类说明信息,正文没变,更新无风险"
    up_date = gh_last_commit_date(repo, path) if (src_type == "github" and path) else None
    loc_date = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(local_sk))) \
        if local_sk and os.path.exists(local_sk) else None
    if up_date and loc_date:
        if up_date > loc_date:
            return "update", f"上游 {up_date} 改过这个文件,你本地版本停在 {loc_date}——上游比你新,建议更新"
        return "keep", f"上游自 {up_date} 就没再动过,差异是你本地的定制,建议保留、不要覆盖"
    if changed <= 5:
        return "update", f"上游只改了 {changed} 行,像小修小补,可放心更新(会先自动备份,可一键恢复)"
    return "manual", f"上游改了 {changed} 行但版本号没变,机器判断不了谁对谁错——先保留,需要时让我人工看差异"


def main():
    inv = json.load(open(os.path.join(DATA, "inventory.json"), encoding="utf-8"))
    lock = json.load(open(os.path.join(HOME, ".agents/.skill-lock.json"), encoding="utf-8")).get("skills", {})
    ok, diff, skip = [], [], []
    for s in inv["skills"]:
        name, src = s["name"], s["source"]
        repo, path, t = src.get("repo"), src.get("path"), src.get("type")
        if t == "plugin" or t in ("self-built", "builtin-app", "unknown", "skillhub") or not repo:
            reason = {"plugin": "插件管理", "self-built": "自建", "builtin-app": "随应用自带",
                      "unknown": "来源不明", "skillhub": "SkillHub 来源无比对接口"}.get(t, "无上游可比")
            skip.append((name, reason))
            continue
        # 本地内容(取优先级最高的非缓存实例)
        local, local_sk = None, None
        for i in sorted(s["instances"], key=lambda x: x.get("priority", 9) if "priority" in x else 9):
            rp = i.get("real_path")
            if rp and not i.get("stale_cache"):
                sk = os.path.join(rp, "SKILL.md")
                if os.path.exists(sk):
                    local = open(sk, encoding="utf-8", errors="ignore").read()
                    local_sk = sk
                    break
        if local is None:
            skip.append((name, "本地文件缺失"))
            continue
        upstream = None
        slug = None
        if path:
            upstream = gh_raw(repo, path)
        if upstream is None and t == "skills.sh" and "/" in repo:
            rp = next((i["real_path"] for i in s["instances"] if i.get("real_path")), None)
            meta = os.path.join(rp, "_meta.json") if rp else None
            slug = (json.load(open(meta)).get("slug") if meta and os.path.exists(meta) else None) or name
            upstream = skills_sh_skillmd(repo, slug)
        if upstream is None:
            skip.append((name, f"上游拉取失败({repo})"))
            continue
        if upstream.strip() == local.strip():
            ok.append(name)
            continue
        lv, uv = fm_version(local), fm_version(upstream)
        if lv and uv and ver_tuple(uv) > ver_tuple(lv):
            status = "upstream-newer"
        elif lv and uv and ver_tuple(uv) < ver_tuple(lv):
            status = "local-ahead"
        else:
            status = "content-diff"
        changed, meta_only, keys = diff_anatomy(local, upstream)
        verdict, reason = judge(status, lv, uv, meta_only, keys, changed, repo, path, t, local_sk)
        diff.append({"name": name, "repo": repo, "type": t, "slug": slug,
                     "local_version": lv, "upstream_version": uv, "status": status,
                     "verdict": verdict, "reason": reason,
                     "changed_lines": changed, "meta_only": meta_only})
    # 缓存给 report.py 生成处理建议
    json.dump({"checked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "differs": diff, "up_to_date": ok},
              open(os.path.join(DATA, "updates.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    if "--json" in sys.argv:
        # 机器可读输出;退出码:0=全部一致,1=有差异
        print(json.dumps({"checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                          "up_to_date": ok, "differs": diff,
                          "skipped": [{"name": n, "reason": w} for n, w in skip]},
                         ensure_ascii=False, indent=1))
        sys.exit(1 if diff else 0)
    print(f"✅ 与上游一致 {len(ok)} 个")
    if diff:
        print(f"🔴 与上游有差异 {len(diff)} 个,结论如下:")
        for d in diff:
            print(f"   - {d['name']} ← {d['repo']}  [{VERDICT_LABEL[d['verdict']]}]  {d['reason']}")
    if skip:
        print(f"⏭️ 跳过 {len(skip)} 个:")
        for n, why in skip:
            print(f"   - {n}({why})")
    if diff:
        print("\n提示:确认要更新时,skills.sh 来源用 `npx -y skills add <owner/repo>@<slug> -g -y`,GitHub 来源用 gh api 拉取覆盖,操作前先备份。"
              "\n或用交互报告一键处理:python3 scripts/report.py --serve")
    # 结果已写入 data/updates.json(供 report.py 生成处理建议)
    sys.exit(1 if diff else 0)


if __name__ == "__main__":
    main()
