"""Settings persistence and validation."""
import json
import os
import tempfile
import unittest

from . import helpers  # noqa: F401
from pulse.settings import DEFAULTS, Settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "sub", "settings.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_defaults_when_missing(self):
        s = Settings(self.path)
        for k, v in DEFAULTS.items():
            self.assertEqual(s.get(k), v)

    def test_roundtrip_and_listeners(self):
        s = Settings(self.path)
        seen = []
        s.connect(lambda k, v: seen.append((k, v)))
        s.set("interval_ms", 500)
        s.set("history_seconds", 120)
        self.assertEqual(seen, [("interval_ms", 500), ("history_seconds", 120)])
        self.assertTrue(os.path.exists(self.path))
        s2 = Settings(self.path)
        self.assertEqual(s2.get("interval_ms"), 500)
        self.assertEqual(s2.get("history_seconds"), 120)
        self.assertEqual(s2.history_points, 240)

    def test_clamping_and_invalid_values(self):
        s = Settings(self.path)
        s.set("interval_ms", 5)
        self.assertEqual(s.get("interval_ms"), 250)
        s.set("interval_ms", 999999)
        self.assertEqual(s.get("interval_ms"), 10000)
        s.set("history_seconds", 77)
        self.assertEqual(s.get("history_seconds"), 60)
        s.set("cpu_view", "bogus")
        self.assertEqual(s.get("cpu_view"), "overall")
        with self.assertRaises(KeyError):
            s.set("nope", 1)

    def test_corrupt_file_is_ignored(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            fh.write("{not json")
        s = Settings(self.path)
        self.assertEqual(s.get("interval_ms"), DEFAULTS["interval_ms"])
        with open(self.path, "w") as fh:
            json.dump({"interval_ms": "string", "history_seconds": 300}, fh)
        s = Settings(self.path)
        self.assertEqual(s.get("interval_ms"), DEFAULTS["interval_ms"])
        self.assertEqual(s.get("history_seconds"), 300)


if __name__ == "__main__":
    unittest.main()
