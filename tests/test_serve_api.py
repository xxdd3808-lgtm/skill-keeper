import json, os, threading, unittest
from http.client import HTTPConnection
from pathlib import Path

from scripts.core.io import FileLock, atomic_write_json
from tests.helpers import temp_home, write_skill

REPO_ROOT = Path(__file__).resolve().parents[1]


def start_server(testcase, home, data):
    from scripts import serve
    httpd, token, ctx = serve.create_server(data, home=home)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    testcase.addCleanup(httpd.shutdown)
    return {"port": httpd.server_port, "token": token, "ctx": ctx}


def minimal_report(testcase, data):
    (Path(data) / "report.html").write_text(
        "<!DOCTYPE html><html><body><h1>报告</h1><script>var x=1;</script></body></html>",
        encoding="utf-8")


class ServeApiTests(unittest.TestCase):
    def _server_with_report(self):
        home = temp_home(self)
        data = home / "data"
        data.mkdir(parents=True, exist_ok=True)
        minimal_report(self, data)
        return start_server(self, home, data), home, data

    def _post(self, port, token, path, body=None, raw=None, origin=None):
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if origin is not None:
            headers["Origin"] = origin
        url = path + ("&" if "?" in path else "?") + "t=" + token
        payload = raw if raw is not None else json.dumps(body or {}).encode()
        conn.request("POST", url, body=payload, headers=headers)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, r.headers, data

    def test_false_string_is_not_confirmation(self):
        srv, _, _ = self._server_with_report()
        status, _, body = self._post(srv["port"], srv["token"], "/api/apply",
                                     {"plan_id": "p", "digest": "d", "confirm": "false"})
        self.assertEqual(status, 400, "字符串 'false' 不得当作确认")

    def test_missing_token_and_oversized_body_are_rejected(self):
        srv, _, _ = self._server_with_report()
        status, _, _ = self._post(srv["port"], "", "/api/plan", {})
        self.assertEqual(status, 403)
        status, _, _ = self._post(srv["port"], srv["token"], "/api/plan", raw=b"x" * 70000)
        self.assertEqual(status, 413)

    def test_cross_origin_post_is_rejected(self):
        srv, _, _ = self._server_with_report()
        status, _, _ = self._post(srv["port"], srv["token"], "/api/plan", {},
                                  origin="http://evil.example")
        self.assertEqual(status, 403)

    def test_security_headers_are_present(self):
        srv, _, _ = self._server_with_report()
        conn = HTTPConnection("127.0.0.1", srv["port"], timeout=10)
        conn.request("GET", "/?t=" + srv["token"])
        r = conn.getresponse()
        body = r.read()
        conn.close()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src", r.headers.get("Content-Security-Policy", ""))
        self.assertIn("script-src 'self' 'sha256-", r.headers.get("Content-Security-Policy", ""))
        self.assertNotIn("unsafe-eval", r.headers.get("Content-Security-Policy", ""))

    def test_remove_plan_apply_roundtrip_on_temp_home(self):
        from scripts.scan import build_inventory
        home = temp_home(self)
        data = home / "data"
        data.mkdir(parents=True, exist_ok=True)
        demo = write_skill(home / ".agents/skills", "demo", body="web demo")
        inv = build_inventory(home, data)
        atomic_write_json(data / "inventory.json", inv)
        iid = inv["instances"][0]["instance_id"]
        srv = start_server(self, home, data)

        status, _, body = self._post(srv["port"], srv["token"], "/api/plan",
                                     {"action": "remove", "instance_ids": [iid],
                                      "reason": "web 测试"})
        self.assertEqual(status, 200, body)
        plan = json.loads(body)
        self.assertTrue(plan["ok"])
        self.assertTrue(plan["plan_id"])
        self.assertTrue(plan["digest"])

        status, _, body = self._post(srv["port"], srv["token"], "/api/apply",
                                     {"plan_id": plan["plan_id"], "digest": plan["digest"],
                                      "confirm": True})
        self.assertEqual(status, 200, body)
        self.assertFalse(demo.exists(), "合法计划应在临时 HOME 完成删除")
        audit = srv["ctx"].engine.audit_path
        self.assertTrue(audit.exists())

    def test_concurrent_apply_is_rejected_while_lock_held(self):
        from scripts.scan import build_inventory
        home = temp_home(self)
        data = home / "data"
        data.mkdir(parents=True, exist_ok=True)
        demo = write_skill(home / ".agents/skills", "demo", body="lock demo")
        inv = build_inventory(home, data)
        atomic_write_json(data / "inventory.json", inv)
        iid = inv["instances"][0]["instance_id"]
        srv = start_server(self, home, data)
        status, _, body = self._post(srv["port"], srv["token"], "/api/plan",
                                     {"action": "remove", "instance_ids": [iid], "reason": "t"})
        plan = json.loads(body)
        with FileLock(srv["ctx"].engine.lock_path):
            status, _, body = self._post(srv["port"], srv["token"], "/api/apply",
                                         {"plan_id": plan["plan_id"], "digest": plan["digest"],
                                          "confirm": True})
        self.assertEqual(status, 409, "并发变更必须安全失败")
        self.assertTrue(demo.exists())

    def test_unknown_route_and_bad_backup_are_handled(self):
        srv, _, _ = self._server_with_report()
        status, _, _ = self._post(srv["port"], srv["token"], "/api/restore-plan",
                                  {"backup_id": "no-such-backup", "confirm": True})
        self.assertEqual(status, 400)
        status, _, _ = self._post(srv["port"], srv["token"], "/api/nothing", {})
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
