#!/usr/bin/env python3
"""skill-keeper v2 交互服务:两阶段 plan/apply API,加固的本地 Web 边界。

安全边界:
- 只绑 127.0.0.1;所有 API 需要随机 token(常量时间比较);POST 校验 Origin;
- 请求体上限 64 KiB;confirm 必须是 JSON 布尔 true,字符串 "false" 一律拒绝;
- 所有响应带 nosniff/no-referrer/DENY/Permissions-Policy;HTML 另带白名单式 CSP
  (交互脚本通过带 token 的同源资源加载,不使用 unsafe-inline/unsafe-eval;
  样式仍允许 style-src unsafe-inline,仅覆盖报告自产内联样式);
- 所有变更动作都走统一变更引擎:计划不可变、执行要 digest 确认、先备份、失败回滚、
  成功失败都写审计;进程内锁 + 文件锁双重防并发;
- 不把异常 repr、绝对用户路径或客户端配置内容返回浏览器。

用法: report.py --serve [--port N] [--no-open](或直接 python3 scripts/serve.py)
"""
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from scripts.core.audit import append_audit, read_audit           # noqa: E402
from scripts.core.changes import (ChangeContext, ChangeError, LockBusy,  # noqa: E402
                                  apply_plan, create_remove_plan,
                                  create_restore_plan, create_update_plan)
from scripts.core.io import atomic_write_json, load_json_checked   # noqa: E402
from scripts.core.provenance import load_user_config               # noqa: E402

MAX_BODY = 64 * 1024
SERVER_VERSION = "skill-keeper/2.0"


class ServiceContext:
    """服务的运行环境:统一 RuntimePaths 解析(F04)、引擎上下文、进程内互斥锁。"""

    def __init__(self, data_dir, home=None, backup_dir=None):
        from scripts.core.runtime import RuntimePaths
        # F04:所有路径(数据/备份/staging)只在这里解析一次,报告、引擎、
        # 子进程刷新全部继承同一份;不再各自给 data/backups 拼一套默认值
        self.paths = RuntimePaths(home=home, data_dir=Path(data_dir), backup_dir=backup_dir)
        self.data_dir = self.paths.data_dir
        self.home = self.paths.home
        self.backup_dir = self.paths.backup_dir
        self.engine = ChangeContext(load_inventory=self._load_inventory,
                                    **self.paths.engine_kwargs())
        self.process_lock = threading.Lock()
        from scripts.core.service import AppService
        self.service = AppService(self.paths)

    def _load_inventory(self):
        inv, issues = load_json_checked(self.data_dir / "inventory.json", {})
        if issues or not isinstance(inv, dict) or not inv.get("instances"):
            raise ChangeError("inventory 缺失或为空,先重跑扫描")
        return inv


def run_scan_report(paths=None):
    """重跑 scan + report(F06):继承传入 paths 的环境,退出码 0/1 都算运行成功
    (1=扫描完成但有红灯,不是运行失败);超时/启动失败返回 False。"""
    env = paths.subprocess_env(home=str(paths.home)) if paths is not None else None
    for script in ("scan.py", "report.py"):
        try:
            r = subprocess.run([sys.executable, os.path.join(BASE, "scripts", script)],
                               capture_output=True, text=True, timeout=180, env=env)
        except (subprocess.TimeoutExpired, OSError):
            return False
        if r.returncode not in (0, 1):
            return False
    return True


def _plan_public(row):
    """计划对浏览器可见的最小信息(不含路径细节)。

    F09:candidate_hash/repo/commit_sha 供网页安检步骤展示"安检对象就是
    本计划绑定的固定候选"。"""
    pre = {}
    for pair in row.get("preconditions", []) or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            pre[str(pair[0])] = pair[1]
    return {"ok": True, "plan_id": row.get("plan_id"), "digest": row.get("digest"),
            "action": row.get("action"), "summary": row.get("summary"),
            "expires_at": row.get("expires_at"), "targets": list(row.get("target_ids", [])),
            "candidate_hash": str(pre.get("candidate_hash") or "") or None,
            "repo": str(pre.get("repo") or "") or None,
            "commit_sha": str(pre.get("commit_sha") or "") or None}


