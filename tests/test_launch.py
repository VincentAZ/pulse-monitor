"""Hand-off helpers: GNOME Disks and the file manager are launched, never re-implemented."""
import unittest
from unittest import mock

from .helpers import ROOT  # noqa: F401

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from pulse.ui import launch  # noqa: E402


class DisksCommandTests(unittest.TestCase):
    def test_bare_name_gets_dev_prefix(self):
        self.assertEqual(launch.disks_command("nvme0n1"), ["gnome-disks", "--block-device=/dev/nvme0n1"])

    def test_full_path_is_kept(self):
        self.assertEqual(launch.disks_command("/dev/sda"), ["gnome-disks", "--block-device=/dev/sda"])

    def test_open_in_disks_spawns_expected_argv(self):
        calls = []
        with mock.patch.object(launch, "disks_available", return_value=True):
            err = launch.open_in_disks("sda", spawn=calls.append)
        self.assertIsNone(err)
        self.assertEqual(calls, [["gnome-disks", "--block-device=/dev/sda"]])

    def test_open_in_disks_reports_missing_app(self):
        calls = []
        with mock.patch.object(launch, "disks_available", return_value=False):
            err = launch.open_in_disks("sda", spawn=calls.append)
        self.assertEqual(calls, [])
        self.assertIn("not installed", err)

    def test_open_in_disks_reports_spawn_failure(self):
        def boom(argv):
            raise GLib.Error("no such file")
        with mock.patch.object(launch, "disks_available", return_value=True):
            err = launch.open_in_disks("sda", spawn=boom)
        self.assertIn("Could not start GNOME Disks", err)
        with mock.patch.object(launch, "disks_available", return_value=True):
            err = launch.open_in_disks("sda", spawn=mock.Mock(side_effect=OSError("denied")))
        self.assertIn("denied", err)

    def test_disks_available_matches_system(self):
        import shutil
        expected = shutil.which("gnome-disks") is not None or launch.find_disks_app() is not None
        self.assertEqual(launch.disks_available(), expected)


class OpenFolderTests(unittest.TestCase):
    def test_launcher_receives_path(self):
        seen = []
        self.assertIsNone(launch.open_folder("/mnt/data", launcher=seen.append))
        self.assertEqual(seen, ["/mnt/data"])

    def test_launcher_failure_is_reported(self):
        err = launch.open_folder("/mnt/data", launcher=mock.Mock(side_effect=RuntimeError("portal down")))
        self.assertIn("/mnt/data", err)
        self.assertIn("portal down", err)


if __name__ == "__main__":
    unittest.main()
