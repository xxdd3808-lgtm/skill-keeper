"""Task 7 HTTP 边界(F08):负 Content-Length、非 JSON 对象、未知路由、
Unicode 非法 token、服务关停 socket 释放——错误不泄露路径。"""
import json
import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path

from scripts.serve import create_server


class HttpEdgeTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.httpd, self.token, self.ctx = create_server(Path(self._td.name) / "data",
                                                         home=Path(self._td.name))
        self.addCleanup(self.httpd.server_close)
        import threading
        self.addCleanup(self.httpd.shutdown)
        t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        t.start()
        self.base = "http://127.0.0.1:{}".format(self.httpd.server_port)

    def _raw(self, method, path, headers=None, body=b""):
        conn = socket.create_connection(("127.0.0.1", self.httpd.server_port), timeout=5)
        try:
            req = "{} {} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n".format(
                method, path)
            for k, v in (headers or {}).items():
                req += "{}: {}\r\n".format(k, v)
            req += "Content-Length: {}\r\n\r\n".format(len(body))
            conn.sendall(req.encode("utf-8") + body)
            chunks = []
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)
            head, _, payload = raw.partition(b"\r\n\r\n")
            status_line = head.split(b"\r\n")[0].decode("latin1")
            return int(status_line.split()[1]), payload
        finally:
            conn.close()

    def test_negative_content_length_is_rejected(self):
        code, _ = self._raw("POST", "/api/plan?t=" + self.token,
                            {"Content-Length": "-5"})
        self.assertIn(code, (400, 413, 500))

    def test_non_object_body_rejected(self):
        code, payload = self._raw("POST", "/api/plan?t=" + self.token,
                                  {"Content-Type": "application/json"},
                                  json.dumps([1, 2]).encode())
        self.assertEqual(code, 400)
        self.assertIn(b"JSON", payload)

    def test_unknown_route_404(self):
        code, _ = self._raw("POST", "/api/definitely-not-here?t=" + self.token)
        self.assertEqual(code, 404)

    def test_unicode_token_rejected_not_500(self):
        code, _ = self._raw("GET", "/?t=%E4%B8%AD%E6%96%87")
        self.assertEqual(code, 403)

    def test_tokened_get_serves_without_path_leak(self):
        code, payload = self._raw("GET", "/api/audit?t=" + self.token)
        self.assertEqual(code, 200)
        self.assertNotIn(b"/Users/", payload)

    def test_server_close_releases_socket(self):
        port = self.httpd.server_port
        self.httpd.shutdown()
        self.httpd.server_close()
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=2).close()


if __name__ == "__main__":
    unittest.main()
