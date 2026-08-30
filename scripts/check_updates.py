#!/usr/bin/env python3
"""skill-keeper 更新检查(只读):对比本地与上游 SKILL.md,不做任何修改"""
import json, os, subprocess, sys, urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DATA = os.path.join(BASE, "data")
HOME = os.path.expanduser("~")

def gh_raw(repo, path):
    """经 gh api 取上游文件原文;失败返回 None"""
    r = subprocess.run(["gh", "api", f"repos/{repo}/contents/{path}",
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

def main():
    inv = json.load(open(os.path.join(DATA, "inventory.json"), encoding="utf-8"))
    known = json.load(open(os.path.join(DATA, "known-sources.json"), encoding="utf-8"))
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
        local = None
        for i in sorted(s["instances"], key=lambda x: x.get("priority", 9) if "priority" in x else 9):
            rp = i.get("real_path")
            if rp and not i.get("stale_cache"):
                sk = os.path.join(rp, "SKILL.md")
                if os.path.exists(sk):
                    local = open(sk, encoding="utf-8", errors="ignore").read()
                    break
        if local is None:
            skip.append((name, "本地文件缺失"))
            continue
        upstream = None
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
        else:
            diff.append((name, repo))
    if "--json" in sys.argv:
        # 机器可读输出;退出码:0=全部一致,1=有差异
        print(json.dumps({"up_to_date": ok, "differs": diff,
                          "skipped": [{"name": n, "reason": w} for n, w in skip]},
                         ensure_ascii=False, indent=1))
        sys.exit(1 if diff else 0)
    print(f"✅ 与上游一致 {len(ok)} 个")
    if diff:
        print(f"🔴 与上游有差异 {len(diff)} 个(可能是上游更新或本地改动,更新前先确认):")
        for n, r in diff:
            print(f"   - {n} ← {r}")
    if skip:
        print(f"⏭️ 跳过 {len(skip)} 个:")
        for n, why in skip:
            print(f"   - {n}({why})")
    if diff:
        print("\n提示:确认要更新时,skills.sh 来源用 `npx -y skills add <owner/repo>@<slug> -g -y`,GitHub 来源用 gh api 拉取覆盖,操作前先备份。")

if __name__ == "__main__":
    main()
