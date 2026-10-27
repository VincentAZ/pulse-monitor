"""Runs every collector against the real machine and checks the numbers are sane."""
import os
import time
import unittest

from . import helpers  # noqa: F401
from pulse.collectors.cpu import CpuCollector
from pulse.collectors.disk import DiskCollector
from pulse.collectors.memory import MemoryCollector
from pulse.collectors.network import NetworkCollector
from pulse.collectors.processes import ProcessCollector
from pulse.desktop_entries import DesktopIndex


class CpuLiveTests(unittest.TestCase):
    def test_static_info(self):
        c = CpuCollector()
        info = c.info
        self.assertNotEqual(info.model, "Unknown CPU")
        self.assertGreaterEqual(info.threads, info.cores)
        self.assertEqual(info.threads, os.cpu_count())
        self.assertGreaterEqual(info.sockets, 1)
        self.assertTrue(info.kernel)

    def test_dynamic_sample(self):
        c = CpuCollector()
        c.sample(time.time())
        # burn some CPU so utilization is measurable
        end = time.time() + 0.3
        while time.time() < end:
            pass
        s = c.sample(time.time())
        self.assertGreaterEqual(s.utilization, 0.0)
        self.assertLessEqual(s.utilization, 1.0)
        self.assertEqual(len(s.per_core), c.info.threads)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in s.per_core))
        self.assertGreater(s.threads, 10)
        self.assertGreater(s.handles, 0)
        self.assertGreater(s.uptime, 0)
        self.assertGreater(s.load[0], -1)
        if s.freq_mhz is not None:
            self.assertGreater(s.freq_mhz, 100)
        if s.temp_c is not None:
            self.assertGreater(s.temp_c, 0)
            self.assertLess(s.temp_c, 130)


class MemoryLiveTests(unittest.TestCase):
    def test_sample(self):
        m = MemoryCollector().sample(time.time())
        self.assertGreater(m.total, 256 * 1024 ** 2)
        self.assertLessEqual(m.used, m.total)
        self.assertLessEqual(m.available, m.total)
        self.assertLessEqual(m.free, m.available)
        self.assertGreaterEqual(m.swap_used, 0)
        self.assertLessEqual(m.swap_used, m.swap_total)


class DiskLiveTests(unittest.TestCase):
    def test_discovery_and_rates(self):
        d = DiskCollector()
        self.assertTrue(d.disks, "expected at least one physical disk")
        system = [x for x in d.disks.values() if x.is_system]
        self.assertEqual(len(system), 1, "exactly one disk should hold /")
        for info in d.disks.values():
            self.assertGreater(info.capacity, 0)
            self.assertIn(info.kind, ("NVMe", "SSD", "HDD", "Removable", "eMMC / SD"))
            for m in info.mounts:
                self.assertLessEqual(m.used, m.total)
        d.sample(time.time())
        # generate a little write traffic on the system disk
        path = os.path.join(os.path.dirname(__file__), ".io_probe")
        with open(path, "wb") as fh:
            fh.write(os.urandom(4 * 1024 * 1024))
            fh.flush()
            os.fsync(fh.fileno())
        os.remove(path)
        time.sleep(0.3)
        samples = d.sample(time.time())
        self.assertEqual(set(samples), set(d.disks))
        for s in samples.values():
            self.assertGreaterEqual(s.read_bps, 0)
            self.assertGreaterEqual(s.write_bps, 0)
            self.assertGreaterEqual(s.busy, 0)
            self.assertLessEqual(s.busy, 1)
        sysname = system[0].name
        self.assertGreater(samples[sysname].write_bps + samples[sysname].total_written, 0)

    def test_volumes_cover_mounts(self):
        d = DiskCollector()
        for info in d.disks.values():
            mounted = {m.mountpoint for m in info.mounts}
            vol_mps = {v.mountpoint for v in info.volumes if v.mounted}
            self.assertTrue(mounted <= vol_mps | {""}, f"{info.name}: mounts {mounted} not covered by volumes {vol_mps}")
            for v in info.volumes:
                self.assertTrue(v.fstype, f"{info.name}: volume {v.device} without fstype")
                self.assertTrue(v.device.startswith(info.name))
        d.request_discovery()
        self.assertEqual(d._last_discovery, 0.0)


