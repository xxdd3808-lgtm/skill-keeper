#!/usr/bin/env python3
"""skill-keeper v2 扫描器:客户端适配器发现 + 完整目录指纹 → inventory v2。

只读铁律:本脚本绝不修改任何 skill;数据目录可用 SKILL_KEEPER_DATA 覆盖(测试/多环境),
未设置时用项目 data/。schema 见 scripts/core/models.py,v2 结构:
  locations(位置)/ instances(物理实例)/ logical_skills(逻辑身份)
  / findings(结构化健康问题)/ config_issues(配置问题)

Task 3 起,未知客户端可由模型提供位置声明(只读、仅本次、不持久化):
  scan.py --root CLIENT=PATH            单根直传
  scan.py --locations-json FILE|-       声明文件或 stdin(字段白名单见 core/location_input.py)
临时声明产生的实例永远不可变,不提供任何变更入口;长期管理请写 client-locations.json。
"""
import json, os, re, shutil, sys, time
from pathlib import Path

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from scripts.core.clients import discover_locations, discover_skill_roots  # noqa: E402
from scripts.core.clients import load_rules  # noqa: E402
from scripts.core.fingerprint import FingerprintError, instance_id, tree_hash  # noqa: E402
from scripts.core.io import atomic_write_json, load_json_checked, redact_secrets  # noqa: E402
from scripts.core.location_input import (LocationInputError, MAX_DECL_BYTES,  # noqa: E402
                                         parse_cli_roots, parse_declaration)
from scripts.core.models import SCHEMA_VERSION, Location  # noqa: E402
from scripts.core.platform import is_absolute_path  # noqa: E402
from scripts.core.runtime import default_data_dir  # noqa: E402

HOME = os.path.expanduser("~")
LOCK_FILE = os.path.join(HOME, ".agents/.skill-lock.json")
CLIENT_LABELS = load_rules.CLIENT_LABELS
DUP_FINDING_CLIENTS = load_rules.DUP_FINDING_CLIENTS

# 加载拓扑规则(F05)已抽出为 scripts/core/clients/load_rules.py:
# 每条规则带来源标识/核实日期/适用范围;重复口径、客户端标签也从那里引用。


def _location_in_client(loc, client):
    return load_rules.location_in_client(loc, client)

# 各位置的加载优先级(数字小先加载,用于遮蔽/重复判定与展示排序)
_PRIORITY_RULES = (
    ("zcode-user", 1), ("shared", 2), ("claude-user", 3), ("codex-user", 4),
    ("ego-user", 5), ("workspace-", 6), ("workbuddy-user", 7), ("workbuddy-connector", 7),
    ("accio-account-", 8), ("cindy-", 9),
)


class InventoryError(ValueError):
    """inventory 自检失败(重复 ID、可变实例越界等),禁止落盘。"""


def data_dir():
    return default_data_dir()


def load_json(p, default):
    """兼容旧导入;新代码请用 scripts.core.io.load_json_checked。"""
    return load_json_checked(p, default)[0]


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


def _parse_inline_string_list(val):
    """YAML 行内字符串列表 [a, b] → ['a','b'];裸标识符是合法 YAML 但不是 Python 字面量。"""
    inner = val[1:-1].strip()
    if not inner:
        return []
    return [item.strip().strip('"').strip("'") for item in inner.split(",")]


