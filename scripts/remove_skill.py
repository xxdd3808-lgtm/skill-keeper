#!/usr/bin/env python3
"""skill-keeper 安全删除:备份 → 从所有位置删除 → 清理锁文件。用法: remove_skill.py <目录名> [更多...]"""
import json, os, subprocess, sys, tarfile, tempfile, time

HOME = os.path.expanduser("~")
BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DATA = os.path.join(BASE, "data")
LOCATIONS = [
    f"{HOME}/.agents/skills", f"{HOME}/.zcode/skills", f"{HOME}/.claude/skills",
    f"{HOME}/.codex/skills", f"{HOME}/.local/share/ego/ego-skills",
]
LOCK_FILE = os.path.join(HOME, ".agents/.skill-lock.json")

def self_built():
    p = os.path.join(DATA, "self-built.txt")
    return {l.strip() for l in open(p, encoding="utf-8") if l.strip() and not l.startswith("#")} if os.path.exists(p) else set()

def main():
    if len(sys.argv) < 2:
        print("用法: remove_skill.py <目录名> [更多目录名...]\n注意:删除自建白名单里的 skill 需加 --force")
        sys.exit(1)
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv
    protected = self_built()
    for d in args:
        if d in protected and not force:
            print(f"🛑 {d} 在自建白名单里,受保护。确认要删请加 --force(仍会先备份)。")
            sys.exit(1)
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(BASE, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    for d in args:
        existing = [p for p in (os.path.join(loc, d) for loc in LOCATIONS) if os.path.lexists(p)]
        if not existing:
            print(f"⏭️ {d}: 所有位置都不存在,跳过")
            continue
        # 1) 备份(所有位置打成一个 tar)
        bak = os.path.join(backup_dir, f"removed-{d}-{ts}.tar.gz")
        with tarfile.open(bak, "w:gz") as t:
            for p in existing:
                t.add(os.path.realpath(p) if os.path.islink(p) else p,
                      arcname=os.path.basename(p), recursive=True)
        # 2) 删除(符号链接只删链接本身)
        for p in existing:
            if os.path.islink(p) or os.path.isfile(p):
                os.remove(p)
            else:
                subprocess.run(["rm", "-rf", p], check=True)
            print(f"🗑️ 已删 {p}")
        print(f"💾 备份: {bak}")
        # 3) 清理锁文件条目
        try:
            lock = json.load(open(LOCK_FILE, encoding="utf-8"))
            if d in lock.get("skills", {}):
                del lock["skills"][d]
                json.dump(lock, open(LOCK_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                print(f"🧹 已清理锁文件条目 {d}")
        except Exception as e:
            print(f"⚠️ 锁文件清理失败: {e}")
    print("\n完成。请重跑 scan.py 刷新盘点。")

if __name__ == "__main__":
    main()
