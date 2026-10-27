"""Disk collector: /sys/block, /proc/self/mounts and statvfs."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from ..util import read_int, read_text

SYS_BLOCK = "/sys/block"
SECTOR = 512


@dataclass
class MountInfo:
    mountpoint: str
    source: str
    fstype: str
    total: int
    used: int


@dataclass
class VolumeInfo:
    """A partition (or whole-disk filesystem) as udev sees it, mounted or not."""
    device: str               # e.g. sda1
    fstype: str               # e.g. ntfs, ext4; "" when udev found no filesystem
    label: str = ""
    mountpoint: str = ""

    @property
    def display_name(self) -> str:
        """Label if the filesystem has one, else the mount point, else the device name."""
        return self.label or self.mountpoint or self.device

    @property
    def mounted(self) -> bool:
        return bool(self.mountpoint)


@dataclass
class DiskInfo:
    name: str
    model: str
    kind: str                 # NVMe / SSD / HDD / Removable / Virtual
    capacity: int
    removable: bool = False
    is_system: bool = False
    mounts: list[MountInfo] = field(default_factory=list)
    partitions: list[str] = field(default_factory=list)
    volumes: list[VolumeInfo] = field(default_factory=list)

    @property
    def formatted(self) -> int:
        return sum(m.total for m in self.mounts)

    @property
    def used(self) -> int:
        return sum(m.used for m in self.mounts)


@dataclass
class DiskStat:
    reads: int = 0
    reads_merged: int = 0
    sectors_read: int = 0
    ms_reading: int = 0
    writes: int = 0
    writes_merged: int = 0
    sectors_written: int = 0
    ms_writing: int = 0
    in_flight: int = 0
    io_ticks: int = 0
    time_in_queue: int = 0


@dataclass
class DiskSample:
    name: str
    ts: float
    read_bps: float = 0.0
    write_bps: float = 0.0
    reads_ps: float = 0.0
    writes_ps: float = 0.0
    busy: float = 0.0            # 0..1
    response_ms: float | None = None
    total_read: int = 0
    total_written: int = 0
    in_flight: int = 0


def parse_block_stat(text: str) -> DiskStat:
    parts = text.split()
    nums = [int(x) for x in parts[:11]]
    nums += [0] * (11 - len(nums))
    return DiskStat(*nums)


def _resolve_disk_for_dev(dev_name: str, parent_map: dict[str, str]) -> str | None:
    """Map a block device name (partition, dm-N, md, ...) to a physical disk name."""
    if dev_name in parent_map:
        return parent_map[dev_name]
    if os.path.isdir(f"{SYS_BLOCK}/{dev_name}"):
        if dev_name in parent_map.values():
            return dev_name
        # device mapper / md: follow slaves
        try:
            slaves = os.listdir(f"{SYS_BLOCK}/{dev_name}/slaves")
        except OSError:
            slaves = []
        for s in slaves:
            r = _resolve_disk_for_dev(s, parent_map)
            if r:
                return r
    return None


UDEV_DATA = "/run/udev/data"
_UDEV_ESCAPE = re.compile(r"\\x([0-9a-fA-F]{2})")


def unescape_udev(value: str) -> str:
    r"""udev's *_ENC properties escape bytes as \xNN (e.g. \x20 for a space)."""
    return _UDEV_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), value)


def parse_udev_data(text: str) -> dict[str, str]:
    """Properties ("E:" lines) from a /run/udev/data/b<maj>:<min> file."""
    props: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("E:") and "=" in line:
            key, _, value = line[2:].partition("=")
            props[key] = value
    return props


def read_volume(disk: str, device: str) -> VolumeInfo | None:
    """Filesystem type and label of a partition from the udev database (readable without root)."""
    devpath = f"{SYS_BLOCK}/{disk}/dev" if disk == device else f"{SYS_BLOCK}/{disk}/{device}/dev"
    majmin = (read_text(devpath, "") or "").strip()
    if not majmin:
        return None
    props = parse_udev_data(read_text(f"{UDEV_DATA}/b{majmin}", "") or "")
    fstype = props.get("ID_FS_TYPE", "")
    label = unescape_udev(props.get("ID_FS_LABEL_ENC", "")) or props.get("ID_FS_LABEL", "").replace("_", " ")
    return VolumeInfo(device=device, fstype=fstype, label=label)