def parse_frontmatter_detailed(text):
    """确定性 frontmatter 子集解析(F05):顶层核心标量、多行 description(|/> )、
    布尔值、metadata.requires.bins(行内/块列表)。

    返回 (fields, ok, warnings);合法但超出子集的结构给 {"code":"unsupported"}
    警告(绝不静默当作没有),截断/缺失给具体问题码。PyYAML 合法性仍由
    yaml_validate 单独标注,不影响核心字段。
    """
    warnings = []
    if not text.startswith("---"):
        return {}, False, [{"code": "missing"}]
    m = re.search(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}, False, [{"code": "truncated"}]
    out = {}
    cur = None      # 当前顶层标量键
    block = False   # 正在读多行 description
    meta = None     # None → "top" → "requires" → "bins"
    for raw in m.group(1).splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        indented = raw[:1] in (" ", "\t")
        if not indented:
            mm = re.match(r"^([A-Za-z_-]+):\s*(.*)$", stripped)
            if not mm:
                warnings.append({"code": "unsupported", "path": stripped[:40]})
                cur, block, meta = None, False, None
                continue
            key, val = mm.group(1), mm.group(2).strip()
            cur, block, meta = key, False, None
            if key == "metadata":
                if val == "":
                    out["metadata"] = {}
                    meta = "top"
                else:
                    warnings.append({"code": "unsupported", "path": "metadata"})
                cur = None
                continue
            if val in (">", ">-", "|", "|-"):
                out[key] = ""
                block = True
            elif val == "true":
                out[key] = True
            elif val == "false":
                out[key] = False
            elif val.startswith("[") and val.endswith("]"):
                try:
                    import ast
                    out[key] = ast.literal_eval(val)
                except (ValueError, SyntaxError):
                    out[key] = val.strip('"').strip("'")
            elif val == "" and key != "description":
                warnings.append({"code": "unsupported", "path": key})
                cur = None
            else:
                out[key] = val.strip('"').strip("'")
            continue
        # 缩进行
        if block and cur and isinstance(out.get(cur), str):
            out[cur] = (out[cur] + " " + stripped).strip()
            continue
        if meta == "top":
            mm = re.match(r"^([A-Za-z_-]+):\s*(.*)$", stripped)
            if mm and mm.group(1) == "requires" and mm.group(2).strip() == "":
                out["metadata"]["requires"] = {}
                meta = "requires"
            else:
                warnings.append({"code": "unsupported", "path": "metadata." + stripped[:30]})
            continue
        if meta == "requires":
            mm = re.match(r"^([A-Za-z_-]+):\s*(.*)$", stripped)
            if mm and mm.group(1) == "bins":
                val = mm.group(2).strip()
                if val.startswith("[") and val.endswith("]"):
                    out["metadata"]["requires"]["bins"] = _parse_inline_string_list(val)
                    meta = None
                elif val == "":
                    out["metadata"]["requires"]["bins"] = []
                    meta = "bins"
                else:
                    warnings.append({"code": "unsupported", "path": "metadata.requires.bins"})
                    meta = None
            else:
                warnings.append({"code": "unsupported", "path": "metadata." + stripped[:30]})
            continue
        if meta == "bins":
            if stripped.startswith("- "):
                out["metadata"]["requires"]["bins"].append(
                    stripped[2:].strip().strip('"').strip("'"))
            else:
                warnings.append({"code": "unsupported", "path": "metadata.requires.bins"})
                meta = None
            continue
        warnings.append({"code": "unsupported", "path": stripped[:40]})
    return out, True, warnings


def parse_frontmatter(text):
    """兼容入口:返回 (dict, ok);警告与逐条问题用 parse_frontmatter_detailed。"""
    fields, ok, _ = parse_frontmatter_detailed(text)
    return fields, ok


