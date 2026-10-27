"""Processes tab: sortable, searchable table with end/kill actions."""
from __future__ import annotations

import os
import signal

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from ..util import fmt_bytes, fmt_percent, fmt_rate  # noqa: E402
from .widgets import label, set_image_icon  # noqa: E402


class ProcessItem(GObject.Object):
    __gtype_name__ = "PulseProcessItem"
    pid = GObject.Property(type=int, default=0)
    name = GObject.Property(type=str, default="")
    user = GObject.Property(type=str, default="")
    cpu = GObject.Property(type=float, default=0.0)
    memory = GObject.Property(type=GObject.TYPE_INT64, default=0)
    disk = GObject.Property(type=float, default=0.0)
    gpu = GObject.Property(type=float, default=0.0)
    gpu_mem = GObject.Property(type=GObject.TYPE_INT64, default=0)
    threads = GObject.Property(type=int, default=0)
    state = GObject.Property(type=str, default="")
    cmdline = GObject.Property(type=str, default="")
    kernel = GObject.Property(type=bool, default=False)
    icon = GObject.Property(type=str, default="")
    # preformatted strings bound to labels
    cpu_text = GObject.Property(type=str, default="")
    memory_text = GObject.Property(type=str, default="")
    disk_text = GObject.Property(type=str, default="")
    gpu_text = GObject.Property(type=str, default="")
    gpu_mem_text = GObject.Property(type=str, default="")
    pid_text = GObject.Property(type=str, default="")

    def apply(self, ps, icon: str) -> None:
        gpu_util = ps.gpu_util or 0.0
        gpu_mem = ps.gpu_mem or 0
        disk = ps.read_bps + ps.write_bps
        self.freeze_notify()
        try:
            if self.pid != ps.pid:
                self.pid = ps.pid
                self.pid_text = str(ps.pid)
            if self.name != ps.name:
                self.name = ps.name
            if self.user != ps.user:
                self.user = ps.user
            if self.cpu != ps.cpu:
                self.cpu = ps.cpu
                self.cpu_text = f"{ps.cpu * 100:.1f}%"
            if self.memory != ps.rss:
                self.memory = ps.rss
                self.memory_text = fmt_bytes(ps.rss)
            if self.disk != disk:
                self.disk = disk
                self.disk_text = fmt_rate(disk, 0) if disk > 0 else "0 B/s"
            if self.gpu != gpu_util:
                self.gpu = gpu_util
                self.gpu_text = f"{gpu_util * 100:.0f}%"
            if self.gpu_mem != gpu_mem:
                self.gpu_mem = gpu_mem
                self.gpu_mem_text = fmt_bytes(gpu_mem) if gpu_mem else "—"
            if self.threads != ps.threads:
                self.threads = ps.threads
            if self.state != ps.state:
                self.state = ps.state
            if self.cmdline != ps.cmdline:
                self.cmdline = ps.cmdline
            if self.kernel != ps.kernel:
                self.kernel = ps.kernel
            if self.icon != icon:
                self.icon = icon
        finally:
            self.thaw_notify()


