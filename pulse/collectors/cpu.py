"""CPU collector: /proc/stat, cpufreq sysfs, topology, caches and hwmon temperatures."""
from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass, field

from ..util import read_int, read_text

CPU_SYS = "/sys/devices/system/cpu"
HWMON = "/sys/class/hwmon"


@dataclass
class CpuInfo:
    model: str = "Unknown CPU"
    arch: str = ""
    sockets: int = 1
    cores: int = 1
    threads: int = 1
    base_mhz: float | None = None
    max_mhz: float | None = None
    min_mhz: float | None = None
    virtualization: str | None = None
    is_vm: bool = False
    l1d: int | None = None
    l1i: int | None = None
    l2: int | None = None
    l3: int | None = None
    kernel: str = ""
    temp_label: str | None = None


@dataclass
class CpuTimes:
    """Jiffies from a /proc/stat 'cpu' line."""
    user: int = 0
    nice: int = 0
    system: int = 0
    idle: int = 0
    iowait: int = 0
    irq: int = 0
    softirq: int = 0
    steal: int = 0

    @property
    def total(self) -> int:
        return (self.user + self.nice + self.system + self.idle + self.iowait
                + self.irq + self.softirq + self.steal)

    @property
    def busy(self) -> int:
        return self.total - self.idle - self.iowait


@dataclass
class CpuSample:
    ts: float
    utilization: float = 0.0          # 0..1 overall
    per_core: list[float] = field(default_factory=list)
    freq_mhz: float | None = None     # average current frequency
    per_core_freq: list[float | None] = field(default_factory=list)
    user: float = 0.0                 # fractions of total time
    system: float = 0.0
    iowait: float = 0.0
    irq: float = 0.0
    threads: int = 0
    handles: int = 0
    uptime: float = 0.0
    temp_c: float | None = None
    load: tuple[float, float, float] = (0.0, 0.0, 0.0)
    context_switches_ps: float | None = None


def parse_proc_stat(text: str) -> tuple[CpuTimes, list[CpuTimes], int | None]:
    """Parse /proc/stat into (total, per-core list, ctxt)."""
    total = CpuTimes()
    cores: list[tuple[int, CpuTimes]] = []
    ctxt = None
    for line in text.splitlines():
        if line.startswith("cpu"):
            parts = line.split()
            nums = [int(x) for x in parts[1:9]]
            nums += [0] * (8 - len(nums))
            times = CpuTimes(*nums)
            if parts[0] == "cpu":
                total = times
            else:
                try:
                    cores.append((int(parts[0][3:]), times))
                except ValueError:
                    continue
        elif line.startswith("ctxt "):
            try:
                ctxt = int(line.split()[1])
            except (ValueError, IndexError):
                ctxt = None
    cores.sort(key=lambda t: t[0])
    return total, [t for _, t in cores], ctxt


def _util(prev: CpuTimes, cur: CpuTimes) -> float:
    dt = cur.total - prev.total
    if dt <= 0:
        return 0.0
    return max(0.0, min(1.0, (cur.busy - prev.busy) / dt))


