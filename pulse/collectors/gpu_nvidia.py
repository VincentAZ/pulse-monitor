"""NVIDIA backend: a thin ctypes binding over libnvidia-ml (NVML). No pynvml needed."""
from __future__ import annotations

import ctypes
import threading
import time

from .gpu import GpuInfo, GpuProcess, GpuSample

NVML_SUCCESS = 0
NVML_ERROR_INSUFFICIENT_SIZE = 7
NVML_ERROR_NOT_FOUND = 6
NVML_CLOCK_GRAPHICS = 0
NVML_CLOCK_SM = 1
NVML_CLOCK_MEM = 2
NVML_TEMPERATURE_GPU = 0
NVML_PCIE_UTIL_TX = 0
NVML_PCIE_UTIL_RX = 1
NVML_VALUE_NOT_AVAILABLE = 0xFFFFFFFFFFFFFFFF


class _Utilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _MemoryV2(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint), ("total", ctypes.c_ulonglong), ("reserved", ctypes.c_ulonglong),
                ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _MemoryV1(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("pid", ctypes.c_uint), ("usedGpuMemory", ctypes.c_ulonglong),
                ("gpuInstanceId", ctypes.c_uint), ("computeInstanceId", ctypes.c_uint)]


class _ProcessInfoV1(ctypes.Structure):
    _fields_ = [("pid", ctypes.c_uint), ("usedGpuMemory", ctypes.c_ulonglong)]


class _ProcessUtilization(ctypes.Structure):
    _fields_ = [("pid", ctypes.c_uint), ("timeStamp", ctypes.c_ulonglong), ("smUtil", ctypes.c_uint),
                ("memUtil", ctypes.c_uint), ("encUtil", ctypes.c_uint), ("decUtil", ctypes.c_uint)]


class _PciInfo(ctypes.Structure):
    _fields_ = [("busIdLegacy", ctypes.c_char * 16), ("domain", ctypes.c_uint), ("bus", ctypes.c_uint),
                ("device", ctypes.c_uint), ("pciDeviceId", ctypes.c_uint), ("pciSubSystemId", ctypes.c_uint),
                ("busId", ctypes.c_char * 32)]


def _normalize_bus_id(bus_id: str) -> str:
    """NVML gives 00000000:01:00.0; sysfs uses 0000:01:00.0."""
    bus_id = bus_id.lower()
    dom, _, rest = bus_id.partition(":")
    try:
        return f"{int(dom, 16):04x}:{rest}"
    except ValueError:
        return bus_id


