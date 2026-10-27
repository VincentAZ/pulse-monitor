"""GUI tests: launches the real application, drives it through every view and checks the screenshots.

Needs a display (Wayland/X11). Skipped when none is available.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from . import helpers

ROOT = helpers.ROOT
HAS_DISPLAY = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def _png_size(path):
    with open(path, "rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def _is_blank(path) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False
    img = Image.open(path).convert("L")
    small = img.resize((64, 48))
    pixels = list(small.getdata())
    return max(pixels) - min(pixels) < 10


@unittest.skipUnless(HAS_DISPLAY, "no display available")
class ScreenshotTourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pulse-ui-")
        cls.shots = os.path.join(cls.tmp, "shots")
        cfg = os.path.join(cls.tmp, "settings.json")
        env = dict(os.environ, PULSE_LOG="INFO", G_DEBUG="fatal-criticals")
        cls.proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "pulse"), "--config", cfg, "--interval", "0.25",
             "--tour-delay", "5", "--screenshot-dir", cls.shots],
            capture_output=True, text=True, timeout=180, env=env, cwd=ROOT)
        cls.stderr = "\n".join(line for line in cls.proc.stderr.splitlines()
                               if "Theme parser" not in line and "Unknown key" not in line)
        keep = os.environ.get("PULSE_KEEP_SHOTS")
        if keep and os.path.isdir(cls.shots):
            os.makedirs(keep, exist_ok=True)
            for f in os.listdir(cls.shots):
                shutil.copy(os.path.join(cls.shots, f), keep)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_exit_code_and_no_python_errors(self):
        self.assertEqual(self.proc.returncode, 0, f"stdout:\n{self.proc.stdout}\nstderr:\n{self.stderr}")
        self.assertNotIn("Traceback", self.stderr, self.stderr)
        self.assertNotIn("CRITICAL", self.stderr, self.stderr)

    def test_every_view_was_captured(self):
        self.assertTrue(os.path.isdir(self.shots), f"no screenshots produced\nstdout:\n{self.proc.stdout}\nstderr:\n{self.stderr}")
        self.assertIn("tour finished", self.proc.stdout)
        self.assertNotIn("FAIL", self.proc.stdout, self.proc.stdout)
        files = sorted(os.listdir(self.shots))
        names = " ".join(files)
        for expected in ("performance-cpu", "performance-cpu-cores", "performance-memory", "performance-disk",
                         "performance-net", "gpu-tab", "processes", "processes-search", "apps"):
            self.assertIn(expected, names, f"missing screenshot for {expected}: {files}")

    def test_screenshots_are_real_images(self):
        self.assertTrue(os.path.isdir(self.shots), "no screenshots produced")
        for f in sorted(os.listdir(self.shots)):
            path = os.path.join(self.shots, f)
            size = _png_size(path)
            self.assertIsNotNone(size, f"{f} is not a PNG")
            self.assertGreater(size[0], 300, f)
            self.assertGreater(size[1], 300, f)
            self.assertGreater(os.path.getsize(path), 10_000, f"{f} suspiciously small")
            self.assertFalse(_is_blank(path), f"{f} looks blank")


@unittest.skipUnless(HAS_DISPLAY, "no display available")
class InProcessWidgetTests(unittest.TestCase):
    """Builds the window in-process and exercises the model logic without a tour."""

    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk
        Adw.init()
        cls.Gtk = Gtk
        from pulse.sampler import Sampler
        from pulse.settings import Settings
        from pulse.ui.window import PulseWindow
        cls.tmp = tempfile.mkdtemp(prefix="pulse-ui2-")
        cls.settings = Settings(os.path.join(cls.tmp, "settings.json"))
        cls.sampler = Sampler(interval_s=0.25, history_points=8, enable_gpu=True)
        cls.app = Adw.Application(application_id="org.pulse.MonitorTest")
        cls.app.register()
        cls.window = PulseWindow(cls.app, cls.sampler, cls.settings)
        cls.snap = cls.sampler.sample_once()
        import time
        time.sleep(0.3)
        cls.snap = cls.sampler.sample_once()
        cls.window.on_snapshot(cls.snap)

    @classmethod
    def tearDownClass(cls):
        cls.sampler.stop()
        cls.window.destroy()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _pump(self):
        from gi.repository import GLib
        ctx = GLib.MainContext.default()
        for _ in range(50):
            if not ctx.iteration(False):
                break

    def test_pages_match_devices(self):
        keys = set(self.window.pages)
        self.assertIn("cpu", keys)
        self.assertIn("memory", keys)
        for d in self.snap.disk_infos:
            self.assertIn(f"disk.{d}", keys)
        for n in self.snap.net_infos:
            self.assertIn(f"net.{n}", keys)
        for g in self.snap.gpu_infos:
            self.assertIn(f"gpu.{g}", keys)
        self.assertEqual(set(self.window.sidebar.rows), keys)

    def test_process_table_matches_snapshot(self):
        pv = self.window.process_view
        self.window.view_stack.set_visible_child_name("processes")
        self.window.on_snapshot(self.snap)
        self._pump()
        self.assertEqual(len(pv.items), len(self.snap.processes))
        self.assertEqual(pv.store.get_n_items(), len(self.snap.processes))
        shown = pv.sort_model.get_n_items()
        kernel = sum(1 for p in self.snap.processes.values() if p.kernel)
        self.assertEqual(shown, len(self.snap.processes) - kernel)
        pv.set_show_kernel(True)
        self.assertEqual(pv.sort_model.get_n_items(), len(self.snap.processes))
        pv.set_show_kernel(False)
        # default sort is CPU descending
        first = pv.sort_model.get_item(0)
        self.assertGreaterEqual(first.cpu, pv.sort_model.get_item(1).cpu)
        pv.sort_by("memory", descending=True)
        self._pump()
        top = pv.sort_model.get_item(0)
        self.assertEqual(top.memory, max(p.rss for p in self.snap.processes.values() if not p.kernel))

    def test_search_filter(self):
        pv = self.window.process_view
        me = os.getpid()
        pv.set_search(str(me))
        self._pump()
        pids = {pv.sort_model.get_item(i).pid for i in range(pv.sort_model.get_n_items())}
        self.assertIn(me, pids)
        self.assertLess(len(pids), 5)
        pv.set_search("zzz-no-such-process-zzz")
        self.assertEqual(pv.sort_model.get_n_items(), 0)
        pv.set_search("")
        self.assertGreater(pv.sort_model.get_n_items(), 10)

    def test_dead_processes_are_removed_from_table(self):
        pv = self.window.process_view
        self.window.view_stack.set_visible_child_name("processes")
        child = subprocess.Popen(["sleep", "30"])
        try:
            snap = self.sampler.sample_once()
            self.window.on_snapshot(snap)
            self._pump()
            self.assertIn(child.pid, pv.items)
        finally:
            child.kill()
            child.wait()
        snap = self.sampler.sample_once()
        self.window.on_snapshot(snap)
        self._pump()
        self.assertNotIn(child.pid, pv.items)
        self.assertEqual(pv.store.get_n_items(), len(pv.items))

    def test_apps_and_gpu_views(self):
        av = self.window.apps_view
        self.window.view_stack.set_visible_child_name("apps")
        self.window.on_snapshot(self.snap)
        self.assertEqual(len(av.items), len(self.snap.apps))
        gv = self.window.gpu_view
        self.window.view_stack.set_visible_child_name("gpu")
        self.window.on_snapshot(self.snap)
        self.assertEqual(len(gv.cards), len(self.snap.gpu_infos))
        expected = sum(len(g.processes) for g in self.snap.gpus.values())
        self.assertEqual(len(gv.items), expected)

    def test_settings_change_propagates(self):
        self.settings.set("history_seconds", 30)
        self.assertEqual(self.sampler.history.capacity, self.settings.history_points)
        self.assertEqual(self.window.pages["cpu"].graph.points, self.settings.history_points)
        self.settings.set("interval_ms", 500)
        self.assertEqual(self.sampler.interval, 0.5)
        self.settings.set("show_kernel_threads", True)
        self.assertTrue(self.window.process_view.show_kernel)
        self.settings.set("show_kernel_threads", False)

    def test_disk_page_volume_buttons(self):
        from unittest import mock
        import pulse.ui.pages.disk as disk_mod
        from pulse.collectors.disk import VolumeInfo
        from pulse.ui.launch import disks_available
        pages = [p for k, p in self.window.pages.items() if k.startswith("disk.")]
        self.assertTrue(pages, "no disk pages were built")
        for page in pages:
            page.update(self.snap)  # only the visible page is updated by the window
            info = self.snap.disk_infos[page.name]
            self.assertEqual(page.disks_button.get_visible(), disks_available())
            actions = [(b.action, b.volume.device) for b in page.volume_buttons]
            for vol in info.volumes:
                if vol.mounted:
                    self.assertIn(("open", vol.device), actions)
                    self.assertEqual(("unmount", vol.device) in actions, not info.is_system and vol.mountpoint != "/")
                else:
                    self.assertIn(("mount", vol.device), actions)
                    self.assertNotIn(("open", vol.device), actions)
        page = pages[0]
        with mock.patch.object(disk_mod, "open_in_disks", return_value=None) as od, \
             mock.patch.object(self.window, "toast") as toast:
            page.disks_button.emit("clicked")
            od.assert_called_once_with(page.name)
            toast.assert_not_called()
        with mock.patch.object(disk_mod, "open_in_disks", return_value="GNOME Disks is not installed"), \
             mock.patch.object(self.window, "toast") as toast:
            page.disks_button.emit("clicked")
            toast.assert_called_once_with("GNOME Disks is not installed")

    def test_disk_page_mount_flow_with_fake_udisks(self):
        """Mount / unmount / open buttons drive a fake udisks client; errors become dialogs or toasts."""
        from unittest import mock
        import pulse.ui.pages.disk as disk_mod
        from pulse.collectors.disk import VolumeInfo

        class FakeUDisks:
            def __init__(self):
                self.calls = []
                self.mount_result = (None, "/run/media/me/Data")
                self.fix_result = (None, "")

            def mount(self, device, cb):
                self.calls.append(("mount", device))
                cb(*self.mount_result)

            def unmount(self, device, cb):
                self.calls.append(("unmount", device))
                cb(None, "")

            def clear_ntfs_dirty(self, device, cb):
                self.calls.append(("fix", device))
                cb(*self.fix_result)

        page = [p for k, p in self.window.pages.items() if k.startswith("disk.")][0]
        saved_vols, saved_key = page.volumes, list(page.volumes)
        fake = FakeUDisks()
        page.udisks = fake
        try:
            vols = [VolumeInfo("sdz1", "ntfs", "Data"), VolumeInfo("sdz2", "ext4", "", "/mnt/two")]
            page.volumes = None
            page._set_volumes(vols, is_system=False)
            by = {(b.action, b.volume.device): b for b in page.volume_buttons}
            self.assertEqual(set(by), {("mount", "sdz1"), ("open", "sdz2"), ("unmount", "sdz2")})
            self.assertEqual(by[("mount", "sdz1")].get_label(), "Mount Data")
            self.assertEqual(by[("open", "sdz2")].get_label(), "Open /mnt/two")
            # successful mount -> toast and rediscovery requested
            with mock.patch.object(self.window, "toast") as toast, \
                 mock.patch.object(self.sampler.disk, "request_discovery") as redisc, \
                 mock.patch.object(self.sampler, "request_sample") as resample:
                by[("mount", "sdz1")].emit("clicked")
                self.assertEqual(fake.calls[-1], ("mount", "sdz1"))
                toast.assert_called_once()
                self.assertIn("/run/media/me/Data", toast.call_args.args[0])
                redisc.assert_called_once()
                resample.assert_called_once()
            self.assertTrue(by[("mount", "sdz1")].get_sensitive())
            # unmount -> toast
            with mock.patch.object(self.window, "toast") as toast:
                by[("unmount", "sdz2")].emit("clicked")
                self.assertEqual(fake.calls[-1], ("unmount", "sdz2"))
                self.assertIn("Unmounted", toast.call_args.args[0])
            # open -> file manager hand-off with the mount point
            with mock.patch.object(disk_mod, "open_folder", return_value=None) as of:
                by[("open", "sdz2")].emit("clicked")
                self.assertEqual(of.call_args.args[0], "/mnt/two")
            # NTFS dirty-volume failure -> dialog offering the ntfsfix repair, which retries the mount
            fake.mount_result = ("Error mounting /dev/sdz1: wrong fs type, bad option, bad superblock on /dev/sdz1", "")
            page.last_dialog = None
            with mock.patch.object(disk_mod, "ntfsfix_available", return_value=True):
                by[("mount", "sdz1")].emit("clicked")
            dialog = page.last_dialog
            self.assertIsNotNone(dialog)
            self.assertIn("Could not mount Data", dialog.get_heading())
            self.assertIn("dirty", dialog.get_body())
            self.assertTrue(dialog.has_response("fix"))
            dialog.force_close()
            fake.mount_result = (None, "/run/media/me/Data")
            with mock.patch.object(self.window, "toast") as toast:
                page._on_mount_error_response(dialog, "fix", vols[0])
            self.assertEqual(fake.calls[-2:], [("fix", "sdz1"), ("mount", "sdz1")])
            self.assertIn("Mounted Data", toast.call_args.args[0])
            # a non-NTFS failure gets a plain dialog without the repair option
            vols2 = [VolumeInfo("sdz3", "ext4", "Photos")]
            page.volumes = None
            page._set_volumes(vols2, is_system=False)
            fake.mount_result = ("Error mounting /dev/sdz3: bad superblock", "")
            page.last_dialog = None
            page.volume_buttons[0].emit("clicked")
            self.assertFalse(page.last_dialog.has_response("fix"))
            page.last_dialog.force_close()
            # a cancelled password prompt is reported as a toast, not a crash
            fake.fix_result = ("ntfsfix failed: authentication was cancelled", "")
            with mock.patch.object(self.window, "toast") as toast:
                page._on_mount_error_response(None, "fix", vols[0])
            self.assertIn("cancelled", toast.call_args.args[0])
            self.assertTrue(all(b.get_sensitive() for b in page.volume_buttons))
        finally:
            page.udisks = disk_mod.UDisksClient()
            page.volumes = None
            page._set_volumes(self.snap.disk_infos[page.name].volumes, self.snap.disk_infos[page.name].is_system)

    def test_end_process_dialog_cancel(self):
        """Opening the confirmation and cancelling must not signal anything."""
        import signal
        pv = self.window.process_view
        child = subprocess.Popen(["sleep", "30"])
        try:
            pv.signal_pid(child.pid, "sleep", signal.SIGTERM)
            dialog = pv.last_dialog
            self.assertIsNotNone(dialog)
            dialog.emit("response", "cancel")
            self._pump()
            self.assertIsNone(child.poll(), "process must still be alive after cancel")
            pv.signal_pid(child.pid, "sleep", signal.SIGTERM, confirm=False)
            child.wait(timeout=5)
            self.assertEqual(child.returncode, -signal.SIGTERM)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


if __name__ == "__main__":
    unittest.main()