def yaml_validate(text):
    """YAML 合法性单独判定:True/False;PyYAML 缺席返回 None(不缺信息就不下结论)。"""
    if not text.startswith("---"):
        return None
    m = re.search(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return False
    try:
        import yaml
    except ImportError:
        return None
    try:
        yaml.safe_load(m.group(1))
        return True
    except Exception:
        return False


def first_sentence(desc, limit=60):
    if not desc:
        return ""
    d = re.sub(r"\s+", " ", str(desc).strip())
    for sep in ["。", "!", "?", ";"]:
        if sep in d:
            d = d.split(sep)[0] + sep
            break
    return d[:limit]


def _load_priority(loc):
    for prefix, prio in _PRIORITY_RULES:
        if loc.location_id == prefix or (prefix.endswith("-") and loc.location_id.startswith(prefix)):
            return prio
    return 9 if loc.kind in ("builtin", "plugin-cache") else 6


def _display_path(path, home):
    try:
        rp = os.path.abspath(path)
        hp = os.path.abspath(home)
        if rp == hp:
            return "~"
        if rp.startswith(hp + os.sep):
            return "~" + rp[len(hp):]
    except Exception:
        pass
    return str(path)


def _plugin_coordinates(root: Path, location_path=None):
    """插件缓存 skills 根 → (插件名, 版本, marketplace);非缓存布局返回 ("", "", "")。

    marketplace 只在嵌套布局(缓存根/<marketplace>/<plugin>/<version>/skills)下识别,
    平铺布局返回空串;同名插件跨 marketplace 互不参与"最高版本"比较。
    """
    try:
        plugin, version = root.parent.parent.name, root.parent.name
        marketplace = ""
        if location_path:
            cache_root = os.path.abspath(str(location_path))
            grand = root.parent.parent.parent
            great = grand.parent
            if os.path.abspath(str(great)) == cache_root:
                marketplace = grand.name
        return plugin, version, marketplace
    except (AttributeError, IndexError):
        return "", "", ""


def _extra_locations(data_dir: Path):
    """可选的 data/client-locations.json:用户登记的额外客户端目录(字段白名单)。

    「可选文件未创建」与「已有文件损坏」分开报告:后者进 config_issues。"""
    rows, issues = [], []
    value, load_issues = load_json_checked(data_dir / "client-locations.json", {})
    if any(i.get("code") == "corrupt-json" for i in load_issues):
        issues.append({"code": "bad-client-locations", "detail": "文件存在但已损坏(非合法 JSON)"})
        return rows, issues
    raw = value.get("locations") if isinstance(value, dict) else None
    if raw is None and not isinstance(value, dict):
        issues.append({"code": "bad-client-locations", "detail": "顶层必须是对象"})
        return rows, issues
    for i, item in enumerate(raw or []):
        if not isinstance(item, dict):
            issues.append({"code": "bad-client-locations", "detail": f"第 {i + 1} 项不是对象"})
            continue
        loc_id = str(item.get("location_id") or "").strip()
        path = os.path.expanduser(str(item.get("path") or "").strip())
        kind = str(item.get("kind") or "").strip()
        client = str(item.get("client") or loc_id or "custom").strip()
        mutable = item.get("mutable")
        if not loc_id or not re.fullmatch(r"[A-Za-z0-9._-]+", loc_id):
            issues.append({"code": "bad-client-locations", "detail": f"第 {i + 1} 项 location_id 非法"})
            continue
        if kind not in ("user", "workspace", "builtin", "plugin-cache"):
            issues.append({"code": "bad-client-locations", "detail": f"{loc_id}: kind 必须是 user|workspace|builtin|plugin-cache"})
            continue
        if not isinstance(mutable, bool):
            issues.append({"code": "bad-client-locations", "detail": f"{loc_id}: mutable 必须是布尔"})
            continue
        if not is_absolute_path(path):
            issues.append({"code": "bad-client-locations", "detail": f"{loc_id}: path 必须是绝对路径"})
            continue
        if Path(path).is_dir():
            rows.append(Location(loc_id, client, path, kind, mutable, ("client-locations.json",)))
    return rows, issues


def _scan_entry(location, root: Path, entry: Path, home):
    """扫描 skills 根下的单个条目(目录或符号链接),返回 (instance dict, findings list)。"""
    dir_name = entry.name
    is_link = entry.is_symlink()
    real = os.path.realpath(entry)
    findings = []
    base = {
        "instance_id": instance_id(location.location_id, dir_name, real),
        "location_id": location.location_id,
        "client": location.client,
        "kind": location.kind,
        "directory_name": dir_name,
        "path": str(entry),
        "display_path": _display_path(entry, home),
        "real_path": real,
        "display_real_path": _display_path(real, home),
        "is_symlink": is_link,
        "mutable": bool(location.mutable),
        "evidence": list(location.evidence),
        "load_priority": _load_priority(location),
        "tree_hash": "",
        "is_skill": False,
        "issue_codes": [],
        "content_status": "complete",
    }
    plugin, version, marketplace = "", "", ""
    if location.kind == "plugin-cache":
        plugin, version, marketplace = _plugin_coordinates(root, location.path)
        base["plugin_name"], base["plugin_version"] = plugin, version
        base["plugin_marketplace"] = marketplace

    if is_link and not os.path.exists(entry):
        findings.append(_finding("dangling-link", "info", base,
                                 "悬空符号链接(目标不存在),不是可用 skill"))
        return base, findings
    if is_link and real == os.path.realpath(root):
        findings.append(_finding("link-loop", "info", base, "循环符号链接"))
        return base, findings
    sk = os.path.join(real, "SKILL.md")
    if not os.path.isfile(sk):
        findings.append(_finding("no-skill-md", "info", base, "无 SKILL.md(非 skill 条目)"))
        return base, findings

    base["is_skill"] = True
    base["content_status"] = "complete"
    try:
        base["tree_hash"] = tree_hash(real)
    except FingerprintError as e:
        # F05:部分读不到的树不得当作完整树;不给完整指纹,变更入口随之停用
        base["tree_hash"] = ""
        base["content_status"] = "unreadable"
        detail = ";".join("{}({})".format(i.get("path"), i.get("code"))
                          for i in (e.issues or [])[:3])
        findings.append(_finding("content-unreadable", "yellow", base,
                                 "内容不完整或不可读,本次不提供指纹: " + detail))
    except OSError as e:
        base["tree_hash"] = ""
        base["content_status"] = "unreadable"
        findings.append(_finding("content-unreadable", "yellow", base,
                                 "内容不可读({})".format(type(e).__name__)))
    try:
        with open(sk, encoding="utf-8", errors="ignore") as f:
            text = f.read(8000)
    except OSError:
        text = ""
    fm, ok, fm_warnings = parse_frontmatter_detailed(text)
    for w in fm_warnings:
        if w.get("code") == "unsupported":
            findings.append(_finding(
                "frontmatter-unsupported", "info", base,
                "frontmatter 含确定性解析器不支持的结构({}),相关字段未提取: {}".format(
                    w.get("path"), w.get("path"))))
        elif w.get("code") == "truncated":
            findings.append(_finding("frontmatter-invalid", "red", base,
                                     "frontmatter 未闭合(缺少结束 ---)"))
    yaml_state = yaml_validate(text)
    base["yaml_ok"] = None if yaml_state is None else bool(yaml_state)
    fm_name = str(fm.get("name") or "").strip()
    desc = str(fm.get("description") or "")
    base["logical_name"] = fm_name or dir_name
    base["description"] = desc
    base["version"] = str(fm.get("version") or "")
    base["function"] = first_sentence(desc)
    base["trigger"] = "slash" if (fm.get("user_invocable") or fm.get("invocable_trigger")) else "auto"
    base["context_bytes"] = len(base["logical_name"].encode()) + len(desc.encode())
    bins = []
    collect_bins(fm, bins)
    base["requires_bins"] = sorted(set(bins))

    if not ok or not fm_name:
        findings.append(_finding("frontmatter-invalid", "red", base,
                                 "frontmatter 缺 name 或解析失败"))
    if not desc:
        findings.append(_finding("frontmatter-missing-description", "red", base,
                                 "frontmatter 缺 description"))
    if yaml_state is False:
        findings.append(_finding("yaml-validation", "yellow", base,
                                 "PyYAML 无法解析 frontmatter(核心字段已按简版解析器提取,客户端可能容忍)"))
    if "已归档" in text[:2000]:
        findings.append(_finding("archived-shell", "red", base, "仍是瘦身触发壳(标记已归档)"))
    missing = sorted({b for b in bins if b and not shutil.which(b)})
    if missing:
        findings.append(_finding("missing-bins", "yellow", base,
                                 "依赖命令缺失: " + ", ".join(missing) + "(skill 可能跑不全)"))
    return base, findings


def _finding(code, severity, inst, message):
    return {"code": code, "severity": severity, "instance_id": inst["instance_id"],
            "skill": inst.get("logical_name") or inst["directory_name"],
            "location_id": inst["location_id"], "message": message, "ignored": False}


def _apply_ignore(findings, data_dir: Path):
    rules = load_json_checked(data_dir / "ignore.json", {})[0]
    if not isinstance(rules, dict):
        return
    for f in findings:
        for rule in rules.get(f["skill"], []) or []:
            if isinstance(rule, str) and (rule in f["message"] or rule == f["code"]):
                f["ignored"] = True


def _version_key(v):
    """宽松版本比较键:'0.4.10' > '0.4.9'。"""
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.-]", str(v or "0")))


