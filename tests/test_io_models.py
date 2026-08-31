import json, tempfile, unittest
from pathlib import Path
from scripts.core.io import FileLock, atomic_write_json, load_json_checked, read_json_fields
from scripts.core.models import Location, SCHEMA_VERSION

class IoModelTests(unittest.TestCase):
    def test_atomic_json_and_field_allowlist(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            p.write_text(json.dumps({"enabled": True, "token": "DO-NOT-LEAK"}), encoding="utf-8")
            self.assertEqual(read_json_fields(p, {"enabled"}), {"enabled": True})
            atomic_write_json(p, {"schema_version": SCHEMA_VERSION, "ok": True})
            self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["schema_version"], 2)
            self.assertNotIn("DO-NOT-LEAK", p.read_text(encoding="utf-8"))

    def test_location_round_trip(self):
        loc = Location("shared", "shared", "/tmp/home/.agents/skills", "user", True, ("configured",), ("haha",))
        self.assertEqual(Location.from_dict(loc.to_dict()), loc)

    def test_load_json_checked_reports_corruption(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "broken.json"
            p.write_text("{not json", encoding="utf-8")
            value, issues = load_json_checked(p, {})
            self.assertEqual(value, {})
            self.assertTrue(issues)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "good.json"
            p.write_text('{"a": 1}', encoding="utf-8")
            value, issues = load_json_checked(p, {})
            self.assertEqual(value, {"a": 1})
            self.assertEqual(issues, [])

    def test_atomic_write_is_durable_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "state.json"
            atomic_write_json(p, {"schema_version": SCHEMA_VERSION})
            self.assertEqual(sorted(x.name for x in Path(td).iterdir()), ["state.json"])

    def test_read_json_fields_never_leaks_values_in_errors(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cfg.json"
            p.write_text('{"api_key": "SHOULD-NOT-LEAK"}', encoding="utf-8")
            self.assertEqual(read_json_fields(p, set()), {})
            self.assertEqual(read_json_fields(p, {"name"}), {})
            self.assertNotIn("SHOULD-NOT-LEAK", str(read_json_fields(p, {"name"})))

    def test_file_lock_is_exclusive(self):
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / "change.lock"
            with FileLock(lock_path):
                try:
                    with FileLock(lock_path):
                        acquired = True
                except Exception:
                    acquired = False
                self.assertFalse(acquired, "第二个锁持有者必须拿不到锁")


if __name__ == "__main__":
    unittest.main()
