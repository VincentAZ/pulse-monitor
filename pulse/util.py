"""Small helpers: fast file reading and human readable formatting."""
from __future__ import annotations

import os

_UNITS_BIN = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]


def read_text(path: str, default: str | None = None) -> str | None:
    """Read a small sysfs/procfs file and strip it. Returns default on error."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return default
    try:
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", "replace").strip()
    except OSError:
        return default
    finally:
        os.close(fd)


def read_int(path: str, default: int | None = None) -> int | None:
    text = read_text(path)
    if text is None:
        return default
    try:
        return int(text.split()[0])
    except (ValueError, IndexError):
        return default


def read_float(path: str, default: float | None = None) -> float | None:
    text = read_text(path)
    if text is None:
        return default
    try:
        return float(text.split()[0])
    except (ValueError, IndexError):
        return default


def fmt_bytes(n: float | None, precision: int = 1) -> str:
    """1536 -> '1.5 KiB'."""
    if n is None:
        return "—"
    n = float(n)
    idx = 0
    while abs(n) >= 1024.0 and idx < len(_UNITS_BIN) - 1:
        n /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(n)} {_UNITS_BIN[idx]}"
    return f"{n:.{precision}f} {_UNITS_BIN[idx]}"


def fmt_rate(bps: float | None, precision: int = 1) -> str:
    """Bytes per second -> '12.3 MiB/s'."""
    if bps is None:
        return "—"
    return fmt_bytes(bps, precision) + "/s"


def fmt_bits_rate(bps: float | None) -> str:
    """Bits per second -> '866 Mbps'."""
    if bps is None:
        return "—"
    units = ["bps", "Kbps", "Mbps", "Gbps", "Tbps"]
    idx = 0
    bps = float(bps)
    while bps >= 1000.0 and idx < len(units) - 1:
        bps /= 1000.0
        idx += 1
    if idx == 0:
        return f"{int(bps)} {units[idx]}"
    return f"{bps:.0f} {units[idx]}" if bps >= 100 else f"{bps:.1f} {units[idx]}"


def fmt_freq(mhz: float | None, precision: int = 2) -> str:
    if mhz is None:
        return "—"
    if mhz >= 1000.0:
        return f"{mhz / 1000.0:.{precision}f} GHz"
    return f"{mhz:.0f} MHz"


def fmt_percent(fraction: float | None, precision: int = 0) -> str:
    if fraction is None:
        return "—"
    return f"{fraction * 100.0:.{precision}f}%"


def fmt_duration(seconds: float | None) -> str:
    """90061 -> '1d 01:01:01'."""
    if seconds is None:
        return "—"
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def fmt_temp(celsius: float | None) -> str:
    if celsius is None:
        return "—"
    return f"{celsius:.0f} °C"


def fmt_watts(watts: float | None, precision: int = 1) -> str:
    if watts is None:
        return "—"
    return f"{watts:.{precision}f} W"


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if value < lo else hi if value > hi else value


def parse_size_with_unit(text: str) -> int | None:
    """Parse fdinfo style '12 KiB' / '2048 KiB' / '0' into bytes."""
    parts = text.strip().split()
    if not parts:
        return None
    try:
        value = float(parts[0])
    except ValueError:
        return None
    if len(parts) == 1:
        return int(value)
    unit = parts[1].lower()
    mult = {
        "b": 1, "kib": 1024, "kb": 1024, "mib": 1024 ** 2, "mb": 1024 ** 2,
        "gib": 1024 ** 3, "gb": 1024 ** 3, "tib": 1024 ** 4,
    }.get(unit, 1)
    return int(value * mult)