class DiskCollector:
    def __init__(self, rediscover_every: float = 10.0):
        self.disks: dict[str, DiskInfo] = {}
        self._prev: dict[str, tuple[float, DiskStat]] = {}
        self._rediscover_every = rediscover_every
        self._last_discovery = 0.0
        self.discover()

    def request_discovery(self) -> None:
        """Make the next sample() re-read partitions and mounts (after a mount/unmount)."""
        self._last_discovery = 0.0

    def discover(self) -> None:
        disks: dict[str, DiskInfo] = {}
        parent_map: dict[str, str] = {}
        try:
            names = sorted(os.listdir(SYS_BLOCK))
        except OSError:
            names = []
        for name in names:
            base = f"{SYS_BLOCK}/{name}"
            if name.startswith(("loop", "ram", "zram", "dm-", "md", "sr", "fd")):
                continue
            if not os.path.exists(f"{base}/device"):
                continue  # virtual device
            size = (read_int(f"{base}/size", 0) or 0) * SECTOR
            if size <= 0:
                continue
            removable = (read_int(f"{base}/removable", 0) or 0) == 1
            rotational = read_int(f"{base}/queue/rotational", 0) == 1
            model = (read_text(f"{base}/device/model", "") or "").strip()
            vendor = (read_text(f"{base}/device/vendor", "") or "").strip()
            if vendor and vendor.lower() not in ("ata", "nvme") and not model.lower().startswith(vendor.lower()):
                model = f"{vendor} {model}".strip()
            if not model:
                model = name
            if name.startswith("nvme"):
                kind = "NVMe"
            elif name.startswith("mmcblk"):
                kind = "eMMC / SD"
            elif removable:
                kind = "Removable"
            elif rotational:
                kind = "HDD"
            else:
                kind = "SSD"
            info = DiskInfo(name=name, model=model, kind=kind, capacity=size, removable=removable)
            try:
                for entry in os.listdir(base):
                    if entry.startswith(name) and os.path.isdir(f"{base}/{entry}"):
                        info.partitions.append(entry)
                        parent_map[entry] = name
            except OSError:
                pass
            parent_map[name] = name
            # Volumes: every partition, or the whole disk when it carries a filesystem directly.
            for part in sorted(info.partitions):
                vol = read_volume(name, part)
                if vol is not None and vol.fstype:
                    info.volumes.append(vol)
            if not info.partitions:
                vol = read_volume(name, name)
                if vol is not None and vol.fstype:
                    info.volumes.append(vol)
            disks[name] = info

        # mounts
        mounts_text = read_text("/proc/self/mounts", "") or ""
        seen_mp: set[str] = set()
        for line in mounts_text.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            source, mp, fstype = parts[0], parts[1], parts[2]
            if not source.startswith("/dev/"):
                continue
            mp = mp.replace("\\040", " ")
            if mp in seen_mp:
                continue
            real = os.path.realpath(source)
            dev = os.path.basename(real)
            disk = _resolve_disk_for_dev(dev, parent_map)
            if not disk or disk not in disks:
                continue
            try:
                st = os.statvfs(mp)
            except OSError:
                continue
            total = st.f_frsize * st.f_blocks
            used = st.f_frsize * (st.f_blocks - st.f_bfree)
            seen_mp.add(mp)
            disks[disk].mounts.append(MountInfo(mp, source, fstype, total, used))
            for vol in disks[disk].volumes:
                if vol.device == dev and not vol.mountpoint:
                    vol.mountpoint = mp
            if mp == "/":
                disks[disk].is_system = True
        self.disks = disks

    def sample(self, ts: float) -> dict[str, DiskSample]:
        if ts - self._last_discovery >= self._rediscover_every:
            self.discover()
            self._last_discovery = ts
        out: dict[str, DiskSample] = {}
        for name in self.disks:
            text = read_text(f"{SYS_BLOCK}/{name}/stat")
            if text is None:
                continue
            st = parse_block_stat(text)
            s = DiskSample(name=name, ts=ts, total_read=st.sectors_read * SECTOR,
                           total_written=st.sectors_written * SECTOR, in_flight=st.in_flight)
            prev = self._prev.get(name)
            if prev is not None:
                pts, pst = prev
                dt = ts - pts
                if dt > 0:
                    s.read_bps = max(0.0, (st.sectors_read - pst.sectors_read) * SECTOR / dt)
                    s.write_bps = max(0.0, (st.sectors_written - pst.sectors_written) * SECTOR / dt)
                    s.reads_ps = max(0.0, (st.reads - pst.reads) / dt)
                    s.writes_ps = max(0.0, (st.writes - pst.writes) / dt)
                    s.busy = max(0.0, min(1.0, (st.io_ticks - pst.io_ticks) / (dt * 1000.0)))
                    ios = (st.reads - pst.reads) + (st.writes - pst.writes)
                    if ios > 0:
                        s.response_ms = ((st.ms_reading - pst.ms_reading) + (st.ms_writing - pst.ms_writing)) / ios
                    else:
                        s.response_ms = 0.0
            self._prev[name] = (ts, st)
            out[name] = s
        return out
