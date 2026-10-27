"""Preferences dialog."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..settings import HISTORY_CHOICES, INTERVAL_MAX_MS, INTERVAL_MIN_MS  # noqa: E402


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, settings):
        super().__init__(title="Preferences")
        self.settings = settings
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        self.add(page)

        group = Adw.PreferencesGroup(title="Sampling")
        page.add(group)
        adj = Gtk.Adjustment(lower=INTERVAL_MIN_MS / 1000.0, upper=INTERVAL_MAX_MS / 1000.0, step_increment=0.25, page_increment=1.0,
                             value=settings.get("interval_ms") / 1000.0)
        self.interval_row = Adw.SpinRow(title="Update interval", subtitle="Seconds between samples", adjustment=adj, digits=2)
        self.interval_row.connect("notify::value", self._on_interval)
        group.add(self.interval_row)

        self.history_row = Adw.ComboRow(title="Graph window", subtitle="How much history the graphs show")
        self.history_row.set_model(Gtk.StringList.new([f"{s} seconds" if s < 120 else f"{s // 60} minutes" for s in HISTORY_CHOICES]))
        self.history_row.set_selected(HISTORY_CHOICES.index(settings.get("history_seconds")))
        self.history_row.connect("notify::selected", self._on_history)
        group.add(self.history_row)

        group2 = Adw.PreferencesGroup(title="Display")
        page.add(group2)
        self.kernel_row = Adw.SwitchRow(title="Show kernel threads", subtitle="List kernel worker threads in the Processes tab")
        self.kernel_row.set_active(settings.get("show_kernel_threads"))
        self.kernel_row.connect("notify::active", lambda r, _p: settings.set("show_kernel_threads", r.get_active()))
        group2.add(self.kernel_row)
        self.virtual_row = Adw.SwitchRow(title="Hide virtual network interfaces", subtitle="Bridges, Docker, VPN tunnels")
        self.virtual_row.set_active(settings.get("hide_virtual_networks"))
        self.virtual_row.connect("notify::active", lambda r, _p: settings.set("hide_virtual_networks", r.get_active()))
        group2.add(self.virtual_row)

    def _on_interval(self, row, _p):
        self.settings.set("interval_ms", int(round(row.get_value() * 1000)))

    def _on_history(self, row, _p):
        idx = row.get_selected()
        if 0 <= idx < len(HISTORY_CHOICES):
            self.settings.set("history_seconds", HISTORY_CHOICES[idx])
