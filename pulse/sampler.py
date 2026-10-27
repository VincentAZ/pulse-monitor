"""Background sampler: runs every collector on a thread, keeps history, hands snapshots to the UI."""
from __future__ import annotations

import logging
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field

from .collectors.cpu import CpuCollector, CpuInfo, CpuSample
from .collectors.disk import DiskCollector, DiskInfo, DiskSample
from .collectors.gpu import GpuInfo, GpuSample
from .collectors.memory import MemoryCollector, MemorySample, MemoryModule
from .collectors.network import NetInfo, NetSample, NetworkCollector
from .collectors.processes import AppSample, ProcessCollector, ProcessSample
from .desktop_entries import DesktopIndex

log = logging.getLogger("pulse.sampler")


class History:
    """Named ring buffers of floats. Thread safe."""

    def __init__(self, capacity: int):
        self._cap = max(2, capacity)
        self._series: dict[str, deque] = {}
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._cap

    def set_capacity(self, capacity: int) -> None:
        capacity = max(2, capacity)
        with self._lock:
            if capacity == self._cap:
                return
            self._cap = capacity
            for key, dq in list(self._series.items()):
                self._series[key] = deque(dq, maxlen=capacity)

    def push(self, key: str, value: float | None) -> None:
        with self._lock:
            dq = self._series.get(key)
            if dq is None:
                dq = self._series[key] = deque(maxlen=self._cap)
            dq.append(0.0 if value is None else float(value))

    def get(self, key: str) -> list[float]:
        with self._lock:
            dq = self._series.get(key)
            return list(dq) if dq else []

    def last(self, key: str, default: float = 0.0) -> float:
        with self._lock:
            dq = self._series.get(key)
            return dq[-1] if dq else default

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._series)

    def drop(self, prefix: str) -> None:
        with self._lock:
            for key in [k for k in self._series if k.startswith(prefix)]:
                del self._series[key]


@dataclass
class Snapshot:
    ts: float
    seq: int
    interval: float
    cpu_info: CpuInfo
    cpu: CpuSample
    memory: MemorySample
    memory_modules: list[MemoryModule]
    disk_infos: dict[str, DiskInfo]
    disks: dict[str, DiskSample]
    net_infos: dict[str, NetInfo]
    nets: dict[str, NetSample]
    gpu_infos: dict[str, GpuInfo]
    gpus: dict[str, GpuSample]
    processes: dict[int, ProcessSample]
    apps: dict[str, AppSample]
    errors: list[str] = field(default_factory=list)
    sample_ms: float = 0.0

    @property
    def process_count(self) -> int:
        return len(self.processes)


