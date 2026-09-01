#!/usr/bin/env python3
"""skill-keeper v2 扫描器:客户端适配器发现 + 完整目录指纹 → inventory v2。

只读铁律:本脚本绝不修改任何 skill;数据目录可用 SKILL_KEEPER_DATA 覆盖(测试/多环境),
未设置时用项目 data/。schema 见 scripts/core/models.py,v2 结构:
  locations(位置)/ instances(物理实例)/ logical_skills(逻辑身份)
  / findings(结构化健康问题)/ config_issues(配置问题)
"""
import json, os, re, shutil, sys, time
from pathlib import Path

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from scripts.core.clients import discover_locations, discover_skill_roots  # noqa: E402
from scripts.core.fingerprint import instance_id, tree_hash  # noqa: E402
from scripts.core.io import atomic_write_json, load_json_checked, redact_secrets  # noqa: E402
from scripts.core.models import SCHEMA_VERSION, Location  # noqa: E402

HOME = os.path.expanduser("~")
LOCK_FILE = os.path.join(HOME, ".agents/.skill-lock.json")

# ZCode 会在用户/共享/插件缓存之间发现同名 skill 并全部列出 → 只在这组客户端里判重复加载
ZCODE_DISCOVERY_CLIENTS = ("zcode", "shared")

# 各位置的加载优先级(数字小先加载,用于遮蔽/重复判定与展示排序)
_PRIORITY_RULES = (
    ("zcode-user", 1), ("shared", 2), ("claude-user", 3), ("codex-user", 4),
    ("ego-user", 5), ("workspace-", 6), ("workbuddy-user", 7), ("workbuddy-connector", 7),
    ("accio-account-", 8), ("cindy-", 9),
)


class InventoryError(ValueError):
    """inventory 自检失败(重复 ID、可变实例越界等),禁止落盘。"""


def data_dir():
    return Path(os.environ.get("SKILL_KEEPER_DATA") or os.path.join(BASE, "data"))


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


def parse_frontmatter(text):
    """返回 (dict, ok)。只用项目自带确定性解析器提取核心字段(name/description/version、
    requires.bins),保证结果与 PyYAML 是否安装、能否解析完全无关;YAML 合法性由
    yaml_validate 单独标注。"""
    if not text.startswith("---"):
        return {}, False
    m = re.search(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}, False
    fm_text = m.group(1)
    out, cur_key = {}, None
    for line in fm_text.splitlines():
        if re.match(r"^\s", line) and cur_key:
            out[cur_key] = (out[cur_key] + " " + line.strip()).strip()
            continue
        mm = re.match(r'^([A-Za-z_-]+):\s*(.*)$', line)
        if not mm:
            continue
        cur_key, val = mm.group(1), mm.group(2).strip()
        if val in (">", ">-", "|", "|-"):
            out[cur_key] = ""
        elif val.startswith("[") and val.endswith("]"):
            try:
                import ast
                out[cur_key] = ast.literal_eval(val)
            except (ValueError, SyntaxError):
                out[cur_key] = val.strip('"').strip("'")
        else:
            out[cur_key] = val.strip('"').strip("'")
    return out, True


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


def _plugin_coordinates(root: Path):
    """插件缓存 skills 根 → (插件名, 版本);非缓存布局返回 ("", "")。"""
    try:
        return root.parent.parent.name, root.parent.name
    except (AttributeError, IndexError):
        return "", ""


def _extra_locations(data_dir: Path):
    """可选的 data/client-locations.json:用户登记的额外客户端目录(字段白名单)。"""
    rows, issues = [], []
    value, _ = load_json_checked(data_dir / "client-locations.json", {})
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
        if not path.startswith("/"):
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
    }
    plugin, version = ("", "")
    if location.kind == "plugin-cache":
        plugin, version = _plugin_coordinates(root)
        base["plugin_name"], base["plugin_version"] = plugin, version

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
    base["tree_hash"] = tree_hash(real)
    try:
        with open(sk, encoding="utf-8", errors="ignore") as f:
            text = f.read(8000)
    except OSError:
        text = ""
    fm, ok = parse_frontmatter(text)
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


