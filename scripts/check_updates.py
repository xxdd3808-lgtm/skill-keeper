#!/usr/bin/env python3
"""skill-keeper v2 更新检查(只读):本地完整树 vs 固定 commit 的完整候选树。

与 v1 的区别:
- 不再无条件打开 .skill-lock.json;来源只认 classify_provenance 的证据;
- 比较的是完整目录树哈希(不是单个 SKILL.md);
- 输出客观状态:有候选更新 / 需审查 / 疑似本地定制 / 无法核实——
  不给任何"改动少就可以直接覆盖"式的背书,更新必须走 plan/apply + 安检。
CLI:
  python3 scripts/check_updates.py [--inventory inventory.json] [--output updates.json] [--json]
  --inventory/--output 可完全绕开项目运行时数据(测试、其他 Agent)。
"""
import argparse, json, os, re, secrets, shutil, sys, tempfile, time
from pathlib import Path

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from scripts.core.fingerprint import tree_hash, tree_manifest          # noqa: E402
from scripts.core.github import cached_repo_snapshot, fetch_skill_tree, gh_cli_runner  # noqa: E402
from scripts.core.io import atomic_write_json, load_json_checked       # noqa: E402
from scripts.core.provenance import classify_provenance, load_user_config  # noqa: E402
from scripts.core.staging import (StagingBoundaryError, cleanup_staging,   # noqa: E402
                                  record_ownership, validate_staging_root)
from scripts.scan import parse_frontmatter                              # noqa: E402


def staging_root_for(output_path=None):
    """候选暂存根。绝不能落在任何客户端会递归扫描的技能目录下——2026-09-02 实测,
    ZCode 的「已安装技能」面板会顺着 ~/.agents/skills/skill-keeper 符号链接递归扫描,
    把仓库 data/staging 里的候选树当技能重复列出(aihot/brainstorming 各出现两次)。
    默认放系统缓存目录;测试用 SKILL_KEEPER_STAGING 指向临时目录。"""
    env = os.environ.get("SKILL_KEEPER_STAGING")
    if env:
        return Path(env)
    home = Path(os.path.expanduser("~"))
    if sys.platform == "darwin":
        return home / "Library/Caches/skill-keeper/staging"
    return home / ".cache/skill-keeper/staging"


def stage_candidate(repo, source_dir, commit_sha, staging_root, gh_runner):
    """把固定 commit 的完整候选树放入 staging(按内容哈希命名,天然不可变)。

    已有同名 cand 目录先按完整哈希复核:一致才复用;不一致(损坏)保留证据,
    以独立临时对象重新物化,绝不覆盖、绝不信任截断哈希目录名。
    返回 {ok, candidate_hash, staging_path, files, commit_sha};失败 {ok: False}。
    """
    staging_root = Path(staging_root)
    staging_root.mkdir(parents=True, exist_ok=True)
    tmp = staging_root / ("tmp-" + secrets.token_hex(4))
    result = fetch_skill_tree(repo, source_dir, commit_sha, tmp, gh_runner)
    if not result.get("ok"):
        shutil.rmtree(tmp, ignore_errors=True)
        return result
    from scripts.core.staging import record_ownership
    final = staging_root / ("cand-" + result["tree_hash"][:12])
    if final.is_dir():
        shutil.rmtree(tmp, ignore_errors=True)  # 同哈希候选已存在
        try:
            intact = tree_hash(final) == result["tree_hash"]
        except (NotADirectoryError, OSError):
            intact = False
        meta = {"candidate_hash": result["tree_hash"], "repo": repo,
                "commit_sha": commit_sha}
        if not intact:
            # 损坏目录保留现场,旁路重物化为独立对象
            side = staging_root / ("cand-" + result["tree_hash"][:12] + "-"
                                   + secrets.token_hex(2))
            rescue = staging_root / ("tmp-" + secrets.token_hex(4))
            fixed = fetch_skill_tree(repo, source_dir, commit_sha, rescue, gh_runner)
            if not fixed.get("ok"):
                shutil.rmtree(rescue, ignore_errors=True)
                return fixed
            os.replace(rescue, side)
            record_ownership(staging_root, side.name, dict(meta, note="re-materialized"))
            return {"ok": True, "candidate_hash": fixed["tree_hash"],
                    "staging_path": str(side), "files": fixed["files"],
                    "commit_sha": commit_sha, "rematerialized": True}
        record_ownership(staging_root, final.name, meta)
        return {"ok": True, "candidate_hash": result["tree_hash"],
                "staging_path": str(final), "files": result["files"],
                "commit_sha": commit_sha, "verified_existing": True}
    os.replace(tmp, final)
    # 所有权记录:清理只认本工具登记过的候选(F07 边界)
    record_ownership(staging_root, final.name,
                     {"candidate_hash": result["tree_hash"], "repo": repo,
                      "commit_sha": commit_sha})
    return {"ok": True, "candidate_hash": result["tree_hash"], "staging_path": str(final),
            "files": result["files"], "commit_sha": commit_sha}

