#!/usr/bin/env python3
"""skill-keeper 交互服务:报告里的 更新/删除/忽略/恢复 按钮在本地直接执行。
安全边界:
- 只绑 127.0.0.1,页面 URL 带随机 token,API 全部校验(防其他网页对 localhost 跨站调用);
- 所有变更动作先 tar 备份到 backups/,成功后自动重扫 scan.py + 重报 report.py;
- 自建白名单 skill 删除仍受保护(需 CLI --force);插件缓存 skill 拒绝操作;
- 更新/删除/恢复需请求体带 confirm:true(对应页面上的确认弹窗);
- 每次动作追加记录到 data/actions.log。
用法: report.py --serve [--port N] [--no-open](或直接 python3 scripts/serve.py)"""
import difflib, json, os, re, secrets, shutil, subprocess, sys, tarfile, time, urllib.parse, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DATA = os.path.join(BASE, "data")
BACKUPS = os.path.join(BASE, "backups")
HOME = os.path.expanduser("~")
LOCATIONS = [f"{HOME}/.agents/skills", f"{HOME}/.zcode/skills", f"{HOME}/.claude/skills",
             f"{HOME}/.codex/skills", f"{HOME}/.local/share/ego/ego-skills"]
TOKEN = secrets.token_urlsafe(24)
sys.path.insert(0, os.path.join(BASE, "scripts"))
from check_updates import gh_raw, skills_sh_skillmd
from scan import sk_signature


def log(action, detail):
    with open(os.path.join(DATA, "actions.log"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action, **detail},
                           ensure_ascii=False) + "\n")


def inventory():
    return json.load(open(os.path.join(DATA, "inventory.json"), encoding="utf-8"))


def valid_dir(d):
    return bool(d) and bool(re.fullmatch(r"[A-Za-z0-9._-]+", d))


def find_skill(d):
    if not valid_dir(d):
        return None
    for s in inventory()["skills"]:
        for i in s["instances"]:
            if i["dir"] == d and i.get("real_path") and not i.get("stale_cache"):
                return s, i
    return None


def self_built_names():
    p = os.path.join(DATA, "self-built.txt")
    return {l.strip() for l in open(p, encoding="utf-8") if l.strip() and not l.startswith("#")} if os.path.exists(p) else set()


def run_scan_report():
    r1 = subprocess.run([sys.executable, os.path.join(BASE, "scripts", "scan.py")],
                        capture_output=True, text=True, timeout=120)
    r2 = subprocess.run([sys.executable, os.path.join(BASE, "scripts", "report.py")],
                        capture_output=True, text=True, timeout=120)
    return r1.returncode == 0 and r2.returncode == 0, (r1.stderr + r2.stderr).strip()[-300:]


def backup_tar(d, tag):
    """把 d 在所有位置的存在打成 tar;返回备份文件路径(不删除任何东西)"""
    os.makedirs(BACKUPS, exist_ok=True)
    bak = os.path.join(BACKUPS, f"{tag}-{d}-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz")
    with tarfile.open(bak, "w:gz") as t:
        for loc in LOCATIONS:
            p = os.path.join(loc, d)
            if os.path.lexists(p):
                t.add(os.path.realpath(p) if os.path.islink(p) else p, arcname=d, recursive=True)
    return bak


