"""User settings persisted as JSON in $XDG_CONFIG_HOME/pulse/settings.json."""
from __future__ import annotations

import json
import os
import threading

DEFAULTS = {
    "interval_ms": 1000,          # sampling interval
    "history_seconds": 60,        # graph window
    "show_kernel_threads": False,
    "hide_virtual_networks": False,
    "cpu_view": "overall",        # overall | cores
    "process_sort": "cpu",
    "window_width": 1180,
    "window_height": 760,
}

INTERVAL_MIN_MS = 250
INTERVAL_MAX_MS = 10000
HISTORY_CHOICES = [30, 60, 120, 300, 600]


class Settings:
    def __init__(self, path: str | None = None):
        if path is None:
            base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
            path = os.path.join(base, "pulse", "settings.json")
        self.path = path
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
        self._listeners: list = []
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
        except (OSError, ValueError):
            return
        if isinstance(loaded, dict):
            for key in DEFAULTS:
                if key in loaded and isinstance(loaded[key], type(DEFAULTS[key])):
                    self._data[key] = loaded[key]
        self._sanitize()

    def _sanitize(self) -> None:
        d = self._data
        d["interval_ms"] = int(min(INTERVAL_MAX_MS, max(INTERVAL_MIN_MS, d["interval_ms"])))
        if d["history_seconds"] not in HISTORY_CHOICES:
            d["history_seconds"] = DEFAULTS["history_seconds"]
        if d["cpu_view"] not in ("overall", "cores"):
            d["cpu_view"] = "overall"

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def get(self, key: str):
        with self._lock:
            return self._data[key]

    def set(self, key: str, value, save: bool = True) -> None:
        with self._lock:
            if key not in DEFAULTS:
                raise KeyError(key)
            self._data[key] = value
            self._sanitize()
            value = self._data[key]
            listeners = list(self._listeners)
        for cb in listeners:
            cb(key, value)
        if save:
            self.save()

    def connect(self, callback) -> None:
        """callback(key, value) runs on the thread that called set()."""
        self._listeners.append(callback)

    def disconnect(self, callback) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    @property
    def interval_s(self) -> float:
        return self.get("interval_ms") / 1000.0

    @property
    def history_points(self) -> int:
        """Number of samples that fit in the graph window at the current interval."""
        return max(2, int(round(self.get("history_seconds") * 1000.0 / self.get("interval_ms"))))