STATUS_LABEL = {
    "candidate-update": "有候选更新(先审查,再 plan/apply)",
    "needs-review": "与上游内容有差异,需审查",
    "local-custom": "疑似本地定制,建议保留本地",
    "unverifiable": "无法核实(缺来源/缺工具/网络失败)",
}


def fm_version(text):
    fm, ok = parse_frontmatter(text or "")
    return str(fm.get("version")) if ok and fm.get("version") else ""


def ver_tuple(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.]", str(v).lstrip("vV")))


def build_receipts(inventory):
    """客户端自带身份证据:来自 inventory 位置/实例的 builtin、plugin-cache 标记。"""
    receipts = {}
    for inst in inventory.get("instances", []):
        if inst.get("kind") in ("builtin", "plugin-cache"):
            receipts[str(inst.get("instance_id"))] = {"type": inst["kind"],
                                                      "repo": inst.get("plugin_name"),
                                                      "client": inst.get("client")}
    return receipts


def diff_summary(local_manifest, candidate_manifest):
    a = {e["path"]: e for e in local_manifest}
    b = {e["path"]: e for e in candidate_manifest}
    modified = sorted(p for p in set(a) & set(b)
                      if any(a[p].get(k) != b[p].get(k) for k in ("sha256", "type", "mode", "target")))
    return {"added": sorted(set(b) - set(a)),
            "removed": sorted(set(a) - set(b)),
            "modified": modified}


def _pick_instance(inventory, logical):
    """一个逻辑 skill 的代表实例:优先可变、非符号链接、有完整指纹的实体。"""
    by_id = {i["instance_id"]: i for i in inventory.get("instances", [])}
    cands = [by_id[i] for i in logical.get("instance_ids", []) if i in by_id]
    cands = [i for i in cands if i.get("tree_hash") and i.get("real_path")] or cands
    cands.sort(key=lambda i: (not i.get("mutable"), i.get("is_symlink", False), i["instance_id"]))
    return cands[0] if cands else None


