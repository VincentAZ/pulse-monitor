"""DRM backend for amdgpu, i915, xe and friends: sysfs for device stats, fdinfo for per-process usage."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from ..util import parse_size_with_unit, read_int, read_text
from .gpu import PCIE_GEN_BY_GTS, GpuInfo, GpuProcess, GpuSample

SYS_DRM = "/sys/class/drm"
PCI_IDS_PATHS = ("/usr/share/hwdata/pci.ids", "/usr/share/misc/pci.ids", "/usr/share/pci.ids")
VENDORS = {"0x1002": "AMD", "0x8086": "Intel", "0x10de": "NVIDIA", "0x1af4": "Virtio", "0x15ad": "VMware",
           "0x1234": "QEMU", "0x5143": "Qualcomm", "0x14e4": "Broadcom"}

# engine name -> metric bucket, per driver family
ENGINE_MAP = {
    "gfx": "gpu", "render": "gpu", "rcs": "gpu", "3d": "gpu", "compute": "gpu", "ccs": "gpu", "cs": "gpu",
    "enc": "enc", "enc_1": "enc", "vcs": "video", "video": "video", "dec": "dec", "jpeg": "dec",
    "vcn_jpeg": "dec", "vecs": "video", "video-enhance": "video",
}


class PciIds:
    """Lazy pci.ids lookup for device names."""

    def __init__(self):
        self._loaded = False
        self._names: dict[tuple[str, str], str] = {}
        self._vendors: dict[str, str] = {}

    def _load(self) -> None:
        self._loaded = True
        for path in PCI_IDS_PATHS:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    vendor = None
                    for line in fh:
                        if not line or line[0] == "#":
                            continue
                        if line[0] != "\t":
                            parts = line.rstrip("\n").split("  ", 1)
                            if len(parts) == 2 and len(parts[0]) == 4:
                                vendor = parts[0].lower()
                                self._vendors[vendor] = parts[1].strip()
                            else:
                                vendor = None
                        elif line[1] != "\t" and vendor:
                            parts = line.strip().split("  ", 1)
                            if len(parts) == 2:
                                self._names[(vendor, parts[0].lower())] = parts[1].strip()
                return
            except OSError:
                continue

    def device_name(self, vendor: str, device: str) -> str | None:
        if not self._loaded:
            self._load()
        return self._names.get((vendor.lower().replace("0x", ""), device.lower().replace("0x", "")))

    def vendor_name(self, vendor: str) -> str | None:
        if not self._loaded:
            self._load()
        return self._vendors.get(vendor.lower().replace("0x", ""))


@dataclass
class FdinfoClient:
    pdev: str
    engines: dict[str, int] = field(default_factory=dict)   # engine -> busy ns
    cycles: dict[str, tuple[int, int]] = field(default_factory=dict)  # xe: engine -> (cycles, total_cycles)
    vram: int = 0
    gtt: int = 0


def parse_fdinfo(text: str) -> FdinfoClient | None:
    """Parse one /proc/<pid>/fdinfo/<fd> blob for DRM keys."""
    data: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.startswith("drm-"):
            data[key.strip()] = value.strip()
    if "drm-driver" not in data:
        return None
    client = FdinfoClient(pdev=data.get("drm-pdev", ""))
    for key, value in data.items():
        if key.startswith("drm-engine-"):
            name = key[len("drm-engine-"):]
            try:
                client.engines[name] = int(value.split()[0])
            except (ValueError, IndexError):
                pass
        elif key.startswith("drm-cycles-"):
            name = key[len("drm-cycles-"):]
            total = data.get(f"drm-total-cycles-{name}")
            try:
                client.cycles[name] = (int(value.split()[0]), int(total.split()[0]) if total else 0)
            except (ValueError, IndexError):
                pass
    vram = data.get("drm-memory-vram") or data.get("drm-resident-vram") or data.get("drm-total-vram")
    gtt = data.get("drm-memory-gtt") or data.get("drm-resident-gtt") or data.get("drm-total-gtt")
    if vram is None and "drm-resident-local0" in data:   # xe discrete
        vram = data.get("drm-resident-local0")
    if gtt is None and "drm-resident-system" in data:
        gtt = data.get("drm-resident-system")
    client.vram = parse_size_with_unit(vram) or 0 if vram else 0
    client.gtt = parse_size_with_unit(gtt) or 0 if gtt else 0
    client.client_id = data.get("drm-client-id", "")
    return client


@dataclass
class _ProcAgg:
    engines: dict[str, int] = field(default_factory=dict)
    cycles: dict[str, tuple[int, int]] = field(default_factory=dict)
    vram: int = 0
    gtt: int = 0


def scan_fdinfo(pids=None) -> dict[str, dict[int, _ProcAgg]]:
    """Return {pdev: {pid: aggregate}} across all processes holding DRM fds."""
    result: dict[str, dict[int, _ProcAgg]] = {}
    if pids is None:
        try:
            pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
        except OSError:
            return result
    for pid in pids:
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        seen: set[tuple[str, str]] = set()
        for fd in fds:
            try:
                target = os.readlink(f"{fd_dir}/{fd}")
            except OSError:
                continue
            if not target.startswith("/dev/dri/"):
                continue
            text = read_text(f"/proc/{pid}/fdinfo/{fd}")
            if not text:
                continue
            client = parse_fdinfo(text)
            if client is None:
                continue
            ident = (client.pdev, client.client_id)
            if ident in seen:
                continue
            seen.add(ident)
            agg = result.setdefault(client.pdev, {}).setdefault(pid, _ProcAgg())
            for name, ns in client.engines.items():
                agg.engines[name] = agg.engines.get(name, 0) + ns
            for name, pair in client.cycles.items():
                old = agg.cycles.get(name, (0, 0))
                agg.cycles[name] = (old[0] + pair[0], max(old[1], pair[1]))
            agg.vram += client.vram
            agg.gtt += client.gtt
    return result


def _hwmon_dir(device_dir: str) -> str | None:
    try:
        entries = os.listdir(f"{device_dir}/hwmon")
    except OSError:
        return None
    for e in sorted(entries):
        return f"{device_dir}/hwmon/{e}"
    return None


def _parse_link_speed(text: str | None) -> int | None:
    """'16.0 GT/s PCIe' -> 4"""
    if not text:
        return None
    m = re.match(r"\s*([\d.]+)\s*GT/s", text)
    if not m:
        return None
    return PCIE_GEN_BY_GTS.get(float(m.group(1)))


def _current_dpm_mhz(text: str | None) -> float | None:
    """pp_dpm_mclk: '0: 600Mhz *' -> the level marked with '*'"""
    if not text:
        return None
    for line in text.splitlines():
        if "*" in line:
            m = re.search(r"(\d+)\s*Mhz", line, re.IGNORECASE)
            if m:
                return float(m.group(1))
    return None


def _max_dpm_mhz(text: str | None) -> float | None:
    """pp_dpm_sclk: '0: 600Mhz *\n1: 700Mhz\n2: 2200Mhz' -> 2200"""
    if not text:
        return None
    best = None
    for line in text.splitlines():
        m = re.search(r"(\d+)\s*Mhz", line, re.IGNORECASE)
        if m:
            v = float(m.group(1))
            best = v if best is None else max(best, v)
    return best


class DrmCollector:
    """One instance covers every DRM card that is not driven by the proprietary NVIDIA driver."""

    def __init__(self, skip_keys: set[str] | None = None):
        self.pci_ids = PciIds()
        self.infos: dict[str, GpuInfo] = {}
        self._cards: dict[str, str] = {}       # key -> /sys/class/drm/cardN
        self._hwmon: dict[str, str | None] = {}
        self._prev_fdinfo: dict[str, dict[int, tuple[float, _ProcAgg]]] = {}
        self._prev_energy: dict[str, tuple[float, int]] = {}
        self.discover(skip_keys or set())

    def discover(self, skip_keys: set[str]) -> None:
        try:
            cards = sorted(c for c in os.listdir(SYS_DRM) if re.fullmatch(r"card\d+", c))
        except OSError:
            cards = []
        for card in cards:
            base = f"{SYS_DRM}/{card}"
            dev = f"{base}/device"
            uevent = read_text(f"{dev}/uevent", "") or ""
            props = dict(line.split("=", 1) for line in uevent.splitlines() if "=" in line)
            driver = props.get("DRIVER", "")
            slot = props.get("PCI_SLOT_NAME") or props.get("OF_FULLNAME") or card
            if driver in ("nvidia", "nvidia-drm") or slot in skip_keys or not driver:
                continue
            vendor_id = read_text(f"{dev}/vendor", "") or ""
            device_id = read_text(f"{dev}/device", "") or ""
            vendor = VENDORS.get(vendor_id.lower(), self.pci_ids.vendor_name(vendor_id) or "Other")
            name = self.pci_ids.device_name(vendor_id, device_id) if vendor_id and device_id else None
            if not name:
                name = (read_text(f"{dev}/product_name") or f"{vendor} GPU ({driver})")
            info = GpuInfo(key=slot, vendor=vendor, name=name, driver=driver, drm_card=card)
            info.vram_total = read_int(f"{dev}/mem_info_vram_total")
            info.shared_total = read_int(f"{dev}/mem_info_gtt_total")
            info.pcie_gen_max = _parse_link_speed(read_text(f"{dev}/max_link_speed"))
            info.pcie_width_max = read_int(f"{dev}/max_link_width")
            info.clock_max_mhz = _max_dpm_mhz(read_text(f"{dev}/pp_dpm_sclk"))
            info.mem_clock_max_mhz = _max_dpm_mhz(read_text(f"{dev}/pp_dpm_mclk"))
            if driver in ("i915", "xe"):
                mx = read_int(f"{base}/gt_max_freq_mhz") or read_int(f"{dev}/tile0/gt0/freq0/max_freq") \
                    or read_int(f"{base}/gt/gt0/rps_max_freq_mhz")
                info.clock_max_mhz = float(mx) if mx else None
                lmem = read_int(f"{base}/lmem_total_bytes")
                info.vram_total = lmem if lmem else None
            hw = _hwmon_dir(dev)
            if hw:
                cap = read_int(f"{hw}/power1_cap")
                info.power_limit_w = cap / 1_000_000.0 if cap else None
            info.driver_version = read_text(f"/sys/module/{driver}/version")
            self.infos[slot] = info
            self._cards[slot] = base
            self._hwmon[slot] = hw
            self._prev_fdinfo.setdefault(slot, {})

    # ------------------------------------------------------------------
    def sample(self, ts: float, fdinfo: dict[str, dict[int, _ProcAgg]] | None = None) -> dict[str, GpuSample]:
        out: dict[str, GpuSample] = {}
        if fdinfo is None and self.infos:
            fdinfo = scan_fdinfo()
        for key, base in self._cards.items():
            dev = f"{base}/device"
            hw = self._hwmon.get(key)
            info = self.infos[key]
            s = GpuSample(key=key, ts=ts)
            busy = read_int(f"{dev}/gpu_busy_percent")
            s.util = busy / 100.0 if busy is not None else None
            s.vram_used = read_int(f"{dev}/mem_info_vram_used")
            s.vram_total = info.vram_total
            s.shared_used = read_int(f"{dev}/mem_info_gtt_used")
            s.shared_total = info.shared_total
            if hw:
                self._fill_hwmon(s, hw, key, ts)
            if info.driver in ("i915", "xe"):
                cur = read_int(f"{base}/gt_cur_freq_mhz") or read_int(f"{dev}/tile0/gt0/freq0/cur_freq") \
                    or read_int(f"{base}/gt/gt0/rps_cur_freq_mhz")
                s.clock_mhz = float(cur) if cur else s.clock_mhz
            if s.clock_mhz is None:
                s.clock_mhz = _current_dpm_mhz(read_text(f"{dev}/pp_dpm_sclk"))
            if s.mem_clock_mhz is None:
                s.mem_clock_mhz = _current_dpm_mhz(read_text(f"{dev}/pp_dpm_mclk"))
            s.pcie_gen = _parse_link_speed(read_text(f"{dev}/current_link_speed"))
            s.pcie_width = read_int(f"{dev}/current_link_width")
            level = read_text(f"{dev}/power_dpm_force_performance_level")
            s.pstate = level if level else None
            s.processes = self._processes(key, ts, (fdinfo or {}).get(key, {}))
            if s.util is None and s.processes:
                # drivers without gpu_busy_percent: derive from per-process engine time
                s.util = min(1.0, sum(p.gpu_util or 0.0 for p in s.processes.values()))
            if s.enc_util is None and s.processes:
                s.enc_util = min(1.0, sum(p.enc_util or 0.0 for p in s.processes.values()))
                s.dec_util = min(1.0, sum(p.dec_util or 0.0 for p in s.processes.values()))
            out[key] = s
        return out

    def _fill_hwmon(self, s: GpuSample, hw: str, key: str, ts: float) -> None:
        try:
            files = os.listdir(hw)
        except OSError:
            return
        for f in files:
            if re.fullmatch(r"temp\d+_input", f):
                label = read_text(f"{hw}/{f[:-6]}_label", "") or ""
                milli = read_int(f"{hw}/{f}")
                if milli is None:
                    continue
                c = milli / 1000.0
                if label in ("edge", "") and s.temp_c is None:
                    s.temp_c = c
                elif label == "junction":
                    s.hotspot_c = c
                elif label == "mem":
                    s.mem_temp_c = c
                elif s.temp_c is None:
                    s.temp_c = c
        p = read_int(f"{hw}/power1_average")
        if p is None:
            p = read_int(f"{hw}/power1_input")
        if p is not None:
            s.power_w = p / 1_000_000.0
        elif "energy1_input" in files:
            e = read_int(f"{hw}/energy1_input")   # microjoules
            prev = self._prev_energy.get(key)
            if e is not None and prev and ts > prev[0]:
                s.power_w = max(0.0, (e - prev[1]) / 1_000_000.0 / (ts - prev[0]))
            if e is not None:
                self._prev_energy[key] = (ts, e)
        cap = read_int(f"{hw}/power1_cap")
        s.power_limit_w = cap / 1_000_000.0 if cap else self.infos[key].power_limit_w
        f1 = read_int(f"{hw}/freq1_input")
        f2 = read_int(f"{hw}/freq2_input")
        s.clock_mhz = f1 / 1_000_000.0 if f1 else None
        s.mem_clock_mhz = f2 / 1_000_000.0 if f2 else None
        fan = read_int(f"{hw}/fan1_input")
        s.fan_rpm = float(fan) if fan is not None else None
        pwm = read_int(f"{hw}/pwm1")
        s.fan_pct = pwm / 255.0 * 100.0 if pwm is not None else None

    def _processes(self, key: str, ts: float, current: dict[int, _ProcAgg]) -> dict[int, GpuProcess]:
        prev = self._prev_fdinfo.get(key, {})
        procs: dict[int, GpuProcess] = {}
        for pid, agg in current.items():
            gp = GpuProcess(pid=pid, vram=agg.vram or None, shared=agg.gtt or None)
            buckets = {"gpu": 0.0, "enc": 0.0, "dec": 0.0, "video": 0.0}
            kinds: set[str] = set()
            old = prev.get(pid)
            if old is not None and ts > old[0]:
                dt_ns = (ts - old[0]) * 1e9
                for name, ns in agg.engines.items():
                    delta = ns - old[1].engines.get(name, 0)
                    if delta <= 0:
                        continue
                    bucket = ENGINE_MAP.get(name)
                    if bucket is None:
                        bucket = "gpu" if name.startswith(("gfx", "render", "compute", "ccs", "rcs")) else None
                    if bucket:
                        buckets[bucket] += delta / dt_ns
                    if name.startswith(("compute", "ccs")):
                        kinds.add("Compute")
                    elif name.startswith(("gfx", "render", "rcs", "3d")):
                        kinds.add("Graphics")
                for name, (cycles, total) in agg.cycles.items():
                    ocycles, ototal = old[1].cycles.get(name, (0, 0))
                    dtotal = total - ototal
                    if dtotal > 0:
                        frac = max(0.0, (cycles - ocycles) / dtotal)
                        bucket = ENGINE_MAP.get(name, "gpu" if name.startswith(("rcs", "ccs")) else None)
                        if bucket:
                            buckets[bucket] += frac
                        kinds.add("Compute" if name.startswith("ccs") else "Graphics")
            else:
                for name in agg.engines:
                    if name.startswith(("compute", "ccs")):
                        kinds.add("Compute")
                    elif name.startswith(("gfx", "render", "rcs", "3d")):
                        kinds.add("Graphics")
            gp.gpu_util = min(1.0, buckets["gpu"])
            gp.enc_util = min(1.0, buckets["enc"] + buckets["video"])
            gp.dec_util = min(1.0, buckets["dec"] + buckets["video"])
            if "Graphics" in kinds and "Compute" in kinds:
                gp.kind = "Graphics+Compute"
            elif kinds:
                gp.kind = next(iter(kinds))
            else:
                gp.kind = "Graphics"
            procs[pid] = gp
        self._prev_fdinfo[key] = {pid: (ts, agg) for pid, agg in current.items()}
        return procs