def _effective_loaded(instances):
    """插件缓存内同一插件多版本并存时,只有最高版本真正参与加载(按 ZCode/Codex 实测行为);
    旧版本目录只是缓存残留。marketplace 参与坐标:同名插件跨 marketplace 互不比较。
    返回 (参与加载的实例, 旧版本残留实例)。"""
    best = {}
    for i in instances:
        if i.get("plugin_name"):
            key = (i["location_id"], i.get("plugin_marketplace") or "", i["plugin_name"])
            v = _version_key(i.get("plugin_version"))
            if key not in best or v > best[key]:
                best[key] = v
    loaded, stale = [], []
    for i in instances:
        if i.get("plugin_name"):
            key = (i["location_id"], i.get("plugin_marketplace") or "", i["plugin_name"])
            if _version_key(i.get("plugin_version")) < best[key]:
                stale.append(i)
                continue
        loaded.append(i)
    return loaded, stale


def _client_load_stats(instances, locations):
    """每个客户端真实加载的技能条目数与同名重复(启动上下文口径)。"""
    loaded, _ = _effective_loaded(instances)
    stats = {}
    for client in CLIENT_LABELS:
        loc_ids = {l["location_id"] for l in locations if _location_in_client(l, client)}
        insts = [i for i in loaded if i["is_skill"] and i["location_id"] in loc_ids]
        by_name = {}
        for i in insts:
            by_name.setdefault(i["logical_name"], []).append(i)
        dups = sorted(n for n, v in by_name.items() if len(v) > 1)
        stats[client] = {
            "entries": len(insts), "skills": len(by_name), "duplicates": dups,
            "dup_entries": len(insts) - len(by_name),
        }
    return stats


def _nested_skill_trees(inst, max_depth=4, limit=5):
    """技能目录内部(深度≥2)还藏着多少 SKILL.md——递归扫描的客户端面板会把它们
    当独立技能列出来(2026-09-02 实测:ZCode 已安装技能页会顺着符号链接扫进仓库 data/)。
    返回相对路径列表(最多 limit 条,另给总数)。"""
    hits = []
    root = inst["real_path"]
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
        depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        if depth >= max_depth:
            dirnames[:] = []
        if depth >= 1 and "SKILL.md" in filenames:
            hits.append(os.path.relpath(dirpath, root))
    return hits[:limit], len(hits)


