"""Network collector: /proc/net/dev, /sys/class/net, ioctl for IPv4, iw for Wi-Fi details."""
from __future__ import annotations

import fcntl
import os
import shutil
import socket
import struct
import subprocess
from dataclasses import dataclass, field

from ..util import read_int, read_text

SYS_NET = "/sys/class/net"
SIOCGIFADDR = 0x8915
SIOCGIFNETMASK = 0x891B


@dataclass
class NetInfo:
    name: str
    kind: str                    # Wi-Fi / Ethernet / Bridge / Virtual / Loopback / Other
    mac: str = ""
    driver: str | None = None
    speed_mbps: int | None = None
    ipv4: list[str] = field(default_factory=list)
    ipv6: list[str] = field(default_factory=list)
    ssid: str | None = None
    freq_mhz: int | None = None
    signal_dbm: int | None = None
    tx_bitrate_mbps: float | None = None
    rx_bitrate_mbps: float | None = None
    virtual: bool = False
    up: bool = False
    mtu: int | None = None


@dataclass
class NetCounters:
    rx_bytes: int = 0
    rx_packets: int = 0
    rx_errs: int = 0
    rx_drop: int = 0
    tx_bytes: int = 0
    tx_packets: int = 0
    tx_errs: int = 0
    tx_drop: int = 0


@dataclass
class NetSample:
    name: str
    ts: float
    rx_bps: float = 0.0
    tx_bps: float = 0.0
    rx_pps: float = 0.0
    tx_pps: float = 0.0
    rx_total: int = 0
    tx_total: int = 0
    rx_errors: int = 0
    tx_errors: int = 0
    up: bool = False


def parse_proc_net_dev(text: str) -> dict[str, NetCounters]:
    out: dict[str, NetCounters] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        name = name.strip()
        f = rest.split()
        if len(f) < 16:
            continue
        try:
            nums = [int(x) for x in f[:16]]
        except ValueError:
            continue
        out[name] = NetCounters(rx_bytes=nums[0], rx_packets=nums[1], rx_errs=nums[2], rx_drop=nums[3],
                                tx_bytes=nums[8], tx_packets=nums[9], tx_errs=nums[10], tx_drop=nums[11])
    return out


def _ioctl_ipv4(name: str, req: int) -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return None
    try:
        ifreq = struct.pack("256s", name.encode("utf-8")[:15])
        res = fcntl.ioctl(s.fileno(), req, ifreq)
        return socket.inet_ntoa(res[20:24])
    except OSError:
        return None
    finally:
        s.close()


def _prefix_from_mask(mask: str) -> int:
    try:
        bits = struct.unpack("!I", socket.inet_aton(mask))[0]
    except OSError:
        return 0
    return bin(bits).count("1")


