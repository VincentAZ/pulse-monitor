"""Main window: Performance (sidebar + pages), GPU, Processes and Apps tabs."""
from __future__ import annotations

import logging
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import APP_NAME  # noqa: E402
from .apps_view import AppsView  # noqa: E402
from .capture import capture_widget  # noqa: E402
from .gpu_view import GpuView  # noqa: E402
from .pages.cpu import CpuPage  # noqa: E402
from .pages.disk import DiskPage  # noqa: E402
from .pages.gpu import GpuPage  # noqa: E402
from .pages.memory import MemoryPage  # noqa: E402
from .pages.network import NetworkPage  # noqa: E402
from .process_view import ProcessView  # noqa: E402
from .sidebar import Sidebar  # noqa: E402

log = logging.getLogger("pulse.window")


class PulseWindow(Adw.ApplicationWindow):
    def __init__(self, app, sampler, settings):
        super().__init__(application=app, title=APP_NAME)
        self.sampler = sampler
        self.settings = settings
        self.history = sampler.history
        self.latest = None
        self.pages: dict[str, object] = {}
        self.current_page_key: str | None = None
        self._tour = None
        self.set_default_size(settings.get("window_width"), settings.get("window_height"))
        self.set_size_request(360, 400)

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)
        self.toolbar = Adw.ToolbarView()
        self.toast_overlay.set_child(self.toolbar)

        self.header = Adw.HeaderBar()
        self.toolbar.add_top_bar(self.header)
        self.view_stack = Adw.ViewStack()
        # not homogeneous: a wide tab (GPU cards) must not dictate the minimum width of the others
        self.view_stack.set_hhomogeneous(False)
        self.view_stack.set_vhomogeneous(False)
        self.switcher = Adw.ViewSwitcher(stack=self.view_stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.header.set_title_widget(self.switcher)
        self.switcher_bar = Adw.ViewSwitcherBar(stack=self.view_stack)
        self.toolbar.add_bottom_bar(self.switcher_bar)

        self.search_button = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Search processes (Ctrl+F)")
        self.search_button.set_visible(False)
        self.header.pack_start(self.search_button)
        self.sidebar_button = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text="Toggle device list")
        self.header.pack_start(self.sidebar_button)

        menu = Gio.Menu()
        menu.append("Preferences", "app.preferences")
        menu.append("Keyboard Shortcuts", "app.shortcuts")
        menu.append("About Pulse", "app.about")
        section = Gio.Menu()
        section.append("Show kernel threads", "win.kernel-threads")
        menu.append_section(None, section)
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main menu")
        self.header.pack_end(menu_button)

        self.toolbar.set_content(self.view_stack)

        # Performance tab
        points = settings.history_points
        self.split = Adw.OverlaySplitView()
        self.split.set_sidebar_width_fraction(0.26)
        self.split.set_min_sidebar_width(230)
        self.split.set_max_sidebar_width(340)
        self.sidebar = Sidebar(self.history, points, self.show_page)
        self.split.set_sidebar(self.sidebar)
        self.page_stack = Gtk.Stack()
        self.page_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.page_stack.set_hexpand(True)
        self.split.set_content(self.page_stack)
        self.view_stack.add_titled_with_icon(self.split, "performance", "Performance", "utilities-system-monitor-symbolic")
        self.sidebar_button.set_active(True)
        self.sidebar_button.connect("toggled", lambda b: self.split.set_show_sidebar(b.get_active()))

        # GPU tab
        self.gpu_view = GpuView(self.history, settings, self)
        self.view_stack.add_titled_with_icon(self.gpu_view, "gpu", "GPU", "video-display-symbolic")

        # Processes tab
        self.process_view = ProcessView(settings, sampler.desktop, self)
        self.view_stack.add_titled_with_icon(self.process_view, "processes", "Processes", "view-list-symbolic")
        self.search_button.bind_property("active", self.process_view.search_bar, "search-mode-enabled", 3)  # BIDIRECTIONAL|SYNC_CREATE

        # Apps tab
        self.apps_view = AppsView(self)
        self.view_stack.add_titled_with_icon(self.apps_view, "apps", "Apps", "view-grid-symbolic")

        self.view_stack.connect("notify::visible-child-name", self._on_tab_changed)
        self._on_tab_changed()

        # responsive layout
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        bp.add_setter(self.switcher_bar, "reveal", True)
        self.narrow_title = Adw.WindowTitle(title=APP_NAME)
        bp.add_setter(self.header, "title-widget", self.narrow_title)
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        # actions
        kernel_action = Gio.SimpleAction.new_stateful("kernel-threads", None, GLib.Variant.new_boolean(settings.get("show_kernel_threads")))
        kernel_action.connect("change-state", self._on_kernel_threads)
        self.add_action(kernel_action)
        for name, cb in (("search", lambda *_: self.focus_search()),
                         ("tab-performance", lambda *_: self.view_stack.set_visible_child_name("performance")),
                         ("tab-gpu", lambda *_: self.view_stack.set_visible_child_name("gpu")),
                         ("tab-processes", lambda *_: self.view_stack.set_visible_child_name("processes")),
                         ("tab-apps", lambda *_: self.view_stack.set_visible_child_name("apps")),
                         ("end-process", lambda *_: self.process_view.signal_selected(15)),
                         ("refresh", lambda *_: self.sampler.request_sample())):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)

        self.settings.connect(self._on_setting_changed)
        self.connect("close-request", self._on_close)
        self.connect("notify::default-width", self._remember_size)
        self.connect("notify::default-height", self._remember_size)

        # placeholder while the first sample arrives
        self.status = Adw.StatusPage(title="Collecting data…", icon_name="utilities-system-monitor-symbolic")
        self.page_stack.add_named(self.status, "status")

    # -- helpers -------------------------------------------------------------
    def toast(self, text: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=text, timeout=3))

    def focus_search(self) -> None:
        self.view_stack.set_visible_child_name("processes")
        self.process_view.toggle_search(True)

    def show_processes_for_app(self, name: str, app_id: str) -> None:
        self.view_stack.set_visible_child_name("processes")
        self.process_view.toggle_search(True)
        self.process_view.set_search(name.split(" ")[0])

    def _on_kernel_threads(self, action, value):
        action.set_state(value)
        self.settings.set("show_kernel_threads", value.get_boolean())

    def _on_tab_changed(self, *_):
        name = self.view_stack.get_visible_child_name()
        self.search_button.set_visible(name == "processes")
        self.sidebar_button.set_visible(name == "performance")
        if self.latest is not None:
            self._update_visible(self.latest, force=True)

    def _remember_size(self, *_):
        width, height = self.get_default_size()
        if width > 0 and height > 0:
            self.settings.set("window_width", width, save=False)
            self.settings.set("window_height", height, save=False)

    def _on_close(self, *_):
        self.settings.save()
        return False

    def _on_setting_changed(self, key, value):
        if key in ("interval_ms", "history_seconds"):
            points = self.settings.history_points
            self.history.set_capacity(points)
            self.sampler.set_interval(self.settings.interval_s)
            self.sidebar.set_points(points)
            self.gpu_view.set_points(points)
            for p in self.pages.values():
                p.set_points(points)
        elif key == "show_kernel_threads":
            self.process_view.set_show_kernel(bool(value))
            act = self.lookup_action("kernel-threads")
            if act is not None and act.get_state().get_boolean() != bool(value):
                act.set_state(GLib.Variant.new_boolean(bool(value)))
        elif key == "hide_virtual_networks" and self.latest is not None:
            self._ensure_pages(self.latest)

    # -- pages ---------------------------------------------------------------
    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        self.current_page_key = key
        self.page_stack.set_visible_child_name(key)
        if self.status is not None:
            GLib.idle_add(self._hide_status)
        self.sidebar.select(key)
        if self.latest is not None:
            page.update(self.latest)
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)

    def _ensure_pages(self, snap) -> None:
        points = self.settings.history_points
        keys = self.sidebar.build(snap, self.settings.get("hide_virtual_networks"))
        if "cpu" not in self.pages:
            self._add_page(CpuPage(self.history, self.settings, points))
        if "memory" not in self.pages:
            self._add_page(MemoryPage(self.history, self.settings, points))
        for i, name in enumerate(snap.disk_infos):
            if f"disk.{name}" not in self.pages:
                self._add_page(DiskPage(self.history, self.settings, points, name, i))
        for name, info in snap.net_infos.items():
            if f"net.{name}" not in self.pages:
                self._add_page(NetworkPage(self.history, self.settings, points, name, info.kind))
        for i, (gkey, info) in enumerate(snap.gpu_infos.items()):
            if f"gpu.{gkey}" not in self.pages:
                self._add_page(GpuPage(self.history, self.settings, points, gkey, i, info))
        if self.current_page_key is None or self.current_page_key not in keys:
            self.show_page(keys[0] if keys else "cpu")

    def _add_page(self, page) -> None:
        self.pages[page.key] = page
        self.page_stack.add_named(page.widget, page.key)

    def _hide_status(self) -> bool:
        # Removing the placeholder from a mapped stack trips a GTK critical; hiding it is enough.
        if self.status is not None and self.page_stack.get_visible_child() is not self.status:
            self.status.set_visible(False)
            self.status = None
        return False

    # -- snapshot delivery -----------------------------------------------
    def on_snapshot(self, snap) -> None:
        """Runs on the GTK main thread."""
        first = self.latest is None
        self.latest = snap
        if first or set(snap.disk_infos) | set(snap.net_infos) | set(snap.gpu_infos) != self._device_set:
            self._device_set = set(snap.disk_infos) | set(snap.net_infos) | set(snap.gpu_infos)
            self._ensure_pages(snap)
        self._update_visible(snap)
        if snap.errors and snap.seq % 30 == 1:
            log.warning("collector errors: %s", snap.errors)

    _device_set: set = set()

    def _update_visible(self, snap, force: bool = False) -> None:
        tab = self.view_stack.get_visible_child_name()
        if tab == "performance" or force:
            self.sidebar.update(snap)
            page = self.pages.get(self.current_page_key or "")
            if page is not None:
                page.update(snap)
        if tab == "gpu" or force:
            self.gpu_view.update(snap)
        if tab == "processes" or force:
            self.process_view.update(snap)
        if tab == "apps" or force:
            self.apps_view.update(snap)

    # -- automated screenshot tour (used by tests) -------------------------
    def set_animations(self, enabled: bool) -> None:
        gtk_settings = Gtk.Settings.get_default()
        if gtk_settings is not None:
            gtk_settings.set_property("gtk-enable-animations", enabled)
        kind = Gtk.StackTransitionType.CROSSFADE if enabled else Gtk.StackTransitionType.NONE
        self.page_stack.set_transition_type(kind)
        cpu = self.pages.get("cpu")
        if cpu is not None:
            cpu.stack.set_transition_type(kind)

    def start_tour(self, directory: str, done, delay_ms: int = 900) -> None:
        os.makedirs(directory, exist_ok=True)
        self.set_animations(False)
        steps = []
        steps.append(("performance-cpu", lambda: (self.view_stack.set_visible_child_name("performance"), self.show_page("cpu"), self.pages["cpu"].set_view("overall"))))
        steps.append(("performance-cpu-cores", lambda: self.pages["cpu"].set_view("cores")))
        steps.append(("performance-memory", lambda: self.show_page("memory")))
        for key in list(self.pages):
            if key.startswith("disk."):
                steps.append((f"performance-{key}", lambda k=key: self.show_page(k)))
        for key in list(self.pages):
            if key.startswith("net."):
                steps.append((f"performance-{key}", lambda k=key: self.show_page(k)))
        for key in list(self.pages):
            if key.startswith("gpu."):
                steps.append((f"performance-{key.replace(':', '-')}", lambda k=key: self.show_page(k)))
        steps.append(("gpu-tab", lambda: self.view_stack.set_visible_child_name("gpu")))
        steps.append(("processes", lambda: (self.view_stack.set_visible_child_name("processes"), self.process_view.toggle_search(False))))
        steps.append(("processes-search", lambda: (self.process_view.toggle_search(True), self.process_view.set_search("python"))))
        steps.append(("apps", lambda: (self.process_view.set_search(""), self.process_view.toggle_search(False), self.view_stack.set_visible_child_name("apps"))))
        results: list[tuple[str, bool]] = []
        state = {"i": 0, "phase": 0}

        def step():
            i = state["i"]
            if i >= len(steps):
                self.view_stack.set_visible_child_name("performance")
                done(results)
                return False
            name, fn = steps[i]
            if state["phase"] == 0:
                try:
                    fn()
                except Exception as exc:
                    log.error("tour step %s failed: %s", name, exc)
                    results.append((name, False))
                    state["i"] += 1
                    return True
                if self.latest is not None:
                    self._update_visible(self.latest, force=True)
                state["phase"] = 1
                return True
            path = os.path.join(directory, f"{i:02d}-{name}.png")
            ok = capture_widget(self, path)
            results.append((name, ok))
            state["phase"] = 0
            state["i"] += 1
            return True

        GLib.timeout_add(delay_ms, step)