def _structural_findings(instances, locations, data_dir):
    """按客户端加载拓扑查重复加载;链接漂移;插件旧版本残留;应用内置技能扩散。"""
    findings = []
    loaded, stale = _effective_loaded(instances)
    by_name = {}
    for inst in loaded:
        if inst["is_skill"]:
            by_name.setdefault(inst["logical_name"], []).append(inst)

    for client in DUP_FINDING_CLIENTS:
        loc_ids = {l["location_id"] for l in locations if _location_in_client(l, client)}
        for name in sorted({n for n, v in by_name.items()
                            if len([i for i in v if i["location_id"] in loc_ids]) > 1}):
            insts = [i for i in by_name[name] if i["location_id"] in loc_ids]
            locs = "、".join(i["display_path"].rsplit("/", 1)[0] for i in insts)
            findings.append({
                "code": "duplicate-load", "severity": "yellow", "instance_id": insts[0]["instance_id"],
                "skill": name, "location_id": insts[0]["location_id"],
                "message": f"{CLIENT_LABELS[client]} 同名 {len(insts)} 份:{locs}"
                           f"(全部进入加载列表,重复占用启动上下文)",
                "ignored": False, "related_ids": [i["instance_id"] for i in insts[1:]],
            })

    # 模型自报客户端(无适配器):其声明根内部的同名重复仍要可见,
    # 但口径是"客户端自报",等待本地确认;绝不冒充精确加载事实。
    known_clients = set(CLIENT_LABELS)
    model_clients = sorted({l["client"] for l in locations
                            if "model-declaration" in (l.get("evidence") or [])
                            and l["client"] not in known_clients})
    for client in model_clients:
        loc_ids = {l["location_id"] for l in locations if l["client"] == client}
        for name in sorted({n for n, v in by_name.items()
                            if len([i for i in v if i["location_id"] in loc_ids]) > 1}):
            insts = [i for i in by_name[name] if i["location_id"] in loc_ids]
            locs = "、".join(i["display_path"].rsplit("/", 1)[0] for i in insts)
            findings.append({
                "code": "duplicate-load", "severity": "yellow",
                "instance_id": insts[0]["instance_id"],
                "skill": name, "location_id": insts[0]["location_id"],
                "message": f"自报客户端 {client} 同名 {len(insts)} 份:{locs}"
                           f"(位置来自模型声明,等待本地确认)",
                "ignored": False, "related_ids": [i["instance_id"] for i in insts[1:]],
            })

    # Haha:同时读 Claude 目录与共享库,镜像同名会双份——聚合为一条,不逐个刷屏
    haha_ids = {l["location_id"] for l in locations if _location_in_client(l, "haha")}
    haha_dups = sorted(n for n, v in by_name.items()
                       if len([i for i in v if i["location_id"] in haha_ids]) > 1)
    if haha_dups:
        findings.append({
            "code": "wrapper-double-load", "severity": "yellow",
            "instance_id": "-", "skill": "(haha)", "location_id": "shared",
            "message": f"Haha 同时读取 Claude 目录与共享库,{len(haha_dups)} 个同名技能会双份加载"
                       f"(Haha 机制固有;精简共享库与 Claude 镜像的同名条目可同步减少)",
            "ignored": False,
        })

    for i in stale:
        findings.append(_finding(
            "stale-plugin-version", "info", i,
            f"插件 {i.get('plugin_name')} 旧版本 {i.get('plugin_version')} 残留缓存"
            f"(客户端只加载最高版本,不占上下文;清理请在所属客户端操作)"))

    # 技能目录内部嵌套技能树:递归扫描的客户端面板(如 ZCode 已安装技能页)会全部列出,
    # 造成"同名两份"的假重复——2026-09-02 的 data/staging 泄漏即此类。
    # 单棵嵌套多为有意的子技能设计(如 workctl/workctl-operator),记 info;
    # 多棵嵌套(候选/缓存泄漏的典型形态)记 yellow。同一真实路径只报一次。
    seen_real = set()
    for inst in instances:
        if not inst["is_skill"] or inst["real_path"] in seen_real:
            continue
        seen_real.add(inst["real_path"])
        sample, total = _nested_skill_trees(inst)
        if not total:
            continue
        sev = "yellow" if total > 1 else "info"
        extra = "" if total > 1 else "(若为有意的子技能设计,可忽略或加 ignore)"
        findings.append(_finding(
            "nested-skill-tree", sev, inst,
            f"技能目录内嵌套 {total} 棵技能树(如 {('、'.join(sample))[:120]})"
            f"——递归扫描的客户端面板会把它们当独立技能重复列出{extra}"))

    # 应用内置技能(builtin-app)出现在共享库 → 会被所有读共享库的客户端加载
    ks = load_json_checked(Path(data_dir) / "known-sources.json", {})[0]
    if isinstance(ks, dict):
        builtin = {k: v for k, v in ks.items()
                   if isinstance(v, dict) and v.get("type") == "builtin-app"}
        for inst in instances:
            if (inst["is_skill"] and inst["location_id"] == "shared"
                    and inst["directory_name"] in builtin):
                meta = builtin[inst["directory_name"]]
                findings.append(_finding(
                    "builtin-app-spread", "yellow", inst,
                    f"应用内置技能 {inst['directory_name']}({meta.get('note') or 'builtin-app'})"
                    f"位于共享库,会被 ZCode/Codex 等所有读取共享库的客户端加载;"
                    f"建议仅保留在所属客户端自己的目录"))

    master_by_name = {}
    for inst in instances:
        if inst["is_skill"] and inst["location_id"] == "shared" and not inst["is_symlink"]:
            master_by_name.setdefault(inst["logical_name"], inst)
    for inst in instances:
        master = master_by_name.get(inst.get("logical_name"))
        if not master or inst is master or not inst["is_symlink"] or not inst["is_skill"]:
            continue
        if inst["tree_hash"] != master["tree_hash"]:
            findings.append(_finding(
                "link-drift", "yellow", inst,
                f"链接漂移: {inst['display_path']} 的内容与主库 {master['display_path']} 不一致"))
    return findings


