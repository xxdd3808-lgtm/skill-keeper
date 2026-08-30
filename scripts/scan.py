#!/usr/bin/env python3
"""skill-keeper 扫描器:全位置扫描本地 skill → data/inventory.json(只读,不改任何 skill)"""
import hashlib, json, os, re, shutil, subprocess, sys, time

HOME = os.path.expanduser("~")
# 项目真实目录:从脚本自身位置反推,经符号链接调用也能定位(项目文件夹可整体迁移)
BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DATA = os.path.join(BASE, "data")
LOCK_FILE = os.path.join(HOME, ".agents/.skill-lock.json")

# (位置, 客户端归属, ZCode发现优先级——数字小先加载,遮蔽同名后者)
LOCATIONS = [
    (f"{HOME}/.zcode/skills", "zcode", 1),
    (f"{HOME}/.agents/skills", "shared", 2),
    (f"{HOME}/.claude/skills", "claude-code", 3),
    (f"{HOME}/.codex/skills", "codex", 4),
    (f"{HOME}/.local/share/ego/ego-skills", "ego", 5),
]
PLUGIN_CACHE = f"{HOME}/.zcode/cli/plugins/cache"

# 工作区级 skill 目录(项目内的 .claude/skills、.agents/skills 等),逐行配置在 data/workspace-locations.txt。
# 这些 skill 不随客户端全局加载,只在进入该项目工作时被发现,客户端标注加"(工作区)"后缀。
def _load_workspace_locations():
    rows = []
    p = os.path.join(DATA, "workspace-locations.txt")
    if not os.path.exists(p):
        return rows
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        path = os.path.expanduser(line)
        parts = os.path.normpath(path).split(os.sep)
        client = "workspace-claude" if ".claude" in parts else "workspace-zcode"
        rows.append((path, client, 6))
    return rows

WORKSPACE_LOCATIONS = _load_workspace_locations()

CLIENTS_OF_LOCATION = {
    "zcode": ["zcode"], "shared": ["zcode"],
    "claude-code": ["claude-code"], "codex": ["codex"], "ego": ["ego"], "plugin": ["zcode"],
    "workspace-zcode": ["zcode(工作区)"], "workspace-claude": ["claude-code(工作区)"],
}
# ZCode 会在这些位置间发现同名 skill 并全部列出(只加载优先级第一个) → 只有它们之间才存在"双份占上下文"
ZCODE_DISCOVERY_CLIENTS = ("zcode", "shared", "plugin")