class ProcessView(Gtk.Box):
    def __init__(self, settings, desktop_index, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.settings = settings
        self.desktop = desktop_index
        self.window = window
        self.store = Gio.ListStore(item_type=ProcessItem)
        self.items: dict[int, ProcessItem] = {}
        self.search_text = ""
        self.show_kernel = settings.get("show_kernel_threads")
        self._icon_cache: dict[str, str] = {}

        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search processes by name, PID, user or command")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self._on_search)
        self.search_bar.set_child(self.search_entry)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.set_key_capture_widget(window)
        self.append(self.search_bar)

        self.filter = Gtk.CustomFilter.new(self._match)
        self.filter_model = Gtk.FilterListModel(model=self.store, filter=self.filter)
        self.sort_model = Gtk.SortListModel(model=self.filter_model)
        self.selection = Gtk.SingleSelection(model=self.sort_model, autoselect=False, can_unselect=True)
        self.view = Gtk.ColumnView(model=self.selection)
        self.view.add_css_class("data-table")
        self.view.set_show_row_separators(False)
        self.view.set_reorderable(False)
        self.sort_model.set_sorter(self.view.get_sorter())
        self._build_columns()
        self.view.connect("activate", self._on_activate)

        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self._on_right_click)
        self.view.add_controller(click)
        self.menu = Gio.Menu()
        self.menu.append("End process (SIGTERM)", "proc.end")
        self.menu.append("Force kill (SIGKILL)", "proc.kill")
        self.menu.append("Stop (SIGSTOP)", "proc.stop")
        self.menu.append("Continue (SIGCONT)", "proc.cont")
        self.menu.append("Details", "proc.details")
        self.popover = Gtk.PopoverMenu.new_from_model(self.menu)
        self.popover.set_parent(self.view)
        self.popover.set_has_arrow(False)
        group = Gio.SimpleActionGroup()
        for name, fn in (("end", lambda *_: self.signal_selected(signal.SIGTERM)),
                         ("kill", lambda *_: self.signal_selected(signal.SIGKILL)),
                         ("stop", lambda *_: self.signal_selected(signal.SIGSTOP, confirm=False)),
                         ("cont", lambda *_: self.signal_selected(signal.SIGCONT, confirm=False)),
                         ("details", lambda *_: self.show_details())):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", fn)
            group.add_action(act)
        self.view.insert_action_group("proc", group)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_child(self.view)
        sw.set_vexpand(True)
        self.append(sw)

        footer = Gtk.Box(spacing=18)
        footer.add_css_class("footer-bar")
        self.footer_label = label("", ["caption", "dim-label", "numeric"], ellipsize=True)
        self.footer_label.set_hexpand(True)
        footer.append(self.footer_label)
        self.end_button = Gtk.Button(label="End process")
        self.end_button.add_css_class("destructive-action")
        self.end_button.set_sensitive(False)
        self.end_button.connect("clicked", lambda *_: self.signal_selected(signal.SIGTERM))
        footer.append(self.end_button)
        self.append(footer)
        self.selection.connect("notify::selected-item", lambda *_: self.end_button.set_sensitive(self.selected_item() is not None))

        # default sort: CPU descending
        self.sort_by("cpu", descending=True)

    # -- columns ------------------------------------------------------------
    def _text_column(self, title: str, prop: str, sort_prop: str | None, numeric: bool, expand: bool = False,
                     width: int = 0, align_end: bool = False):
        factory = Gtk.SignalListItemFactory()

        def setup(_f, item):
            lbl = Gtk.Label(xalign=1.0 if align_end else 0.0)
            if numeric:
                lbl.add_css_class("cell-numeric")
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_hexpand(True)
            self._attach_context_gesture(lbl, item)
            item.set_child(lbl)

        def bind(_f, item):
            obj = item.get_item()
            lbl = item.get_child()
            item._binding = obj.bind_property(prop, lbl, "label", GObject.BindingFlags.SYNC_CREATE)

        def unbind(_f, item):
            b = getattr(item, "_binding", None)
            if b:
                b.unbind()
                item._binding = None

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        factory.connect("unbind", unbind)
        col = Gtk.ColumnViewColumn(title=title, factory=factory)
        col.set_expand(expand)
        col.set_resizable(True)
        if width:
            col.set_fixed_width(width)
        if sort_prop:
            expr = Gtk.PropertyExpression.new(ProcessItem, None, sort_prop)
            if sort_prop in ("name", "user", "state"):
                sorter = Gtk.StringSorter.new(expr)
                sorter.set_ignore_case(True)
            else:
                sorter = Gtk.NumericSorter.new(expr)
            col.set_sorter(sorter)
        self.view.append_column(col)
        return col

    def _build_columns(self):
        factory = Gtk.SignalListItemFactory()

        def setup(_f, item):
            box = Gtk.Box(spacing=8)
            img = Gtk.Image()
            img.set_pixel_size(16)
            box.append(img)
            lbl = Gtk.Label(xalign=0.0)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_hexpand(True)
            lbl.set_size_request(160, -1)   # keep names readable; the table scrolls sideways when narrow
            box.append(lbl)
            self._attach_context_gesture(box, item)
            item.set_child(box)

        def bind(_f, item):
            obj = item.get_item()
            box = item.get_child()
            img = box.get_first_child()
            lbl = img.get_next_sibling()
            item._b1 = obj.bind_property("name", lbl, "label", GObject.BindingFlags.SYNC_CREATE)
            set_image_icon(img, obj.icon or None)
            item._b2 = obj.connect("notify::icon", lambda o, _p: set_image_icon(img, o.icon or None))
            lbl.set_tooltip_text(obj.cmdline or obj.name)

        def unbind(_f, item):
            b = getattr(item, "_b1", None)
            if b:
                b.unbind()
                item._b1 = None
            h = getattr(item, "_b2", None)
            if h:
                item.get_item().disconnect(h)
                item._b2 = None

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        factory.connect("unbind", unbind)
        name_col = Gtk.ColumnViewColumn(title="Name", factory=factory)
        name_col.set_expand(True)
        name_col.set_resizable(True)
        expr = Gtk.PropertyExpression.new(ProcessItem, None, "name")
        sorter = Gtk.StringSorter.new(expr)
        sorter.set_ignore_case(True)
        name_col.set_sorter(sorter)
        self.view.append_column(name_col)
        self.columns = {"name": name_col}
        self.columns["pid"] = self._text_column("PID", "pid_text", "pid", True, width=80, align_end=True)
        self.columns["user"] = self._text_column("User", "user", "user", False, width=100)
        self.columns["cpu"] = self._text_column("CPU", "cpu_text", "cpu", True, width=80, align_end=True)
        self.columns["memory"] = self._text_column("Memory", "memory_text", "memory", True, width=110, align_end=True)
        self.columns["disk"] = self._text_column("Disk", "disk_text", "disk", True, width=110, align_end=True)
        self.columns["gpu"] = self._text_column("GPU", "gpu_text", "gpu", True, width=70, align_end=True)
        self.columns["gpu_mem"] = self._text_column("GPU memory", "gpu_mem_text", "gpu_mem", True, width=110, align_end=True)
        self.columns["state"] = self._text_column("State", "state", "state", False, width=100)

    def sort_by(self, key: str, descending: bool = True) -> None:
        col = self.columns.get(key)
        if col is not None:
            self.view.sort_by_column(col, Gtk.SortType.DESCENDING if descending else Gtk.SortType.ASCENDING)

    # -- filtering ------------------------------------------------------------
    def _match(self, item: ProcessItem) -> bool:
        if item.kernel and not self.show_kernel:
            return False
        q = self.search_text
        if not q:
            return True
        return (q in item.name.lower() or q in item.cmdline.lower() or q == item.pid_text or q in item.user.lower())

    def _on_search(self, entry):
        self.search_text = entry.get_text().strip().lower()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    def set_search(self, text: str) -> None:
        self.search_entry.set_text(text)
        self._on_search(self.search_entry)

    def toggle_search(self, show: bool | None = None) -> None:
        state = (not self.search_bar.get_search_mode()) if show is None else show
        self.search_bar.set_search_mode(state)
        if state:
            self.search_entry.grab_focus()

    def set_show_kernel(self, show: bool) -> None:
        self.show_kernel = show
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    # -- data ----------------------------------------------------------------
    def _icon_for(self, ps) -> str:
        key = ps.app_id or ps.exe or ps.name
        cached = self._icon_cache.get(key)
        if cached is not None:
            return cached
        entry = None
        if ps.app_id:
            entry = self.desktop.lookup_id(ps.app_id)
        if entry is None and ps.exe:
            entry = self.desktop.lookup_exec(ps.exe)
        if entry is None:
            entry = self.desktop.lookup_exec(ps.name)
        icon = entry.icon if entry and entry.icon else ""
        self._icon_cache[key] = icon
        return icon

    def update(self, snap) -> None:
        procs = snap.processes
        selected = self.selected_pid()
        gone = [pid for pid in self.items if pid not in procs]
        self.store.freeze_notify() if hasattr(self.store, "freeze_notify") else None
        if gone:
            gone_set = set(gone)
            # remove from the end so indices stay valid
            n = self.store.get_n_items()
            for i in range(n - 1, -1, -1):
                it = self.store.get_item(i)
                if it.pid in gone_set:
                    self.store.remove(i)
            for pid in gone:
                del self.items[pid]
        new_items = []
        for pid, ps in procs.items():
            item = self.items.get(pid)
            icon = self._icon_for(ps)
            if item is None:
                item = ProcessItem()
                item.apply(ps, icon)
                self.items[pid] = item
                new_items.append(item)
            else:
                item.apply(ps, icon)
        if new_items:
            self.store.splice(self.store.get_n_items(), 0, new_items)
        # property changes do not re-sort automatically
        sorter = self.view.get_sorter()
        if sorter is not None:
            sorter.changed(Gtk.SorterChange.DIFFERENT)
        if selected is not None and self.selected_pid() != selected:
            self._reselect(selected)
        total_cpu = sum(p.cpu for p in procs.values())
        total_mem = sum(p.rss for p in procs.values())
        self.footer_label.set_label(
            f"{len(procs)} processes · {self.sort_model.get_n_items()} shown · CPU {fmt_percent(min(1.0, total_cpu))} · RSS {fmt_bytes(total_mem)}")

    def _reselect(self, pid: int) -> None:
        for i in range(self.sort_model.get_n_items()):
            if self.sort_model.get_item(i).pid == pid:
                self.selection.set_selected(i)
                return

    def selected_item(self) -> ProcessItem | None:
        return self.selection.get_selected_item()

    def selected_pid(self) -> int | None:
        item = self.selected_item()
        return item.pid if item else None

    # -- actions -------------------------------------------------------------
    def _attach_context_gesture(self, widget: Gtk.Widget, list_item) -> None:
        gesture = Gtk.GestureClick(button=3)
        gesture.connect("pressed", self._on_cell_right_click, widget, list_item)
        widget.add_controller(gesture)

    def _on_cell_right_click(self, gesture, n_press, x, y, widget, list_item):
        obj = list_item.get_item()
        if obj is None:
            return
        for i in range(self.sort_model.get_n_items()):
            if self.sort_model.get_item(i) is obj:
                self.selection.set_selected(i)
                break
        ok, px, py = widget.translate_coordinates(self.view, x, y) if hasattr(widget, "translate_coordinates") else (False, 0, 0)
        if not ok:
            px, py = x, y
        self._popup_menu(px, py)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_right_click(self, gesture, n_press, x, y):
        # background click: just show the menu for the current selection
        self._popup_menu(x, y)

    def _popup_menu(self, x: float, y: float) -> None:
        from gi.repository import Gdk
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self.popover.set_pointing_to(rect)
        self.popover.popup()

    def _on_activate(self, view, position):
        self.show_details()

    def signal_selected(self, sig: int, confirm: bool = True) -> None:
        item = self.selected_item()
        if item is None:
            return
        self.signal_pid(item.pid, item.name, sig, confirm)

    def signal_pid(self, pid: int, name: str, sig: int, confirm: bool = True) -> None:
        if not confirm:
            self._send(pid, sig)
            return
        verb = "End" if sig == signal.SIGTERM else "Kill"
        dialog = Adw.AlertDialog(heading=f"{verb} \u201c{name}\u201d?",
                                 body=f"PID {pid}. Unsaved data in this process will be lost.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", f"{verb} process")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda d, r: self._send(pid, sig) if r == "ok" else None)
        dialog.present(self.window)
        self.last_dialog = dialog

    def _send(self, pid: int, sig: int) -> None:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            self.window.toast("Process already exited")
        except PermissionError:
            self.window.toast(f"Permission denied sending signal to PID {pid}")
        else:
            name = signal.Signals(sig).name
            self.window.toast(f"Sent {name} to PID {pid}")

    def show_details(self) -> None:
        item = self.selected_item()
        if item is None:
            return
        body = (f"PID: {item.pid}\nUser: {item.user}\nState: {item.state}\nThreads: {item.threads}\n"
                f"CPU: {item.cpu_text}\nMemory: {item.memory_text}\nGPU: {item.gpu_text}  ({item.gpu_mem_text})\n\n"
                f"Command line:\n{item.cmdline or '(kernel thread)'}")
        dialog = Adw.AlertDialog(heading=item.name, body=body)
        dialog.add_response("close", "Close")
        dialog.present(self.window)