def _build_logical_skills(instances):
    """v2 逻辑身份:同完整指纹(内容身份)合并;来源核实身份在 provenance 阶段叠加。"""
    groups = {}
    for inst in instances:
        if not inst["is_skill"]:
            continue
        groups.setdefault(inst["tree_hash"], []).append(inst)
    rows = []
    for th, insts in groups.items():
        lead = sorted(insts, key=lambda x: (x["load_priority"], x["directory_name"]))[0]
        rows.append({
            "logical_id": instance_id("logical", lead["logical_name"], th),
            "name": lead["logical_name"],
            "tree_hash": th,
            "instance_ids": [i["instance_id"] for i in sorted(insts, key=lambda x: x["instance_id"])],
            "clients": sorted({i["client"] for i in insts}),
            "function": lead.get("function", ""),
            "trigger": lead.get("trigger", "auto"),
            "version": lead.get("version", ""),
            "context_bytes": sum(i.get("context_bytes", 0) for i in insts),
        })
    rows.sort(key=lambda x: x["name"].lower())
    return rows


def _need_vet(instances, data_dir):
    """需要安检的第三方实例 ID:来源核实为 third-party 且 review_required。"""
    from scripts.core.provenance import classify_provenance, load_user_config
    known = load_user_config(data_dir)
    receipts = {}
    for inst in instances:
        if inst.get("kind") in ("builtin", "plugin-cache"):
            receipts[str(inst.get("instance_id"))] = {"type": inst["kind"]}
    out = []
    for inst in instances:
        if not inst.get("is_skill") or not inst.get("mutable"):
            continue
        prov = classify_provenance(inst, receipts, known)
        if prov.get("review_required"):
            out.append(str(inst["instance_id"]))
    return sorted(out)


def _model_locations(model_roots, home):
    """模型临时位置声明 → Location 行(mutable 恒为 False)。

    只在本函数把声明路径变成扫描目标;声明解析本身(location_input)不打开任何文件。
    目录不存在的声明进 findings(model-root-missing),绝不当作空位置扫描成功。
    """
    rows, misses = [], []
    for root in model_roots or []:
        real = os.path.realpath(root["path"])
        if not os.path.isdir(real) or real == os.path.realpath(str(home)):
            misses.append({"client": root["client"],
                           "display": _display_path(root["path"], home)})
            continue
        loc_id = "model-{}-{}".format(root["client"],
                                      instance_id("model", root["client"], real)[:8])
        rows.append(Location(
            loc_id, root["client"], real, "user", False,
            ("model-declaration", "scope:" + root["scope"],
             "load-state:" + root["load_state"])))
    return rows, misses


