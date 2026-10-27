"""Unit tests for the pure parsing / formatting code (no hardware needed)."""
import unittest

from . import helpers  # noqa: F401
from pulse.collectors.cpu import CpuTimes, parse_cache_size, parse_proc_stat
from pulse.collectors.disk import VolumeInfo, parse_block_stat, parse_udev_data, unescape_udev
from pulse.collectors.gpu_drm import _max_dpm_mhz, _parse_link_speed, parse_fdinfo
from pulse.collectors.gpu_nvidia import _normalize_bus_id
from pulse.collectors.memory import parse_meminfo, parse_smbios_type17
from pulse.collectors.network import parse_if_inet6, parse_iw_link, parse_proc_net_dev
from pulse.collectors.processes import parse_stat_line
from pulse.desktop_entries import app_id_from_cgroup, exec_basename
from pulse.ui.graph import nice_ceiling
from pulse.ui.udisks import block_path, is_ntfs_dirty_error, ntfsfix_command
from pulse.util import (fmt_bits_rate, fmt_bytes, fmt_duration, fmt_freq, fmt_percent, fmt_rate,
                        parse_size_with_unit)

PROC_STAT = """cpu  100 10 50 800 20 0 5 0 0 0
cpu0 50 5 25 400 10 0 3 0 0 0
cpu1 50 5 25 400 10 0 2 0 0 0
intr 123 0 0
ctxt 987654
btime 1700000000
processes 4242
"""

MEMINFO = """MemTotal:       16000000 kB
MemFree:         2000000 kB
MemAvailable:   10000000 kB
Buffers:          500000 kB
Cached:          6000000 kB
SwapTotal:       8000000 kB
SwapFree:        7000000 kB
Dirty:              1000 kB
Writeback:           500 kB
Shmem:            300000 kB
SReclaimable:     400000 kB
Committed_AS:   12000000 kB
HugePages_Total:       0
Hugepagesize:       2048 kB
"""

NET_DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 1000 10 0 0 0 0 0 0 1000 10 0 0 0 0 0 0
  eth0: 5000000 4000 1 2 0 0 0 0 250000 3000 3 4 0 0 0 0
