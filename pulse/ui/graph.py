"""History graphs and composition bars drawn with GTK4's native Gsk path API (no cairo needed)."""
from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402

COLORS = {
    "cpu": "#3584e4",
    "mem": "#9141ac",
    "disk": "#2ec27e",
    "net": "#e66100",
    "net2": "#f5c211",
    "gpu": "#1c9ea8",
    "gpu2": "#c061cb",
    "enc": "#33d17a",
    "dec": "#e5a50a",
    "swap": "#c01c28",
    "fg": "#777777",
}


def hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def rgba(r: float, g: float, b: float, a: float = 1.0) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = r, g, b, a
    return c


def nice_ceiling(value: float) -> float:
    """Round up to 1, 2, 2.5, 5 x 10^n so axis maxima look tidy."""
    if value <= 0:
        return 1.0
    exp = math.floor(math.log10(value))
    base = 10 ** exp
    for mult in (1.0, 2.0, 2.5, 5.0, 10.0):
        if value <= mult * base:
            return mult * base
    return 10.0 * base


class Series:
    __slots__ = ("key", "color", "fill", "dashed", "label")

    def __init__(self, key: str, color: str, fill: bool = True, dashed: bool = False, label: str | None = None):
        self.key = key
        self.color = hex_to_rgb(color)
        self.fill = fill
        self.dashed = dashed
        self.label = label


def _rect(x: float, y: float, w: float, h: float) -> Graphene.Rect:
    return Graphene.Rect().init(x, y, w, h)


def _rounded(x: float, y: float, w: float, h: float, radius: float) -> Gsk.RoundedRect:
    rr = Gsk.RoundedRect()
    rr.init_from_rect(_rect(x, y, w, h), radius)
    return rr