class Sampler:
    """Runs collectors on its own thread. `callback(snapshot)` is invoked from that thread."""

    def __init__(self, interval_s: float = 1.0, history_points: int = 60, callback=None,
                 enable_processes: bool = True, enable_gpu: bool = True):
        self.interval = max(0.1, interval_s)
        self.history = History(history_points)
        self.callback = callback
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._seq = 0
        self.latest: Snapshot | None = None
        self.errors: list[str] = []

        self.cpu = CpuCollector()
        self.memory = MemoryCollector()
        self.disk = DiskCollector()
        self.network = NetworkCollector()
        self.desktop = DesktopIndex()
        self.processes = ProcessCollector(self.cpu.ncpu, self.desktop) if enable_processes else None
        self.nvidia = None
        self.drm = None
        if enable_gpu:
            try:
                from .collectors.gpu_nvidia import NvidiaCollector
                self.nvidia = NvidiaCollector()
            except Exception as exc:  # NVML missing or failed
                log.info("NVML unavailable: %s", exc)
            try:
                from .collectors.gpu_drm import DrmCollector
                self.drm = DrmCollector(skip_keys=set(self.nvidia.infos) if self.nvidia else set())
            except Exception as exc:
                log.warning("DRM collector failed: %s", exc)
        self.gpu_infos: dict[str, GpuInfo] = {}
        if self.nvidia:
            self.gpu_infos.update(self.nvidia.infos)
        if self.drm:
            self.gpu_infos.update(self.drm.infos)

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pulse-sampler", daemon=True)
        self._thread.start()

    def stop(self, join: bool = True) -> None:
        self._stop.set()
        self._wake.set()
        if join and self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=5.0)
        if self.nvidia:
            try:
                self.nvidia.close()
            except Exception:
                pass

    def set_interval(self, seconds: float) -> None:
        """Change the cadence; takes effect immediately (the wait is recomputed)."""
        self.interval = max(0.1, seconds)
        self._wake.set()

    def request_sample(self) -> None:
        """Sample as soon as possible instead of waiting for the next tick."""
        self._force = True
        self._wake.set()

    _force = False

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            self._force = False
            snap = self.sample_once()
            if snap is not None and self.callback is not None:
                try:
                    self.callback(snap)
                except Exception:
                    log.error("callback failed:\n%s", traceback.format_exc())
            # wait until started + interval, re-evaluating whenever the interval changes
            while not self._stop.is_set() and not self._force:
                wait = started + self.interval - time.monotonic()
                if wait <= 0:
                    break
                self._wake.wait(wait)
                self._wake.clear()

    # -- one tick ----------------------------------------------------------
    def sample_once(self) -> Snapshot:
        t0 = time.monotonic()
        ts = time.time()
        errors: list[str] = []

        def guard(name, fn, default):
            try:
                return fn()
            except Exception as exc:
                log.error("%s collector failed: %s\n%s", name, exc, traceback.format_exc())
                errors.append(f"{name}: {exc}")
                return default

        cpu = guard("cpu", lambda: self.cpu.sample(ts), CpuSample(ts=ts))
        mem = guard("memory", lambda: self.memory.sample(ts), MemorySample(ts=ts))
        disks = guard("disk", lambda: self.disk.sample(ts), {})
        nets = guard("network", lambda: self.network.sample(ts), {})
        gpus: dict[str, GpuSample] = {}
        if self.nvidia:
            gpus.update(guard("nvidia", lambda: self.nvidia.sample(ts), {}))
        if self.drm:
            gpus.update(guard("drm", lambda: self.drm.sample(ts), {}))
        procs = guard("processes", lambda: self.processes.sample(ts, gpus), {}) if self.processes else {}
        apps = guard("apps", lambda: self.processes.group_apps(procs), {}) if self.processes else {}

        self._push_history(cpu, mem, disks, nets, gpus)
        self._seq += 1
        snap = Snapshot(ts=ts, seq=self._seq, interval=self.interval, cpu_info=self.cpu.info, cpu=cpu, memory=mem,
                        memory_modules=self.memory.modules, disk_infos=dict(self.disk.disks), disks=disks,
                        net_infos=dict(self.network.interfaces), nets=nets, gpu_infos=dict(self.gpu_infos),
                        gpus=gpus, processes=procs, apps=apps, errors=errors,
                        sample_ms=(time.monotonic() - t0) * 1000.0)
        self.latest = snap
        return snap

    def _push_history(self, cpu, mem, disks, nets, gpus) -> None:
        h = self.history
        h.push("cpu.util", cpu.utilization)
        h.push("cpu.user", cpu.user)
        h.push("cpu.system", cpu.system)
        h.push("cpu.freq", cpu.freq_mhz or 0.0)
        for i, v in enumerate(cpu.per_core):
            h.push(f"cpu.core.{i}", v)
        if cpu.temp_c is not None:
            h.push("cpu.temp", cpu.temp_c)
        h.push("mem.used", mem.used)
        h.push("mem.used_frac", mem.used / mem.total if mem.total else 0.0)
        h.push("mem.swap", mem.swap_used)
        h.push("mem.swap_frac", mem.swap_used / mem.swap_total if mem.swap_total else 0.0)
        h.push("mem.cache", mem.cache_total)
        for name, d in disks.items():
            h.push(f"disk.{name}.read", d.read_bps)
            h.push(f"disk.{name}.write", d.write_bps)
            h.push(f"disk.{name}.busy", d.busy)
        for name, n in nets.items():
            h.push(f"net.{name}.rx", n.rx_bps)
            h.push(f"net.{name}.tx", n.tx_bps)
        for key, g in gpus.items():
            h.push(f"gpu.{key}.util", g.util or 0.0)
            h.push(f"gpu.{key}.mem_util", g.mem_util or 0.0)
            h.push(f"gpu.{key}.enc", g.enc_util or 0.0)
            h.push(f"gpu.{key}.dec", g.dec_util or 0.0)
            h.push(f"gpu.{key}.vram", g.vram_fraction or 0.0)
            h.push(f"gpu.{key}.temp", g.temp_c or 0.0)
            h.push(f"gpu.{key}.power", g.power_w or 0.0)
            h.push(f"gpu.{key}.clock", g.clock_mhz or 0.0)
