"""Common GPU types shared by the NVML and DRM backends."""
from __future__ import annotations

from dataclasses import dataclass, field

PCIE_GEN_BY_GTS = {2.5: 1, 5.0: 2, 8.0: 3, 16.0: 4, 32.0: 5, 64.0: 6}


@dataclass
class GpuInfo:
    key: str                      # PCI slot e.g. 0000:01:00.0 (stable across backends)
    vendor: str                   # NVIDIA / AMD / Intel / Other
    name: str
    driver: str
    driver_version: str | None = None
    vram_total: int | None = None
    shared_total: int | None = None
    pcie_gen_max: int | None = None
    pcie_width_max: int | None = None
    clock_max_mhz: float | None = None
    mem_clock_max_mhz: float | None = None
    power_limit_w: float | None = None
    drm_card: str | None = None
    extra: dict = field(default_factory=dict)   # vbios, cuda, cores, ...


@dataclass
class GpuProcess:
    pid: int
    kind: str = ""                     # Graphics / Compute / Graphics+Compute
    gpu_util: float | None = None      # 0..1
    mem_util: float | None = None
    enc_util: float | None = None
    dec_util: float | None = None
    vram: int | None = None
    shared: int | None = None


@dataclass
class GpuSample:
    key: str
    ts: float
    util: float | None = None
    mem_util: float | None = None      # memory controller activity
    enc_util: float | None = None
    dec_util: float | None = None
    vram_used: int | None = None
    vram_total: int | None = None
    shared_used: int | None = None
    shared_total: int | None = None
    temp_c: float | None = None
    hotspot_c: float | None = None
    mem_temp_c: float | None = None
    power_w: float | None = None
    power_limit_w: float | None = None
    clock_mhz: float | None = None
    mem_clock_mhz: float | None = None
    fan_pct: float | None = None
    fan_rpm: float | None = None
    pcie_gen: int | None = None
    pcie_width: int | None = None
    pcie_tx_bps: float | None = None
    pcie_rx_bps: float | None = None
    pstate: str | None = None
    processes: dict[int, GpuProcess] = field(default_factory=dict)

    @property
    def vram_fraction(self) -> float | None:
        if self.vram_used is None or not self.vram_total:
            return None
        return min(1.0, self.vram_used / self.vram_total)
