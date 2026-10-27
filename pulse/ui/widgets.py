"""Reusable UI building blocks."""
from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

FALLBACK_ICON = "application-x-executable-symbolic"
_icon_theme = None
_icon_cache: dict[str, str | None] = {}


def resolve_icon(icon: str | None) -> tuple[str, bool]:
    """Returns (icon_name_or_path, is_path). Falls back to a generic icon."""
    global _icon_theme
    if not icon:
        return FALLBACK_ICON, False
    if icon in _icon_cache:
        cached = _icon_cache[icon]
        return (cached, os.path.isabs(cached)) if cached else (FALLBACK_ICON, False)
    result: str | None = None
    if os.path.isabs(icon):
        result = icon if os.path.exists(icon) else None
    else:
        name = icon
        for ext in (".png", ".svg", ".xpm"):
            if name.endswith(ext):
                name = name[:-len(ext)]
        if _icon_theme is None:
            display = Gdk.Display.get_default()
            _icon_theme = Gtk.IconTheme.get_for_display(display) if display else None
        if _icon_theme is not None and _icon_theme.has_icon(name):
            result = name
        else:
            for d in ("/usr/share/pixmaps", "/usr/share/icons/hicolor/scalable/apps", "/usr/share/icons/hicolor/48x48/apps"):
                for ext in (".svg", ".png", ".xpm"):
                    p = os.path.join(d, name + ext)
                    if os.path.exists(p):
                        result = p
                        break
                if result:
                    break
    _icon_cache[icon] = result
    return (result, os.path.isabs(result)) if result else (FALLBACK_ICON, False)


def set_image_icon(image: Gtk.Image, icon: str | None) -> None:
    name, is_path = resolve_icon(icon)
    if is_path:
        image.set_from_gicon(Gio.FileIcon.new(Gio.File.new_for_path(name)))
    else:
        image.set_from_icon_name(name)


def label(text: str = "", css: list[str] | None = None, xalign: float = 0.0, ellipsize: bool = False,
          selectable: bool = False) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=xalign)
    for c in css or []:
        lbl.add_css_class(c)
    if ellipsize:
        from gi.repository import Pango
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
    lbl.set_selectable(selectable)
    return lbl


class PageHeader(Gtk.Box):
    """Big title on the left, model/subtitle on the right, like Mission Center."""

    def __init__(self, title: str, subtitle: str = ""):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.title = label(title, ["title-1"])
        self.title.set_hexpand(True)
        self.subtitle = label(subtitle, ["dim-label"], xalign=1.0, ellipsize=True, selectable=True)
        self.subtitle.set_valign(Gtk.Align.END)
        self.append(self.title)
        self.append(self.subtitle)


class GraphBlock(Gtk.Box):
    """Caption row (left/right) above a graph, with an optional footer row."""

    def __init__(self, graph: Gtk.Widget, left: str = "", right: str = "", footer_left: str = "", footer_right: str = "",
                 height: int = 220):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        top = Gtk.Box(spacing=12)
        self.left = label(left, ["caption", "dim-label"])
        self.left.set_hexpand(True)
        self.right = label(right, ["caption", "dim-label"], xalign=1.0)
        top.append(self.left)
        top.append(self.right)
        self.append(top)
        self.graph = graph
        graph.set_size_request(-1, height)
        self.append(graph)
        bottom = Gtk.Box(spacing=12)
        self.footer_left = label(footer_left, ["caption", "dim-label"])
        self.footer_left.set_hexpand(True)
        self.footer_right = label(footer_right, ["caption", "dim-label"], xalign=1.0)
        bottom.append(self.footer_left)
        bottom.append(self.footer_right)
        self.append(bottom)


class StatsGrid(Adw.WrapBox):
    """Live stats: dim caption above a large value. Tiles wrap to the available width."""

    def __init__(self, columns: int = 3, tile_width: int = 140):
        super().__init__(child_spacing=18, line_spacing=12)
        self.set_natural_line_length(columns * (tile_width + 18) - 18)
        self.set_valign(Gtk.Align.START)
        self.tile_width = tile_width
        self._values: dict[str, Gtk.Label] = {}

    def add(self, key: str, caption: str, initial: str = "—") -> Gtk.Label:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_size_request(self.tile_width, -1)
        box.append(label(caption, ["caption", "dim-label"]))
        value = label(initial, ["title-3", "numeric"], selectable=True)
        box.append(value)
        self.append(box)
        self._values[key] = value
        return value

    def set(self, key: str, text: str) -> None:
        lbl = self._values.get(key)
        if lbl is not None and lbl.get_label() != text:
            lbl.set_label(text)


class InfoList(Gtk.Grid):
    """Static info: 'Caption   Value' rows."""

    def __init__(self):
        super().__init__(column_spacing=18, row_spacing=6)
        self._values: dict[str, Gtk.Label] = {}
        self._rows = 0

    def add(self, key: str, caption: str, initial: str = "—") -> Gtk.Label:
        cap = label(caption, ["dim-label"])
        cap.set_valign(Gtk.Align.START)
        value = label(initial, ["numeric"], selectable=True, ellipsize=True)
        value.set_max_width_chars(36)
        value.set_size_request(170, -1)
        value.set_xalign(0.0)
        self.attach(cap, 0, self._rows, 1, 1)
        self.attach(value, 1, self._rows, 1, 1)
        self._rows += 1
        self._values[key] = value
        return value

    def set(self, key: str, text: str) -> None:
        lbl = self._values.get(key)
        if lbl is not None and lbl.get_label() != text:
            lbl.set_label(text)


def section_title(text: str) -> Gtk.Label:
    return label(text, ["heading"])


def scrolled(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    sw = Gtk.ScrolledWindow()
    # No horizontal scrolling: children wrap (Adw.WrapBox) instead of growing past the viewport
    sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sw.set_child(child)
    sw.set_vexpand(True)
    sw.set_hexpand(True)
    return sw