def parse_cache_size(text: str) -> int | None:
    """'48K' -> 49152, '1024K', '32768K', '2M'."""
    m = re.match(r"^\s*(\d+)\s*([KMG]?)", text or "")
    if not m:
        return None
    n = int(m.group(1))
    return n * {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[m.group(2)]


class CpuCollector:
    def __init__(self):
        self.info = self._read_static()
        self._prev_total: CpuTimes | None = None
        self._prev_cores: list[CpuTimes] = []
        self._prev_ctxt: int | None = None
        self._prev_ts: float | None = None
        self._freq_paths = self._discover_freq_paths()
        self._temp_path, self.info.temp_label = self._discover_temp_sensor()
        self.ncpu = self.info.threads

    # -- static information ------------------------------------------------
    def _read_static(self) -> CpuInfo:
        info = CpuInfo(arch=platform.machine(), kernel=platform.release())
        cpuinfo = read_text("/proc/cpuinfo", "") or ""
        flags: set[str] = set()
        for line in cpuinfo.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if key in ("model name", "Model", "cpu model", "Hardware") and info.model == "Unknown CPU":
                info.model = value
            elif key in ("flags", "Features") and not flags:
                flags = set(value.split())
        if "svm" in flags:
            info.virtualization = "AMD-V"
        elif "vmx" in flags:
            info.virtualization = "VT-x"
        info.is_vm = "hypervisor" in flags

        # topology
        packages: set[int] = set()
        cores: set[tuple[int, int]] = set()
        threads = 0
        try:
            for entry in os.listdir(CPU_SYS):
                if not re.fullmatch(r"cpu\d+", entry):
                    continue
                online = read_int(f"{CPU_SYS}/{entry}/online", 1)
                if online == 0:
                    continue
                threads += 1
                pkg = read_int(f"{CPU_SYS}/{entry}/topology/physical_package_id", 0) or 0
                core = read_int(f"{CPU_SYS}/{entry}/topology/core_id", threads) or 0
                packages.add(pkg)
                cores.add((pkg, core))
        except OSError:
            pass
        info.threads = max(1, threads or os.cpu_count() or 1)
        info.cores = max(1, len(cores)) if cores else info.threads
        info.sockets = max(1, len(packages))

        # frequencies (kHz in sysfs)
        base = read_int(f"{CPU_SYS}/cpu0/cpufreq/base_frequency")
        if base:
            info.base_mhz = base / 1000.0
        else:
            m = re.search(r"@\s*([\d.]+)\s*GHz", info.model)
            if m:
                info.base_mhz = float(m.group(1)) * 1000.0
        mx = read_int(f"{CPU_SYS}/cpu0/cpufreq/cpuinfo_max_freq")
        mn = read_int(f"{CPU_SYS}/cpu0/cpufreq/cpuinfo_min_freq")
        info.max_mhz = mx / 1000.0 if mx else None
        info.min_mhz = mn / 1000.0 if mn else None

        # caches: sum unique (level, type, id) across all cpus
        seen: set[tuple[int, str, int]] = set()
        totals = {"l1d": 0, "l1i": 0, "l2": 0, "l3": 0}
        try:
            for entry in os.listdir(CPU_SYS):
                if not re.fullmatch(r"cpu\d+", entry):
                    continue
                cache_dir = f"{CPU_SYS}/{entry}/cache"
                try:
                    indexes = os.listdir(cache_dir)
                except OSError:
                    continue
                for idx in indexes:
                    if not idx.startswith("index"):
                        continue
                    level = read_int(f"{cache_dir}/{idx}/level")
                    ctype = read_text(f"{cache_dir}/{idx}/type", "") or ""
                    cid = read_int(f"{cache_dir}/{idx}/id")
                    size = parse_cache_size(read_text(f"{cache_dir}/{idx}/size", "") or "")
                    if level is None or size is None:
                        continue
                    if cid is None:
                        # no id attribute: use shared_cpu_list as identity
                        shared = read_text(f"{cache_dir}/{idx}/shared_cpu_list", entry) or entry
                        cid = hash(shared)
                    key = (level, ctype, cid)
                    if key in seen:
                        continue
                    seen.add(key)
                    if level == 1 and ctype == "Data":
                        totals["l1d"] += size
                    elif level == 1 and ctype == "Instruction":
                        totals["l1i"] += size
                    elif level == 2:
                        totals["l2"] += size
                    elif level == 3:
                        totals["l3"] += size
        except OSError:
            pass
        info.l1d = totals["l1d"] or None
        info.l1i = totals["l1i"] or None
        info.l2 = totals["l2"] or None
        info.l3 = totals["l3"] or None
        return info

    def _discover_freq_paths(self) -> list[str | None]:
        paths: list[str | None] = []
        for i in range(self.info.threads):
            p = f"{CPU_SYS}/cpu{i}/cpufreq/scaling_cur_freq"
            paths.append(p if os.path.exists(p) else None)
        return paths

    @staticmethod
    def _discover_temp_sensor() -> tuple[str | None, str | None]:
        """Pick the best CPU temperature input from hwmon."""
        preferred_drivers = ["k10temp", "zenpower", "coretemp", "cpu_thermal", "soc_thermal", "acpitz"]
        preferred_labels = ["Tctl", "Tdie", "Package id 0", "Physical id 0", "Core 0", "CPU"]
        candidates: dict[str, list[tuple[str, str]]] = {}
        try:
            hwmons = os.listdir(HWMON)
        except OSError:
            hwmons = []
        for hw in hwmons:
            name = read_text(f"{HWMON}/{hw}/name", "") or ""
            if name not in preferred_drivers:
                continue
            try:
                files = os.listdir(f"{HWMON}/{hw}")
            except OSError:
                continue
            for f in files:
                if re.fullmatch(r"temp\d+_input", f):
                    label = read_text(f"{HWMON}/{hw}/{f[:-6]}_label", "") or ""
                    candidates.setdefault(name, []).append((f"{HWMON}/{hw}/{f}", label))
        for drv in preferred_drivers:
            inputs = candidates.get(drv)
            if not inputs:
                continue
            for pref in preferred_labels:
                for path, label in inputs:
                    if label == pref:
                        return path, f"{drv} {label}".strip()
            inputs.sort()
            path, label = inputs[0]
            return path, f"{drv} {label}".strip()
        # thermal zones as a last resort
        try:
            for tz in sorted(os.listdir("/sys/class/thermal")):
                if not tz.startswith("thermal_zone"):
                    continue
                ttype = read_text(f"/sys/class/thermal/{tz}/type", "") or ""
                if ttype in ("x86_pkg_temp", "cpu-thermal", "cpu_thermal", "soc_thermal"):
                    return f"/sys/class/thermal/{tz}/temp", ttype
        except OSError:
            pass
        return None, None

    # -- dynamic sampling ----------------------------------------------------
    def sample(self, ts: float) -> CpuSample:
        s = CpuSample(ts=ts)
        text = read_text("/proc/stat", "") or ""
        total, cores, ctxt = parse_proc_stat(text)
        if self._prev_total is not None:
            s.utilization = _util(self._prev_total, total)
            dt = total.total - self._prev_total.total
            if dt > 0:
                p = self._prev_total
                s.user = max(0.0, (total.user + total.nice - p.user - p.nice) / dt)
                s.system = max(0.0, (total.system - p.system) / dt)
                s.iowait = max(0.0, (total.iowait - p.iowait) / dt)
                s.irq = max(0.0, (total.irq + total.softirq - p.irq - p.softirq) / dt)
            n = min(len(cores), len(self._prev_cores))
            s.per_core = [_util(self._prev_cores[i], cores[i]) for i in range(n)]
            if ctxt is not None and self._prev_ctxt is not None and self._prev_ts:
                wall = ts - self._prev_ts
                if wall > 0:
                    s.context_switches_ps = (ctxt - self._prev_ctxt) / wall
        else:
            s.per_core = [0.0] * len(cores)
        self._prev_total, self._prev_cores, self._prev_ctxt, self._prev_ts = total, cores, ctxt, ts

        freqs: list[float | None] = []
        acc = 0.0
        cnt = 0
        for p in self._freq_paths:
            khz = read_int(p) if p else None
            if khz:
                freqs.append(khz / 1000.0)
                acc += khz / 1000.0
                cnt += 1
            else:
                freqs.append(None)
        if cnt == 0:
            # fall back to /proc/cpuinfo "cpu MHz"
            cpuinfo = read_text("/proc/cpuinfo", "") or ""
            freqs = []
            for line in cpuinfo.splitlines():
                if line.startswith("cpu MHz"):
                    try:
                        v = float(line.split(":")[1])
                        freqs.append(v)
                        acc += v
                        cnt += 1
                    except (ValueError, IndexError):
                        pass
        s.per_core_freq = freqs
        s.freq_mhz = acc / cnt if cnt else None

        loadavg = (read_text("/proc/loadavg", "") or "").split()
        if len(loadavg) >= 4:
            try:
                s.load = (float(loadavg[0]), float(loadavg[1]), float(loadavg[2]))
                s.threads = int(loadavg[3].split("/")[1])
            except (ValueError, IndexError):
                pass
        s.handles = read_int("/proc/sys/fs/file-nr", 0) or 0
        uptime = read_text("/proc/uptime", "0") or "0"
        try:
            s.uptime = float(uptime.split()[0])
        except (ValueError, IndexError):
            s.uptime = 0.0
        if self._temp_path:
            milli = read_int(self._temp_path)
            if milli is not None:
                s.temp_c = milli / 1000.0
        return s