class Nvml:
    """Loads NVML lazily; every call returns None instead of raising when unsupported."""

    def __init__(self):
        self.lib = None
        self._lock = threading.Lock()
        for name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
            try:
                self.lib = ctypes.CDLL(name)
                break
            except OSError:
                continue
        if self.lib is None:
            raise OSError("NVML library not found")
        rc = self._call("nvmlInit_v2") if hasattr(self.lib, "nvmlInit_v2") else self._call("nvmlInit")
        if rc != NVML_SUCCESS:
            raise OSError(f"nvmlInit failed: {rc}")

    def _fn(self, name: str):
        return getattr(self.lib, name, None)

    def _call(self, name: str, *args) -> int:
        fn = self._fn(name)
        if fn is None:
            return -1
        try:
            return int(fn(*args))
        except (ctypes.ArgumentError, OSError):
            return -1

    def shutdown(self) -> None:
        with self._lock:
            self._call("nvmlShutdown")

    # -- helpers ------------------------------------------------------------
    def _string(self, name: str, *args, size: int = 128) -> str | None:
        buf = ctypes.create_string_buffer(size)
        if self._call(name, *args, buf, ctypes.c_uint(size)) == NVML_SUCCESS:
            return buf.value.decode("utf-8", "replace")
        return None

    def _uint(self, name: str, *args) -> int | None:
        v = ctypes.c_uint()
        if self._call(name, *args, ctypes.byref(v)) == NVML_SUCCESS:
            return v.value
        return None

    def device_count(self) -> int:
        return self._uint("nvmlDeviceGetCount_v2") or self._uint("nvmlDeviceGetCount") or 0

    def handle(self, index: int):
        h = ctypes.c_void_p()
        if self._call("nvmlDeviceGetHandleByIndex_v2", ctypes.c_uint(index), ctypes.byref(h)) != NVML_SUCCESS:
            if self._call("nvmlDeviceGetHandleByIndex", ctypes.c_uint(index), ctypes.byref(h)) != NVML_SUCCESS:
                return None
        return h

    def driver_version(self) -> str | None:
        return self._string("nvmlSystemGetDriverVersion", size=96)

    def cuda_version(self) -> str | None:
        v = ctypes.c_int()
        if self._call("nvmlSystemGetCudaDriverVersion_v2", ctypes.byref(v)) == NVML_SUCCESS or \
           self._call("nvmlSystemGetCudaDriverVersion", ctypes.byref(v)) == NVML_SUCCESS:
            return f"{v.value // 1000}.{(v.value % 1000) // 10}"
        return None

    def name(self, h) -> str | None:
        return self._string("nvmlDeviceGetName", h, size=96)

    def vbios(self, h) -> str | None:
        return self._string("nvmlDeviceGetVbiosVersion", h, size=96)

    def pci_bus_id(self, h) -> str | None:
        info = _PciInfo()
        for fn in ("nvmlDeviceGetPciInfo_v3", "nvmlDeviceGetPciInfo_v2", "nvmlDeviceGetPciInfo"):
            if self._call(fn, h, ctypes.byref(info)) == NVML_SUCCESS:
                return _normalize_bus_id(info.busId.decode("ascii", "replace"))
        return None

    def memory(self, h) -> tuple[int, int, int] | None:
        m2 = _MemoryV2()
        m2.version = (2 << 24) | ctypes.sizeof(_MemoryV2)
        if self._call("nvmlDeviceGetMemoryInfo_v2", h, ctypes.byref(m2)) == NVML_SUCCESS:
            return m2.total, m2.used, m2.reserved
        m1 = _MemoryV1()
        if self._call("nvmlDeviceGetMemoryInfo", h, ctypes.byref(m1)) == NVML_SUCCESS:
            return m1.total, m1.used, 0
        return None

    def utilization(self, h) -> tuple[int, int] | None:
        u = _Utilization()
        if self._call("nvmlDeviceGetUtilizationRates", h, ctypes.byref(u)) == NVML_SUCCESS:
            return u.gpu, u.memory
        return None

    def encoder_util(self, h) -> int | None:
        u = ctypes.c_uint()
        p = ctypes.c_uint()
        if self._call("nvmlDeviceGetEncoderUtilization", h, ctypes.byref(u), ctypes.byref(p)) == NVML_SUCCESS:
            return u.value
        return None

    def decoder_util(self, h) -> int | None:
        u = ctypes.c_uint()
        p = ctypes.c_uint()
        if self._call("nvmlDeviceGetDecoderUtilization", h, ctypes.byref(u), ctypes.byref(p)) == NVML_SUCCESS:
            return u.value
        return None

    def temperature(self, h) -> int | None:
        return self._uint("nvmlDeviceGetTemperature", h, ctypes.c_uint(NVML_TEMPERATURE_GPU))

    def power_mw(self, h) -> int | None:
        return self._uint("nvmlDeviceGetPowerUsage", h)

    def power_limit_mw(self, h) -> int | None:
        return self._uint("nvmlDeviceGetEnforcedPowerLimit", h) or self._uint("nvmlDeviceGetPowerManagementLimit", h)

    def clock(self, h, which: int) -> int | None:
        return self._uint("nvmlDeviceGetClockInfo", h, ctypes.c_uint(which))

    def max_clock(self, h, which: int) -> int | None:
        return self._uint("nvmlDeviceGetMaxClockInfo", h, ctypes.c_uint(which))

    def fan_speed(self, h) -> int | None:
        return self._uint("nvmlDeviceGetFanSpeed", h)

    def pcie_gen(self, h) -> int | None:
        return self._uint("nvmlDeviceGetCurrPcieLinkGeneration", h)

    def pcie_width(self, h) -> int | None:
        return self._uint("nvmlDeviceGetCurrPcieLinkWidth", h)

    def pcie_gen_max(self, h) -> int | None:
        return self._uint("nvmlDeviceGetMaxPcieLinkGeneration", h)

    def pcie_width_max(self, h) -> int | None:
        return self._uint("nvmlDeviceGetMaxPcieLinkWidth", h)

    def pcie_throughput_kbps(self, h, counter: int) -> int | None:
        return self._uint("nvmlDeviceGetPcieThroughput", h, ctypes.c_uint(counter))

    def pstate(self, h) -> int | None:
        return self._uint("nvmlDeviceGetPerformanceState", h)

    def num_cores(self, h) -> int | None:
        return self._uint("nvmlDeviceGetNumGpuCores", h)

    def compute_capability(self, h) -> str | None:
        major = ctypes.c_int()
        minor = ctypes.c_int()
        if self._call("nvmlDeviceGetCudaComputeCapability", h, ctypes.byref(major), ctypes.byref(minor)) == NVML_SUCCESS:
            return f"{major.value}.{minor.value}"
        return None

    def _running_processes(self, h, fn_base: str) -> dict[int, int | None] | None:
        for fn, struct in ((fn_base + "_v3", _ProcessInfo), (fn_base + "_v2", _ProcessInfo), (fn_base, _ProcessInfoV1)):
            if self._fn(fn) is None:
                continue
            count = ctypes.c_uint(0)
            rc = self._call(fn, h, ctypes.byref(count), None)
            if rc == NVML_SUCCESS:
                return {}
            if rc != NVML_ERROR_INSUFFICIENT_SIZE:
                continue
            n = count.value + 16
            arr = (struct * n)()
            count.value = n
            rc = self._call(fn, h, ctypes.byref(count), arr)
            if rc != NVML_SUCCESS:
                continue
            out: dict[int, int | None] = {}
            for i in range(count.value):
                used = arr[i].usedGpuMemory
                out[arr[i].pid] = None if used == NVML_VALUE_NOT_AVAILABLE else int(used)
            return out
        return None

    def compute_processes(self, h):
        return self._running_processes(h, "nvmlDeviceGetComputeRunningProcesses")

    def graphics_processes(self, h):
        return self._running_processes(h, "nvmlDeviceGetGraphicsRunningProcesses")

    def process_utilization(self, h, last_seen_us: int) -> list[_ProcessUtilization] | None:
        if self._fn("nvmlDeviceGetProcessUtilization") is None:
            return None
        count = ctypes.c_uint(0)
        rc = self._call("nvmlDeviceGetProcessUtilization", h, None, ctypes.byref(count), ctypes.c_ulonglong(last_seen_us))
        if rc == NVML_ERROR_NOT_FOUND or (rc == NVML_SUCCESS and count.value == 0):
            return []
        if rc not in (NVML_SUCCESS, NVML_ERROR_INSUFFICIENT_SIZE):
            return None
        n = count.value + 16
        arr = (_ProcessUtilization * n)()
        count.value = n
        rc = self._call("nvmlDeviceGetProcessUtilization", h, arr, ctypes.byref(count), ctypes.c_ulonglong(last_seen_us))
        if rc == NVML_ERROR_NOT_FOUND:
            return []
        if rc != NVML_SUCCESS:
            return None
        return [arr[i] for i in range(count.value)]