def check(inventory, data_dir, output_path, gh_runner=None, staging_root=None):
    """产出 v2 updates 结构(不落盘);网络只在本地内容可核实时才会发起。"""
    gh_runner = gh_runner or gh_cli_runner()
    known_sources = load_user_config(data_dir)
    receipts = build_receipts(inventory)
    reputation_path = Path(output_path).parent / "reputation.json"
    staging_root = Path(staging_root) if staging_root else staging_root_for(output_path)
    # F07 边界:暂存根不得落在技能树/数据目录/安装位置内外链里(环境变量覆盖也不行)
    protected = [str(l.get("path")) for l in inventory.get("locations", []) if l.get("path")]
    protected.append(str(Path(data_dir).resolve()))
    staging_root = validate_staging_root(staging_root, protected)

    differs, up_to_date, skipped = [], [], []
    for logical in inventory.get("logical_skills", []):
        name = logical.get("name") or "?"
        inst = _pick_instance(inventory, logical)
        if not inst:
            skipped.append({"name": name, "reason": "没有可核实的本地实例"})
            continue
        source = classify_provenance(inst, receipts, known_sources)
        if source["class"] == "protected":
            reason = {"self-built": "自建,不从上游更新",
                      "builtin-app": "应用内置 skill,更新或卸载走所属客户端"}.get(
                source["type"], "客户端自带/插件管理(" + source["type"] + "),不可更新")
            skipped.append({"name": name, "reason": reason})
            continue
        repo = source.get("repo") or (source.get("candidate_source") or {}).get("repo")
        path = source.get("path") or (source.get("candidate_source") or {}).get("path")
        if not repo:
            skipped.append({"name": name, "reason": "来源不明,没有可对比的上游(补 known-sources.json 或让我搜索候选)"})
            continue
        # 先核本地:本地不存在/算不出指纹,就不发任何网络请求
        try:
            local_hash = tree_hash(inst["real_path"])
            local_manifest = tree_manifest(inst["real_path"])
        except (NotADirectoryError, OSError):
            skipped.append({"name": name, "reason": "本地内容缺失,无法核实"})
            continue
        if not path:
            skipped.append({"name": name, "reason": "来源缺路径,无法定位上游目录"})
            continue
        source_dir = path[:-len("/SKILL.md")] if path.endswith("/SKILL.md") else path
        snap = cached_repo_snapshot(repo, reputation_path, gh_runner)
        commit_sha = snap.get("commit_sha")
        if not snap.get("ok") and not commit_sha:
            skipped.append({"name": name,
                            "reason": "上游仓库数据{}({})".format(
                                "已过期,本次无法刷新" if snap.get("stale") else "拉取失败",
                                snap.get("error") or repo)})
            continue
        if not commit_sha:
            skipped.append({"name": name, "reason": "无法确定上游 commit,拒绝猜测"})
            continue
        staged = stage_candidate(repo, source_dir, commit_sha, staging_root, gh_runner)
        if not staged.get("ok"):
            skipped.append({"name": name, "reason": "候选树拉取失败({})".format(staged.get("error"))})
            continue
        staging_path = Path(staged["staging_path"])
        candidate_hash = staged["candidate_hash"]
        candidate_manifest = tree_manifest(staging_path)
        try:
            with open(staging_path / "SKILL.md", encoding="utf-8",
                      errors="ignore") as _fm:
                candidate_version = fm_version(_fm.read())
        except OSError:
            candidate_version = ""
        if candidate_hash == local_hash:
            up_to_date.append({"name": name, "instance_id": inst["instance_id"], "repo": repo,
                               "commit_sha": commit_sha})
            # 不在这里删 staging:同一上游候选可能被另一个待更新逻辑引用,统一在循环外清理
            continue
        lv, cv = str(logical.get("version") or ""), candidate_version
        if lv and cv and ver_tuple(cv) > ver_tuple(lv):
            status = "candidate-update"
        elif lv and cv and ver_tuple(cv) < ver_tuple(lv):
            status = "local-custom"
        else:
            status = "needs-review"
        note = "" if status != "local-custom" else "本地版本更高,保留本地"
        differs.append({"name": name, "instance_id": inst["instance_id"], "repo": repo,
                        "commit_sha": commit_sha, "candidate_hash": candidate_hash,
                        "source_dir": source_dir,
                        "staging_path": str(staging_path),
                        "local_hash": local_hash, "local_version": lv, "candidate_version": cv,
                        "status": status, "note": note,
                        "full_diff_summary": diff_summary(local_manifest, candidate_manifest),
                        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    # 循环结束后统一清理:只清"本工具登记所有权且无有效引用"的候选目录。
    # 引用覆盖本轮/上次更新结果 + 未过期计划 + 执行中/待恢复事务(F07)。
    referenced = {str(Path(d["staging_path"])) for d in differs}
    try:
        prev, _ = load_json_checked(Path(output_path), {})
        for d in ((prev or {}).get("differs") or []):
            if d.get("staging_path"):
                referenced.add(str(Path(d["staging_path"])))
    except (OSError, ValueError, TypeError):
        pass
    from scripts.core.staging import load_reference_inputs
    _, plan_rows, txn_rows = load_reference_inputs(Path(output_path).parent)
    from scripts.core.staging import collect_staging_references
    referenced |= collect_staging_references({}, plan_rows, txn_rows)
    staging_cleanup = cleanup_staging(staging_root, referenced)
    return {"schema_version": 2, "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "differs": differs, "up_to_date": up_to_date, "skipped": skipped,
            "staging_cleanup": {k: staging_cleanup[k] for k in ("removed", "unowned", "errors")}}


def main():
    ap = argparse.ArgumentParser(description="与上游比对完整内容树(只读)")
    ap.add_argument("--inventory", default=None, help="inventory v2 JSON 路径(默认 <data>/inventory.json)")
    ap.add_argument("--output", default=None, help="结果缓存路径(默认 <data>/updates.json)")
    ap.add_argument("--data-dir", default=os.environ.get("SKILL_KEEPER_DATA") or os.path.join(BASE, "data"))
    ap.add_argument("--json", action="store_true", help="机器可读输出;退出码 0=无差异 1=有差异")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    inventory_path = Path(args.inventory) if args.inventory else data_dir / "inventory.json"
    output_path = Path(args.output) if args.output else data_dir / "updates.json"

    inv, issues = load_json_checked(inventory_path, {})
    if issues or not isinstance(inv, dict) or not inv.get("instances"):
        result = {"schema_version": 2, "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "differs": [], "up_to_date": [], "skipped": [{"name": "-",
                  "reason": "inventory 缺失或为空({})".format(issues[0]["code"] if issues else "empty")}],
                  "operational_ok": issues == []}
        atomic_write_json(output_path, result)
        # F05:输入缺失/损坏是操作失败,退出码 2;不得覆盖已有成功结果为"全都无差异"
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1))
            sys.exit(2)
        print("⛔ inventory 缺失或为空,先跑 scan.py")
        sys.exit(2)

    try:
        result = check(inv, data_dir, output_path)
    except StagingBoundaryError as e:
        if args.json:
            print(json.dumps({"schema_version": 2, "error": "staging-boundary",
                              "message": str(e)}, ensure_ascii=False))
        print("⛔ 暂存目录位置不合法,已拒绝检查: {}".format(e))
        sys.exit(2)
    result["operational_ok"] = True
    atomic_write_json(output_path, result)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        sys.exit(1 if result["differs"] else 0)
    print(f"✅ 与上游一致 {len(result['up_to_date'])} 个")
    if result["differs"]:
        print(f"🟡 有差异 {len(result['differs'])} 个(更新必须先审查再 plan/apply):")
        for d in result["differs"]:
            print("   - {name} ← {repo} [{status}] {note}".format(**{**d, "status": STATUS_LABEL.get(d["status"], d["status"])}))
    if result["skipped"]:
        print(f"⏭️ 跳过 {len(result['skipped'])} 个:")
        for s in result["skipped"]:
            print(f"   - {s['name']}({s['reason']})")
    sys.exit(1 if result["differs"] else 0)


if __name__ == "__main__":
    main()
