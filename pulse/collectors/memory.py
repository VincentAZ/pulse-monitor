"""Memory collector: /proc/meminfo plus optional SMBIOS DIMM info (root only)."""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field


@dataclass
class MemoryModule:
    size: int
    speed_mts: int | None
    configured_mts: int | None
    form_factor: str
    mem_type: str
    locator: str


@dataclass
class MemorySample:
    ts: float
    total: int = 0
    free: int = 0
    available: int = 0
    buffers: int = 0
    cached: int = 0
    shmem: int = 0
    sreclaimable: int = 0
    dirty: int = 0
    writeback: int = 0
    swap_total: int = 0
    swap_free: int = 0
    committed: int = 0
    commit_limit: int = 0
    zswap: int = 0
    hugepages_total: int = 0
    hugepage_size: int = 0

    @property
    def used(self) -> int:
        return max(0, self.total - self.available)

    @property
    def swap_used(self) -> int:
        return max(0, self.swap_total - self.swap_free)

    @property
    def modified(self) -> int:
        return self.dirty + self.writeback

    @property
    def standby(self) -> int:
        """Reclaimable page cache: available but not free."""
        return max(0, self.available - self.free)

    @property
    def cache_total(self) -> int:
        return self.cached + self.buffers + self.sreclaimable


def parse_meminfo(text: str, ts: float = 0.0) -> MemorySample:
    s = MemorySample(ts=ts)
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, sep, rest = line.partition(":")
        if not sep:
            continue
        parts = rest.split()
        if not parts:
            continue
        try:
            n = int(parts[0])
        except ValueError:
            continue
        if len(parts) > 1 and parts[1] == "kB":
            n *= 1024
        values[key] = n
    s.total = values.get("MemTotal", 0)
    s.free = values.get("MemFree", 0)
    s.available = values.get("MemAvailable", s.free)
    s.buffers = values.get("Buffers", 0)
    s.cached = values.get("Cached", 0)
    s.shmem = values.get("Shmem", 0)
    s.sreclaimable = values.get("SReclaimable", 0)
    s.dirty = values.get("Dirty", 0)
    s.writeback = values.get("Writeback", 0)
    s.swap_total = values.get("SwapTotal", 0)
    s.swap_free = values.get("SwapFree", 0)
    s.committed = values.get("Committed_AS", 0)
    s.commit_limit = values.get("CommitLimit", 0)
    s.zswap = values.get("Zswap", 0)
    s.hugepages_total = values.get("HugePages_Total", 0)
    s.hugepage_size = values.get("Hugepagesize", 0)
    return s


_FORM_FACTORS = {
    0x01: "Other", 0x02: "Unknown", 0x03: "SIMM", 0x04: "SIP", 0x05: "Chip", 0x06: "DIP",
    0x07: "ZIP", 0x08: "Proprietary Card", 0x09: "DIMM", 0x0A: "TSOP", 0x0B: "Row of chips",
    0x0C: "RIMM", 0x0D: "SODIMM", 0x0E: "SRIMM", 0x0F: "FB-DIMM", 0x10: "Die",
}
_MEM_TYPES = {
    0x18: "DDR3", 0x1A: "DDR4", 0x1B: "LPDDR", 0x1C: "LPDDR2", 0x1D: "LPDDR3", 0x1E: "LPDDR4",
    0x1F: "Logical non-volatile", 0x20: "HBM", 0x21: "HBM2", 0x22: "DDR5", 0x23: "LPDDR5",
    0x24: "HBM3", 0x12: "DDR", 0x13: "DDR2",
}


def parse_smbios_type17(raw: bytes) -> MemoryModule | None:
    """Decode an SMBIOS type 17 (Memory Device) structure."""
    if len(raw) < 0x15 or raw[0] != 17:
        return None
    length = raw[1]
    size_field = struct.unpack_from("<H", raw, 0x0C)[0]
    if size_field == 0 or size_field == 0xFFFF:
        return None  # empty slot / unknown
    if size_field == 0x7FFF and length >= 0x20:
        size = struct.unpack_from("<I", raw, 0x1C)[0] * 1024 * 1024
    elif size_field & 0x8000:
        size = (size_field & 0x7FFF) * 1024
    else:
        size = size_field * 1024 * 1024
    form_factor = _FORM_FACTORS.get(raw[0x0E], "Unknown")
    mem_type = _MEM_TYPES.get(raw[0x12], "Unknown")
    speed = struct.unpack_from("<H", raw, 0x15)[0] if length >= 0x17 else None
    configured = struct.unpack_from("<H", raw, 0x20)[0] if length >= 0x22 else None
    # strings follow the formatted area, NUL separated
    strings_area = raw[length:]
    strings = strings_area.split(b"\x00")
    locator_idx = raw[0x10]
    locator = ""
    if 0 < locator_idx <= len(strings):
        locator = strings[locator_idx - 1].decode("utf-8", "replace")
    return MemoryModule(size=size, speed_mts=speed or None, configured_mts=configured or None,
                        form_factor=form_factor, mem_type=mem_type, locator=locator)


def read_memory_modules() -> list[MemoryModule]:
    """Requires read access to /sys/firmware/dmi/entries (normally root only)."""
    base = "/sys/firmware/dmi/entries"
    modules: list[MemoryModule] = []
    try:
        entries = sorted(e for e in os.listdir(base) if e.startswith("17-"))
    except OSError:
        return modules
    for e in entries:
        try:
            with open(f"{base}/{e}/raw", "rb") as fh:
                raw = fh.read()
        except OSError:
            return []
        mod = parse_smbios_type17(raw)
        if mod:
            modules.append(mod)
    return modules


class MemoryCollector:
    def __init__(self):
        self.modules: list[MemoryModule] = read_memory_modules()

    def sample(self, ts: float) -> MemorySample:
        try:
            with open("/proc/meminfo", "r", encoding="ascii") as fh:
                text = fh.read()
        except OSError:
            text = ""
        return parse_meminfo(text, ts)