class NetworkLiveTests(unittest.TestCase):
    def test_discovery_and_rates(self):
        n = NetworkCollector()
        self.assertNotIn("lo", n.interfaces)
        n.sample(time.time())
        time.sleep(0.2)
        samples = n.sample(time.time())
        for name, s in samples.items():
            self.assertIn(name, n.interfaces)
            self.assertGreaterEqual(s.rx_bps, 0)
            self.assertGreaterEqual(s.tx_bps, 0)
        for info in n.interfaces.values():
            self.assertIn(info.kind, ("Wi-Fi", "Ethernet", "Bridge", "Bond", "Virtual", "Tunnel", "Other"))
            self.assertTrue(info.mac is not None)
            if info.kind == "Wi-Fi" and info.up and info.ssid:
                self.assertTrue(info.freq_mhz is None or info.freq_mhz > 2000)


class ProcessLiveTests(unittest.TestCase):
    def test_processes_and_apps(self):
        pc = ProcessCollector(os.cpu_count() or 1, DesktopIndex())
        pc.sample(time.time())
        end = time.time() + 0.3
        while time.time() < end:
            pass
        procs = pc.sample(time.time())
        me = procs.get(os.getpid())
        self.assertIsNotNone(me, "our own process must be listed")
        self.assertIn("python", me.name)
        self.assertGreater(me.cpu, 0.0, "we just burned CPU, usage must be > 0")
        self.assertLessEqual(me.cpu, 1.0)
        self.assertGreater(me.rss, 1024 * 1024)
        self.assertEqual(me.uid, os.getuid())
        self.assertTrue(me.cmdline)
        self.assertIn(1, procs)
        self.assertEqual(procs[1].user, "root")
        kernel = [p for p in procs.values() if p.kernel]
        self.assertTrue(kernel, "kernel threads should be detected")
        apps = pc.group_apps(procs)
        for app in apps.values():
            self.assertTrue(app.pids)
            self.assertTrue(app.name)
            self.assertLessEqual(app.cpu, 1.0)

    def test_dead_pid_cache_is_cleaned(self):
        import subprocess
        pc = ProcessCollector(os.cpu_count() or 1, DesktopIndex())
        child = subprocess.Popen(["sleep", "5"])
        procs = pc.sample(time.time())
        self.assertIn(child.pid, procs)
        self.assertEqual(procs[child.pid].name, "sleep")
        child.kill()
        child.wait()
        procs = pc.sample(time.time())
        self.assertNotIn(child.pid, procs)
        self.assertNotIn(child.pid, pc._static)


class GpuLiveTests(unittest.TestCase):
    def test_nvidia_if_present(self):
        try:
            from pulse.collectors.gpu_nvidia import NvidiaCollector
            nv = NvidiaCollector()
        except OSError:
            self.skipTest("NVML not available")
        self.assertTrue(nv.infos)
        nv.sample(time.time())
        time.sleep(0.5)
        samples = nv.sample(time.time())
        for key, s in samples.items():
            info = nv.infos[key]
            self.assertTrue(info.name)
            self.assertRegex(key, r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9]$")
            self.assertIsNotNone(s.util)
            self.assertLessEqual(s.util, 1.0)
            self.assertGreater(s.vram_total, 0)
            self.assertLessEqual(s.vram_used, s.vram_total)
            for pid, gp in s.processes.items():
                self.assertIn(gp.kind, ("Graphics", "Compute", "Graphics+Compute"))
                self.assertTrue(gp.gpu_util is None or 0.0 <= gp.gpu_util <= 1.0)
        nv.close()

    def test_drm_if_present(self):
        from pulse.collectors.gpu_drm import DrmCollector, scan_fdinfo
        drm = DrmCollector()
        if not drm.infos:
            self.skipTest("no DRM GPU")
        t0 = time.monotonic()
        fd = scan_fdinfo()
        self.assertLess(time.monotonic() - t0, 1.0, "fdinfo scan must stay cheap")
        drm.sample(time.time(), fd)
        time.sleep(0.5)
        samples = drm.sample(time.time())
        for key, s in samples.items():
            self.assertIn(key, drm.infos)
            self.assertTrue(s.util is None or 0.0 <= s.util <= 1.0)
            for gp in s.processes.values():
                self.assertTrue(0.0 <= (gp.gpu_util or 0.0) <= 1.0)


if __name__ == "__main__":
    unittest.main()
