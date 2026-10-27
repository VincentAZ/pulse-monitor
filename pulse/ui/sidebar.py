"""Device list with sparklines (left pane of the Performance view)."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..util import fmt_bytes, fmt_freq, fmt_percent, fmt_rate, fmt_temp  # noqa: E402
from .graph import COLORS, HistoryGraph, Series  # noqa: E402
from .widgets import label  # noqa: E402


class DeviceRow(Gtk.ListBoxRow):
    def __init__(self, key: str, title: str, series: list[Series], history, points: int, max_value=1.0, min_auto_max=1.0):
        super().__init__()
        self.key = key
        box = Gtk.Box(spacing=10)
        box.add_css_class("sidebar-row")
        self.graph = HistoryGraph(history, series, max_value, points, mini=True, min_auto_max=min_auto_max)
        self.graph.set_size_request(56, 40)
        self.graph.set_valign(Gtk.Align.CENTER)
        box.append(self.graph)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.set_hexpand(True)
        self.title = label(title, ["sidebar-title"], ellipsize=True)
        self.subtitle = label("", ["caption", "dim-label", "numeric"], ellipsize=True)
        text.append(self.title)
        text.append(self.subtitle)
        box.append(text)
        self.set_child(box)

    def set_subtitle(self, text: str) -> None:
        if self.subtitle.get_label() != text:
            self.subtitle.set_label(text)


class Sidebar(Gtk.Box):
    def __init__(self, history, points: int, on_select):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.history = history
        self.points = points
        self.on_select = on_select
        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self._row_selected)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_child(self.listbox)
        sw.set_vexpand(True)
        self.append(sw)
        self.rows: dict[str, DeviceRow] = {}
        self._suppress = False

    def _row_selected(self, listbox, row):
        if row is not None and not self._suppress:
            self.on_select(row.key)

    def add_row(self, row: DeviceRow) -> None:
        self.rows[row.key] = row
        self.listbox.append(row)

    def select(self, key: str) -> None:
        row = self.rows.get(key)
        if row is not None:
            self._suppress = True
            self.listbox.select_row(row)
            self._suppress = False

    def set_points(self, points: int) -> None:
        self.points = points
        for row in self.rows.values():
            row.graph.set_points(points)

    def build(self, snap, hide_virtual: bool) -> list[str]:
        """Create rows for devices present in the snapshot. Returns the ordered list of keys."""
        keys: list[str] = []
        h = self.history
        if "cpu" not in self.rows:
            self.add_row(DeviceRow("cpu", "CPU", [Series("cpu.util", COLORS["cpu"])], h, self.points))
        keys.append("cpu")
        if "memory" not in self.rows:
            self.add_row(DeviceRow("memory", "Memory", [Series("mem.used_frac", COLORS["mem"])], h, self.points))
        keys.append("memory")
        for i, name in enumerate(snap.disk_infos):
            key = f"disk.{name}"
            if key not in self.rows:
                self.add_row(DeviceRow(key, f"Disk {i} ({name})", [Series(f"disk.{name}.busy", COLORS["disk"])], h, self.points))
            keys.append(key)
        for name, info in snap.net_infos.items():
            if hide_virtual and info.virtual:
                continue
            key = f"net.{name}"
            if key not in self.rows:
                self.add_row(DeviceRow(key, f"{info.kind} ({name})",
                                       [Series(f"net.{name}.rx", COLORS["net"]), Series(f"net.{name}.tx", COLORS["net2"], fill=False)],
                                       h, self.points, None, 1024.0 * 16))
            keys.append(key)
        for i, (gkey, info) in enumerate(snap.gpu_infos.items()):
            key = f"gpu.{gkey}"
            if key not in self.rows:
                self.add_row(DeviceRow(key, f"GPU {i}", [Series(f"gpu.{gkey}.util", COLORS["gpu"])], h, self.points))
            keys.append(key)
        # hide rows that disappeared / are filtered
        for key, row in self.rows.items():
            row.set_visible(key in keys)
        return keys

    def update(self, snap) -> None:
        r = self.rows.get("cpu")
        if r:
            r.set_subtitle(f"{fmt_percent(snap.cpu.utilization)}  {fmt_freq(snap.cpu.freq_mhz)}")
        r = self.rows.get("memory")
        if r:
            m = snap.memory
            r.set_subtitle(f"{fmt_bytes(m.used)} / {fmt_bytes(m.total)} ({fmt_percent(m.used / m.total if m.total else 0)})")
        for name, d in snap.disks.items():
            r = self.rows.get(f"disk.{name}")
            if r:
                r.set_subtitle(f"{fmt_percent(d.busy)}  R {fmt_rate(d.read_bps, 0)}  W {fmt_rate(d.write_bps, 0)}")
        for name, n in snap.nets.items():
            r = self.rows.get(f"net.{name}")
            if r:
                r.set_subtitle(f"↓ {fmt_rate(n.rx_bps, 0)}  ↑ {fmt_rate(n.tx_bps, 0)}" if n.up else "Disconnected")
        for key, g in snap.gpus.items():
            r = self.rows.get(f"gpu.{key}")
            if r:
                info = snap.gpu_infos.get(key)
                parts = [fmt_percent(g.util)]
                if g.temp_c is not None:
                    parts.append(fmt_temp(g.temp_c))
                if g.vram_fraction is not None:
                    parts.append(f"VRAM {fmt_percent(g.vram_fraction)}")
                r.set_subtitle("  ".join(parts))
                if info and r.title.get_label().startswith("GPU") and "(" not in r.title.get_label():
                    short = info.name.replace("NVIDIA ", "").replace("Corporation ", "")
                    r.title.set_label(f"{r.title.get_label()} ({short})")
                    r.set_tooltip_text(info.name)
        for row in self.rows.values():
            if row.get_visible():
                row.graph.refresh()
