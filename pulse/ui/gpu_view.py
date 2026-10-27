"""GPU tab: nvtop style per-device cards and a per-process GPU table."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GObject, Gtk, Pango  # noqa: E402

from ..util import fmt_bytes, fmt_percent, fmt_rate  # noqa: E402
from .graph import COLORS, HistoryGraph, Series  # noqa: E402
from .widgets import label  # noqa: E402


class GpuCard(Gtk.Box):
    def __init__(self, history, points: int, index: int, key: str, info):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("gpu-card")
        self.key = key
        self.info = info
        top = Gtk.Box(spacing=12)
        self.title = label(f"Device {index}  [{info.name}]", ["gpu-header"], ellipsize=True)
        self.title.set_hexpand(True)
        top.append(self.title)
        self.pcie = label("", ["nv-stat", "dim-label"], xalign=1.0, ellipsize=True)
        top.append(self.pcie)
        self.append(top)
        self.stats = label("", ["nv-stat"])
        self.stats.set_selectable(True)
        self.stats.set_wrap(True)
        self.stats.set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
        self.append(self.stats)

        bars = Gtk.Box(spacing=18, homogeneous=True)
        self.gpu_bar = Gtk.ProgressBar(show_text=True, text="GPU 0%")
        self.mem_bar = Gtk.ProgressBar(show_text=True, text="MEM 0%")
        bars.append(self.gpu_bar)
        bars.append(self.mem_bar)
        self.append(bars)

        graphs = Gtk.Box(spacing=18, homogeneous=True)
        self.graphs = []
        g1 = HistoryGraph(history, [Series(f"gpu.{key}.util", COLORS["gpu"]), Series(f"gpu.{key}.vram", COLORS["gpu2"], fill=False, dashed=True)], 1.0, points)
        g1.set_size_request(-1, 110)
        g2 = HistoryGraph(history, [Series(f"gpu.{key}.enc", COLORS["enc"]), Series(f"gpu.{key}.dec", COLORS["dec"], fill=False, dashed=True)], 1.0, points)
        g2.set_size_request(-1, 110)
        for g, caption in ((g1, "GPU (solid) · VRAM (dashed) — 100%"), (g2, "Encode (solid) · Decode (dashed) — 100%")):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.append(label(caption, ["caption", "dim-label"]))
            box.append(g)
            graphs.append(box)
            self.graphs.append(g)
        self.append(graphs)

    def set_points(self, n: int) -> None:
        for g in self.graphs:
            g.set_points(n)

    def update(self, g) -> None:
        if g is None:
            return
        if g.pcie_gen and g.pcie_width:
            txt = f"PCIe GEN {g.pcie_gen}@{g.pcie_width}x"
            if g.pcie_rx_bps is not None:
                txt += f"   RX {fmt_rate(g.pcie_rx_bps, 0)}   TX {fmt_rate(g.pcie_tx_bps, 0)}"
            self.pcie.set_label(txt)
        parts = []
        parts.append(f"GPU {g.clock_mhz:.0f} MHz" if g.clock_mhz else "GPU — MHz")
        parts.append(f"MEM {g.mem_clock_mhz:.0f} MHz" if g.mem_clock_mhz else "MEM — MHz")
        parts.append(f"TEMP {g.temp_c:.0f}°C" if g.temp_c is not None else "TEMP —")
        if g.fan_pct is not None:
            parts.append(f"FAN {g.fan_pct:.0f}%")
        elif g.fan_rpm is not None:
            parts.append(f"FAN {g.fan_rpm:.0f} rpm")
        if g.power_w is not None:
            p = f"POW {g.power_w:.1f}" if g.power_w < 10 else f"POW {g.power_w:.0f}"
            if g.power_limit_w:
                p += f" / {g.power_limit_w:.0f}"
            parts.append(p + " W")
        if g.pstate:
            parts.append(f"{g.pstate}")
        self.stats.set_label("   ".join(parts))
        util = g.util or 0.0
        self.gpu_bar.set_fraction(util)
        self.gpu_bar.set_text(f"GPU {util * 100:.0f}%")
        vf = g.vram_fraction
        if vf is not None:
            self.mem_bar.set_fraction(vf)
            self.mem_bar.set_text(f"MEM {vf * 100:.0f}%  {fmt_bytes(g.vram_used, 2)} / {fmt_bytes(g.vram_total, 2)}")
        elif g.shared_used is not None and g.shared_total:
            f = min(1.0, g.shared_used / g.shared_total)
            self.mem_bar.set_fraction(f)
            self.mem_bar.set_text(f"SHARED {f * 100:.0f}%  {fmt_bytes(g.shared_used)}")
        else:
            self.mem_bar.set_fraction(0)
            self.mem_bar.set_text("MEM —")
        for g_ in self.graphs:
            g_.refresh()


class GpuProcItem(GObject.Object):
    __gtype_name__ = "PulseGpuProcItem"
    uid = GObject.Property(type=str, default="")      # f"{gpu_key}:{pid}"
    pid = GObject.Property(type=int, default=0)
    pid_text = GObject.Property(type=str, default="")
    user = GObject.Property(type=str, default="")
    dev = GObject.Property(type=int, default=0)
    dev_text = GObject.Property(type=str, default="")
    kind = GObject.Property(type=str, default="")
    gpu = GObject.Property(type=float, default=0.0)
    gpu_text = GObject.Property(type=str, default="")
    gpu_mem = GObject.Property(type=GObject.TYPE_INT64, default=0)
    gpu_mem_text = GObject.Property(type=str, default="")
    enc = GObject.Property(type=float, default=0.0)
    enc_text = GObject.Property(type=str, default="")
    cpu = GObject.Property(type=float, default=0.0)
    cpu_text = GObject.Property(type=str, default="")
    host_mem = GObject.Property(type=GObject.TYPE_INT64, default=0)
    host_mem_text = GObject.Property(type=str, default="")
    command = GObject.Property(type=str, default="")

    def apply(self, dev_index: int, gp, ps) -> None:
        self.freeze_notify()
        try:
            self.pid = gp.pid
            self.pid_text = str(gp.pid)
            self.dev = dev_index
            self.dev_text = str(dev_index)
            self.kind = gp.kind or "—"
            u = gp.gpu_util or 0.0
            self.gpu = u
            self.gpu_text = f"{u * 100:.0f}%"
            vram = gp.vram or 0
            self.gpu_mem = vram
            self.gpu_mem_text = fmt_bytes(vram) if vram else "—"
            e = max(gp.enc_util or 0.0, gp.dec_util or 0.0)
            self.enc = e
            self.enc_text = f"{(gp.enc_util or 0) * 100:.0f}% / {(gp.dec_util or 0) * 100:.0f}%"
            if ps is not None:
                self.user = ps.user
                self.cpu = ps.cpu
                self.cpu_text = f"{ps.cpu * 100:.1f}%"
                self.host_mem = ps.rss
                self.host_mem_text = fmt_bytes(ps.rss)
                self.command = ps.cmdline or ps.name
            else:
                self.user = "?"
                self.command = f"pid {gp.pid}"
        finally:
            self.thaw_notify()


class GpuView(Gtk.Paned):
    """Top: one card per GPU (scrollable). Bottom: table of every process using a GPU."""

    def __init__(self, history, settings, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.history = history
        self.settings = settings
        self.window = window
        self.cards: dict[str, GpuCard] = {}
        self.index_by_key: dict[str, int] = {}
        self.cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.cards_box.add_css_class("page")
        self.empty = Gtk.Label(label="No supported GPU found. NVIDIA needs the proprietary driver (NVML); AMD and Intel need a DRM driver with fdinfo support.")
        self.empty.add_css_class("dim-label")
        self.empty.set_wrap(True)
        self.empty.set_visible(False)
        self.cards_box.append(self.empty)
        top = Gtk.ScrolledWindow()
        top.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        top.set_child(self.cards_box)
        top.set_vexpand(True)
        self.set_start_child(top)
        self.set_resize_start_child(True)
        self.set_shrink_start_child(False)

        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        heading = label("GPU processes", ["heading"])
        heading.set_margin_start(24)
        heading.set_margin_top(8)
        heading.set_margin_bottom(4)
        bottom.append(heading)
        self.set_end_child(bottom)
        self.set_resize_end_child(True)
        self.set_shrink_end_child(False)
        self.set_position(430)
        self.store = Gio.ListStore(item_type=GpuProcItem)
        self.items: dict[str, GpuProcItem] = {}
        self.sort_model = Gtk.SortListModel(model=self.store)
        self.selection = Gtk.SingleSelection(model=self.sort_model, autoselect=False, can_unselect=True)
        self.view = Gtk.ColumnView(model=self.selection)
        self.view.add_css_class("data-table")
        self.sort_model.set_sorter(self.view.get_sorter())
        self.columns = {
            "pid": self._col("PID", "pid_text", "pid", True, width=80, align_end=True),
            "user": self._col("User", "user", "user", width=100),
            "dev": self._col("Dev", "dev_text", "dev", True, width=50, align_end=True),
            "kind": self._col("Type", "kind", "kind", width=140),
            "gpu": self._col("GPU", "gpu_text", "gpu", True, width=70, align_end=True),
            "gpu_mem": self._col("GPU memory", "gpu_mem_text", "gpu_mem", True, width=110, align_end=True),
            "enc": self._col("Enc / Dec", "enc_text", "enc", True, width=100, align_end=True),
            "cpu": self._col("CPU", "cpu_text", "cpu", True, width=70, align_end=True),
            "host_mem": self._col("Host memory", "host_mem_text", "host_mem", True, width=110, align_end=True),
            "command": self._col("Command", "command", "command", expand=True),
        }
        self.view.sort_by_column(self.columns["gpu_mem"], Gtk.SortType.DESCENDING)
        sw = Gtk.ScrolledWindow()
        sw.set_child(self.view)
        sw.set_vexpand(True)
        sw.set_size_request(-1, 120)
        bottom.append(sw)
        footer = Gtk.Box(spacing=12)
        footer.add_css_class("footer-bar")
        self.footer = label("", ["caption", "dim-label"], ellipsize=True)
        self.footer.set_hexpand(True)
        footer.append(self.footer)
        self.end_button = Gtk.Button(label="End process")
        self.end_button.add_css_class("destructive-action")
        self.end_button.set_sensitive(False)
        self.end_button.connect("clicked", self._end_selected)
        footer.append(self.end_button)
        bottom.append(footer)
        self.selection.connect("notify::selected-item", lambda *_: self.end_button.set_sensitive(self.selection.get_selected_item() is not None))

    def _end_selected(self, *_):
        item = self.selection.get_selected_item()
        if item is not None:
            self.window.process_view.signal_pid(item.pid, item.command.split(" ")[0] or f"pid {item.pid}", 15)

    def _col(self, title, prop, sort_prop, numeric=False, expand=False, width=0, align_end=False):
        factory = Gtk.SignalListItemFactory()

        def setup(_f, item):
            lbl = Gtk.Label(xalign=1.0 if align_end else 0.0)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            if numeric:
                lbl.add_css_class("cell-numeric")
            if expand:
                lbl.set_size_request(200, -1)
            item.set_child(lbl)

        def bind(_f, item):
            obj = item.get_item()
            item._b = obj.bind_property(prop, item.get_child(), "label", GObject.BindingFlags.SYNC_CREATE)
            if prop == "command":
                item.get_child().set_tooltip_text(obj.command)

        def unbind(_f, item):
            b = getattr(item, "_b", None)
            if b:
                b.unbind()
                item._b = None

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        factory.connect("unbind", unbind)
        col = Gtk.ColumnViewColumn(title=title, factory=factory)
        col.set_expand(expand)
        col.set_resizable(True)
        if width:
            col.set_fixed_width(width)
        expr = Gtk.PropertyExpression.new(GpuProcItem, None, sort_prop)
        if sort_prop in ("user", "kind", "command"):
            sorter = Gtk.StringSorter.new(expr)
            sorter.set_ignore_case(True)
        else:
            sorter = Gtk.NumericSorter.new(expr)
        col.set_sorter(sorter)
        self.view.append_column(col)
        return col

    def set_points(self, n: int) -> None:
        for c in self.cards.values():
            c.set_points(n)

    def selected_pid(self) -> int | None:
        item = self.selection.get_selected_item()
        return item.pid if item else None

    def update(self, snap) -> None:
        points = self.settings.history_points
        for i, (key, info) in enumerate(snap.gpu_infos.items()):
            self.index_by_key[key] = i
            if key not in self.cards:
                card = GpuCard(self.history, points, i, key, info)
                self.cards[key] = card
                self.cards_box.append(card)
        self.empty.set_visible(not self.cards)
        for key, card in self.cards.items():
            card.update(snap.gpus.get(key))

        current: dict[str, tuple[int, object, object]] = {}
        for key, g in snap.gpus.items():
            dev = self.index_by_key.get(key, 0)
            for pid, gp in g.processes.items():
                current[f"{key}:{pid}"] = (dev, gp, snap.processes.get(pid))
        gone = [u for u in self.items if u not in current]
        if gone:
            gone_set = set(gone)
            for i in range(self.store.get_n_items() - 1, -1, -1):
                if self.store.get_item(i).uid in gone_set:
                    self.store.remove(i)
            for u in gone:
                del self.items[u]
        new_items = []
        for uid, (dev, gp, ps) in current.items():
            item = self.items.get(uid)
            if item is None:
                item = GpuProcItem()
                item.uid = uid
                item.apply(dev, gp, ps)
                self.items[uid] = item
                new_items.append(item)
            else:
                item.apply(dev, gp, ps)
        if new_items:
            self.store.splice(self.store.get_n_items(), 0, new_items)
        sorter = self.view.get_sorter()
        if sorter is not None:
            sorter.changed(Gtk.SorterChange.DIFFERENT)
        total_vram = sum(gp.vram or 0 for g in snap.gpus.values() for gp in g.processes.values())
        self.footer.set_label(f"{len(current)} GPU processes · {fmt_bytes(total_vram)} VRAM in use by processes · "
                              + "  ".join(f"GPU {self.index_by_key.get(k, 0)}: {fmt_percent(g.util)}" for k, g in snap.gpus.items()))