def parse_if_inet6(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        raw, prefix, name = parts[0], parts[2], parts[5]
        try:
            addr = socket.inet_ntop(socket.AF_INET6, bytes.fromhex(raw))
            plen = int(prefix, 16)
        except (ValueError, OSError):
            continue
        out.setdefault(name, []).append(f"{addr}/{plen}")
    return out


def parse_iw_link(text: str) -> dict:
    """Parse `iw dev X link` output."""
    info: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("SSID:"):
            info["ssid"] = line[5:].strip()
        elif line.startswith("freq:"):
            try:
                info["freq_mhz"] = int(float(line[5:].strip().split()[0]))
            except (ValueError, IndexError):
                pass
        elif line.startswith("signal:"):
            try:
                info["signal_dbm"] = int(line[7:].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif line.startswith("tx bitrate:"):
            try:
                info["tx_bitrate_mbps"] = float(line[11:].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif line.startswith("rx bitrate:"):
            try:
                info["rx_bitrate_mbps"] = float(line[11:].strip().split()[0])
            except (ValueError, IndexError):
                pass
    return info


def _find_iw() -> str | None:
    for candidate in (shutil.which("iw"), "/usr/sbin/iw", "/sbin/iw", "/usr/bin/iw"):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return None


class NetworkCollector:
    def __init__(self, rediscover_every: float = 5.0):
        self.interfaces: dict[str, NetInfo] = {}
        self._prev: dict[str, tuple[float, NetCounters]] = {}
        self._rediscover_every = rediscover_every
        self._last_discovery = 0.0
        self._iw = _find_iw()
        self.discover()

    @staticmethod
    def classify(name: str) -> tuple[str, bool]:
        base = f"{SYS_NET}/{name}"
        if name == "lo" or read_int(f"{base}/type", 1) == 772:
            return "Loopback", True
        if os.path.isdir(f"{base}/wireless") or os.path.isdir(f"{base}/phy80211"):
            return "Wi-Fi", False
        if os.path.isdir(f"{base}/bridge"):
            return "Bridge", True
        if os.path.isdir(f"{base}/bonding"):
            return "Bond", False
        if name.startswith(("veth", "virbr", "vnet", "tap", "tun", "wg", "docker", "br-", "vmnet", "ztt", "tailscale")):
            return "Virtual", True
        if read_int(f"{base}/type", 1) in (65534, 65535, 776):
            return "Tunnel", True
        if os.path.exists(f"{base}/device"):
            if os.path.exists(f"{base}/device/ieee80211"):
                return "Wi-Fi", False
            return "Ethernet", False
        return "Virtual", True

    def discover(self) -> None:
        inet6 = parse_if_inet6(read_text("/proc/net/if_inet6", "") or "")
        found: dict[str, NetInfo] = {}
        try:
            names = sorted(os.listdir(SYS_NET))
        except OSError:
            names = []
        for name in names:
            base = f"{SYS_NET}/{name}"
            kind, virtual = self.classify(name)
            if kind == "Loopback" or name.startswith("veth"):
                continue
            old = self.interfaces.get(name)
            info = NetInfo(name=name, kind=kind, virtual=virtual)
            info.mac = read_text(f"{base}/address", "") or ""
            info.mtu = read_int(f"{base}/mtu")
            info.up = (read_text(f"{base}/operstate", "") or "") in ("up", "unknown")
            speed = read_int(f"{base}/speed")
            info.speed_mbps = speed if speed and speed > 0 else None
            try:
                info.driver = os.path.basename(os.readlink(f"{base}/device/driver"))
            except OSError:
                info.driver = None
            addr = _ioctl_ipv4(name, SIOCGIFADDR)
            if addr:
                mask = _ioctl_ipv4(name, SIOCGIFNETMASK)
                info.ipv4 = [f"{addr}/{_prefix_from_mask(mask)}" if mask else addr]
            info.ipv6 = inet6.get(name, [])
            if kind == "Wi-Fi":
                self._fill_wifi(info, old)
            found[name] = info
        self.interfaces = found

    def _fill_wifi(self, info: NetInfo, old: NetInfo | None) -> None:
        if not self._iw:
            return
        try:
            out = subprocess.run([self._iw, "dev", info.name, "link"], capture_output=True,
                                 text=True, timeout=2.0, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            return
        parsed = parse_iw_link(out)
        info.ssid = parsed.get("ssid")
        info.freq_mhz = parsed.get("freq_mhz")
        info.signal_dbm = parsed.get("signal_dbm")
        info.tx_bitrate_mbps = parsed.get("tx_bitrate_mbps")
        info.rx_bitrate_mbps = parsed.get("rx_bitrate_mbps")

    def sample(self, ts: float) -> dict[str, NetSample]:
        if ts - self._last_discovery >= self._rediscover_every:
            self.discover()
            self._last_discovery = ts
        counters = parse_proc_net_dev(read_text("/proc/net/dev", "") or "")
        out: dict[str, NetSample] = {}
        for name, info in self.interfaces.items():
            c = counters.get(name)
            if c is None:
                continue
            s = NetSample(name=name, ts=ts, rx_total=c.rx_bytes, tx_total=c.tx_bytes,
                          rx_errors=c.rx_errs + c.rx_drop, tx_errors=c.tx_errs + c.tx_drop, up=info.up)
            prev = self._prev.get(name)
            if prev is not None:
                pts, pc = prev
                dt = ts - pts
                if dt > 0:
                    s.rx_bps = max(0.0, (c.rx_bytes - pc.rx_bytes) / dt)
                    s.tx_bps = max(0.0, (c.tx_bytes - pc.tx_bytes) / dt)
                    s.rx_pps = max(0.0, (c.rx_packets - pc.rx_packets) / dt)
                    s.tx_pps = max(0.0, (c.tx_packets - pc.tx_packets) / dt)
            self._prev[name] = (ts, c)
            out[name] = s
        return out