def _structural_findings(instances):
    """重复加载(ZCode 发现集内同名多份)与链接漂移(符号链接内容 ≠ shared 正本)。"""
    findings = []
    by_name = {}
    for inst in instances:
        if inst["is_skill"]:
            by_name.setdefault(inst["logical_name"], []).append(inst)

    for name, insts in sorted(by_name.items()):
        zc = [i for i in insts if i["client"] in ZCODE_DISCOVERY_CLIENTS]
        if len(zc) > 1:
            locs = "、".join(i["display_path"].rsplit("/", 1)[0] for i in zc)
            findings.append({
                "code": "duplicate-load", "severity": "yellow", "instance_id": zc[0]["instance_id"],
                "skill": name, "location_id": zc[0]["location_id"],
                "message": f"ZCode 同名 {len(zc)} 份:{locs}(全部进列表,只加载第一份)",
                "ignored": False, "related_ids": [i["instance_id"] for i in zc[1:]],
            })
        master = next((i for i in insts if i["location_id"] == "shared" and not i["is_symlink"]), None)
        if not master:
            continue
        for i in insts:
            if i is master or not i["is_symlink"] or not i["is_skill"]:
                continue
            if i["tree_hash"] != master["tree_hash"]:
                findings.append(_finding(
                    "link-drift", "yellow", i,
                    f"链接漂移: {i['display_path']} 的内容与主库 {master['display_path']} 不一致"))
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


def build_inventory(home, data_dir) -> dict:
    """聚合全部客户端位置 → inventory v2 dict(只读;不写任何文件)。"""
    home, data_dir = Path(home), Path(data_dir)
    locations = discover_locations(home, data_dir)
    extra, config_issues = _extra_locations(data_dir)
    locations = sorted(_dedupe(locations + extra), key=lambda x: x.location_id)

    instances, findings = [], []
    for loc in locations:
        for root in discover_skill_roots(loc):
            try:
                entries = sorted(root.iterdir())
            except OSError:
                continue
            for entry in entries:
                inst, f = _scan_entry(loc, root, entry, home)
                instances.append(inst)
                findings.extend(f)
    findings.extend(_structural_findings(instances))
    _apply_ignore(findings, data_dir)
    logical = _build_logical_skills(instances)

    inv = {
        "schema_version": SCHEMA_VERSION,
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "home_display": "~",
        "locations": [loc.to_dict() for loc in locations],
        "instances": instances,
        "logical_skills": logical,
        "findings": findings,
        "config_issues": config_issues,
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


def main():
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        print("用法: scan.py [--json]  (--json: 机器可读输出,退出码 0=健康 1=有红色问题)")
        print("环境变量: SKILL_KEEPER_DATA 可覆盖数据目录(测试/多环境)")
        sys.exit(0)
    home = Path(os.path.expanduser("~"))
    ddir = data_dir()
    try:
        inv = build_inventory(home, ddir)
    except InventoryError as e:
        print(json.dumps({"operational_ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(2)

    cur = ddir / "inventory.json"
    ddir.mkdir(parents=True, exist_ok=True)
    if cur.exists():
        shutil.copy2(cur, ddir / "inventory-last.json")
    atomic_write_json(cur, inv)

    red, yellow, dup, ignored_n = _summary_rows(inv)
    if "--json" in argv:
        # 退出码:0=健康,1=有红色问题,2=运行失败(operational_ok=false)
        print(json.dumps({
            "schema_version": inv["schema_version"], "scanned_at": inv["scanned_at"],
            "total": inv["total"], "instances": len(inv["instances"]),
            "locations": len(inv["locations"]), "duplicated": dup,
            "red": red, "yellow": yellow, "junk_count": len(inv["instances"]) - sum(1 for i in inv["instances"] if i["is_skill"]),
            "ignored_issues": ignored_n, "need_vet": [],
            "operational_ok": inv["operational_ok"], "health_status": inv["health_status"],
        }, ensure_ascii=False, indent=1))
        sys.exit(1 if red else 0)

    print(f"✅ 扫描完成:{inv['total']} 个逻辑 skill / {len(inv['instances'])} 个安装实例 → {cur}")
    print(f"位置:{len(inv['locations'])} 个;" + "、".join(sorted({loc['client'] for loc in inv['locations']})))
    print(f"健康:{len(red)} 红 / {len(yellow)} 黄;重复加载 {len(dup)} 个;忽略 {ignored_n} 条")
    print(f"详细报告: python3 {os.path.join(BASE, 'scripts', 'report.py')}")
    sys.exit(1 if red else 0)


if __name__ == "__main__":
    main()
