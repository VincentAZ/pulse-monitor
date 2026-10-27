from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ...util import fmt_bytes, fmt_duration, fmt_freq, fmt_percent, fmt_temp  # noqa: E402
from ..graph import COLORS, HistoryGraph, Series  # noqa: E402
from ..widgets import GraphBlock, InfoList, PageHeader, StatsGrid, label  # noqa: E402
from . import Page  # noqa: E402


class CpuPage(Page):
    key = "cpu"
    sidebar_title = "CPU"

    def __init__(self, history, settings, points: int):
        super().__init__()
        self.history = history
        self.settings = settings
        self.points = points
        self.header = PageHeader("CPU", "")
        self.append(self.header)

        # view switch: overall / logical processors
        self.toggle = Adw.ToggleGroup()
        self.toggle.add(Adw.Toggle(name="overall", label="Overall utilization"))
        self.toggle.add(Adw.Toggle(name="cores", label="Logical processors"))
        self.toggle.set_active_name(settings.get("cpu_view"))
        self.toggle.set_halign(Gtk.Align.START)
        self.toggle.connect("notify::active-name", self._on_toggle)
        self.append(self.toggle)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.append(self.stack)

        # overall graph
        self.graph = self.register_graph(HistoryGraph(history, [Series("cpu.util", COLORS["cpu"])], 1.0, points))
        self.block = GraphBlock(self.graph, "% Utilization over 60 seconds", "100%", "", "0")
        self.stack.add_named(self.block, "overall")

        # per core grid
        self.core_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.core_caption = label("% Utilization per logical processor", ["caption", "dim-label"])
        self.core_box.append(self.core_caption)
        self.core_grid = Gtk.Grid(column_spacing=6, row_spacing=6, column_homogeneous=True, row_homogeneous=True)
        self.core_grid.set_vexpand(True)
        self.core_box.append(self.core_grid)
        self.core_graphs: list[HistoryGraph] = []
        self.core_labels: list[Gtk.Label] = []
        self.stack.add_named(self.core_box, "cores")
        self.stack.set_visible_child_name(settings.get("cpu_view"))

        bottom = Adw.WrapBox(child_spacing=24, line_spacing=18)
        self.stats = StatsGrid(columns=3)
        self.stats.add("util", "Utilization", "0%")
        self.stats.add("speed", "Speed")
        self.stats.add("temp", "Temperature")
        self.stats.add("processes", "Processes")
        self.stats.add("threads", "Threads")
        self.stats.add("handles", "Handles")
        self.stats.add("uptime", "Up time")
        self.stats.add("load", "Load average")
        self.stats.add("ctxt", "Context switches")
        self.stats.set_valign(Gtk.Align.START)
        bottom.append(self.stats)
        self.info = InfoList()
        self.info.add("base", "Base speed")
        self.info.add("max", "Max speed")
        self.info.add("sockets", "Sockets")
        self.info.add("cores", "Cores")
        self.info.add("logical", "Logical processors")
        self.info.add("virt", "Virtualization")
        self.info.add("vm", "Virtual machine")
        self.info.add("l1", "L1 cache")
        self.info.add("l2", "L2 cache")
        self.info.add("l3", "L3 cache")
        self.info.add("kernel", "Kernel")
        self.info.set_valign(Gtk.Align.START)
        bottom.append(self.info)
        self.append(bottom)
        self._static_done = False
        self._ncores = 0

    def _on_toggle(self, *_):
        name = self.toggle.get_active_name() or "overall"
        self.stack.set_visible_child_name(name)
        self.settings.set("cpu_view", name)

    def set_view(self, name: str) -> None:
        self.toggle.set_active_name(name)

    def _ensure_core_graphs(self, n: int) -> None:
        if n == self._ncores:
            return
        self._ncores = n
        for g in self.core_graphs:
            self._graphs.remove(g)
        child = self.core_grid.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.core_grid.remove(child)
            child = nxt
        self.core_graphs = []
        self.core_labels = []
        cols = max(1, min(8, math.ceil(math.sqrt(n * 1.6))))
        for i in range(n):
            g = self.register_graph(HistoryGraph(self.history, [Series(f"cpu.core.{i}", COLORS["cpu"])], 1.0, self.points, mini=True))
            g.grid_lines = 2
            g.set_size_request(48, 52)
            g.set_vexpand(True)
            over = Gtk.Overlay()
            over.set_child(g)
            lbl = label(f"{i}", ["caption", "dim-label", "numeric"])
            lbl.set_halign(Gtk.Align.START)
            lbl.set_valign(Gtk.Align.START)
            lbl.set_margin_start(5)
            lbl.set_margin_top(2)
            over.add_overlay(lbl)
            self.core_grid.attach(over, i % cols, i // cols, 1, 1)
            self.core_graphs.append(g)
            self.core_labels.append(lbl)

    def set_points(self, points: int) -> None:
        self.points = points
        super().set_points(points)

    def update(self, snap) -> None:
        cpu = snap.cpu
        info = snap.cpu_info
        if not self._static_done:
            self.header.subtitle.set_label(info.model)
            self.info.set("base", fmt_freq(info.base_mhz) if info.base_mhz else "—")
            self.info.set("max", fmt_freq(info.max_mhz))
            self.info.set("sockets", str(info.sockets))
            self.info.set("cores", str(info.cores))
            self.info.set("logical", str(info.threads))
            self.info.set("virt", info.virtualization or "Not supported")
            self.info.set("vm", "Yes" if info.is_vm else "No")
            l1 = (info.l1d or 0) + (info.l1i or 0)
            self.info.set("l1", fmt_bytes(l1, 0) if l1 else "—")
            self.info.set("l2", fmt_bytes(info.l2, 0) if info.l2 else "—")
            self.info.set("l3", fmt_bytes(info.l3, 0) if info.l3 else "—")
            self.info.set("kernel", info.kernel)
            self._static_done = True
        secs = self.settings.get("history_seconds")
        self.block.left.set_label(f"% Utilization over {secs} seconds")
        self.stats.set("util", fmt_percent(cpu.utilization))
        self.stats.set("speed", fmt_freq(cpu.freq_mhz))
        self.stats.set("temp", fmt_temp(cpu.temp_c))
        self.stats.set("processes", str(snap.process_count))
        self.stats.set("threads", str(cpu.threads))
        self.stats.set("handles", str(cpu.handles))
        self.stats.set("uptime", fmt_duration(cpu.uptime))
        self.stats.set("load", f"{cpu.load[0]:.2f}  {cpu.load[1]:.2f}  {cpu.load[2]:.2f}")
        self.stats.set("ctxt", f"{cpu.context_switches_ps:,.0f}/s" if cpu.context_switches_ps is not None else "—")
        self._ensure_core_graphs(len(cpu.per_core))
        for i, lbl in enumerate(self.core_labels):
            freq = cpu.per_core_freq[i] if i < len(cpu.per_core_freq) else None
            util = cpu.per_core[i] if i < len(cpu.per_core) else 0.0
            text = f"{i}  {util * 100:.0f}%"
            if freq:
                text += f"\n{freq / 1000:.1f} GHz"
            if lbl.get_label() != text:
                lbl.set_label(text)
        self.refresh_graphs()