def _handle_vet(ctx, body):
    """候选安检记账(F09/任务1缺口1):绑定 body 指定的计划。

    安检结论必须携带可核查证据——confirm 只表示"确实要记账",绝不等于 safe;
    空 evidence 一律拒绝,绝不自动生成"网页确认"式依据。浏览器流程在计划
    建立后暂停并给出可续办的正式 CLI 命令(manage.py vet)。
    """
    if body.get("confirm") is not True:
        raise ChangeError("缺少明确确认:confirm 必须是布尔 true")
    evidence = body.get("evidence")
    if isinstance(evidence, str):
        evidence = [evidence] if evidence.strip() else []
    if not evidence:
        raise ChangeError(
            "安检结论必须携带可核查证据(evidence 不能为空);网页点击不构成安检,"
            "请用正式流程: skill-keeper manage vet {} --verdict safe "
            "--evidence <你的核查依据>".format(str(body.get("plan_id") or "<plan_id>")))
    return ctx.service.vet_candidate(str(body.get("plan_id") or ""),
                                      str(body.get("verdict") or ""), evidence)


def _handle_plan(ctx, body):
    action = str(body.get("action") or "").strip()
    known = load_user_config(ctx.data_dir)
    with ctx.process_lock:
        if action == "remove":
            plan = create_remove_plan(body.get("instance_ids") or [], ctx._load_inventory(),
                                      str(body.get("reason") or ""), ctx.engine.plans_dir,
                                      known_sources=known)
        elif action == "restore":
            backup_id = str(body.get("backup_id") or "")
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", backup_id):
                raise ChangeError("backup_id 格式不合法")
            plan = create_restore_plan(backup_id, ctx.backup_dir, ctx.engine.plans_dir)
        elif action == "update":
            plan = _plan_update_from_updates(ctx, body, known)
        else:
            raise ChangeError("action 必须是 remove|restore|update")
    row = _plan_public(plan.to_dict())
    # 复核修复(2026-09-06):此前指向旧式 remove_skill.py(只打印迁移说明),
    # 且离开仓库不可用;统一改为安装态 CLI
    row["apply_hint"] = shlex.join(["skill-keeper", "manage", "apply", row["plan_id"],
                                    "--digest", row["digest"], "--confirm"]) \
        if action == "remove" else None
    return row


def _plan_update_from_updates(ctx, body, known=None):
    """从 check_updates 的 staging 结果生成更新计划(候选必须已 staged 且 hash 一致)。"""
    iid = str(body.get("instance_id") or "")
    updates, _ = load_json_checked(ctx.data_dir / "updates.json", {})
    hit = None
    for d in (updates or {}).get("differs", []) if isinstance(updates, dict) else []:
        if d.get("instance_id") == iid and d.get("staging_path"):
            hit = d
            break
    if not hit:
        raise ChangeError("该实例没有已暂存的候选更新(先跑 check_updates)")
    snapshot = {"instance_id": iid, "staging_path": hit["staging_path"],
                "candidate_hash": hit.get("candidate_hash"), "repo": hit.get("repo"),
                "source": "github", "source_dir": hit.get("source_dir") or "",
                "commit_sha": hit.get("commit_sha")}
    return create_update_plan(iid, snapshot, ctx._load_inventory(), ctx.engine.plans_dir,
                              known_sources=known)


def _handle_apply(ctx, body):
    """执行计划并发布新快照(F08):文件事务成功与报告刷新失败用 snapshot_status 区分。"""
    plan_id = str(body.get("plan_id") or "")
    digest = str(body.get("digest") or "")
    result = ctx.service.apply_action(plan_id, digest, body.get("confirm"),
                                      accept_warning=body.get("accept_warning") is True)
    payload = {"ok": True,
               "message": result.get("message") or "已执行",
               "backup": os.path.basename(str(result.get("backup_path") or "")),
               "backup_id": result.get("backup_id"),
               "transaction_status": result.get("transaction_status"),
               "result_hashes": result.get("result_hashes"),
               "snapshot_status": result.get("snapshot_status"),
               "snapshot_id": result.get("snapshot_id"),
               "plan_id": plan_id}
    if result.get("audit_pending"):
        payload["audit_pending"] = True
    return payload