"""

FDINFO_AMD = """pos:\t0
flags:\t02100002
mnt_id:\t27
ino:\t1210
drm-driver:\tamdgpu
drm-client-id:\t91
drm-pdev:\t0000:10:00.0
drm-engine-gfx:\t5000000000 ns
drm-engine-compute:\t1000000000 ns
drm-engine-enc:\t0 ns
drm-memory-vram:\t12 KiB
drm-memory-gtt: \t2048 KiB
"""

FDINFO_I915 = """drm-driver:\ti915
drm-client-id:\t7
drm-pdev:\t0000:00:02.0
drm-engine-render:\t123456789 ns
drm-engine-video:\t42 ns
drm-total-system0:\t64 MiB
"""

FDINFO_XE = """drm-driver:\txe
drm-client-id:\t12
drm-pdev:\t0000:03:00.0
drm-cycles-rcs:\t5000
drm-total-cycles-rcs:\t10000
drm-resident-local0:\t256 MiB
"""


class FormatTests(unittest.TestCase):
    def test_fmt_bytes(self):
        self.assertEqual(fmt_bytes(0), "0 B")
        self.assertEqual(fmt_bytes(1023), "1023 B")
        self.assertEqual(fmt_bytes(1536), "1.5 KiB")
        self.assertEqual(fmt_bytes(3 * 1024 ** 3), "3.0 GiB")
        self.assertEqual(fmt_bytes(None), "—")

    def test_fmt_rate_and_bits(self):
        self.assertEqual(fmt_rate(2048), "2.0 KiB/s")
        self.assertEqual(fmt_bits_rate(866_000_000), "866 Mbps")
        self.assertEqual(fmt_bits_rate(2_500_000_000), "2.5 Gbps")

    def test_fmt_misc(self):
        self.assertEqual(fmt_freq(3251.4), "3.25 GHz")
        self.assertEqual(fmt_freq(800), "800 MHz")
        self.assertEqual(fmt_percent(0.4567), "46%")
        self.assertEqual(fmt_percent(0.4567, 1), "45.7%")
        self.assertEqual(fmt_duration(90061), "1d 01:01:01")
        self.assertEqual(fmt_duration(61), "00:01:01")

    def test_parse_size_with_unit(self):
        self.assertEqual(parse_size_with_unit("12 KiB"), 12 * 1024)
        self.assertEqual(parse_size_with_unit("2048 KiB"), 2048 * 1024)
        self.assertEqual(parse_size_with_unit("3 MiB"), 3 * 1024 ** 2)
        self.assertEqual(parse_size_with_unit("0"), 0)
        self.assertIsNone(parse_size_with_unit(""))

    def test_nice_ceiling(self):
        self.assertEqual(nice_ceiling(0), 1.0)
        self.assertEqual(nice_ceiling(1.0), 1.0)
        self.assertEqual(nice_ceiling(1.1), 2.0)
        self.assertEqual(nice_ceiling(2.3), 2.5)
        self.assertEqual(nice_ceiling(7_000_000), 10_000_000)
        self.assertEqual(nice_ceiling(2_400_000), 2_500_000)


class CpuParserTests(unittest.TestCase):
    def test_parse_proc_stat(self):
        total, cores, ctxt = parse_proc_stat(PROC_STAT)
        self.assertEqual(total.user, 100)
        self.assertEqual(total.idle, 800)
        self.assertEqual(total.total, 985)
        self.assertEqual(total.busy, 985 - 800 - 20)
        self.assertEqual(len(cores), 2)
        self.assertEqual(cores[1].softirq, 2)
        self.assertEqual(ctxt, 987654)

    def test_proc_stat_cores_are_sorted(self):
        text = "cpu 1 0 0 0 0 0 0 0 0 0\ncpu10 1 0 0 0 0 0 0 0 0 0\ncpu2 2 0 0 0 0 0 0 0 0 0\n"
        _, cores, _ = parse_proc_stat(text)
        self.assertEqual([c.user for c in cores], [2, 1])

    def test_util_math(self):
        prev = CpuTimes(user=100, idle=900)
        cur = CpuTimes(user=150, idle=950)
        from pulse.collectors.cpu import _util
        self.assertAlmostEqual(_util(prev, cur), 0.5)
        self.assertEqual(_util(cur, cur), 0.0)

    def test_cache_size(self):
        self.assertEqual(parse_cache_size("48K"), 48 * 1024)
        self.assertEqual(parse_cache_size("32768K"), 32 * 1024 * 1024)
        self.assertEqual(parse_cache_size("2M"), 2 * 1024 ** 2)
        self.assertIsNone(parse_cache_size(""))


class MemoryParserTests(unittest.TestCase):
    def test_parse_meminfo(self):
        m = parse_meminfo(MEMINFO)
        self.assertEqual(m.total, 16000000 * 1024)
        self.assertEqual(m.used, (16000000 - 10000000) * 1024)
        self.assertEqual(m.swap_used, 1000000 * 1024)
        self.assertEqual(m.modified, 1500 * 1024)
        self.assertEqual(m.standby, (10000000 - 2000000) * 1024)
        self.assertEqual(m.hugepages_total, 0)
        self.assertEqual(m.hugepage_size, 2048 * 1024)

    def test_smbios_type17(self):
        raw = bytearray(0x28)
        raw[0] = 17
        raw[1] = 0x28
        raw[0x0C:0x0E] = (16384).to_bytes(2, "little")     # 16 GiB in MB
        raw[0x0E] = 0x09                                    # DIMM
        raw[0x10] = 1                                       # locator string #1
        raw[0x12] = 0x22                                    # DDR5
        raw[0x15:0x17] = (6000).to_bytes(2, "little")
        raw[0x20:0x22] = (5600).to_bytes(2, "little")
        raw += b"DIMM_A1\x00\x00"
        mod = parse_smbios_type17(bytes(raw))
        self.assertIsNotNone(mod)
        self.assertEqual(mod.size, 16 * 1024 ** 3)
        self.assertEqual(mod.form_factor, "DIMM")
        self.assertEqual(mod.mem_type, "DDR5")
        self.assertEqual(mod.speed_mts, 6000)
        self.assertEqual(mod.configured_mts, 5600)
        self.assertEqual(mod.locator, "DIMM_A1")

    def test_smbios_empty_slot(self):
        raw = bytearray(0x28)
        raw[0] = 17
        raw[1] = 0x28
        self.assertIsNone(parse_smbios_type17(bytes(raw)))


class DiskNetParserTests(unittest.TestCase):
    def test_block_stat(self):
        st = parse_block_stat("1990979 56159 318499695 373655 6481667 3248477 1390654682 18080992 0 1246630 18656350 66357 0 6823500944 93244 135241 108457")
        self.assertEqual(st.reads, 1990979)
        self.assertEqual(st.sectors_read, 318499695)
        self.assertEqual(st.sectors_written, 1390654682)
        self.assertEqual(st.io_ticks, 1246630)

    def test_proc_net_dev(self):
        d = parse_proc_net_dev(NET_DEV)
        self.assertIn("eth0", d)
        self.assertEqual(d["eth0"].rx_bytes, 5000000)
        self.assertEqual(d["eth0"].tx_bytes, 250000)
        self.assertEqual(d["eth0"].rx_errs, 1)
        self.assertEqual(d["eth0"].tx_drop, 4)
        self.assertEqual(d["lo"].rx_packets, 10)

    def test_if_inet6(self):
        text = "fe80000000000000742ddbfffe56357b 04 40 20 80  docker0\n00000000000000000000000000000001 01 80 10 80       lo\n"
        d = parse_if_inet6(text)
        self.assertEqual(d["docker0"], ["fe80::742d:dbff:fe56:357b/64"])
        self.assertEqual(d["lo"], ["::1/128"])

    def test_iw_link(self):
        text = """Connected to aa:bb:cc:dd:ee:ff (on wlp6s0)
