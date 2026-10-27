"""Adw.Application, CLI options and global actions."""
from __future__ import annotations

import logging
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import APP_ID, APP_NAME, __version__  # noqa: E402
from ..sampler import Sampler  # noqa: E402
from ..settings import Settings  # noqa: E402
from .prefs import PreferencesDialog  # noqa: E402
from .window import PulseWindow  # noqa: E402

log = logging.getLogger("pulse.app")


class PulseApplication(Adw.Application):
    def __init__(self):
        # NON_UNIQUE: a monitor may be opened several times (and tests run instances in parallel)
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.settings = Settings()
        self.sampler: Sampler | None = None
        self.window: PulseWindow | None = None
        self.screenshot_dir: str | None = None
        self.exit_after: float | None = None
        self.tour_delay: float = 4.0
        self.add_main_option("version", ord("v"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Print version and exit", None)
        self.add_main_option("interval", ord("i"), GLib.OptionFlags.NONE, GLib.OptionArg.DOUBLE, "Sampling interval in seconds", "SECONDS")
        self.add_main_option("screenshot-dir", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Run an automated tour, save a PNG of every view to DIR, then exit", "DIR")
        self.add_main_option("tour-delay", 0, GLib.OptionFlags.NONE, GLib.OptionArg.DOUBLE, "Seconds to wait before the screenshot tour (default 4)", "SECONDS")
        self.add_main_option("exit-after", 0, GLib.OptionFlags.NONE, GLib.OptionArg.DOUBLE, "Quit after N seconds (testing)", "SECONDS")
        self.add_main_option("config", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING, "Settings file to use", "PATH")
        self.connect("handle-local-options", self._on_local_options)
        self.connect("shutdown", self._on_shutdown)

    def _on_local_options(self, app, options: GLib.VariantDict) -> int:
        if options.contains("version"):
            print(f"{APP_NAME} {__version__}")
            return 0
        if options.contains("config"):
            self.settings = Settings(options.lookup_value("config", None).get_string())
        if options.contains("interval"):
            self.settings.set("interval_ms", int(options.lookup_value("interval", None).get_double() * 1000), save=False)
        if options.contains("screenshot-dir"):
            self.screenshot_dir = options.lookup_value("screenshot-dir", None).get_string()
        if options.contains("tour-delay"):
            self.tour_delay = options.lookup_value("tour-delay", None).get_double()
        if options.contains("exit-after"):
            self.exit_after = options.lookup_value("exit-after", None).get_double()
        return -1

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(os.path.dirname(__file__), "style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for name, cb, accels in (("quit", lambda *_: self.quit(), ["<Control>q", "<Control>w"]),
                                 ("preferences", self._on_preferences, ["<Control>comma"]),
                                 ("about", self._on_about, []),
                                 ("shortcuts", self._on_shortcuts, ["<Control>question"])):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)
        self.set_accels_for_action("win.search", ["<Control>f"])
        self.set_accels_for_action("win.tab-performance", ["<Control>1"])
        self.set_accels_for_action("win.tab-gpu", ["<Control>2"])
        self.set_accels_for_action("win.tab-processes", ["<Control>3"])
        self.set_accels_for_action("win.tab-apps", ["<Control>4"])
        self.set_accels_for_action("win.end-process", ["Delete"])
        self.set_accels_for_action("win.refresh", ["F5"])

    def do_activate(self):
        if self.window is None:
            self.sampler = Sampler(interval_s=self.settings.interval_s, history_points=self.settings.history_points,
                                   callback=self._deliver_snapshot)
            self.window = PulseWindow(self, self.sampler, self.settings)
            self.sampler.start()
            if self.screenshot_dir:
                GLib.timeout_add(int(self.tour_delay * 1000), self._begin_tour)
            if self.exit_after:
                GLib.timeout_add(int(self.exit_after * 1000), lambda: (self.quit(), False)[1])
        self.window.present()

    def _deliver_snapshot(self, snap):
        GLib.idle_add(self._on_snapshot_main, snap, priority=GLib.PRIORITY_DEFAULT)

    def _on_snapshot_main(self, snap):
        if self.window is not None:
            try:
                self.window.on_snapshot(snap)
            except Exception:
                log.exception("UI update failed")
        return False

    def _begin_tour(self):
        def done(results):
            failed = [n for n, ok in results if not ok]
            for name, ok in results:
                print(f"screenshot {'ok  ' if ok else 'FAIL'} {name}")
            print(f"tour finished: {len(results) - len(failed)}/{len(results)} captured")
            self._tour_failed = bool(failed)
            self.quit()
        self.window.start_tour(self.screenshot_dir, done)
        return False

    def _on_shutdown(self, *_):
        if self.sampler:
            self.sampler.stop(join=True)
        self.settings.save()

    def _on_preferences(self, *_):
        PreferencesDialog(self.settings).present(self.window)

    def _on_about(self, *_):
        about = Adw.AboutDialog(application_name=APP_NAME, application_icon=APP_ID, version=__version__,
                                developer_name="Pulse contributors", license_type=Gtk.License.MIT_X11,
                                comments="System and GPU monitor for Linux. CPU, memory, disks, network, NVIDIA / AMD / Intel GPUs, "
                                         "processes and apps, read straight from /proc, /sys, DRM fdinfo and NVML.",
                                website="https://github.com/", issue_url="https://github.com/")
        about.present(self.window)

    def _on_shortcuts(self, *_):
        try:
            dialog = Adw.ShortcutsDialog()
            section = Adw.ShortcutsSection(title="General")
            for title, accel in (("Search processes", "<Control>f"), ("Preferences", "<Control>comma"),
                                 ("Performance tab", "<Control>1"), ("GPU tab", "<Control>2"), ("Processes tab", "<Control>3"),
                                 ("Apps tab", "<Control>4"), ("End selected process", "Delete"), ("Refresh now", "F5"),
                                 ("Keyboard shortcuts", "<Control>question"), ("Quit", "<Control>q")):
                section.add(Adw.ShortcutsItem.new(title, accel))
            dialog.add(section)
            dialog.present(self.window)
        except (AttributeError, TypeError) as exc:
            log.warning("shortcuts dialog unavailable: %s", exc)
            self.window.toast("Ctrl+F search · Ctrl+1..4 tabs · Ctrl+, preferences · Ctrl+Q quit")


def Gdk_display():
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk
    return Gdk.Display.get_default()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("PULSE_LOG", "WARNING"), format="%(levelname)s %(name)s: %(message)s")
    app = PulseApplication()
    rc = app.run(sys.argv if argv is None else argv)
    if getattr(app, "_tour_failed", False):
        return 2
    return rc
