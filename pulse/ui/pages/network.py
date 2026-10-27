from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ...util import fmt_bits_rate, fmt_bytes, fmt_rate  # noqa: E402
from ..graph import COLORS, HistoryGraph, Series  # noqa: E402
from ..widgets import GraphBlock, InfoList, PageHeader, StatsGrid  # noqa: E402
from . import Page  # noqa: E402


class NetworkPage(Page):
    def __init__(self, history, settings, points: int, name: str, kind: str):
        super().__init__()
        self.key = f"net.{name}"
        self.name = name
        self.sidebar_title = f"{kind} ({name})"
        self.history = history
        self.settings = settings
        self.header = PageHeader(f"{kind} ({name})", "")
        self.append(self.header)
        self.graph = self.register_graph(HistoryGraph(
            history, [Series(f"net.{name}.rx", COLORS["net"]), Series(f"net.{name}.tx", COLORS["net2"], fill=False, dashed=True)],
            None, points, min_auto_max=1024.0 * 16))
        self.block = GraphBlock(self.graph, "Throughput", "", "Receive (solid)  ·  Send (dashed)", "0")
        self.append(self.block)

        bottom = Adw.WrapBox(child_spacing=24, line_spacing=18)
        self.stats = StatsGrid(columns=3)
        self.stats.add("rx", "Receive")
        self.stats.add("tx", "Send")
        self.stats.add("pps", "Packets/s")
        self.stats.add("rxt", "Total received")
        self.stats.add("txt", "Total sent")
        self.stats.add("errors", "Errors / drops")
        self.stats.set_valign(Gtk.Align.START)
        bottom.append(self.stats)
        self.info = InfoList()
        self.info.add("state", "State")
        self.info.add("kind", "Connection type")
        self.info.add("ssid", "SSID")
        self.info.add("signal", "Signal strength")
        self.info.add("freq", "Frequency")
        self.info.add("bitrate", "Link speed")
        self.info.add("mac", "Hardware address")
        self.info.add("ipv4", "IPv4 address")
        self.info.add("ipv6", "IPv6 address")
        self.info.add("driver", "Driver")
        self.info.add("mtu", "MTU")
        self.info.set_valign(Gtk.Align.START)
        bottom.append(self.info)
        self.append(bottom)

    def update(self, snap) -> None:
        info = snap.net_infos.get(self.name)
        n = snap.nets.get(self.name)
        secs = self.settings.get("history_seconds")
        self.block.left.set_label(f"Throughput over {secs} seconds")
        if info:
            self.header.subtitle.set_label(info.driver or info.kind)
            self.info.set("state", "Connected" if info.up else "Disconnected")
            self.info.set("kind", info.kind)
            self.info.set("ssid", info.ssid or "—")
            if info.signal_dbm is not None:
                q = max(0, min(100, 2 * (info.signal_dbm + 100)))
                self.info.set("signal", f"{info.signal_dbm} dBm ({q}%)")
            else:
                self.info.set("signal", "—")
            self.info.set("freq", f"{info.freq_mhz / 1000:.3f} GHz" if info.freq_mhz else "—")
            if info.tx_bitrate_mbps or info.rx_bitrate_mbps:
                self.info.set("bitrate", f"↓ {fmt_bits_rate((info.rx_bitrate_mbps or 0) * 1e6)}  ↑ {fmt_bits_rate((info.tx_bitrate_mbps or 0) * 1e6)}")
            elif info.speed_mbps:
                self.info.set("bitrate", fmt_bits_rate(info.speed_mbps * 1e6))
            else:
                self.info.set("bitrate", "—")
            self.info.set("mac", info.mac or "—")
            self.info.set("ipv4", "\n".join(info.ipv4) or "—")
            self.info.set("ipv6", "\n".join(info.ipv6[:3]) or "—")
            self.info.set("driver", info.driver or "—")
            self.info.set("mtu", str(info.mtu) if info.mtu else "—")
        if n:
            self.stats.set("rx", fmt_rate(n.rx_bps))
            self.stats.set("tx", fmt_rate(n.tx_bps))
            self.stats.set("pps", f"↓ {n.rx_pps:.0f}  ↑ {n.tx_pps:.0f}")
            self.stats.set("rxt", fmt_bytes(n.rx_total))
            self.stats.set("txt", fmt_bytes(n.tx_total))
            self.stats.set("errors", f"{n.rx_errors} / {n.tx_errors}")
        self.refresh_graphs()
        self.block.right.set_label(fmt_rate(self.graph.current_max))