\tSSID: Home 5G
\tfreq: 5220.0
\tRX: 123 bytes (4 packets)
\tsignal: -61 dBm
\trx bitrate: 866.7 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2
\ttx bitrate: 780.0 MBit/s
"""
        info = parse_iw_link(text)
        self.assertEqual(info["ssid"], "Home 5G")
        self.assertEqual(info["freq_mhz"], 5220)
        self.assertEqual(info["signal_dbm"], -61)
        self.assertEqual(info["rx_bitrate_mbps"], 866.7)
        self.assertEqual(info["tx_bitrate_mbps"], 780.0)


class VolumeParserTests(unittest.TestCase):
    UDEV = (
        "S:disk/by-uuid/4EA02A97A02A8595\n"
        "I:1234\n"
        "E:ID_FS_LABEL=Seagate_Expansion_Drive\n"
        "E:ID_FS_LABEL_ENC=Seagate\\x20Expansion\\x20Drive\n"
        "E:ID_FS_UUID=4EA02A97A02A8595\n"
        "E:ID_FS_TYPE=ntfs\n"
        "E:ID_PART_ENTRY_TYPE=0x7\n"
        "G:systemd\n"
    )

    def test_parse_udev_data(self):
        props = parse_udev_data(self.UDEV)
        self.assertEqual(props["ID_FS_TYPE"], "ntfs")
        self.assertEqual(props["ID_PART_ENTRY_TYPE"], "0x7")
        self.assertNotIn("1234", props)
        self.assertEqual(parse_udev_data(""), {})

    def test_unescape_udev(self):
        self.assertEqual(unescape_udev(parse_udev_data(self.UDEV)["ID_FS_LABEL_ENC"]), "Seagate Expansion Drive")
        self.assertEqual(unescape_udev("plain"), "plain")
        self.assertEqual(unescape_udev("a\\x2fb"), "a/b")

    def test_volume_display_name(self):
        self.assertEqual(VolumeInfo("sda1", "ntfs", "Data", "/mnt/d").display_name, "Data")
        self.assertEqual(VolumeInfo("nvme0n1p1", "vfat", "", "/boot/efi").display_name, "/boot/efi")
        self.assertEqual(VolumeInfo("sdb1", "exfat").display_name, "sdb1")
        self.assertFalse(VolumeInfo("sdb1", "exfat").mounted)

    def test_udisks_object_paths(self):
        self.assertEqual(block_path("sda1"), "/org/freedesktop/UDisks2/block_devices/sda1")
        self.assertEqual(block_path("/dev/nvme0n1p2"), "/org/freedesktop/UDisks2/block_devices/nvme0n1p2")
        self.assertEqual(block_path("dm-0"), "/org/freedesktop/UDisks2/block_devices/dm_2d0")

    def test_ntfs_dirty_detection(self):
        msg = ("Error mounting /dev/sda1 at /run/media/v/X: wrong fs type, bad option, bad superblock on "
               "/dev/sda1, missing codepage or helper program, or other error")
        self.assertTrue(is_ntfs_dirty_error("ntfs", msg))
        self.assertTrue(is_ntfs_dirty_error("NTFS", "volume is dirty"))
        self.assertFalse(is_ntfs_dirty_error("ext4", msg))
        self.assertFalse(is_ntfs_dirty_error("ntfs", "Not authorized to perform operation"))
        self.assertEqual(ntfsfix_command("sda1"), ["pkexec", "ntfsfix", "-d", "/dev/sda1"])


class GpuParserTests(unittest.TestCase):
    def test_fdinfo_amd(self):
        c = parse_fdinfo(FDINFO_AMD)
        self.assertEqual(c.pdev, "0000:10:00.0")
        self.assertEqual(c.client_id, "91")
        self.assertEqual(c.engines["gfx"], 5_000_000_000)
        self.assertEqual(c.engines["compute"], 1_000_000_000)
        self.assertEqual(c.vram, 12 * 1024)
        self.assertEqual(c.gtt, 2048 * 1024)

    def test_fdinfo_i915_and_xe(self):
        c = parse_fdinfo(FDINFO_I915)
        self.assertEqual(c.engines["render"], 123456789)
        x = parse_fdinfo(FDINFO_XE)
        self.assertEqual(x.cycles["rcs"], (5000, 10000))
        self.assertEqual(x.vram, 256 * 1024 ** 2)

    def test_fdinfo_non_drm(self):
        self.assertIsNone(parse_fdinfo("pos:\t0\nflags:\t02\n"))

    def test_link_speed_and_dpm(self):
        self.assertEqual(_parse_link_speed("16.0 GT/s PCIe"), 4)
        self.assertEqual(_parse_link_speed("32.0 GT/s PCIe"), 5)
        self.assertIsNone(_parse_link_speed("Unknown"))
        self.assertEqual(_max_dpm_mhz("0: 600Mhz *\n1: 700Mhz\n2: 2200Mhz\n"), 2200.0)

    def test_nvml_bus_id(self):
        self.assertEqual(_normalize_bus_id("00000000:01:00.0"), "0000:01:00.0")
        self.assertEqual(_normalize_bus_id("0000:0A:00.0"), "0000:0a:00.0")


class ProcessParserTests(unittest.TestCase):
    def test_stat_line_with_parens_in_comm(self):
        line = "1234 (my (weird) proc) S 1 1234 1234 0 -1 4194560 100 0 0 0 11 12 0 0 20 0 7 0 99 1000 50 18446744073709551615"
        comm, f = parse_stat_line(line)
        self.assertEqual(comm, "my (weird) proc")
        self.assertEqual(f[0], "S")
        self.assertEqual(int(f[1]), 1)
        self.assertEqual(int(f[11]), 11)
        self.assertEqual(int(f[12]), 12)
        self.assertEqual(int(f[17]), 7)
        self.assertEqual(int(f[19]), 99)
        self.assertEqual(int(f[21]), 50)

    def test_stat_line_garbage(self):
        self.assertIsNone(parse_stat_line("not a stat line"))
        self.assertIsNone(parse_stat_line("1 (x) S 1"))


class DesktopEntryTests(unittest.TestCase):
    def test_app_id_from_cgroup(self):
        base = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
        self.assertEqual(app_id_from_cgroup(base + "app-gnome-firefox-1234.scope"), "firefox")
        self.assertEqual(app_id_from_cgroup(base + "app-com.google.Chrome-12083.scope"), "com.google.Chrome")
        self.assertEqual(app_id_from_cgroup(base + "app-flatpak-org.mozilla.firefox-5555.scope"), "org.mozilla.firefox")
        self.assertEqual(app_id_from_cgroup(base + "snap.firefox.firefox-3f2a.scope"), "firefox_firefox")
        self.assertEqual(app_id_from_cgroup(base + "app-gnome-gnome\\x2dterminal-1234.scope"), "gnome-terminal")
        self.assertIsNone(app_id_from_cgroup("0::/user.slice/user-1000.slice/user@1000.service/session.slice/org.gnome.Shell@ubuntu.service"))
        self.assertIsNone(app_id_from_cgroup("0::/system.slice/docker.service"))

    def test_exec_basename(self):
        self.assertEqual(exec_basename("/usr/bin/firefox %u"), "firefox")
        self.assertEqual(exec_basename("env FOO=bar /opt/app/bin/app --flag"), "app")
        self.assertEqual(exec_basename("flatpak run org.gnome.Calculator"), "org.gnome.Calculator")
        self.assertEqual(exec_basename("sh -c 'exec /usr/bin/foo --bar'"), "foo")
        self.assertEqual(exec_basename("flatpak run --branch=stable --file-forwarding org.gnome.Calculator @@u %U @@"), "org.gnome.Calculator")
        self.assertIsNone(exec_basename(""))


if __name__ == "__main__":
    unittest.main()
