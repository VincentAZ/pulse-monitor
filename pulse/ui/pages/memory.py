from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ...util import fmt_bytes, fmt_percent  # noqa: E402
from ..graph import COLORS, CompositionBar, HistoryGraph, Series  # noqa: E402
from ..widgets import GraphBlock, InfoList, PageHeader, StatsGrid, label  # noqa: E402
from . import Page  # noqa: E402


class MemoryPage(Page):
    key = "memory"
    sidebar_title = "Memory"

    def __init__(self, history, settings, points: int):
        super().__init__()
        self.history = history
        self.settings = settings
        self.header = PageHeader("Memory", "")
        self.append(self.header)
        self.graph = self.register_graph(HistoryGraph(history, [Series("mem.used_frac", COLORS["mem"])], 1.0, points))
        self.block = GraphBlock(self.graph, "Memory usage", "", "", "0")
        self.append(self.block)

        comp_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        comp_box.append(label("Memory composition", ["caption", "dim-label"]))
        self.bar = CompositionBar(30)
        comp_box.append(self.bar)
        legend = Gtk.Box(spacing=18)
        self.legend_labels = {}
        for key, name, color in (("inuse", "In use", COLORS["mem"]), ("modified", "Modified", COLORS["swap"]),
                                 ("standby", "Standby (cache)", COLORS["net2"]), ("free", "Free", COLORS["fg"])):
            item = Gtk.Box(spacing=6)
            dot = Gtk.Label(label="●")
            dot.set_markup(f'<span foreground="{color}">●</span>')
            item.append(dot)
            lbl = label(name, ["caption", "dim-label"])
            item.append(lbl)
            legend.append(item)
            self.legend_labels[key] = lbl
        comp_box.append(legend)
        self.append(comp_box)

        swap_block_graph = self.register_graph(HistoryGraph(history, [Series("mem.swap_frac", COLORS["swap"])], 1.0, points))
        self.swap_block = GraphBlock(swap_block_graph, "Swap usage", "", "", "0", height=90)
        self.append(self.swap_block)

        bottom = Adw.WrapBox(child_spacing=24, line_spacing=18)
        self.stats = StatsGrid(columns=3)
        self.stats.add("inuse", "In use")
        self.stats.add("available", "Available")
        self.stats.add("committed", "Committed")
        self.stats.add("cached", "Cached")
        self.stats.add("swap_avail", "Swap available")
        self.stats.add("swap_used", "Swap used")
        self.stats.add("shmem", "Shared")
        self.stats.add("dirty", "Dirty")
        self.stats.add("zswap", "Zswap")
        self.stats.set_valign(Gtk.Align.START)
        bottom.append(self.stats)
        self.info = InfoList()
        self.info.add("total", "Total")
        self.info.add("swap_total", "Swap total")
        self.info.add("hugepages", "Huge pages")
        self.info.add("modules", "Modules")
        self.info.add("speed", "Speed")
        self.info.add("form", "Form factor")
        self.info.add("type", "Type")
        self.info.set_valign(Gtk.Align.START)
        bottom.append(self.info)
        self.append(bottom)
        self._static_done = False

    def update(self, snap) -> None:
        m = snap.memory
        if not self._static_done and m.total:
            self.header.subtitle.set_label(fmt_bytes(m.total))
            self.block.right.set_label(fmt_bytes(m.total))
            self.info.set("total", fmt_bytes(m.total))
            self.info.set("swap_total", fmt_bytes(m.swap_total) if m.swap_total else "None")
            self.swap_block.right.set_label(fmt_bytes(m.swap_total) if m.swap_total else "No swap")
            hp = f"{m.hugepages_total} × {fmt_bytes(m.hugepage_size, 0)}" if m.hugepages_total else "0"
            self.info.set("hugepages", hp)
            mods = snap.memory_modules
            if mods:
                self.info.set("modules", f"{len(mods)} × " + ", ".join(sorted({fmt_bytes(x.size, 0) for x in mods})))
                speeds = sorted({x.configured_mts or x.speed_mts or 0 for x in mods})
                self.info.set("speed", ", ".join(f"{s} MT/s" for s in speeds if s) or "—")
                self.info.set("form", ", ".join(sorted({x.form_factor for x in mods})))
                self.info.set("type", ", ".join(sorted({x.mem_type for x in mods})))
            else:
                for k in ("modules", "speed", "form", "type"):
                    self.info.set(k, "Needs root (SMBIOS)")
            self._static_done = True
        secs = self.settings.get("history_seconds")
        self.block.left.set_label(f"Memory usage over {secs} seconds")
        self.block.footer_left.set_label(f"{fmt_percent(m.used / m.total if m.total else 0)} in use")
        self.stats.set("inuse", fmt_bytes(m.used))
        self.stats.set("available", fmt_bytes(m.available))
        self.stats.set("committed", f"{fmt_bytes(m.committed)} / {fmt_bytes(m.commit_limit)}")
        self.stats.set("cached", fmt_bytes(m.cache_total))
        self.stats.set("swap_avail", fmt_bytes(m.swap_free))
        self.stats.set("swap_used", fmt_bytes(m.swap_used))
        self.stats.set("shmem", fmt_bytes(m.shmem))
        self.stats.set("dirty", fmt_bytes(m.modified))
        self.stats.set("zswap", fmt_bytes(m.zswap))
        if m.total:
            inuse = (m.used - m.modified) / m.total
            self.bar.set_segments([(max(0.0, inuse), COLORS["mem"]), (m.modified / m.total, COLORS["swap"]),
                                   (m.standby / m.total, COLORS["net2"]), (m.free / m.total, COLORS["fg"])])
            self.legend_labels["inuse"].set_label(f"In use  {fmt_bytes(m.used)}")
            self.legend_labels["modified"].set_label(f"Modified  {fmt_bytes(m.modified)}")
            self.legend_labels["standby"].set_label(f"Standby  {fmt_bytes(m.standby)}")
            self.legend_labels["free"].set_label(f"Free  {fmt_bytes(m.free)}")
        self.refresh_graphs()