def build_inventory(home, data_dir, workspace=None, model_roots=None) -> dict:
    """聚合全部客户端位置 → inventory v2 dict(只读;不写任何文件)。

    workspace=None 表示全局上下文(不选中任何项目);传入项目路径时,该工作区
    位置参与加载上下文评估。inventory 附 observation(观察完整性)与每个实例的
    content_status;观察不完整时相关对象不得用于变更。
    """
    home, data_dir = Path(home), Path(data_dir)
    from scripts.core.observations import evaluate_load, load_receipt_evidence
    locations = discover_locations(home, data_dir)
    extra, config_issues = _extra_locations(data_dir)
    locations = sorted(_dedupe(locations + extra), key=lambda x: x.location_id)

    # Task 3:模型临时位置声明 —— 只读、不可变;与已有位置按真实路径去重,
    # 本机事实优先(适配器/client-locations 命中的路径不再接受声明副本)。
    model_rows, model_misses = _model_locations(model_roots, home)
    existing_real = {os.path.realpath(l.path) for l in locations}
    model_seen = set()
    model_deduped = []
    for row in model_rows:
        real = os.path.realpath(row.path)
        if real in existing_real or real in model_seen:
            continue
        model_seen.add(real)
        model_deduped.append(row)
    locations = sorted(_dedupe(locations + model_deduped), key=lambda x: x.location_id)

    instances, findings = [], []
    obs_issues = []
    for loc in locations:
        for root in discover_skill_roots(loc):
            try:
                entries = sorted(root.iterdir())
            except OSError as e:
                # F05:位置读不到必须可见,不能当作"空位置"扫描成功
                obs_issues.append({"code": "location-unreadable",
                                   "location_id": loc.location_id,
                                   "path": _display_path(root, home),
                                   "reason": type(e).__name__})
                continue
            for entry in entries:
                inst, f = _scan_entry(loc, root, entry, home)
                instances.append(inst)
                findings.extend(f)
                if inst.get("content_status") == "unreadable":
                    obs_issues.append({"code": "instance-unreadable",
                                       "instance_id": inst["instance_id"],
                                       "path": inst.get("display_path")})
    findings.extend(_structural_findings(instances, [l.to_dict() for l in locations], data_dir))
    for miss in model_misses:
        findings.append({
            "code": "model-root-missing", "severity": "yellow", "instance_id": "-",
            "skill": miss["client"], "location_id": "-",
            "message": "自报客户端 {} 声明的根目录本机不存在: {}(等待模型或用户复核)".format(
                miss["client"], miss["display"]),
            "ignored": False})
    _apply_ignore(findings, data_dir)
    logical = _build_logical_skills(instances)
    client_load = _client_load_stats(instances, [l.to_dict() for l in locations])

    # 已有配置文件损坏(known-sources)也计入观察问题;可选文件未创建不算
    ks_value, ks_issues = load_json_checked(Path(data_dir) / "known-sources.json", {})
    for issue in ks_issues:
        if issue.get("code") != "missing-file":
            obs_issues.append({"code": "config-corrupt", "source": "known-sources.json",
                               "reason": issue.get("reason") or issue.get("code")})

    loc_dicts = [l.to_dict() for l in locations]
    load_contexts = {}
    for client in load_rules.CLIENT_LABELS:
        load_contexts[client] = evaluate_load(instances, loc_dicts, client,
                                              workspace=workspace)
    observation = {
        "complete": not obs_issues,
        "issues": obs_issues,
        "observed_scope": {
            "workspace": os.path.abspath(str(workspace)) if workspace else None,
            "locations": len(locations),
            "instances": len(instances),
            "model_roots": len(model_deduped),
            "model_inputs_complete": bool(model_roots) and all(
                r.get("complete") for r in (model_roots or [])),
        },
        "rule_version": load_rules.RULE_VERSION,
        "load_contexts": load_contexts,
    }

    inv = {
        "schema_version": SCHEMA_VERSION,
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "home_display": "~",
        "locations": loc_dicts,
        "instances": instances,
        "logical_skills": logical,
        "client_load": client_load,
        "findings": findings,
        "config_issues": config_issues,
        "observation": observation,
        "total": len(logical),
        "by_source": {},
        "operational_ok": True,
        "health_status": "ok",
    }
    _validate_inventory(inv)
    inv = redact_secrets(inv)
    live = [f for f in findings if not f["ignored"]]
    if any(f["severity"] == "red" for f in live):
        inv["health_status"] = "red"
    elif any(f["severity"] == "yellow" for f in live):
        inv["health_status"] = "yellow"
    return inv


def _dedupe(locations):
    seen, out = set(), []
    for loc in locations:
        if loc.location_id in seen:
            continue
        seen.add(loc.location_id)
        out.append(loc)
    return out


def _validate_inventory(inv):
    """落盘前自检:instance_id 唯一;可变实例的 path 必须落在已登记的可变位置内。"""
    ids = [i["instance_id"] for i in inv["instances"]]
    if len(ids) != len(set(ids)):
        raise InventoryError("instance_id 重复,拒绝写出 inventory")
    by_loc = {loc["location_id"]: loc for loc in inv["locations"]}
    for inst in inv["instances"]:
        if not inst["mutable"]:
            continue
        loc = by_loc.get(inst["location_id"])
        if not loc or not loc["mutable"]:
            raise InventoryError(f"实例 {inst['instance_id']} 声称可变但位置不可变")
        root = os.path.realpath(loc["path"])
        # 符号链接实例的 realpath 会指向位置之外,归属校验只看条目自身所在父目录
        parent = os.path.realpath(os.path.dirname(inst["path"]))
        if parent != root and not parent.startswith(root + os.sep):
            raise InventoryError(f"实例 {inst['instance_id']} 越出位置根目录,拒绝写出 inventory")


def _summary_rows(inv):
    live = [f for f in inv["findings"] if not f["ignored"]]
    red = sorted({f["skill"] for f in live if f["severity"] == "red"})
    yellow = sorted({f["skill"] for f in live if f["severity"] == "yellow"})
    dup = sorted({f["skill"] for f in live if f["code"] == "duplicate-load"})
    return red, yellow, dup, len([f for f in inv["findings"] if f["ignored"]])


