"""Performance pages (one per device), Mission Center style."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..widgets import scrolled  # noqa: E402


class Page(Gtk.Box):
    key: str = ""
    sidebar_title: str = ""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.add_css_class("page")
        self._graphs = []
        self.scroller = scrolled(self)

    @property
    def widget(self) -> Gtk.Widget:
        return self.scroller

    def register_graph(self, graph):
        self._graphs.append(graph)
        return graph

    def set_points(self, points: int) -> None:
        for g in self._graphs:
            g.set_points(points)

    def refresh_graphs(self) -> None:
        for g in self._graphs:
            g.refresh()

    def update(self, snap) -> None:
        raise NotImplementedError