class HistoryGraph(Gtk.Widget):
    """Draws the last `points` values of each series from a History, newest at the right edge.

    max_value: fixed scale (1.0 for percentages) or None for auto scaling with nice ceilings.
    """

    def __init__(self, history, series: list[Series], max_value: float | None = 1.0, points: int = 60,
                 mini: bool = False, min_auto_max: float = 1.0, stacked: bool = False, border_color: str | None = None):
        super().__init__()
        self.history = history
        self.series = list(series)
        self.max_value = max_value
        self.points = max(2, points)
        self.mini = mini
        self.min_auto_max = min_auto_max
        self.stacked = stacked
        self.current_max = max_value if max_value is not None else min_auto_max
        self.border = hex_to_rgb(border_color) if border_color else (self.series[0].color if self.series else hex_to_rgb(COLORS["fg"]))
        self.grid_lines = 0 if mini else 4
        self.set_hexpand(True)
        self.set_vexpand(not mini)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        if mini:
            self.set_size_request(48, 32)
        else:
            self.set_size_request(120, 120)
        self._dash_stroke = Gsk.Stroke.new(1.0)
        self._dash_stroke.set_dash([2.0, 4.0])
        self._series_dash = Gsk.Stroke.new(1.5)
        self._series_dash.set_dash([4.0, 3.0])

    def set_points(self, points: int) -> None:
        self.points = max(2, points)
        self.queue_draw()

    def set_max(self, value: float | None) -> None:
        self.max_value = value
        self.queue_draw()

    def set_series(self, series: list[Series]) -> None:
        self.series = list(series)
        if self.series:
            self.border = self.series[0].color
        self.queue_draw()

    def refresh(self) -> None:
        self.queue_draw()

    def _compute_max(self, datasets: list[list[float]]) -> float:
        if self.max_value is not None:
            return self.max_value
        peak = 0.0
        if self.stacked and datasets:
            n = max(len(d) for d in datasets)
            for i in range(n):
                total = 0.0
                for d in datasets:
                    off = n - len(d)
                    if i >= off:
                        total += d[i - off]
                peak = max(peak, total)
        else:
            for d in datasets:
                if d:
                    peak = max(peak, max(d))
        return nice_ceiling(max(peak, self.min_auto_max))

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width = float(self.get_width())
        height = float(self.get_height())
        if width <= 2 or height <= 2:
            return
        fg = self.get_color()
        dark = fg.red + fg.green + fg.blue > 1.5  # light text => dark theme
        radius = 4.0 if self.mini else 8.0
        frame = _rounded(0.5, 0.5, width - 1.0, height - 1.0, radius)

        snapshot.push_rounded_clip(frame)
        snapshot.append_color(rgba(1, 1, 1, 0.04) if dark else rgba(0, 0, 0, 0.03), _rect(0, 0, width, height))

        datasets = [self.history.get(s.key)[-self.points:] for s in self.series]
        scale_max = self._compute_max(datasets)
        self.current_max = scale_max

        if self.grid_lines:
            builder = Gsk.PathBuilder()
            for i in range(1, self.grid_lines):
                y = round(height * i / self.grid_lines) + 0.5
                builder.move_to(0.0, y)
                builder.line_to(width, y)
            vcols = 6
            for i in range(1, vcols):
                x = round(width * i / vcols) + 0.5
                builder.move_to(x, 0.0)
                builder.line_to(x, height)
            snapshot.append_stroke(builder.to_path(), self._dash_stroke, rgba(fg.red, fg.green, fg.blue, 0.12))

        step = (width - 2.0) / (self.points - 1)
        baseline = [0.0] * self.points
        for series, data in zip(self.series, datasets):
            if not data:
                continue
            n = len(data)
            offset = self.points - n   # right align
            pts = []
            for i, v in enumerate(data):
                x = 1.0 + (offset + i) * step
                vv = max(0.0, v) / scale_max if scale_max > 0 else 0.0
                if self.stacked:
                    vv += baseline[offset + i]
                    baseline[offset + i] = vv
                y = height - 1.0 - min(1.0, vv) * (height - 2.0)
                pts.append((x, y))
            r, g, b = series.color
            if series.fill:
                fb = Gsk.PathBuilder()
                fb.move_to(pts[0][0], height)
                for x, y in pts:
                    fb.line_to(x, y)
                fb.line_to(pts[-1][0], height)
                fb.close()
                snapshot.append_fill(fb.to_path(), Gsk.FillRule.WINDING, rgba(r, g, b, 0.28 if dark else 0.22))
            if n >= 2:
                lb = Gsk.PathBuilder()
                lb.move_to(*pts[0])
                for x, y in pts[1:]:
                    lb.line_to(x, y)
                stroke = self._series_dash if series.dashed else Gsk.Stroke.new(1.2 if self.mini else 1.5)
                stroke.set_line_join(Gsk.LineJoin.ROUND)
                snapshot.append_stroke(lb.to_path(), stroke, rgba(r, g, b, 1.0))
        snapshot.pop()

        r, g, b = self.border
        border = rgba(r, g, b, 0.55)
        snapshot.append_border(frame, [1.0, 1.0, 1.0, 1.0], [border, border, border, border])


class CompositionBar(Gtk.Widget):
    """Horizontal stacked bar: segments are (fraction, color_hex) tuples."""

    def __init__(self, height: int = 28):
        super().__init__()
        self.segments: list[tuple[float, tuple[float, float, float]]] = []
        self.set_hexpand(True)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.set_size_request(100, height)

    def set_segments(self, segments: list[tuple[float, str]]) -> None:
        self.segments = [(max(0.0, f), hex_to_rgb(c)) for f, c in segments]
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width = float(self.get_width())
        height = float(self.get_height())
        if width <= 2 or height <= 2:
            return
        fg = self.get_color()
        frame = _rounded(0.5, 0.5, width - 1.0, height - 1.0, 6.0)
        snapshot.push_rounded_clip(frame)
        snapshot.append_color(rgba(fg.red, fg.green, fg.blue, 0.08), _rect(0, 0, width, height))
        x = 0.0
        for frac, (r, g, b) in self.segments:
            w = min(1.0, frac) * width
            if w <= 0:
                continue
            snapshot.append_color(rgba(r, g, b, 0.85), _rect(x, 0, w, height))
            x += w
            snapshot.append_color(rgba(1, 1, 1, 0.6), _rect(round(x), 0, 1, height))
        snapshot.pop()
        border = rgba(fg.red, fg.green, fg.blue, 0.25)
        snapshot.append_border(frame, [1.0, 1.0, 1.0, 1.0], [border, border, border, border])