class NvidiaCollector:
    """Collects one GpuSample per NVIDIA device. Construct inside try/except: raises if NVML is missing."""

    PROCESS_UTIL_HOLD_S = 2.5

    def __init__(self):
        self.nvml = Nvml()
        self.devices: list[tuple[str, object]] = []   # (key, handle)
        self.infos: dict[str, GpuInfo] = {}
        self._last_seen_us: dict[str, int] = {}
        self._proc_util_cache: dict[str, dict[int, tuple[float, GpuProcess]]] = {}
        driver = self.nvml.driver_version()
        cuda = self.nvml.cuda_version()
        for i in range(self.nvml.device_count()):
            h = self.nvml.handle(i)
            if h is None:
                continue
            key = self.nvml.pci_bus_id(h) or f"nvidia{i}"
            mem = self.nvml.memory(h)
            info = GpuInfo(key=key, vendor="NVIDIA", name=self.nvml.name(h) or f"NVIDIA GPU {i}", driver="nvidia",
                           driver_version=driver, vram_total=mem[0] if mem else None,
                           pcie_gen_max=self.nvml.pcie_gen_max(h), pcie_width_max=self.nvml.pcie_width_max(h),
                           clock_max_mhz=self.nvml.max_clock(h, NVML_CLOCK_SM),
                           mem_clock_max_mhz=self.nvml.max_clock(h, NVML_CLOCK_MEM))
            limit = self.nvml.power_limit_mw(h)
            info.power_limit_w = limit / 1000.0 if limit else None
            info.extra = {"vbios": self.nvml.vbios(h), "cuda": cuda, "cores": self.nvml.num_cores(h),
                          "compute_capability": self.nvml.compute_capability(h), "index": i}
            self.devices.append((key, h))
            self.infos[key] = info
            self._last_seen_us[key] = 0
            self._proc_util_cache[key] = {}

    def close(self) -> None:
        self.nvml.shutdown()

    def sample(self, ts: float) -> dict[str, GpuSample]:
        out: dict[str, GpuSample] = {}
        nv = self.nvml
        for key, h in self.devices:
            s = GpuSample(key=key, ts=ts)
            util = nv.utilization(h)
            if util:
                s.util = util[0] / 100.0
                s.mem_util = util[1] / 100.0
            enc = nv.encoder_util(h)
            dec = nv.decoder_util(h)
            s.enc_util = enc / 100.0 if enc is not None else None
            s.dec_util = dec / 100.0 if dec is not None else None
            mem = nv.memory(h)
            if mem:
                s.vram_total, s.vram_used = mem[0], mem[1]
            s.temp_c = nv.temperature(h)
            p = nv.power_mw(h)
            s.power_w = p / 1000.0 if p is not None else None
            s.power_limit_w = self.infos[key].power_limit_w
            s.clock_mhz = nv.clock(h, NVML_CLOCK_SM)
            s.mem_clock_mhz = nv.clock(h, NVML_CLOCK_MEM)
            s.fan_pct = nv.fan_speed(h)
            s.pcie_gen = nv.pcie_gen(h)
            s.pcie_width = nv.pcie_width(h)
            tx = nv.pcie_throughput_kbps(h, NVML_PCIE_UTIL_TX)
            rx = nv.pcie_throughput_kbps(h, NVML_PCIE_UTIL_RX)
            s.pcie_tx_bps = tx * 1000.0 if tx is not None else None
            s.pcie_rx_bps = rx * 1000.0 if rx is not None else None
            ps = nv.pstate(h)
            s.pstate = f"P{ps}" if ps is not None and ps < 32 else None
            s.processes = self._processes(key, h, ts)
            out[key] = s
        return out

    def _processes(self, key: str, h, ts: float) -> dict[int, GpuProcess]:
        nv = self.nvml
        compute = nv.compute_processes(h) or {}
        graphics = nv.graphics_processes(h) or {}
        procs: dict[int, GpuProcess] = {}
        for pid, mem in compute.items():
            procs[pid] = GpuProcess(pid=pid, kind="Compute", vram=mem, gpu_util=0.0, mem_util=0.0, enc_util=0.0, dec_util=0.0)
        for pid, mem in graphics.items():
            gp = procs.get(pid)
            if gp:
                gp.kind = "Graphics+Compute"
                if gp.vram is None:
                    gp.vram = mem
            else:
                procs[pid] = GpuProcess(pid=pid, kind="Graphics", vram=mem, gpu_util=0.0, mem_util=0.0, enc_util=0.0, dec_util=0.0)

        samples = nv.process_utilization(h, self._last_seen_us[key])
        cache = self._proc_util_cache[key]
        if samples:
            latest: dict[int, _ProcessUtilization] = {}
            newest_ts = self._last_seen_us[key]
            for smp in samples:
                if smp.timeStamp > newest_ts:
                    newest_ts = smp.timeStamp
                cur = latest.get(smp.pid)
                if cur is None or smp.timeStamp >= cur.timeStamp:
                    latest[smp.pid] = smp
            # ask for strictly newer samples next time
            self._last_seen_us[key] = newest_ts
            for pid, smp in latest.items():
                gp = GpuProcess(pid=pid, gpu_util=min(1.0, smp.smUtil / 100.0), mem_util=min(1.0, smp.memUtil / 100.0),
                                enc_util=min(1.0, smp.encUtil / 100.0), dec_util=min(1.0, smp.decUtil / 100.0))
                cache[pid] = (ts, gp)
        elif samples is None and self._last_seen_us[key] == 0:
            pass  # unsupported on this device / driver
        # apply cached utilization, expiring stale entries
        for pid in list(cache):
            seen_ts, gp = cache[pid]
            if ts - seen_ts > self.PROCESS_UTIL_HOLD_S or pid not in procs:
                if pid not in procs:
                    del cache[pid]
                continue
            target = procs[pid]
            target.gpu_util, target.mem_util = gp.gpu_util, gp.mem_util
            target.enc_util, target.dec_util = gp.enc_util, gp.dec_util
        return procs
