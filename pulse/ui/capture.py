"""Render a widget tree to PNG without depending on the compositor's frame clock.

Used by the automated UI tests: a freshly created or occluded window may never receive
frame callbacks, so instead of asking GtkWidgetPaintable for its cached image we force a
layout pass and snapshot the subtree ourselves.
"""
from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402

log = logging.getLogger("pulse.capture")


def window_background(widget: Gtk.Widget) -> Gdk.RGBA:
    """The toplevel's CSS background is drawn by GTK itself, not by any child, so look it up."""
    ctx = widget.get_style_context()
    try:
        ok, color = ctx.lookup_color("window_bg_color")
    except (AttributeError, TypeError):
        ok, color = False, None
    if ok and color is not None:
        return color
    fg = widget.get_color()
    dark = fg.red + fg.green + fg.blue > 1.5
    rgba = Gdk.RGBA()
    rgba.parse("#222226" if dark else "#fafafa")
    return rgba


def capture_widget(widget: Gtk.Widget, path: str) -> bool:
    native = widget.get_native()
    if native is None:
        log.error("capture: widget has no native")
        return False
    renderer = native.get_renderer()
    if renderer is None:
        log.error("capture: no renderer")
        return False
    width = widget.get_width()
    height = widget.get_height()
    if width <= 0 or height <= 0:
        log.error("capture: zero size %sx%s", width, height)
        return False
    snapshot = Gtk.Snapshot()
    if isinstance(widget, Gtk.Root):
        snapshot.append_color(window_background(widget), Graphene.Rect().init(0, 0, width, height))
        # Snapshot every direct child of the window (content plus any popovers/dialogs)
        child = widget.get_first_child()
        while child is not None:
            if child.get_visible():
                child.allocate(width, height, -1, None)
                widget.snapshot_child(child, snapshot)
            child = child.get_next_sibling()
    else:
        parent = widget.get_parent()
        if parent is None:
            log.error("capture: widget has no parent")
            return False
        parent.snapshot_child(widget, snapshot)
    node = snapshot.to_node()
    if node is None:
        log.error("capture: empty render node")
        return False
    rect = Graphene.Rect().init(0, 0, width, height)
    texture = renderer.render_texture(node, rect)
    if texture is None:
        log.error("capture: render_texture returned None")
        return False
    try:
        ok = bool(texture.save_to_png(path))
    except Exception as exc:
        log.error("capture: save failed: %s", exc)
        return False
    if not ok:
        log.error("capture: save_to_png returned False for %s", path)
    return ok