def _collect_model_inputs(argv):
    """解析 --root CLIENT=PATH 与 --locations-json FILE|-;返回 normalized model_roots。

    只读文本;拒绝发生在任何扫描/落盘之前。声明经 stdin("-")时按 64KiB 上限读取。
    """
    pairs, decl_source = [], None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root":
            i += 1
            if i >= len(argv):
                raise LocationInputError("--root 缺少 CLIENT=PATH 值")
            pairs.append(argv[i])
        elif arg.startswith("--root="):
            pairs.append(arg.split("=", 1)[1])
        elif arg == "--locations-json":
            i += 1
            if i >= len(argv):
                raise LocationInputError("--locations-json 缺少 FILE 或 -")
            decl_source = argv[i]
        elif arg.startswith("--locations-json="):
            decl_source = arg.split("=", 1)[1]
        i += 1

    roots = parse_cli_roots(pairs) if pairs else []
    if decl_source is not None:
        if decl_source == "-":
            decl = parse_declaration(sys.stdin.buffer.read(MAX_DECL_BYTES + 1))
        else:
            try:
                raw = Path(decl_source).read_bytes()
            except OSError:
                raise LocationInputError("声明文件无法读取")
            decl = parse_declaration(raw)
        for root in decl["roots"]:
            roots.append({"client": decl["client"], "path": root["path"],
                          "scope": root["scope"], "load_state": root["load_state"],
                          "complete": decl["complete"]})
    if len(roots) > 32:
        raise LocationInputError("模型位置根总数超过 32 个上限")
    for root in roots:
        root.setdefault("complete", False)
    return roots


def main(argv=None):
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        print("用法: scan.py [--json] [--root CLIENT=PATH ...] "
              "[--locations-json FILE|-]")
        print("  --json: 机器可读输出,退出码 0=健康 1=有红色问题 2=运行失败/观察不完整")
        print("  --root/--locations-json: 模型提供的临时位置声明(只读,仅本次扫描,不持久化)")
        print("环境变量: SKILL_KEEPER_DATA 可覆盖数据目录(测试/多环境)")
        return 0
    try:
        model_roots = _collect_model_inputs(argv)
    except LocationInputError as e:
        print(json.dumps({"ok": False, "error": "位置声明被拒绝: {}".format(e)},
                         ensure_ascii=False))
        return 2
    home = Path(os.path.expanduser("~"))
    ddir = data_dir()
    try:
        inv = build_inventory(home, ddir, model_roots=model_roots or None)
    except InventoryError as e:
        print(json.dumps({"operational_ok": False, "error": str(e)}, ensure_ascii=False))
        return 2

    cur = ddir / "inventory.json"
    ddir.mkdir(parents=True, exist_ok=True)
    if cur.exists():
        shutil.copy2(cur, ddir / "inventory-last.json")
    atomic_write_json(cur, inv)

    red, yellow, dup, ignored_n = _summary_rows(inv)
    obs = inv.get("observation") or {}
    obs_complete = bool(obs.get("complete"))
    if "--json" in argv:
        # 退出码:0=健康,1=有红色问题,2=运行失败或观察不完整(数据不得当作可信)
        print(json.dumps({
            "schema_version": inv["schema_version"], "scanned_at": inv["scanned_at"],
            "total": inv["total"], "instances": len(inv["instances"]),
            "locations": len(inv["locations"]), "duplicated": dup,
            "client_load": inv.get("client_load", {}),
            "red": red, "yellow": yellow, "junk_count": len(inv["instances"]) - sum(1 for i in inv["instances"] if i["is_skill"]),
            "ignored_issues": ignored_n,
            "need_vet": _need_vet(inv["instances"], ddir),
            "observation_complete": obs_complete,
            "observation_issues": obs.get("issues", []),
            "operational_ok": inv["operational_ok"], "health_status": inv["health_status"],
        }, ensure_ascii=False, indent=1))
        return 2 if (not inv["operational_ok"] or not obs_complete) else (1 if red else 0)

    print(f"✅ 扫描完成:{inv['total']} 个逻辑 skill / {len(inv['instances'])} 个安装实例 → {cur}")
    print(f"位置:{len(inv['locations'])} 个;" + "、".join(sorted({loc['client'] for loc in inv['locations']})))
    cl = inv.get("client_load", {})
    load_line = "、".join(
        f"{CLIENT_LABELS[c]} {cl[c]['entries']}" + (f"(重复{cl[c]['dup_entries']})" if cl[c]["dup_entries"] else "")
        for c in ("zcode", "codex", "claude-code", "haha", "cindy", "accio", "workbuddy", "ego")
        if cl.get(c, {}).get("entries"))
    print(f"客户端加载条目:{load_line or '无'}")
    print(f"健康:{len(red)} 红 / {len(yellow)} 黄;重复加载 {len(dup)} 个;忽略 {ignored_n} 条")
    if not obs_complete:
        print(f"⚠️ 观察不完整({len(obs.get('issues', []))} 项),相关对象已停用变更入口")
    print(f"详细报告: python3 {os.path.join(BASE, 'scripts', 'report.py')}")
    return 2 if (not inv["operational_ok"] or not obs_complete) else (1 if red else 0)


if __name__ == "__main__":
    sys.exit(main())