def gh_download_dir(repo, repo_path, dest):
    """递归拉取 GitHub 上 repo_path 目录下所有文件,写入 dest。返回文件数。"""
    def list_dir(path):
        r = subprocess.run(["gh", "api", f"repos/{repo}/contents/{urllib.parse.quote(path)}"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"gh api 列目录失败: {(r.stderr or '').strip()[:200]}")
        return json.loads(r.stdout)

    n = 0
    for item in list_dir(repo_path):
        if item.get("type") == "file":
            content = gh_raw(repo, item["path"])
            if content is None:
                raise RuntimeError(f"拉取失败: {item['path']}")
            local = os.path.join(dest, os.path.relpath(item["path"], repo_path))
            os.makedirs(os.path.dirname(local), exist_ok=True)
            with open(local, "w", encoding="utf-8") as f:
                f.write(content)
            n += 1
        elif item.get("type") == "dir":
            n += gh_download_dir(repo, item["path"], dest)
    return n


def upstream_dir_path(path):
    """known-sources 里的 path 指向 SKILL.md;更新时取其所在目录"""
    p = path or ""
    return os.path.dirname(p) if p.endswith("SKILL.md") else p


def drop_stale_update(name):
    """更新成功后清掉 updates.json 里该 skill 的旧差异记录,免得报告显示过期的「建议更新」。
    下次跑 check_updates 时按更新后的内容重新评估。"""
    p = os.path.join(DATA, "updates.json")
    try:
        u = json.load(open(p, encoding="utf-8"))
        u["differs"] = [x for x in u.get("differs", []) if x.get("name") != name]
        json.dump(u, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


def do_update(d):
    hit = find_skill(d)
    if not hit:
        return False, f"找不到 skill: {d}"
    s, _ = hit
    src = s["source"]
    if src.get("type") == "plugin":
        return False, "插件 skill 由插件系统管理,不能在这里更新"
    if d in self_built_names():
        return False, "自建 skill 不从上游更新"
    repo, path = src.get("repo"), src.get("path")
    ent = next((x for x in s["instances"] if x.get("real_path") and not x.get("is_symlink") and not x.get("stale_cache")), None)
    if not ent:
        return False, f"找不到 {d} 的本地实体(可能只有符号链接实例)"
    real = ent["real_path"]
    bak = backup_tar(d, "update")
    try:
        if src.get("type") == "github" and repo and path:
            tmp = real + ".upstream-tmp"
            if os.path.lexists(tmp):
                shutil.rmtree(tmp)
            os.makedirs(tmp)
            n = gh_download_dir(repo, upstream_dir_path(path), tmp)
            for x in os.listdir(real):
                p = os.path.join(real, x)
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
            for x in os.listdir(tmp):
                shutil.move(os.path.join(tmp, x), os.path.join(real, x))
            os.rmdir(tmp)
            msg = f"已从 {repo} 更新 {d}({n} 个文件);备份: {os.path.basename(bak)}"
        elif src.get("type") == "skills.sh" and repo and "/" in repo:
            meta = os.path.join(real, "_meta.json")
            slug = (json.load(open(meta, encoding="utf-8")).get("slug") if os.path.exists(meta) else None) or d
            r = subprocess.run(["npx", "-y", "skills", "add", f"{repo}@{slug}", "-g", "-y"],
                               capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout or "").strip()[:300] or "skills add 失败")
            msg = f"已通过 skills.sh 更新 {d};备份: {os.path.basename(bak)}"
        else:
            return False, f"该 skill(来源 {src.get('type')})没有可用的更新通道,请手动处理"
    except Exception as e:
        return False, f"更新失败(已先备份 {os.path.basename(bak)},原内容未动): {e}"
    ok, err = run_scan_report()
    drop_stale_update(s["name"])  # s 是更新前抓的,名字即使变了也能清掉旧记录
    log("update", {"dir": d, "backup": os.path.basename(bak), "ok": ok})
    return True, msg + (";已重扫并刷新报告" if ok else f";⚠️ 重扫失败请手动跑 scan.py: {err}")


def do_remove(d, confirm):
    if not valid_dir(d):
        return False, f"非法目录名: {d}"
    if not confirm:
        return False, "缺少 confirm(防误触)"
    args = [sys.executable, os.path.join(BASE, "scripts", "remove_skill.py"), d]
    r = subprocess.run(args, capture_output=True, text=True, timeout=120)
    out = (r.stdout or r.stderr).strip()
    if r.returncode != 0:
        return False, out or "删除失败"
    ok, err = run_scan_report()
    log("remove", {"dir": d, "ok": ok})
    return True, out.splitlines()[-1] if out else "已删除" + (";已重扫并刷新报告" if ok else f";⚠️ 重扫失败: {err}")


def do_restore(bak_name, confirm):
    if not confirm:
        return False, "缺少 confirm(防误触)"
    if not bak_name or not re.fullmatch(r"[A-Za-z0-9._-]+\.tar\.gz", bak_name):
        return False, f"非法备份名: {bak_name}"
    p = os.path.join(BACKUPS, bak_name)
    if not os.path.exists(p):
        return False, "备份不存在"
    dest = os.path.join(HOME, ".agents", "skills")
    with tarfile.open(p) as t:
        for m in t.getmembers():
            if m.name.startswith("/") or ".." in m.name.split("/"):
                return False, "备份含不安全路径,拒绝恢复"
        t.extractall(dest)
    ok, err = run_scan_report()
    log("restore", {"backup": bak_name, "ok": ok})
    return True, f"已从 {bak_name} 恢复到 ~/.agents/skills;已重扫并刷新报告" if ok else f"已恢复,但重扫失败: {err}"


def do_ignore(name, match, remove):
    if not name or not match:
        return False, "缺少 name 或 match"
    p = os.path.join(DATA, "ignore.json")
    cur = {}
    if os.path.exists(p):
        try:
            cur = json.load(open(p, encoding="utf-8"))
        except Exception:
            cur = {}
    rules = [r for r in (cur.get(name) or []) if r != match]
    if not remove:
        rules.append(match)
    if rules:
        cur[name] = rules
    else:
        cur.pop(name, None)
    json.dump(cur, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    run_scan_report()
    log("ignore", {"name": name, "match": match, "remove": bool(remove)})
    return True, ("已取消忽略并刷新报告" if remove else "已忽略并刷新报告(规则写入 data/ignore.json)")


def do_vet_record(d, verdict, note, confirm):
    """安检记账:把按 skill-vetter 清单审出的结论写进 data/vetted.json,记当前内容指纹。"""
    if not confirm:
        return False, "缺少 confirm(防误触)"
    if verdict not in ("safe", "warning", "danger"):
        return False, "verdict 必须是 safe|warning|danger"
    hit = find_skill(d)
    if not hit:
        return False, f"找不到 skill: {d}"
    _, i = hit
    if not i.get("real_path") or not os.path.exists(os.path.join(i["real_path"], "SKILL.md")):
        return False, f"找不到 {d} 的本地实体"
    p = os.path.join(DATA, "vetted.json")
    try:
        cur = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    except Exception:
        cur = {}
    cur[d] = {"verdict": verdict, "note": (note or "")[:200],
              "vetted_at": time.strftime("%Y-%m-%d"), "sk_hash": sk_signature(i["real_path"])}
    json.dump(cur, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    run_scan_report()
    log("vet_record", {"dir": d, "verdict": verdict})
    label = {"safe": "🛡️ 安全", "warning": "⚠️ 存疑", "danger": "☠️ 判危"}[verdict]
    return True, f"已记账:{d} → {label};已重扫并刷新报告"


def do_diff(d):
    hit = find_skill(d)
    if not hit:
        return False, f"找不到 skill: {d}"
    s, i = hit
    src = s["source"]
    repo, path = src.get("repo"), src.get("path")
    sk = os.path.join(i["real_path"], "SKILL.md")
    local = open(sk, encoding="utf-8", errors="ignore").read() if os.path.exists(sk) else ""
    upstream = gh_raw(repo, path) if path else None
    if upstream is None and src.get("type") == "skills.sh" and repo and "/" in repo:
        meta = os.path.join(i["real_path"], "_meta.json")
        slug = (json.load(open(meta, encoding="utf-8")).get("slug") if os.path.exists(meta) else None) or d
        upstream = skills_sh_skillmd(repo, slug)
    if upstream is None:
        return False, f"拉取上游失败({repo})"
    diff = "".join(difflib.unified_diff(local.splitlines(keepends=True), upstream.splitlines(keepends=True),
                                        fromfile=f"本地 {d}/SKILL.md", tofile=f"上游 {repo}"))
    return True, diff if diff.strip() else "本地与上游内容一致(仅空白差异)"


def jdump(ok, message):
    return json.dumps({"ok": bool(ok), "message": message}, ensure_ascii=False)


class Handler(BaseHTTPRequestHandler):
    server_version = "skill-keeper/1.1"

    def _auth(self, q):
        return q.get("t", [""])[0] == TOKEN

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if not self._auth(q):
            return self._send(403, jdump(False, "token 缺失或不正确"))
        if u.path in ("/", "/report"):
            try:
                with open(os.path.join(DATA, "report.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                return self._send(500, jdump(False, "report.html 不存在,请先跑 report.py"))
        if u.path == "/api/diff":
            d = q.get("dir", [""])[0]
            ok, msg = do_diff(d)
            if not ok:
                return self._send(400, jdump(False, msg))
            if "raw" in q:  # 纯文本版(调试/管道用);页面默认走 JSON
                return self._send(200, msg, "text/plain; charset=utf-8")
            return self._send(200, json.dumps({"ok": True, "diff": msg}, ensure_ascii=False))
        self._send(404, jdump(False, "not found"))

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if not self._auth(q):
            return self._send(403, jdump(False, "token 缺失或不正确"))
        try:
            body = self._body()
        except Exception:
            return self._send(400, jdump(False, "请求体不是合法 JSON"))
        try:
            if u.path == "/api/remove":
                ok, msg = do_remove(body.get("dir", ""), bool(body.get("confirm")))
            elif u.path == "/api/update":
                ok, msg = do_update(body.get("dir", "")) if body.get("confirm") else (False, "缺少 confirm(防误触)")
            elif u.path == "/api/restore":
                ok, msg = do_restore(body.get("backup", ""), bool(body.get("confirm")))
            elif u.path == "/api/ignore":
                ok, msg = do_ignore(body.get("name", ""), body.get("match", ""), bool(body.get("remove")))
            elif u.path == "/api/vet_record":
                ok, msg = do_vet_record(body.get("dir", ""), body.get("verdict", ""),
                                        body.get("note", ""), bool(body.get("confirm")))
            elif u.path == "/api/rescan":
                ok, err = run_scan_report()
                msg = "已重扫并刷新报告" if ok else f"重扫失败: {err}"
            else:
                return self._send(404, jdump(False, "not found"))
        except Exception as e:
            return self._send(500, jdump(False, f"执行异常: {e}"))
        self._send(200 if ok else 400, jdump(ok, msg))

    def log_message(self, *a):
        pass  # 静默访问日志(actions.log 记录变更)


def main():
    argv = sys.argv[1:]
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 0
    ok, err = run_scan_report()
    if not ok:
        print(f"⚠️ 启动前重扫失败: {err}", file=sys.stderr)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{httpd.server_port}/?t={TOKEN}"
    print(f"✅ skill-keeper 交互报告:{url}", flush=True)
    print("   仅本机可访问;Ctrl+C 退出;所有操作先备份、后自动重扫重报,动作记录在 data/actions.log", flush=True)
    if "--no-open" not in argv:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