def _build_handler(ctx, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = SERVER_VERSION

        # ---------- 基础设施 ----------
        def _send(self, code, body, ctype="application/json; charset=utf-8", html=None):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
            if html is not None:
                self.send_header("Content-Security-Policy", _csp_for(html))
            self.end_headers()
            self.wfile.write(data)

        def _auth(self, q):
            given = q.get("t", [""])[0]
            # 常量时间比较必须用字节串:非 ASCII token 的字符串比较会 TypeError
            return secrets.compare_digest(str(given).encode("utf-8"),
                                          str(token).encode("utf-8"))

        def _check_origin(self):
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return origin == "http://127.0.0.1:%d" % self.server.server_port

        def _body(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise _BadRequest("Content-Length 不合法")
            if n < 0 or n > MAX_BODY:
                raise _TooLarge()
            raw = self.rfile.read(n) if n else b"{}"
            try:
                value = json.loads(raw or b"{}")
            except ValueError:
                raise _BadRequest("请求体不是合法 JSON")
            if not isinstance(value, dict):
                raise _BadRequest("请求体必须是 JSON 对象")
            return value

        def log_message(self, *a):
            pass

        # ---------- GET ----------
        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/", "/report"):
                if not self._auth(q):
                    return self._send(403, {"ok": False, "error": "token 缺失或不正确"})
                try:
                    html = (ctx.data_dir / "report.html").read_bytes()
                except OSError:
                    return self._send(404, {"ok": False, "error": "report.html 不存在,先跑 report.py"})
                html = _externalize_report_script(html, token)
                return self._send(200, html, "text/html; charset=utf-8", html=html)
            if u.path == "/report.js":
                if not self._auth(q):
                    return self._send(403, {"ok": False, "error": "token 缺失或不正确"})
                from scripts.report import JS_BLOB
                return self._send(200, JS_BLOB.encode("utf-8"),
                                  "application/javascript; charset=utf-8")
            if not self._auth(q):
                return self._send(403, {"ok": False, "error": "token 缺失或不正确"})
            m = re.fullmatch(r"/api/plan/([A-Za-z0-9._-]{1,80})", u.path)
            if m:
                path = ctx.engine.plans_dir / (m.group(1) + ".json")
                row, issues = load_json_checked(path, {})
                if issues or not isinstance(row, dict):
                    return self._send(404, {"ok": False, "error": "计划不存在"})
                return self._send(200, _plan_public(row))
            if u.path == "/api/audit":
                rows = read_audit(ctx.engine.audit_path)[-20:]
                return self._send(200, {"ok": True, "events": rows})
            self._send(404, {"ok": False, "error": "not found"})

        # ---------- POST ----------
        def do_POST(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if not self._auth(q):
                return self._send(403, {"ok": False, "error": "token 缺失或不正确"})
            if not self._check_origin():
                return self._send(403, {"ok": False, "error": "Origin 不合法"})
            try:
                body = self._body()
            except _TooLarge:
                return self._send(413, {"ok": False, "error": "请求体超过 64 KiB 上限"})
            except _BadRequest as e:
                return self._send(400, {"ok": False, "error": str(e)})
            try:
                if u.path == "/api/plan":
                    return self._send(200, _handle_plan(ctx, body))
                if u.path == "/api/vet":
                    with ctx.process_lock:
                        return self._send(200, _handle_vet(ctx, body))
                if u.path == "/api/apply":
                    if body.get("confirm") is not True:
                        raise ChangeError("缺少明确确认:confirm 必须是布尔 true")
                    return self._send(200, _handle_apply(ctx, body))
                if u.path == "/api/restore-plan":
                    body = dict(body)
                    body["action"] = "restore"
                    return self._send(200, _handle_plan(ctx, body))
                if u.path == "/api/rescan":
                    with ctx.process_lock:
                        ok = run_scan_report(ctx.service.paths)
                    if ok:
                        return self._send(200, {"ok": True, "message": "已重扫并刷新报告"})
                    return self._send(500, {"ok": False, "error": "重扫失败,请手动跑 scan.py"})
                if u.path == "/api/ignore":
                    # 写入口一致拿进程内锁(ignore.json 是读改写,且随后触发重扫)
                    with ctx.process_lock:
                        return self._send(200, _handle_ignore(ctx, body))
                return self._send(404, {"ok": False, "error": "not found"})
            except LockBusy as e:
                append_audit({"action": "web-rejected", "reason": str(e)[:120],
                              "status": "failed", "error": str(e)[:200]},
                             ctx.engine.audit_path)
                return self._send(409, {"ok": False, "error": str(e)})
            except ChangeError as e:
                append_audit({"action": "web-rejected", "reason": str(e)[:120],
                              "status": "failed", "error": str(e)[:200]},
                             ctx.engine.audit_path)
                return self._send(400, {"ok": False, "error": str(e)})
            except Exception:
                return self._send(400, {"ok": False, "error": "执行失败,请查看本地审计日志"})

    return Handler


class _TooLarge(Exception):
    pass


class _BadRequest(Exception):
    pass


def _handle_ignore(ctx, body):
    """忽略规则管理(写入 data/ignore.json,属用户配置,不是 skill 变更)。"""
    if body.get("confirm") is not True:
        raise ChangeError("缺少明确确认")
    name = str(body.get("name") or "")
    match = str(body.get("match") or "")
    remove = body.get("remove") is True
    if not name or not match:
        raise ChangeError("缺少 name 或 match")
    path = ctx.data_dir / "ignore.json"
    cur, _ = load_json_checked(path, {})
    cur = cur if isinstance(cur, dict) else {}
    rules = [r for r in (cur.get(name) or []) if r != match]
    if not remove:
        rules.append(match)
    if rules:
        cur[name] = rules
    else:
        cur.pop(name, None)
    atomic_write_json(path, cur)
    run_scan_report(ctx.service.paths)
    return {"ok": True, "message": "已更新忽略规则并刷新报告"}


def _csp_for(html: bytes) -> str:
    """为报告页计算白名单式 CSP:同源脚本或内联脚本 hash,不用 unsafe-eval。"""
    text = html.decode("utf-8", errors="ignore")
    script_srcs = ["'self'"]
    for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", text, re.S):
        digest = hashlib.sha256(m.group(1).encode("utf-8")).hexdigest()
        script_srcs.append("'sha256-{}'".format(digest))
    return ("default-src 'self'; connect-src 'self'; img-src 'self'; "
            "script-src {}; style-src 'self' 'unsafe-inline'; "
            "form-action 'self'; base-uri 'none'".format(" ".join(script_srcs)))


def _externalize_report_script(html, token):
    """把报告中的内联脚本改成同源脚本,让严格 CSP 下的浏览器也能执行交互。"""
    marker = re.search(rb"<script>(.*?)</script>", html, re.S)
    if not marker:
        return html
    src = b'<script src="/report.js?t=' + token.encode("ascii") + b'"></script>'
    return html[:marker.start()] + src + html[marker.end():]


def create_server(data_dir, home=None, port=0, backup_dir=None):
    ctx = ServiceContext(data_dir, home=home, backup_dir=backup_dir)
    token = secrets.token_urlsafe(24)
    handler = _build_handler(ctx, token)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    return httpd, token, ctx


def main():
    argv = sys.argv[1:]
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 0
    from scripts.core.runtime import RuntimePaths
    paths = RuntimePaths()
    ok = run_scan_report(paths)
    if not ok:
        print("⚠️ 启动前重扫失败,报告可能不是最新", file=sys.stderr)
    httpd, token, _ctx = create_server(paths.data_dir, port=port)
    url = "http://127.0.0.1:{}/?t={}".format(httpd.server_port, token)
    print("✅ skill-keeper 交互报告(v2 plan/apply):" + url, flush=True)
    print("   仅本机可访问;所有变更动作走 计划→确认→备份→执行→验证 流程;Ctrl+C 退出", flush=True)
    if "--no-open" not in argv:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