def load_json(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default

KNOWN = load_json(os.path.join(DATA, "known-sources.json"), {})
_GROUPS_RAW = {k: v for k, v in load_json(os.path.join(DATA, "groups.json"), {}).items() if not k.startswith("_")}
# 反转成 目录名/名字 → 组名,便于查表
GROUPS = {}
for _g, _dirs in _GROUPS_RAW.items():
    for _d in _dirs:
        GROUPS[_d] = _g
SELF_BUILT = set()
_p = os.path.join(DATA, "self-built.txt")
if os.path.exists(_p):
    SELF_BUILT = {l.strip() for l in open(_p, encoding="utf-8") if l.strip() and not l.startswith("#")}
# 忽略规则:{skill名: [子串,…]},命中的健康问题不再计入红黄,单独记入 health.ignored 供报告展示
IGNORE = {k: [str(x) for x in v] for k, v in load_json(os.path.join(DATA, "ignore.json"), {}).items()
          if isinstance(v, list)}
# 安检台账(data/vetted.json):{skill目录名: {verdict: safe|warning|danger, note, vetted_at, sk_hash}}
# sk_hash 是安检时的内容指纹;扫描发现指纹变了,就把旧结论降级为"需复检"。
VETTED = load_json(os.path.join(DATA, "vetted.json"), {})

def collect_bins(obj, out):
    """递归找 frontmatter 里声明的依赖命令(metadata.*.requires.bins)"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "bins" and isinstance(v, list):
                out.extend(str(x) for x in v)
            else:
                collect_bins(v, out)
    elif isinstance(obj, list):
        for x in obj:
            collect_bins(x, out)

def parse_frontmatter(text):
    """返回 (dict, ok)。优先 PyYAML,缺失时用简版解析(覆盖 name/description/version 常规写法)。"""
    if not text.startswith("---"):
        return {}, False
    m = re.search(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}, False
    fm_text = m.group(1)
    try:
        import yaml
        return (yaml.safe_load(fm_text) or {}), True
    except ImportError:
        pass
    except Exception:
        return {}, False
    out, cur_key, cur_mode = {}, None, None
    for line in fm_text.splitlines():
        if re.match(r"^\s", line) and cur_key:
            out[cur_key] = (out[cur_key] + " " + line.strip()).strip()
            continue
        mm = re.match(r'^([A-Za-z_-]+):\s*(.*)$', line)
        if not mm:
            continue
        cur_key, val = mm.group(1), mm.group(2).strip()
        cur_mode = None
        if val in (">", ">-", "|", "|-"):
            cur_mode, out[cur_key] = val, ""
        else:
            out[cur_key] = val.strip('"').strip("'")
    return out, True

def sk_signature(real_path):
    """skill 内容指纹:SKILL.md 的 sha256 前 16 位 + 文件数。安检记账用,指纹变了就该复检。"""
    sk = os.path.join(real_path, "SKILL.md")
    try:
        h = hashlib.sha256(open(sk, "rb").read()).hexdigest()[:16]
    except OSError:
        h = "none"
    n = sum(len(fs) for _, _, fs in os.walk(real_path))
    return f"{h}:{n}"

def first_sentence(desc, limit=60):
    if not desc:
        return ""
    d = re.sub(r"\s+", " ", str(desc).strip())
    for sep in ["。", "!", "?", ";"]:
        if sep in d:
            d = d.split(sep)[0] + sep
            break
    return d[:limit]

def source_of(dir_name, fm, inst_dir):
    """来源推断:自建白名单 → known-sources → _meta.json → 锁文件 → homepage → unknown"""
    if dir_name in SELF_BUILT or str(fm.get("name", "")).lower().replace(" ", "-") in SELF_BUILT:
        return {"type": "self-built", "repo": None, "path": None, "note": "自建白名单受保护"}
    if dir_name in KNOWN:
        k = dict(KNOWN[dir_name]); k.pop("_comment", None); return k
    if dir_name.startswith("autoglm-"):
        return {"type": "builtin-app", "repo": None, "path": None, "note": "智谱 AutoGLM 随应用自带"}
    meta = load_json(os.path.join(inst_dir, "_meta.json"), None)
    if isinstance(meta, dict) and meta.get("slug"):
        return {"type": "skills.sh", "repo": None, "path": None,
                "note": f"skills.sh 回执 slug={meta['slug']} v{meta.get('version','?')}"}
    hp = str(fm.get("homepage") or "")
    if "clawic.com" in hp:
        return {"type": "github", "repo": "clawic/skills", "path": f"skills/{dir_name}/SKILL.md"}
    lock = load_json(LOCK_FILE, {}).get("skills", {})
    if dir_name in lock:
        return {"type": "github", "repo": lock[dir_name].get("source"), "path": lock[dir_name].get("skillPath")}
    return {"type": "unknown", "repo": None, "path": None, "note": "来源不明,待补 known-sources.json"}

def scan_instance(loc_path, d, client, priority, location_label):
    p = os.path.join(loc_path, d)
    inst = {"dir": d, "name": d, "location": location_label, "client": client, "priority": priority,
            "is_symlink": os.path.islink(p), "health": {"issues": []}, "files": 0, "size": 0, "mtime": None}
    real = os.path.realpath(p)
    if os.path.islink(p) and not os.path.exists(p):
        inst["health"]["issues"].append("🔴 悬空符号链接")
        inst["junk"] = True
        return inst
    inst["real_path"] = real
    if os.path.islink(p) and real == os.path.realpath(loc_path):
        inst["health"]["issues"].append("🔴 循环符号链接")
        inst["junk"] = True
        return inst
    sk = os.path.join(real, "SKILL.md")
    if not os.path.exists(sk):
        inst["health"]["issues"].append("🔴 无 SKILL.md(非 skill 条目)")
        inst["junk"] = True
        return inst
    for root, _, fs in os.walk(real):
        for f in fs:
            fp = os.path.join(root, f)
            try:
                inst["size"] += os.path.getsize(fp)
            except OSError:
                pass
            inst["files"] += 1
    inst["sk_hash"] = sk_signature(real)
    inst["mtime"] = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(sk)))
    fm, ok = parse_frontmatter(open(sk, encoding="utf-8", errors="ignore").read(8000))
    inst["yaml_ok"] = ok
    inst["version"] = str(fm.get("version") or "")
    inst["fm"] = {k: fm.get(k) for k in ("name", "description", "version", "homepage", "user_invocable", "invocable_trigger") if fm.get(k)}
    if not ok or not fm.get("name"):
        inst["health"]["issues"].append("🔴 frontmatter 缺 name 或解析失败")
    if not fm.get("description"):
        inst["health"]["issues"].append("🔴 frontmatter 缺 description")
    if "已归档" in open(sk, encoding="utf-8", errors="ignore").read(2000):
        inst["health"]["issues"].append("🔴 仍是瘦身触发壳")
    inst["name"] = str(fm.get("name") or d).strip()
    desc = str(fm.get("description") or "")
    inst["context_bytes"] = len(inst["name"].encode()) + len(desc.encode())
    inst["trigger"] = "slash" if (fm.get("user_invocable") or fm.get("invocable_trigger")) else "auto"
    inst["function"] = first_sentence(desc)
    inst["source"] = source_of(d, fm, real)
    # 依赖命令检查:skill 声明的外部程序是否存在
    bins = []
    collect_bins(fm, bins)
    missing = sorted({b for b in bins if b and not shutil.which(b)})
    inst["requires_bins"] = sorted(set(bins))
    if missing:
        inst["health"]["issues"].append(f"🟡 依赖命令缺失: {', '.join(missing)}(skill 可能跑不全)")
    return inst

def scan_all():
    instances = []
    for loc_path, client, prio in LOCATIONS:
        label = loc_path.replace(HOME, "~")
        if not os.path.isdir(loc_path):
            continue
        for d in sorted(os.listdir(loc_path)):
            instances.append(scan_instance(loc_path, d, client, prio, label))
    # 工作区级 skill(配置在 data/workspace-locations.txt):项目内 .claude/skills、.agents/skills
    for loc_path, client, prio in WORKSPACE_LOCATIONS:
        label = loc_path.replace(HOME, "~")
        if not os.path.isdir(loc_path):
            continue
        for d in sorted(os.listdir(loc_path)):
            instances.append(scan_instance(loc_path, d, client, prio, label))
    # 插件缓存:~/.zcode/cli/plugins/cache/<market>/<plugin>/<ver>/skills/<name>/SKILL.md
    # 同一插件可能有多个历史版本缓存,只有最新版会被加载,旧版标注为可清理缓存
    def ver_key(v):
        return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.]", str(v)))
    plugin_versions = {}
    for sk in __import__("glob").glob(f"{PLUGIN_CACHE}/*/*/*/skills/*/SKILL.md"):
        parts = sk.split("/plugins/cache/")[1].split("/")
        plugin, ver = parts[1], parts[2]
        plugin_versions.setdefault((plugin, os.path.basename(os.path.dirname(sk))), []).append((ver, sk))
    for (plugin, d), vers in plugin_versions.items():
        latest = max(vers, key=lambda x: ver_key(x[0]))[0]
        for ver, sk in vers:
            inst = {"dir": f"{plugin}:{d}", "location": "plugin-cache", "client": "plugin", "priority": 9,
                    "is_symlink": False, "real_path": os.path.dirname(sk), "health": {"issues": []},
                    "files": 0, "size": 0, "mtime": None, "plugin_version": ver}
            fm, ok = parse_frontmatter(open(sk, encoding="utf-8", errors="ignore").read(8000))
            inst["yaml_ok"] = ok
            inst["fm"] = {k: fm.get(k) for k in ("name", "description", "version") if fm.get(k)}
            # 插件 skill 命名空间化,避免与用户同名 skill 被错误合并
            inst["name"] = f"{plugin}:{str(fm.get('name') or d).strip()}"
            desc = str(fm.get("description") or "")
            inst["context_bytes"] = len(inst["name"].encode()) + len(desc.encode())
            inst["trigger"] = "auto"
            inst["function"] = first_sentence(desc)
            inst["source"] = {"type": "plugin", "repo": plugin, "path": None,
                              "note": f"插件自带 v{ver},由插件系统管理"}
            if ver != latest:
                inst["stale_cache"] = True
                inst["health"]["issues"].append(f"🟡 插件旧版本缓存 v{ver}(最新 v{latest},未加载,可清理)")
            instances.append(inst)
    return instances

def aggregate(instances):
    junk = [i for i in instances if i.get("junk")]
    groups = {}
    for inst in instances:
        if not inst.get("junk"):
            groups.setdefault(inst["name"], []).append(inst)
    skills = []
    for name, insts in groups.items():
        # 加载实例:同优先级时取非旧缓存版本
        insts.sort(key=lambda x: (x.get("priority", 9), bool(x.get("stale_cache"))))
        loaded = insts[0]
        clients, seen = [], set()
        for i in insts:
            for c in CLIENTS_OF_LOCATION.get(i["client"], [i["client"]]):
                if c not in seen:
                    seen.add(c); clients.append(c)
            # 实体物理位于某客户端专属目录时(如 ego-browser 经符号链接进 .agents),该客户端也在用
            rp = i.get("real_path") or ""
            for loc_path, client, _ in LOCATIONS:
                if client != "shared" and rp.startswith(loc_path + os.sep):
                    if client not in seen:
                        seen.add(client); clients.append(client)
        srcs = {json.dumps(i.get("source"), ensure_ascii=False, sort_keys=True)
                for i in insts if i.get("source") and not i.get("stale_cache")}
        issues = sorted({iss for i in insts for iss in i["health"]["issues"]})
        ign_rules = IGNORE.get(name, [])
        kept, dropped = [], []
        for iss in issues:
            (dropped if any(r in iss for r in ign_rules) else kept).append(iss)
        # 只有 ZCode 的多位置发现机制才会造成"双份进上下文";跨客户端符号链接是正常拓扑
        zcode_insts = [i for i in insts if i["client"] in ZCODE_DISCOVERY_CLIENTS and not i.get("stale_cache")]
        duplicated = len(zcode_insts) > 1
        if duplicated:
            locs = "、".join(i["location"] for i in zcode_insts)
            issues.append(f"🟡 ZCode 同名 {len(zcode_insts)} 份:{locs}(全部进列表,只加载第一份)")
        # 链接漂移检测:快捷方式指向的内容 vs 主库本体是否一致
        master = next((i for i in insts if i.get("location") == "~/.agents/skills"
                       and not i.get("is_symlink") and i.get("real_path")), None)
        if master and os.path.exists(os.path.join(master["real_path"], "SKILL.md")):
            master_md = open(os.path.join(master["real_path"], "SKILL.md"), encoding="utf-8", errors="ignore").read()
            for i in insts:
                if i is master or not i.get("is_symlink") or not i.get("real_path"):
                    continue
                i_sk = os.path.join(i["real_path"], "SKILL.md")
                if not os.path.exists(i_sk):
                    issues.append(f"🟡 链接目标缺 SKILL.md: {i['location']}/{i['dir']}")
                elif open(i_sk, encoding="utf-8", errors="ignore").read() != master_md:
                    issues.append(f"🟡 链接漂移: {i['location']}/{i['dir']} 的内容与主库不一致")
        # 安检状态:自建/插件/随应用自带免检;第三方查台账,内容指纹变了降级为需复检
        ent_inst = next((x for x in insts if x.get("real_path") and not x.get("is_symlink") and not x.get("stale_cache")), None)
        if (loaded.get("source") or {}).get("type") in ("self-built", "plugin", "builtin-app") or not ent_inst:
            vetting = {"status": "exempt"}
        else:
            rec = VETTED.get(ent_inst["dir"]) or {}
            if not rec:
                vetting = {"status": "unvetted"}
            elif rec.get("sk_hash") != ent_inst.get("sk_hash"):
                vetting = {"status": "changed", "vetted_at": rec.get("vetted_at"), "note": rec.get("note")}
            else:
                vetting = {"status": rec.get("verdict", "unvetted"), "vetted_at": rec.get("vetted_at"), "note": rec.get("note")}
        if vetting["status"] == "warning":
            kept.append(f"🟡 安检存疑({vetting.get('vetted_at') or '?'}): {(vetting.get('note') or '')[:80]}")
        elif vetting["status"] == "danger":
            kept.append(f"🔴 安检判危({vetting.get('vetted_at') or '?'}): {(vetting.get('note') or '')[:80]}")
        entry = {
            "name": name,
            "function": loaded.get("function", ""),
            "source": loaded.get("source", {"type": "unknown"}),
            "group": GROUPS.get(loaded["dir"])
                     or ("ZCode 插件" if loaded.get("source", {}).get("type") == "plugin" else "未分组"),
            "clients": clients,
            "trigger": loaded.get("trigger", "auto"),
            "context_bytes": sum(i.get("context_bytes", 0) for i in insts),
            "instances": [{k: i.get(k) for k in ("dir", "location", "client", "is_symlink", "version", "real_path", "plugin_version", "stale_cache", "context_bytes", "sk_hash")} for i in insts],
            "duplicated": duplicated,
            "vetting": vetting,
            "health": {"issues": kept, "ignored": dropped},
            "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if len(srcs) > 1:
            entry["health"]["issues"].append("🟡 各副本来源不一致")
        skills.append(entry)
    skills.sort(key=lambda s: s["name"].lower())
    return skills, junk

def main():
    instances = scan_all()
    cur = os.path.join(DATA, "inventory.json")
    if os.path.exists(cur):
        shutil.copy2(cur, os.path.join(DATA, "inventory-last.json"))
    skills, junk = aggregate(instances)
    inv = {
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(skills),
        "by_source": {},
        "junk": [{"dir": j["dir"], "location": j["location"], "issues": j["health"]["issues"]} for j in junk],
        "skills": skills,
    }
    for s in skills:
        t = s["source"].get("type", "unknown")
        inv["by_source"][t] = inv["by_source"].get(t, 0) + 1
    json.dump(inv, open(cur, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    red = [{"name": s["name"], "issues": [i for i in s["health"]["issues"] if i.startswith("🔴")]}
           for s in skills if any(i.startswith("🔴") for i in s["health"]["issues"])]
    yellow = [{"name": s["name"], "issues": [i for i in s["health"]["issues"] if i.startswith("🟡")]}
              for s in skills if any(i.startswith("🟡") for i in s["health"]["issues"])]
    dup = [s["name"] for s in skills if s["duplicated"]]
    ignored_n = sum(len(s["health"].get("ignored", [])) for s in skills)
    need_vet = [s["name"] for s in skills if (s.get("vetting") or {}).get("status") in ("unvetted", "changed")]

    if "--json" in sys.argv:
        # 机器可读输出;退出码:0=健康,1=有红色问题,2=用法错误
        print(json.dumps({
            "scanned_at": inv["scanned_at"], "total": inv["total"],
            "by_source": inv["by_source"],
            "duplicated": dup, "red": red, "yellow": yellow, "junk_count": len(junk),
            "ignored_issues": ignored_n, "need_vet": need_vet,
        }, ensure_ascii=False, indent=1))
        sys.exit(1 if red else 0)

    # 概要输出
    print(f"✅ 扫描完成:{inv['total']} 个 skill → {cur}")
    print("来源分布:", json.dumps(inv["by_source"], ensure_ascii=False))
    if junk:
        print(f"非 skill 条目(已归档到 junk 段,不计入总数): {len(junk)} 个")
        for j in junk:
            print(f"   - {j['dir']} @ {j['location']}  {';'.join(j['health']['issues'])}")
    print(f"重复加载: {len(dup)} 个" + (":" + "、".join(dup) if dup else ""))
    print(f"健康问题: {len(red)} 个红色 / {len(yellow)} 个黄色" +
          ("  🔴:" + "、".join(r["name"] for r in red) if red else "") +
          ("  🟡:" + "、".join(y["name"] for y in yellow) if yellow else ""))
    if ignored_n:
        print(f"已忽略问题: {ignored_n} 条(规则在 data/ignore.json,详情见报告)")
    if need_vet:
        print(f"待安检: {len(need_vet)} 个第三方 skill 未安检或内容已变(清单在报告处理建议区)")
    print(f"详细报告: python3 {os.path.join(BASE, 'scripts', 'report.py')}")

if __name__ == "__main__":
    main()
