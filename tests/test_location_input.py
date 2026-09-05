"""Task 3(v4):模型位置声明解析合同 —— 白名单、限额、只读、不可升级。

解析是纯文本操作:不打开声明中的任何文件;错误不回显字段值。
"""
import json
import os
import unittest

from scripts.core.location_input import (LocationInputError, MAX_DECL_BYTES,  # noqa: E402
                                         MAX_ROOTS, parse_cli_roots,
                                         parse_declaration)


def _decl(**overrides):
    base = {
        "schema_version": 1,
        "client": "example-agent",
        "observed_by": "model",
        "complete": False,
        "roots": [{"path": "~/.example-agent/skills", "scope": "user",
                   "load_state": "reported"}],
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


class ParseDeclarationTests(unittest.TestCase):
    def test_valid_declaration_normalized(self):
        d = parse_declaration(_decl())
        self.assertEqual(d["schema_version"], 1)
        self.assertEqual(d["client"], "example-agent")
        self.assertEqual(d["observed_by"], "model")
        self.assertFalse(d["complete"])
        self.assertEqual(len(d["roots"]), 1)
        self.assertTrue(d["roots"][0]["path"].startswith("/"),
                        "~ 必须展开为绝对路径")
        self.assertEqual(d["roots"][0]["load_state"], "reported")

    def test_defaults_apply(self):
        d = parse_declaration(json.dumps({
            "schema_version": 1, "client": "tiny", "roots": []}))
        self.assertEqual(d["observed_by"], "model")
        self.assertFalse(d["complete"])
        self.assertEqual(d["roots"], [])

    def test_forbidden_keys_rejected_everywhere(self):
        for payload in (
            _decl(mutable=True),
            _decl(instance_id="abc"),
            _decl(tree_hash="0" * 64),
            _decl(command="rm -rf /"),
            _decl(url="https://example.com"),
            _decl(token="FAKE-SECRET-000"),
            _decl(env={"A": "B"}),
            json.dumps({"schema_version": 1, "client": "c", "roots":
                        [{"path": "/tmp/x", "mutable": True}]}),
            json.dumps({"schema_version": 1, "client": "c", "roots":
                        [{"path": "/tmp/x", "instance_id": "i-1"}]}),
            json.dumps({"schema_version": 1, "client": "c", "roots":
                        [{"path": "/tmp/x", "tree_hash": "0" * 64}]}),
            json.dumps({"schema_version": 1, "client": "c", "roots":
                        [{"path": "/tmp/x", "note": "多余键"}]}),
        ):
            with self.assertRaises(LocationInputError, msg=payload[:60]):
                parse_declaration(payload)

    def test_error_messages_never_echo_values(self):
        secret = "SUPER-SECRET-VALUE-000"
        with self.assertRaises(LocationInputError) as ctx:
            parse_declaration(json.dumps({"schema_version": 1, "client": "c",
                                          "token": secret, "roots": []}))
        self.assertNotIn(secret, str(ctx.exception))
        with self.assertRaises(LocationInputError) as ctx2:
            parse_declaration(json.dumps({"schema_version": 1, "client": "c" * 5000,
                                          "roots": []}))
        self.assertNotIn("c" * 100, str(ctx2.exception))

    def test_size_and_count_limits(self):
        with self.assertRaises(LocationInputError):
            parse_declaration(_decl(client="a" * 5000))
        with self.assertRaises(LocationInputError):
            parse_declaration(json.dumps({
                "schema_version": 1, "client": "c",
                "roots": [{"path": "/tmp/" + "x" * 5000}]}))
        many = {"schema_version": 1, "client": "c",
                "roots": [{"path": "/tmp/r{}".format(i)} for i in range(MAX_ROOTS + 1)]}
        with self.assertRaises(LocationInputError):
            parse_declaration(json.dumps(many))
        # 总字节上限:32 个根 × ~2.1KiB > 64KiB
        big = {"schema_version": 1, "client": "c",
               "roots": [{"path": "/tmp/" + "y" * 2100} for _ in range(MAX_ROOTS)]}
        with self.assertRaises(LocationInputError):
            parse_declaration(json.dumps(big))
        # bytes 输入同样受限
        with self.assertRaises(LocationInputError):
            parse_declaration(b'{"schema_version": 1, "client": "c", "roots": []} '
                              b'* ' + b'#' * MAX_DECL_BYTES)

    def test_non_utf8_bytes_rejected(self):
        with self.assertRaises(LocationInputError):
            parse_declaration(b'{"client": "\xff\xfe"}')

    def test_deep_nesting_rejected_not_crash(self):
        with self.assertRaises(LocationInputError):
            parse_declaration("[" * 50000 + "]" * 50000)

    def test_structural_type_errors(self):
        for payload in (
            "not json",
            "[]",
            _decl(schema_version=2),
            _decl(schema_version=True),
            _decl(observed_by="user"),
            _decl(complete="yes"),
            _decl(roots="x"),
            _decl(roots=[("path",)]),
            _decl(roots=[{"scope": "system"}]),        # path 缺失
            _decl(roots=[{"path": "relative/path"}]),
            _decl(roots=[{"path": "~/x", "scope": "system"}]),
            _decl(roots=[{"path": "~/x", "load_state": "confirmed"}]),
            _decl(roots=[{"path": "~/x", "load_state": "active"}]),
            _decl(client="bad client"),
            _decl(client="a/b"),
            _decl(client=""),
        ):
            with self.assertRaises(LocationInputError, msg=payload[:60]):
                parse_declaration(payload)

    def test_parse_never_touches_filesystem(self):
        """声明指向不存在/无权路径也能解析(打开文件是扫描器稍后的事)。"""
        d = parse_declaration(_decl(roots=[{"path": "/nonexistent/skills-fixture-x"}]))
        self.assertEqual(d["roots"][0]["path"], "/nonexistent/skills-fixture-x")


class ParseCliRootsTests(unittest.TestCase):
    def test_valid_pairs(self):
        roots = parse_cli_roots(["my-agent=~/.my-agent/skills"])
        self.assertEqual(roots[0]["client"], "my-agent")
        self.assertEqual(roots[0]["scope"], "user")
        self.assertEqual(roots[0]["load_state"], "reported")
        self.assertTrue(os.path.isabs(roots[0]["path"]))

    def test_rejects_bad_pairs(self):
        for bad in ("no-equals", "=only-path", "bad name=/tmp/x", "/tmp/only-path",
                    "client=relative/path", "client=", "a=b=c"):
            with self.assertRaises(LocationInputError, msg=bad):
                parse_cli_roots([bad])

    def test_root_count_limit(self):
        with self.assertRaises(LocationInputError):
            parse_cli_roots(["c{}=/tmp/x".format(i) for i in range(MAX_ROOTS + 1)])
