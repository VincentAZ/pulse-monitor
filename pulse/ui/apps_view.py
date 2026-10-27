"""Apps tab: processes grouped by application scope (cgroup), with desktop names and icons."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GObject, Gtk, Pango  # noqa: E402

from ..util import fmt_bytes, fmt_percent, fmt_rate  # noqa: E402
from .widgets import label, set_image_icon  # noqa: E402


class AppItem(GObject.Object):
    __gtype_name__ = "PulseAppItem"
    app_id = GObject.Property(type=str, default="")
    name = GObject.Property(type=str, default="")
    icon = GObject.Property(type=str, default="")
    count = GObject.Property(type=int, default=0)
    cpu = GObject.Property(type=float, default=0.0)
    memory = GObject.Property(type=GObject.TYPE_INT64, default=0)
    disk = GObject.Property(type=float, default=0.0)
    gpu = GObject.Property(type=float, default=0.0)
    gpu_mem = GObject.Property(type=GObject.TYPE_INT64, default=0)
    count_text = GObject.Property(type=str, default="")
    cpu_text = GObject.Property(type=str, default="")
    memory_text = GObject.Property(type=str, default="")
    disk_text = GObject.Property(type=str, default="")
    gpu_text = GObject.Property(type=str, default="")
    gpu_mem_text = GObject.Property(type=str, default="")

    def apply(self, a) -> None:
        self.freeze_notify()
        try:
            self.app_id = a.app_id
            if self.name != a.name:
                self.name = a.name
            if self.icon != (a.icon or ""):
                self.icon = a.icon or ""
            n = len(a.pids)
            if self.count != n:
                self.count = n
                self.count_text = str(n)
            if self.cpu != a.cpu:
                self.cpu = a.cpu
                self.cpu_text = f"{a.cpu * 100:.1f}%"
            if self.memory != a.memory:
                self.memory = a.memory
                self.memory_text = fmt_bytes(a.memory)
            d = a.read_bps + a.write_bps
            if self.disk != d:
                self.disk = d
                self.disk_text = fmt_rate(d, 0) if d else "0 B/s"
            if self.gpu != a.gpu_util:
                self.gpu = a.gpu_util
                self.gpu_text = f"{a.gpu_util * 100:.0f}%"
            if self.gpu_mem != a.gpu_mem:
                self.gpu_mem = a.gpu_mem
                self.gpu_mem_text = fmt_bytes(a.gpu_mem) if a.gpu_mem else "—"
        finally:
            self.thaw_notify()


class AppsView(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.store = Gio.ListStore(item_type=AppItem)
        self.items: dict[str, AppItem] = {}
        self.sort_model = Gtk.SortListModel(model=self.store)
        self.selection = Gtk.SingleSelection(model=self.sort_model, autoselect=False, can_unselect=True)
        self.view = Gtk.ColumnView(model=self.selection)
        self.view.add_css_class("data-table")
        self.sort_model.set_sorter(self.view.get_sorter())
        self._build_columns()
        sw = Gtk.ScrolledWindow()
        sw.set_child(self.view)
        sw.set_vexpand(True)
        self.append(sw)
        footer = Gtk.Box()
        footer.add_css_class("footer-bar")
        self.footer_label = label("", ["caption", "dim-label"], ellipsize=True)
        footer.append(self.footer_label)
        self.append(footer)
        self.view.sort_by_column(self.columns["memory"], Gtk.SortType.DESCENDING)
        self.view.connect("activate", self._on_activate)

    def _on_activate(self, view, pos):
        item = self.selection.get_selected_item()
        if item is not None:
            self.window.show_processes_for_app(item.name, item.app_id)

    def _col(self, title, prop, sort_prop, numeric=False, expand=False, width=0, align_end=False, with_icon=False):
        factory = Gtk.SignalListItemFactory()

        def setup(_f, item):
            if with_icon:
                box = Gtk.Box(spacing=8)
                img = Gtk.Image()
                img.set_pixel_size(20)
                box.append(img)
                lbl = Gtk.Label(xalign=0.0)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                lbl.set_size_request(160, -1)
                box.append(lbl)
                item.set_child(box)
            else:
                lbl = Gtk.Label(xalign=1.0 if align_end else 0.0)
                if numeric:
                    lbl.add_css_class("cell-numeric")
                item.set_child(lbl)

        def bind(_f, item):
            obj = item.get_item()
            child = item.get_child()
            if with_icon:
                img = child.get_first_child()
                lbl = img.get_next_sibling()
                set_image_icon(img, obj.icon or None)
                item._h = obj.connect("notify::icon", lambda o, _p: set_image_icon(img, o.icon or None))
            else:
                lbl = child
            item._b = obj.bind_property(prop, lbl, "label", GObject.BindingFlags.SYNC_CREATE)

        def unbind(_f, item):
            b = getattr(item, "_b", None)
            if b:
                b.unbind()
                item._b = None
            h = getattr(item, "_h", None)
            if h:
                item.get_item().disconnect(h)
                item._h = None

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        factory.connect("unbind", unbind)
        col = Gtk.ColumnViewColumn(title=title, factory=factory)
        col.set_expand(expand)
        col.set_resizable(True)
        if width:
            col.set_fixed_width(width)
        expr = Gtk.PropertyExpression.new(AppItem, None, sort_prop)
        if sort_prop == "name":
            sorter = Gtk.StringSorter.new(expr)
            sorter.set_ignore_case(True)
        else:
            sorter = Gtk.NumericSorter.new(expr)
        col.set_sorter(sorter)
        self.view.append_column(col)
        return col

    def _build_columns(self):
        self.columns = {
            "name": self._col("Name", "name", "name", expand=True, with_icon=True),
            "count": self._col("Processes", "count_text", "count", True, width=100, align_end=True),
            "cpu": self._col("CPU", "cpu_text", "cpu", True, width=80, align_end=True),
            "memory": self._col("Memory", "memory_text", "memory", True, width=110, align_end=True),
            "disk": self._col("Disk", "disk_text", "disk", True, width=110, align_end=True),
            "gpu": self._col("GPU", "gpu_text", "gpu", True, width=70, align_end=True),
            "gpu_mem": self._col("GPU memory", "gpu_mem_text", "gpu_mem", True, width=110, align_end=True),
        }

    def update(self, snap) -> None:
        apps = snap.apps
        gone = [k for k in self.items if k not in apps]
        if gone:
            gone_set = set(gone)
            for i in range(self.store.get_n_items() - 1, -1, -1):
                if self.store.get_item(i).app_id in gone_set:
                    self.store.remove(i)
            for k in gone:
                del self.items[k]
        new_items = []
        for key, a in apps.items():
            item = self.items.get(key)
            if item is None:
                item = AppItem()
                item.apply(a)
                self.items[key] = item
                new_items.append(item)
            else:
                item.apply(a)
        if new_items:
            self.store.splice(self.store.get_n_items(), 0, new_items)
        sorter = self.view.get_sorter()
        if sorter is not None:
            sorter.changed(Gtk.SorterChange.DIFFERENT)
        self.footer_label.set_label(f"{len(apps)} applications · {sum(len(a.pids) for a in apps.values())} processes · "
                                    f"CPU {fmt_percent(min(1.0, sum(a.cpu for a in apps.values())))} · "
                                    f"RSS {fmt_bytes(sum(a.memory for a in apps.values()))}")
